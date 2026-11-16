"""Mott Foundation adapter: collect public grant records from mott.org.

Grant URLs come from the site's own XML sitemaps. Each page is cached under
data/raw/mott/ so the crawl can be stopped and resumed, then parsed into
data/raw/mott/parsed.jsonl (one grant per line).

    uv run python -m sources.mott            # fetch (polite: a few requests at a time) + parse
    uv run python -m sources.mott --parse    # re-parse the cache only, no network
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

SITEMAP_INDEX = "https://www.mott.org/sitemap_index.xml"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "mott"
OUT = RAW / "parsed.jsonl"
HEADERS = {"User-Agent": "grantlens (research demo; low-rate crawler)"}
FUNDER = "Mott Foundation"


def load() -> list[dict]:
    """Parsed Mott grants in GrantLens's common format (see db.COLUMNS)."""
    out = []
    for line in OUT.open(encoding="utf-8"):
        r = json.loads(line)
        out.append({
            "funder": FUNDER,
            "grant_id": r["grant_id"],
            "url": r["url"],
            "title": r["title"],
            "grantee": r["grantee"],
            "program": r["program"],
            "subprogram": r["initiative"],
            "amount_usd": r["amount_usd"],
            "start_date": r["start_date"],
            "end_date": r["end_date"],
            "location": r["location"],
            "country": (r["location"] or "").rsplit(",", 1)[-1].strip() or None,
            "geographic_focus": r["geographic_focus"],
            "description": r["description"],
        })
    return out
CONCURRENCY = 8
DELAY_S = 0.25


def grant_id_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


async def grant_urls(client: httpx.AsyncClient) -> list[str]:
    index = (await client.get(SITEMAP_INDEX)).text
    maps = [u for u in re.findall(r"<loc>([^<]+)</loc>", index) if "/grant-sitemap" in u]
    urls: list[str] = []
    for m in maps:
        urls += re.findall(r"<loc>([^<]+/grants/[^<]+)</loc>", (await client.get(m)).text)
    return sorted(set(urls))


async def fetch_all(urls: list[str]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    todo = [u for u in urls if not (RAW / f"{grant_id_from_url(u)}.html").exists()]
    print(f"{len(urls)} grant pages, {len(urls) - len(todo)} cached, {len(todo)} to fetch", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:

        async def one(url: str) -> None:
            nonlocal done
            async with sem:
                for attempt in range(4):
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            (RAW / f"{grant_id_from_url(url)}.html").write_text(r.text, encoding="utf-8")
                            break
                        if r.status_code == 404:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(2 ** attempt)
                await asyncio.sleep(DELAY_S)
                done += 1
                if done % 250 == 0:
                    print(f"  fetched {done}/{len(todo)}", flush=True)

        await asyncio.gather(*(one(u) for u in todo))


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _parse_date(s: str) -> str | None:
    try:
        return datetime.strptime(s.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def parse_page(grant_id: str, html: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.select_one("h1.insert1-title")
    if not title:
        return None
    fields = {}
    for li in soup.select("ul.list1-entries > li"):
        label = _text(li.find("strong"))
        value = _text(li.find("span"))
        if label:
            fields[label] = value

    amount = fields.get("Grant Amount", "")
    amount_usd = int(re.sub(r"[^\d]", "", amount)) if re.search(r"\d", amount) else None
    start = end = None
    period = fields.get("Grant Period", "")
    if "–" in period:
        a, b = period.split("–", 1)
        start, end = _parse_date(a), _parse_date(b)

    body = soup.select_one("div.body1")
    if body and body.find("h2"):
        body.find("h2").decompose()
    website = soup.select_one("p.insert1-lead a")

    return {
        "grant_id": grant_id,
        "url": f"https://www.mott.org/grants/{grant_id}/",
        "title": _text(title),
        "grantee": _text(soup.select_one("p.insert1-subtitle")),
        "grantee_website": website.get("href") if website else None,
        "program": fields.get("Program"),
        "initiative": fields.get("Program Initiative"),
        "amount_usd": amount_usd,
        "start_date": start,
        "end_date": end,
        "location": fields.get("Location"),
        "geographic_focus": fields.get("Geographic Focus"),
        "description": _text(body),
    }


def parse_all() -> None:
    rows = []
    for f in sorted(RAW.glob("*.html")):
        row = parse_page(f.stem, f.read_text(encoding="utf-8"))
        if row:
            rows.append(row)
    with OUT.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} grants to {OUT}")


async def main() -> None:
    async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        urls = await grant_urls(client)
    await fetch_all(urls)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--parse", action="store_true", help="parse cached pages only")
    ap.add_argument("--limit", type=int, help="fetch only the first N urls (testing)")
    args = ap.parse_args()
    if not args.parse:
        if args.limit:
            async def _limited():
                async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as c:
                    await fetch_all((await grant_urls(c))[: args.limit])
            asyncio.run(_limited())
        else:
            asyncio.run(main())
    parse_all()
    sys.exit(0)
