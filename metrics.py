"""How well do the AI labels match a person's? Hand labels live in data/human_labels.csv."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime

import db
import taxonomy

HUMAN_PATH = db.DATA / "human_labels.csv"
HUMAN_FIELDS = ["grant_id", "theme", "strategy", "population", "note", "labeled_at"]


def load_human() -> dict[str, dict]:
    if not HUMAN_PATH.exists():
        return {}
    with HUMAN_PATH.open(encoding="utf-8", newline="") as fh:
        return {r["grant_id"]: r for r in csv.DictReader(fh)}


def save_human(grant_id: str, theme: str, strategy: str, population: str, note: str = "") -> None:
    rows = load_human()
    rows[grant_id] = {"grant_id": grant_id, "theme": theme, "strategy": strategy, "population": population,
                      "note": note, "labeled_at": datetime.now().isoformat(timespec="seconds")}
    with HUMAN_PATH.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, HUMAN_FIELDS)
        w.writeheader()
        w.writerows(rows.values())


def cohens_kappa(a: list[str], b: list[str]) -> float | None:
    """Agreement beyond chance between two raters: 1 = perfect, 0 = no better than chance."""
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def interpret(k: float | None) -> str:
    """Landis & Koch (1977) bands, the convention in health research."""
    if k is None:
        return ""
    for cut, word in ((0.8, "almost perfect"), (0.6, "substantial"), (0.4, "moderate"), (0.2, "fair"), (0.0, "slight")):
        if k > cut:
            return word
    return "poor"


def model_labels(con, model: str, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    q = f"SELECT * FROM labels WHERE model = ? AND grant_id IN ({', '.join('?' * len(ids))})"
    return {r["grant_id"]: dict(r) for r in con.execute(q, [model, *ids])}


def agreement(con, funder: str | None = None) -> list[dict]:
    """One row per model: how often it matches the hand labels, per field (optionally one funder's sample only)."""
    human = load_human()
    if funder:
        keep = {g["grant_id"] for g in db.get(con, list(human)) if g["funder"] == funder}
        human = {k: v for k, v in human.items() if k in keep}
    models = [r[0] for r in con.execute("SELECT DISTINCT model FROM labels ORDER BY model")]
    out = []
    for m in models:
        ai = model_labels(con, m, list(human))
        both = [g for g in human if g in ai]
        if not both:
            continue
        row = {"model": m, "grants compared": len(both)}
        for f in taxonomy.FIELDS:
            h, a = [human[g][f] for g in both], [ai[g][f] for g in both]
            row[f"{f} agree %"] = round(100 * sum(x == y for x, y in zip(h, a)) / len(both))
            row[f"{f} kappa"] = cohens_kappa(h, a)
        out.append(row)
    return out


def disagreements(con, model: str) -> list[dict]:
    human = load_human()
    ai = model_labels(con, model, list(human))
    grants = {g["grant_id"]: g for g in db.get(con, list(ai))}
    rows = []
    for gid, a in ai.items():
        h = human[gid]
        diff = [f for f in taxonomy.FIELDS if h[f] != a[f]]
        if diff:
            g = grants.get(gid, {})
            rows.append({
                "grant": g.get("url"), "funder": g.get("funder"), "grantee": g.get("grantee"),
                "fields": ", ".join(diff),
                **{f"you: {f}": h[f] for f in diff}, **{f"AI: {f}": a[f] for f in diff},
                "AI confidence": a["confidence"], "AI rationale": a["rationale"], "your note": h.get("note", ""),
            })
    return rows


def confusion(con, model: str, field: str = "theme") -> list[dict]:
    human = load_human()
    ai = model_labels(con, model, list(human))
    c = Counter((human[g][field], ai[g][field]) for g in ai)
    return [{"reference": h, "AI": a, "grants": n} for (h, a), n in c.items()]


def accuracy_by_confidence(con, model: str) -> list[dict]:
    """Does the model know when it's unsure? Theme agreement split by the model's own confidence."""
    human = load_human()
    ai = model_labels(con, model, list(human))
    out = []
    for level in taxonomy.CONFIDENCE:
        ids = [g for g, a in ai.items() if a["confidence"] == level]
        if ids:
            out.append({"AI confidence": level, "grants": len(ids),
                        "theme agree %": round(100 * sum(human[g]["theme"] == ai[g]["theme"] for g in ids) / len(ids))})
    return out
