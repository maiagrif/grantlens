"""Label grants with Claude, using the shared categories in taxonomy.py.

Typical flow:
    uv run python label.py sample                          # pick the 300-grant validation sample (free)
    uv run python label.py validate opus sonnet haiku      # label the sample with each model; shows cost
    uv run python db.py && uv run python benchmark.py      # score each model against the funders' own categories
    uv run python label.py submit haiku                    # label every in-scope grant via the Batch API (asks first)
    uv run python label.py collect                         # fetch finished batches
    uv run python db.py                                    # rebuild the database with the new labels

Labels are appended to data/labels/<model>.jsonl, and grants that already have a label for a model are skipped,
so every command can be re-run safely.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

import db
import taxonomy

MODELS = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5"}
# Standard $ per million tokens (input, output). The Batch API charges half.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}
SAMPLE_PATH = db.DATA / "validation_sample.csv"
BATCHES_PATH = db.LABELS_DIR / "batches.json"
SAMPLE_PER_FUNDER = 150


def resolve(name: str) -> str:
    return MODELS.get(name, name)


def request_params(model: str, grant: dict) -> dict:
    """One labeling request. No `fallbacks`: each label must come from the model being measured."""
    return {
        "model": model,
        "max_tokens": 4096,  # thinking counts toward this; labels themselves are ~60 tokens
        "system": [{"type": "text", "text": taxonomy.LABEL_INSTRUCTIONS, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": taxonomy.grant_text(grant)}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": taxonomy.LABEL_SCHEMA}},
    }


def parse_label(message, grant_id: str, model: str) -> dict | None:
    if message.stop_reason in ("refusal", "max_tokens"):
        return None
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return {"grant_id": grant_id, "model": model, **{k: data[k] for k in taxonomy.LABEL_SCHEMA["required"]}}


def labeled_ids(model: str) -> set[str]:
    path = db.LABELS_DIR / f"{model}.jsonl"
    if not path.exists():
        return set()
    return {json.loads(line)["grant_id"] for line in path.open(encoding="utf-8") if line.strip()}


def append_labels(model: str, labels: list[dict]) -> None:
    db.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    with (db.LABELS_DIR / f"{model}.jsonl").open("a", encoding="utf-8") as fh:
        for lab in labels:
            fh.write(json.dumps(lab, ensure_ascii=False) + "\n")


def in_scope_grants(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM grants WHERE in_scope = 1 ORDER BY grant_id")]


def sample_ids() -> list[str]:
    with SAMPLE_PATH.open(encoding="utf-8") as fh:
        return [r["grant_id"] for r in csv.DictReader(fh)]


def cost(model: str, usage: dict, batch: bool) -> float:
    pin, pout = PRICES.get(model, (0, 0))
    c = (usage["input_tokens"] * pin + usage["cache_write"] * pin * 1.25 + usage["cache_read"] * pin * 0.1
         + usage["output_tokens"] * pout) / 1e6
    return c / 2 if batch else c


# ------------------------------------------------------------------ commands

def cmd_sample(args) -> None:
    """Random grants per funder, drawn from those with a funder-assigned theme so every one can be scored."""
    import benchmark  # noqa: PLC0415

    if SAMPLE_PATH.exists() and not args.force:
        sys.exit(f"{SAMPLE_PATH.name} already exists (labels and any hand labels refer to it). Use --force to redraw.")
    rng = random.Random(2026)
    refs = benchmark.references()
    rows = []
    for funder in sorted({v["funder"] for v in refs.values()}):
        ids = sorted(g for g, v in refs.items() if v["funder"] == funder)
        rows += [{"grant_id": g, "funder": funder} for g in rng.sample(ids, min(SAMPLE_PER_FUNDER, len(ids)))]
    rng.shuffle(rows)
    with SAMPLE_PATH.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["grant_id", "funder"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} grants to {SAMPLE_PATH.relative_to(db.DATA.parent)}")


def cmd_validate(args) -> None:
    con = db.connect()
    grants = db.get(con, sample_ids())
    client = anthropic.Anthropic()
    n_scope = con.execute("SELECT COUNT(*) FROM grants WHERE in_scope = 1").fetchone()[0]
    print(f"{'model':<20}{'labeled':>9}{'failed':>8}{'$ this run':>12}{'$ per grant':>13}{'est. $ full batch':>19}")
    for model in map(resolve, args.models):
        todo = [g for g in grants if g["grant_id"] not in labeled_ids(model)]
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}

        def one(g):
            msg = client.messages.create(**request_params(model, g))
            u = msg.usage
            return parse_label(msg, g["grant_id"], model), {
                "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "cache_write": u.cache_creation_input_tokens or 0, "cache_read": u.cache_read_input_tokens or 0}

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(one, todo))
        labels = [lab for lab, _ in results if lab]
        for _, u in results:
            for k in usage:
                usage[k] += u[k]
        append_labels(model, labels)
        spent = cost(model, usage, batch=False)
        per = cost(model, usage, batch=True) / max(len(todo), 1)
        print(f"{model:<20}{len(labels):>9}{len(todo) - len(labels):>8}{spent:>12.2f}{per:>13.5f}"
              f"{per * n_scope:>19.2f}" if todo else f"{model:<20}  (sample already labeled)")
    print("\nRebuild the database (uv run python db.py) and open the app's “Check the AI” page to compare models.")


def cmd_submit(args) -> None:
    model = resolve(args.model)
    con = db.connect()
    done = labeled_ids(model)
    grants = [g for g in in_scope_grants(con) if g["grant_id"] not in done][: args.limit or None]
    if not grants:
        sys.exit(f"Nothing to label: every in-scope grant already has a {model} label.")
    print(f"{len(grants):,} grants to label with {model} through the Batch API (half price).")
    vpath = db.LABELS_DIR / f"{model}.jsonl"
    if not vpath.exists():
        print("Run `label.py validate` first to see a measured cost per grant for this model.")
    if not args.yes and input("Submit? [y/N] ").strip().lower() != "y":
        sys.exit("Cancelled.")
    client = anthropic.Anthropic()
    batch_ids = []
    for i in range(0, len(grants), 20_000):  # stay well under the per-batch limits
        chunk = grants[i:i + 20_000]
        batch = client.messages.batches.create(requests=[
            Request(custom_id=g["grant_id"], params=MessageCreateParamsNonStreaming(**request_params(model, g)))
            for g in chunk
        ])
        batch_ids.append(batch.id)
        print(f"submitted {batch.id} ({len(chunk):,} requests)")
    pending = json.loads(BATCHES_PATH.read_text()) if BATCHES_PATH.exists() else []
    pending += [{"id": b, "model": model} for b in batch_ids]
    BATCHES_PATH.write_text(json.dumps(pending, indent=2))
    print("Most batches finish within an hour. Then run: uv run python label.py collect")


def cmd_collect(args) -> None:
    if not BATCHES_PATH.exists():
        sys.exit("No submitted batches.")
    client = anthropic.Anthropic()
    pending = json.loads(BATCHES_PATH.read_text())
    still = []
    for b in pending:
        batch = client.messages.batches.retrieve(b["id"])
        if batch.processing_status != "ended":
            c = batch.request_counts
            print(f"{b['id']}: {batch.processing_status} ({c.succeeded:,} done, {c.processing:,} processing)")
            still.append(b)
            continue
        labels, failed = [], 0
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}
        done = labeled_ids(b["model"])
        for res in client.messages.batches.results(b["id"]):
            if res.result.type != "succeeded":
                failed += 1
                continue
            msg = res.result.message
            usage["input_tokens"] += msg.usage.input_tokens
            usage["output_tokens"] += msg.usage.output_tokens
            usage["cache_write"] += msg.usage.cache_creation_input_tokens or 0
            usage["cache_read"] += msg.usage.cache_read_input_tokens or 0
            lab = parse_label(msg, res.custom_id, b["model"])
            if lab and lab["grant_id"] not in done:
                labels.append(lab)
            elif not lab:
                failed += 1
        append_labels(b["model"], labels)
        print(f"{b['id']}: saved {len(labels):,} labels, {failed:,} failed, cost ≈ ${cost(b['model'], usage, True):.2f}")
    BATCHES_PATH.write_text(json.dumps(still, indent=2))
    if not still:
        print("All batches collected. Rebuild with: uv run python db.py  (failed grants can be resubmitted with `submit`)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample", help="draw the validation sample")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_sample)
    p = sub.add_parser("validate", help="label the validation sample with one or more models")
    p.add_argument("models", nargs="+", help="opus, sonnet, haiku, or a full model ID")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("submit", help="label all in-scope grants with one model via the Batch API")
    p.add_argument("model")
    p.add_argument("--limit", type=int, help="label only the first N unlabeled grants")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.set_defaults(fn=cmd_submit)
    p = sub.add_parser("collect", help="fetch results of submitted batches")
    p.set_defaults(fn=cmd_collect)
    args = ap.parse_args()
    args.fn(args)
