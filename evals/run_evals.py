"""Score the assistant on evals/questions.yaml.

    uv run python evals/run_evals.py            # all questions (~11 API calls; roughly $1-3 per run on Opus)
    uv run python evals/run_evals.py flint-water # one question by id

Writes evals/results/<timestamp>.json and prints a scorecard.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent  # noqa: E402
import db as grants_db  # noqa: E402

HERE = Path(__file__).parent
SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "b": 1e9}
NUM = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(thousand|million|billion|[kmb])?\b", re.I)


def numbers_in(text: str) -> list[float]:
    text = agent.CITE.sub(" ", text)  # grant IDs are not answers
    out = []
    for m in NUM.finditer(text):
        v = float(m.group(1).replace(",", ""))
        if m.group(2):
            v *= SCALE[m.group(2).lower()]
        out.append(v)
    return out


def check(case: dict, ans: agent.Answer, con) -> tuple[bool, str]:
    kind = case["kind"]
    if kind == "decline":
        said = any(p in ans.text.lower() for p in case["expect_any"])
        return said and not ans.unknown_ids, "said data can't answer" if said else "did not say data can't answer"

    rows = grants_db.run_select(con, case["expected_sql"], max_rows=5000)["rows"]
    if kind == "number":
        expected = float(rows[0][0] or 0)
        is_money = "amount_usd" in case["expected_sql"]
        tol = 0.01 * expected if is_money else 0.5  # dollar figures may be rounded; counts must be exact
        hit = any(abs(v - expected) <= tol for v in numbers_in(ans.text))
        return hit, f"expected {expected:,.0f}"
    if kind == "text":
        expected = str(rows[0][0])
        return expected.lower() in ans.text.lower(), f"expected “{expected}”"
    if kind == "retrieve":
        expected = {r[0] for r in rows}
        found = expected & set(ans.cited_ids)
        return bool(found), f"{len(found)} of {len(ans.cited_ids)} cited are in the {len(expected)} relevant grants"
    raise ValueError(kind)


def main() -> None:
    cases = yaml.safe_load((HERE / "questions.yaml").read_text())
    if len(sys.argv) > 1:
        cases = [c for c in cases if c["id"] in sys.argv[1:]]
    con = grants_db.connect()
    results = []
    for c in cases:
        t0 = time.time()
        try:
            ans = agent.ask(c["question"])
            ok, note = check(c, ans, con)
        except Exception as e:
            ans, ok, note = None, False, f"error: {type(e).__name__}: {e}"
        results.append({
            "id": c["id"], "kind": c["kind"], "pass": ok, "note": note,
            "citations_valid": bool(ans) and not ans.unknown_ids,
            "seconds": round(time.time() - t0, 1),
            "usage": ans.usage if ans else {},
            "answer": ans.text if ans else None,
        })
        print(f"{'PASS' if ok else 'FAIL'}  {c['id']:<32} {note}")

    n = len(results)
    passed = sum(r["pass"] for r in results)
    valid = sum(r["citations_valid"] for r in results)
    tokens_in = sum(r["usage"].get("input_tokens", 0) for r in results)
    tokens_out = sum(r["usage"].get("output_tokens", 0) for r in results)
    print(f"\nAccuracy: {passed}/{n}   Citations all valid: {valid}/{n}   "
          f"Tokens: {tokens_in:,} in / {tokens_out:,} out")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"model": agent.MODEL, "results": results}, indent=2, ensure_ascii=False))
    print(f"Saved {path.relative_to(HERE.parent)}")


if __name__ == "__main__":
    main()
