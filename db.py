"""GrantLens database: every funder's grants in one table, plus AI labels.

    uv run python db.py      # (re)build data/grantlens.db from the sources and data/labels/*.jsonl

Files under data/ that are committed:
    grantlens.db              built database the app reads
    labels/<model>.jsonl      AI labels, one file per model (written by label.py)
    validation_sample.csv     the fixed random sample used to measure accuracy
    human_labels.csv          your hand labels for that sample (written by the app's Label page)
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

DATA = Path(__file__).parent / "data"
DB_PATH = DATA / "grantlens.db"
LABELS_DIR = DATA / "labels"

# Which grants get AI labels: all of Mott (to see change since 2006), Gates since 2015.
SCOPE_SQL = "(funder = 'Mott Foundation' OR (funder = 'Gates Foundation' AND start_year >= 2015))"

COLUMNS = [
    "funder", "grant_id", "url", "title", "grantee", "program", "subprogram", "amount_usd",
    "start_date", "end_date", "location", "country", "geographic_focus", "description",
]

SCHEMA = """
CREATE TABLE grants (
    funder           TEXT,              -- 'Mott Foundation' or 'Gates Foundation'
    grant_id         TEXT PRIMARY KEY,  -- funder's grant ID, e.g. '2026-14727' (Mott) or 'INV-023730' (Gates); cite with this
    url              TEXT,              -- public page for the grant
    title            TEXT,              -- grant purpose / title
    grantee          TEXT,
    program          TEXT,              -- funder's own program or division, e.g. 'Flint Area', 'Global Health'
    subprogram       TEXT,              -- funder's initiative or topic within the program
    amount_usd       INTEGER,           -- total award in US dollars
    start_date       TEXT,              -- ISO date: grant period start (Mott) or date committed (Gates)
    end_date         TEXT,
    start_year       INTEGER,           -- year of start_date; use for "grants made in year X"
    location         TEXT,              -- grantee's city, state, country
    country          TEXT,              -- grantee's country
    is_us            INTEGER,           -- 1 if the grantee is in the United States
    geographic_focus TEXT,              -- where the work happens, as the funder records it
    description      TEXT,              -- funder's description (Gates: a one-line purpose statement)
    in_scope         INTEGER            -- 1 if included in AI labeling (all Mott; Gates since 2015)
);
CREATE TABLE labels (
    grant_id   TEXT,
    model      TEXT,
    theme      TEXT,
    strategy   TEXT,
    population TEXT,
    confidence TEXT,  -- 'high' | 'medium' | 'low'
    rationale  TEXT,
    PRIMARY KEY (grant_id, model)
);
CREATE VIRTUAL TABLE grants_fts USING fts5(
    grant_id UNINDEXED, title, grantee, program, subprogram, location, geographic_focus, description,
    content='grants', tokenize='porter unicode61'
);
"""

# The labeled view the assistant and dashboard use; built from whichever model labeled the most grants.
VIEW_DOC = """grants_labeled = every column of grants, plus theme, strategy, population, confidence
(AI labels from the primary labeling model; NULL for grants not yet labeled)"""


def build() -> None:
    from sources import gates, mott

    rows = []
    for src in (mott, gates):
        try:
            got = src.load()
        except FileNotFoundError:
            print(f"skipping {src.FUNDER}: no raw data yet (run: uv run python -m {src.__name__})")
            continue
        rows += got
        print(f"{src.FUNDER}: {len(got):,} grants")

    seen: set[str] = set()
    rows = [r for r in rows if not (r["grant_id"] in seen or seen.add(r["grant_id"]))]

    DB_PATH.unlink(missing_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.executemany(
        f"INSERT INTO grants ({', '.join(COLUMNS)}, start_year, is_us) VALUES ({', '.join('?' * (len(COLUMNS) + 2))})",
        [
            [r.get(c) for c in COLUMNS]
            + [int(r["start_date"][:4]) if r.get("start_date") else None,
               int((r.get("country") or "").strip() in ("United States", "USA", "US"))]
            for r in rows
        ],
    )
    con.execute(f"UPDATE grants SET in_scope = CASE WHEN {SCOPE_SQL} THEN 1 ELSE 0 END")
    con.execute(
        "INSERT INTO grants_fts (rowid, grant_id, title, grantee, program, subprogram, location, geographic_focus, description) "
        "SELECT rowid, grant_id, title, grantee, program, subprogram, location, geographic_focus, description FROM grants"
    )

    n_labels = 0
    for f in sorted(LABELS_DIR.glob("*.jsonl")):
        recs = [json.loads(line) for line in f.open(encoding="utf-8") if line.strip()]
        con.executemany(
            "INSERT OR REPLACE INTO labels VALUES (?, ?, ?, ?, ?, ?, ?)",
            [[r["grant_id"], r["model"], r["theme"], r["strategy"], r["population"], r["confidence"], r["rationale"]]
             for r in recs],
        )
        n_labels += len(recs)

    # Prefer the Claude model with the most labels; fall back to the keyword baseline before any AI run.
    primary = con.execute(
        "SELECT model FROM labels GROUP BY model "
        "ORDER BY model = 'keyword-baseline', COUNT(*) DESC, model LIMIT 1").fetchone()
    model = primary[0] if primary else ""
    con.execute(f"""
        CREATE VIEW grants_labeled AS
        SELECT g.*, l.theme, l.strategy, l.population, l.confidence
        FROM grants g LEFT JOIN labels l ON l.grant_id = g.grant_id AND l.model = '{model}'""")
    con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('primary_label_model', ?)", [model])
    con.commit()
    print(f"built {DB_PATH.name}: {len(rows):,} grants, {n_labels:,} labels"
          + (f", primary label model {model}" if model else ""))
    con.close()


def connect() -> sqlite3.Connection:
    """Read-only connection: nothing that queries through it can modify the data."""
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def primary_model(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT value FROM meta WHERE key = 'primary_label_model'").fetchone()
    return row[0] if row else ""


def _fts_query(text: str) -> str:
    phrases = re.findall(r'"([^"]+)"', text)
    rest = re.sub(r'"[^"]+"', " ", text)
    words = [w for w in re.findall(r"[\w']+", rest) if len(w) > 1]
    terms = [f'"{p}"' for p in phrases] + [f'"{w}"' for w in words]
    return " OR ".join(terms) if terms else '""'


def search(con, query, funder=None, program=None, theme=None, year_from=None, year_to=None,
           min_amount=None, limit=15) -> list[dict]:
    where, params = ["grants_fts MATCH ?"], [_fts_query(query)]
    for col, val in (("g.funder", funder), ("g.program", program), ("g.theme", theme)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    for cond, val in (("g.start_year >= ?", year_from), ("g.start_year <= ?", year_to), ("g.amount_usd >= ?", min_amount)):
        if val:
            where.append(cond)
            params.append(val)
    params.append(max(1, min(limit, 50)))
    sql = f"""
        SELECT g.funder, g.grant_id, g.title, g.grantee, g.program, g.amount_usd, g.start_date,
               g.location, g.theme, g.strategy, substr(g.description, 1, 300) AS description_excerpt
        FROM grants_fts JOIN grants b ON b.rowid = grants_fts.rowid
        JOIN grants_labeled g ON g.grant_id = b.grant_id
        WHERE {' AND '.join(where)}
        ORDER BY bm25(grants_fts, 0, 4.0, 3.0, 1.0, 1.0, 1.0, 1.0, 1.0)
        LIMIT ?"""
    return [dict(r) for r in con.execute(sql, params)]


READ_ONLY = re.compile(r"^\s*(WITH|SELECT)\b", re.I)


def run_select(con, sql: str, max_rows: int = 200) -> dict:
    if not READ_ONLY.match(sql) or ";" in sql.strip().rstrip(";"):
        raise ValueError("Only a single SELECT (or WITH ... SELECT) statement is allowed.")
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchmany(max_rows + 1)
    return {"columns": cols, "rows": [list(r) for r in rows[:max_rows]], "truncated": len(rows) > max_rows}


def get(con, grant_ids: list[str]) -> list[dict]:
    if not grant_ids:
        return []
    q = f"SELECT * FROM grants_labeled WHERE grant_id IN ({', '.join('?' * len(grant_ids))})"
    return [dict(r) for r in con.execute(q, grant_ids)]


def overview(con) -> list[dict]:
    return [dict(r) for r in con.execute("""
        SELECT funder, COUNT(*) AS grants, SUM(amount_usd) AS total_usd, MIN(start_year) AS first_year,
               MAX(start_year) AS last_year, SUM(in_scope) AS in_scope,
               SUM(theme IS NOT NULL) AS labeled
        FROM grants_labeled GROUP BY funder ORDER BY funder""")]


if __name__ == "__main__":
    build()
