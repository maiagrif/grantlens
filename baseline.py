"""A free keyword-rules labeler: the bar the AI has to clear.

    uv run python baseline.py      # writes data/labels/keyword-baseline.jsonl for every in-scope grant

If Claude can't beat simple keyword matching against the funders' own categories, it isn't worth paying for.
"""

from __future__ import annotations

import json
import re

import db
import taxonomy

MODEL = "keyword-baseline"

THEME_WORDS = {
    "Education": r"school|student|educat|teacher|learning|literacy|afterschool|after-school|universit|college|early childhood",
    "Health": r"health|disease|vaccin|malaria|hiv|tubercul|polio|nutrition|maternal|newborn|clinic|medic|diagnos|immuniz|sanitation",
    "Agriculture & Food Systems": r"agricultur|farm|crop|seed|livestock|smallholder|food security|dairy",
    "Economic Opportunity": r"econom|job|workforce|employ|financial|poverty|small business|entrepreneur|income|credit|payment",
    "Environment & Climate": r"environment|water|climate|conservation|energy|river|great lakes|pollution|sustainab|freshwater",
    "Community Development": r"neighborhood|community development|housing|revitaliz|downtown|community vitality|placemaking",
    "Arts & Culture": r"\barts?\b|museum|cultur|music|theat|orchestra|artist",
    "Democracy & Civic Life": r"civic|democra|governance|voting|civil society|government|public policy|accountab",
    "Justice & Rights": r"justice|rights|legal|prison|incarcer|police|juvenile",
    "Gender Equality": r"gender|women|girls",
    "Philanthropy & Nonprofit Sector": r"philanthrop|community foundation|nonprofit sector|grantmak|foundation development",
    "Data, Research & Technology": r"\bdata\b|digital|technolog|software|research infrastructure",
    "Media & Public Awareness": r"media|journalis|news|awareness|communicat",
    "Emergency Response": r"emergency|disaster|relief|crisis|humanitarian|covid",
}
STRATEGY_WORDS = [
    ("General Operating Support", r"general operating|general purpose|general support|core support"),
    ("Policy & Advocacy", r"advoca|policy|policies|legislat|regulat"),
    ("Research & Evaluation", r"research|study|evaluat|assess|data collection|survey|learning agenda"),
    ("Product & Technology Development", r"develop (a|an|new)|vaccine|diagnostic|prototype|clinical trial|technolog"),
    ("Capital & Infrastructure", r"construct|building|renovat|endow|capital|facility|equipment"),
    ("Communications & Convening", r"conference|convening|convene|campaign|communicat|meeting|summit"),
    ("Capacity Building & Networks", r"capacity|network|coalition|leadership|strengthen|technical assistance|training"),
]
POPULATION_WORDS = [
    ("Women & Girls", r"women|girls"),
    ("Children & Youth", r"child|youth|young people|student|adolescen|infant|newborn|teen"),
    ("Farmers & Rural Communities", r"farmer|smallholder|rural"),
    ("Patients & People with Specific Conditions", r"patient|disabilit|people living with|people with"),
    ("Low-Income People", r"low-income|poor|poverty|vulnerable"),
    ("Racial & Ethnic Communities", r"african american|black|latin|hispanic|native american|indigenous|tribal|racial"),
]


def label(g: dict) -> dict:
    text = f"{g['title']} {g['description']}".lower()  # no funder categories: they're the benchmark
    scores = {t: len(re.findall(p, text)) for t, p in THEME_WORDS.items()}
    theme, best = max(scores.items(), key=lambda kv: kv[1])
    if best == 0:
        theme = "Other"
    strategy = next((s for s, p in STRATEGY_WORDS if re.search(p, text)), "Direct Services")
    population = next((s for s, p in POPULATION_WORDS if re.search(p, text)), "Not Specified")
    hits = sorted(scores.values(), reverse=True)
    confidence = "medium" if best and best > hits[1] else "low"
    return {"grant_id": g["grant_id"], "model": MODEL, "theme": theme, "strategy": strategy,
            "population": population, "confidence": confidence,
            "rationale": f"keyword matches for {theme}: {best}"}


if __name__ == "__main__":
    con = db.connect()
    grants = [dict(r) for r in con.execute("SELECT * FROM grants WHERE in_scope = 1")]
    labels = [label(g) for g in grants]
    assert all(lab[f] in taxonomy.FIELDS[f] for lab in labels for f in taxonomy.FIELDS)
    db.LABELS_DIR.mkdir(parents=True, exist_ok=True)
    with (db.LABELS_DIR / f"{MODEL}.jsonl").open("w", encoding="utf-8") as fh:
        for lab in labels:
            fh.write(json.dumps(lab) + "\n")
    print(f"wrote {len(labels):,} baseline labels; now run: uv run python db.py")
