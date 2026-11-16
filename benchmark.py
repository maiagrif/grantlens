"""Check AI labels without hand-labeling.

1. Theme accuracy vs the funder's own staff categories. data/funder_category_map.csv maps each funder category
   (Gates topic; Mott program | initiative) to one shared theme, or leaves it blank when the category spans themes.
   A grant gets a reference theme only when its mapped categories point to exactly one theme. The labelers never see
   these categories (taxonomy.grant_text), so they can't copy them.
2. Consistency between models, for every field, including strategy and population, which funders don't record.
   This is agreement, not accuracy: two models can agree and both be wrong.
"""

from __future__ import annotations

import csv
from collections import Counter
from functools import lru_cache
from itertools import combinations

import db
import metrics
import taxonomy

MAP_PATH = db.DATA / "funder_category_map.csv"


@lru_cache(maxsize=1)
def category_map() -> dict[tuple[str, str], str]:
    with MAP_PATH.open(encoding="utf-8", newline="") as fh:
        return {(r["funder"], r["category"]): r["theme"] for r in csv.DictReader(fh)}


def categories(g: dict) -> list[str]:
    if g["funder"] == "Gates Foundation":
        return [t.strip() for t in (g["subprogram"] or "").split("|") if t.strip()]
    return [f"{g['program']} | {g['subprogram']}"]


def reference_theme(g: dict) -> str | None:
    cmap = category_map()
    themes = {cmap.get((g["funder"], c)) for c in categories(g)} - {None, ""}
    return themes.pop() if len(themes) == 1 else None


@lru_cache(maxsize=1)
def _references_cached(db_mtime: float) -> dict[str, dict]:
    con = db.connect()
    out = {}
    for r in con.execute("SELECT grant_id, funder, program, subprogram FROM grants WHERE in_scope = 1"):
        ref = reference_theme(dict(r))
        if ref:
            out[r["grant_id"]] = {"funder": r["funder"], "theme": ref}
    return out


def references() -> dict[str, dict]:
    """grant_id -> {funder, theme} for every in-scope grant with an unambiguous funder-assigned theme."""
    return _references_cached(db.DB_PATH.stat().st_mtime)


def coverage(con) -> list[dict]:
    refs = references()
    out = []
    for f, n in con.execute("SELECT funder, COUNT(*) FROM grants WHERE in_scope = 1 GROUP BY funder ORDER BY funder"):
        k = sum(1 for v in refs.values() if v["funder"] == f)
        out.append({"funder": f, "in scope": n, "with a funder theme": k, "share": k / n})
    return out


def _labels(con, model: str) -> dict[str, dict]:
    return {r["grant_id"]: dict(r) for r in con.execute("SELECT * FROM labels WHERE model = ?", [model])}


def models(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT model FROM labels GROUP BY model ORDER BY COUNT(*) DESC, model")]


def _pairs(con, model: str, funder: str | None, sample_only: bool) -> list[tuple[str, str, dict]]:
    refs, labs = references(), _labels(con, model)
    ids = set(label_sample_ids()) if sample_only else set(refs)
    return [(gid, refs[gid]["theme"], labs[gid]) for gid in ids
            if gid in refs and gid in labs and (not funder or refs[gid]["funder"] == funder)]


def label_sample_ids() -> list[str]:
    import label  # noqa: PLC0415  (avoid importing the API client at module load)
    try:
        return label.sample_ids()
    except FileNotFoundError:
        return []


def theme_accuracy(con, funder: str | None = None, sample_only: bool = False) -> list[dict]:
    """One row per model: agreement of its theme with the funder-assigned theme."""
    out = []
    for m in models(con):
        pairs = _pairs(con, m, funder, sample_only)
        if not pairs:
            continue
        ref, ai = [p[1] for p in pairs], [p[2]["theme"] for p in pairs]
        k = metrics.cohens_kappa(ref, ai)
        out.append({"model": m, "grants compared": len(pairs),
                    "agree %": round(100 * sum(a == b for a, b in zip(ref, ai)) / len(pairs), 1),
                    "kappa": k, "strength": metrics.interpret(k)})
    return out


def confusion(con, model: str, funder: str | None = None, sample_only: bool = False) -> list[dict]:
    c = Counter((ref, lab["theme"]) for _, ref, lab in _pairs(con, model, funder, sample_only))
    return [{"reference": ref, "AI": ai, "grants": n} for (ref, ai), n in c.items()]


def accuracy_by_confidence(con, model: str, funder: str | None = None, sample_only: bool = False) -> list[dict]:
    pairs = _pairs(con, model, funder, sample_only)
    out = []
    for level in taxonomy.CONFIDENCE:
        sub = [(ref, lab) for _, ref, lab in pairs if lab["confidence"] == level]
        if sub:
            out.append({"AI confidence": level, "grants": len(sub),
                        "agree %": round(100 * sum(ref == lab["theme"] for ref, lab in sub) / len(sub), 1)})
    return out


def disagreements(con, model: str, funder: str | None = None, sample_only: bool = False, limit: int = 300) -> list[dict]:
    pairs = [p for p in _pairs(con, model, funder, sample_only) if p[1] != p[2]["theme"]][:limit]
    grants = {g["grant_id"]: g for g in db.get(con, [p[0] for p in pairs])}
    return [{
        "grant": grants[gid]["url"], "funder": grants[gid]["funder"], "grantee": grants[gid]["grantee"],
        "funder category": " | ".join(categories(grants[gid])), "funder theme": ref, "AI theme": lab["theme"],
        "AI confidence": lab["confidence"], "AI rationale": lab["rationale"],
    } for gid, ref, lab in pairs if gid in grants]


def model_agreement(con, funder: str | None = None) -> list[dict]:
    """Pairwise kappa between every two labelers, per field, on the grants both labeled."""
    labs = {m: _labels(con, m) for m in models(con)}
    if funder:
        keep = {r[0] for r in con.execute("SELECT grant_id FROM grants WHERE funder = ?", [funder])}
        labs = {m: {g: v for g, v in d.items() if g in keep} for m, d in labs.items()}
    out = []
    for a, b in combinations(labs, 2):
        both = sorted(set(labs[a]) & set(labs[b]))
        if not both:
            continue
        row = {"labelers": f"{a} vs {b}", "grants": len(both)}
        for f in taxonomy.FIELDS:
            row[f"{f} kappa"] = metrics.cohens_kappa([labs[a][g][f] for g in both], [labs[b][g][f] for g in both])
        out.append(row)
    return out


if __name__ == "__main__":
    import pandas as pd

    con = db.connect()
    pd.set_option("display.width", 200)
    for title, funder in (("Both funders", None), ("Mott", "Mott Foundation"), ("Gates", "Gates Foundation")):
        print(f"\nTheme vs funder categories, validation sample, {title}")
        print(pd.DataFrame(theme_accuracy(con, funder, sample_only=True)).round(3).to_string(index=False))
    print("\nModel-to-model agreement (kappa)")
    print(pd.DataFrame(model_agreement(con)).round(3).to_string(index=False))
    for m in models(con):
        print(f"\n{m}: accuracy by confidence (sample)")
        print(pd.DataFrame(accuracy_by_confidence(con, m, sample_only=True)).to_string(index=False))
