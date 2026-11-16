"""Gates Foundation adapter: the foundation's published committed-grants CSV.

    uv run python -m sources.gates     # download data/raw/gates/gates.csv
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import httpx

CSV_URL = "https://www.gatesfoundation.org/-/media/files/bmgf-grants.csv"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw" / "gates"
CSV_PATH = RAW / "gates.csv"
FUNDER = "Gates Foundation"


def download() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", CSV_URL, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with CSV_PATH.open("wb") as fh:
            for chunk in r.iter_bytes():
                fh.write(chunk)
    print(f"saved {CSV_PATH}")


def _add_months(year: int, month: int, months: int) -> str:
    m = year * 12 + (month - 1) + max(months, 0)
    return date(m // 12, m % 12 + 1, 1).isoformat()


def load() -> list[dict]:
    """Gates grants in GrantLens's common format (see db.COLUMNS)."""
    csv.field_size_limit(10**8)
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as fh:
        next(fh)  # first line is an "Updated <date>" note, not the header
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        gid, committed = (r.get("GRANT ID") or "").strip(), (r.get("DATE COMMITTED") or "").strip()
        if not gid or "\x00" in gid or len(committed) < 7:
            continue
        year, month = int(committed[:4]), int(committed[5:7])
        try:
            months = int(r.get("DURATION (MONTHS)") or 0)
        except ValueError:
            months = 0
        try:
            amount = int(float(r.get("AMOUNT COMMITTED") or 0))
        except ValueError:
            amount = None
        city, state, country = (r.get(k, "").strip() for k in ("GRANTEE CITY", "GRANTEE STATE", "GRANTEE COUNTRY"))
        purpose = (r.get("PURPOSE") or "").strip()
        out.append({
            "funder": FUNDER,
            "grant_id": gid,
            "url": f"https://www.gatesfoundation.org/about/committed-grants/{year}/{month:02d}/{gid.lower().replace('-', '')}",
            "title": purpose[:1].upper() + purpose[1:],
            "grantee": (r.get("GRANTEE") or "").strip(),
            "program": (r.get("DIVISION") or "").strip(),
            "subprogram": (r.get("TOPIC") or "").strip(),
            "amount_usd": amount,
            "start_date": f"{year}-{month:02d}-01",
            "end_date": _add_months(year, month, months),
            "location": ", ".join(x for x in (city, state, country) if x),
            "country": country or None,
            "geographic_focus": (r.get("REGION SERVED") or "").strip(),
            "description": purpose,
        })
    return out


if __name__ == "__main__":
    download()
    print(f"{len(load())} grants")
