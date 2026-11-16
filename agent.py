"""The Ask tab: Claude answers questions about the grants with three read-only tools,
citing the grant IDs behind every claim so each answer can be checked.

    uv run python agent.py "How do Mott and Gates differ in general operating support since 2015?"
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Callable

import anthropic
from anthropic import beta_tool

import db
import taxonomy

MODEL = "claude-opus-5-5"
CITE = re.compile(r"\[\[([\w-]+)\]\]")

_con = db.connect()


def _system() -> str:
    funders = "\n".join(
        f"- {o['funder']}: {o['grants']:,} grants, start years {o['first_year']}-{o['last_year']}, "
        f"{o['labeled']:,} with AI labels" for o in db.overview(_con))
    return f"""You answer questions about philanthropic grants for people analyzing funders' portfolios.

Your only data is a SQLite database built from funders' public grant records:
{funders}

Schema:
{db.SCHEMA.split('CREATE TABLE labels')[0].strip()}

View: {db.VIEW_DOC}
Label values (shared across funders so they can be compared):
- theme: {', '.join(taxonomy.THEMES)}
- strategy: {', '.join(taxonomy.STRATEGIES)}
- population: {', '.join(taxonomy.POPULATIONS)}

Tools:
- search_grants: full-text search for grants about a topic, grantee or place.
- query_grants_sql: one read-only SQLite SELECT, for counts, totals, shares, rankings and trends. Every number you
  report must come from this tool or from records you retrieved; never estimate or recall figures.
- get_grants: full records for specific grant IDs.

How to answer:
- Cite every grant you mention with its ID in double brackets, e.g. [[2026-14727]] or [[INV-023730]]. For totals,
  cite two or three representative grants, or say the figure comes from a query across N grants.
- Lead with the direct answer in one or two sentences, then supporting detail. Keep it short.
- When comparing funders, compare shares of each funder's own dollars or grants, over the same years. Gates
  labels exist only from 2015 on; Mott's go back further.
- Theme, strategy and population are AI labels: say so when an answer rests on them.
- Mott's descriptions are paragraphs; Gates's are one-line purpose statements. Mott start_date is the grant period
  start; Gates start_date is the month committed.
- If the data can't answer (no outcomes, reports or internal notes), say so plainly rather than guessing."""


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@beta_tool
def search_grants(
    query: str,
    funder: str | None = None,
    program: str | None = None,
    theme: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    min_amount: int | None = None,
    limit: int = 15,
) -> str:
    """Full-text search over grant titles, grantees, programs, locations and descriptions, best matches first.

    Args:
        query: Words or "quoted phrases" describing the topic, grantee or place.
        funder: Optional: "Mott Foundation" or "Gates Foundation".
        program: Optional exact program name, e.g. "Flint Area" or "Global Health".
        theme: Optional AI theme label, e.g. "Education".
        year_from: Optional earliest start year (inclusive).
        year_to: Optional latest start year (inclusive).
        min_amount: Optional minimum award in US dollars.
        limit: Number of results, at most 50.
    """
    return _json(db.search(_con, query, funder, program, theme, year_from, year_to, min_amount, limit))


@beta_tool
def query_grants_sql(sql: str) -> str:
    """Run one read-only SQLite SELECT against grants / grants_labeled and return columns and up to 200 rows.

    Args:
        sql: A single SELECT or WITH ... SELECT statement.
    """
    try:
        return _json(db.run_select(_con, sql))
    except Exception as e:  # returned to the model so it can fix its query
        return _json({"error": str(e)})


@beta_tool
def get_grants(grant_ids: list[str]) -> str:
    """Fetch full records, including AI labels, for specific grants.

    Args:
        grant_ids: Grant IDs such as "2026-14727" or "INV-023730" (up to 20).
    """
    return _json(db.get(_con, grant_ids[:20]))


TOOLS = [search_grants, query_grants_sql, get_grants]


@dataclass
class Answer:
    text: str
    cited_ids: list[str]
    unknown_ids: list[str]  # cited but not in the data: flagged in the UI, counted in evals
    steps: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)


def describe_step(name: str, args: dict) -> str:
    if name == "search_grants":
        scope = " · ".join(str(args[k]) for k in ("funder", "program", "theme") if args.get(k))
        return f"Searched grants for “{args.get('query')}”" + (f" ({scope})" if scope else "")
    if name == "query_grants_sql":
        return "Ran a SQL query"
    if name == "get_grants":
        return f"Read {len(args.get('grant_ids', []))} grant record(s)"
    return name


def ask(question: str, history: list[dict] | None = None,
        on_step: Callable[[str, dict], None] | None = None,
        client: anthropic.Anthropic | None = None) -> Answer:
    client = client or anthropic.Anthropic()
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=16000,
        system=_system(),
        tools=TOOLS,
        max_iterations=12,
        output_config={"effort": "medium"},
        # Each tool round re-sends the conversation; caching it makes the repeated part ~90% cheaper.
        cache_control={"type": "ephemeral"},
        # If the main model declines a request, the API retries it on a fallback model in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[*(history or []), {"role": "user", "content": question}],
    )
    steps: list[str] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_write": 0, "cache_read": 0}
    final = None
    for message in runner:
        final = message
        usage["input_tokens"] += message.usage.input_tokens
        usage["output_tokens"] += message.usage.output_tokens
        usage["cache_write"] += message.usage.cache_creation_input_tokens or 0
        usage["cache_read"] += message.usage.cache_read_input_tokens or 0
        for block in message.content:
            if block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                steps.append(describe_step(block.name, args))
                if on_step:
                    on_step(block.name, args)

    if final is None or final.stop_reason == "refusal":
        text = "Sorry, I can't help with that question."
    else:
        text = "\n".join(b.text for b in final.content if b.type == "text").strip()
        if final.stop_reason == "max_tokens":
            text += "\n\n_(Answer was cut off; try a narrower question.)_"

    cited = list(dict.fromkeys(CITE.findall(text)))
    known = {r["grant_id"] for r in db.get(_con, cited)}
    return Answer(text=text, cited_ids=[c for c in cited if c in known],
                  unknown_ids=[c for c in cited if c not in known], steps=steps, usage=usage)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "How do Mott and Gates differ in general operating support since 2015?"
    a = ask(q, on_step=lambda n, a: print(f"  · {describe_step(n, a)}", file=sys.stderr))
    print(a.text)
    u = a.usage
    usd = (u["input_tokens"] * 4 + u["cache_write"] * 5 + u["cache_read"] * 0.4 + u["output_tokens"] * 20) / 1e6
    print(f"\ncited: {len(a.cited_ids)}  unknown: {a.unknown_ids}  usage: {u}  ≈ ${usd:.2f}")
