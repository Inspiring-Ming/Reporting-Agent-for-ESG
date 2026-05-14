"""Build a single trusted company -> SASB industry CSV.

Strategy (decided with the user):
  - Teamnifflers (40k) is the base: SASB-aligned industry names, 0 blanks.
  - Teamagile (65k) enriches when Teamnifflers doesn't have the perm_id and
    the Teamagile industry name is non-blank AND already a known SASB name.
  - externalAPI (55k) uses non-SASB vocabulary, so we only use it as a last-
    resort enrichment via a manual mapping table for the few industries we
    actually care about.

Output: data/companies_master.csv with columns
  perm_id, company_name, ticker, sasb_industry, source
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Source CSVs from the Clarity AI dataset. Override via env var on a fresh
# clone; default points to the original path on the author's laptop.
SRC = Path(os.environ.get(
    "AMP_INDUSTRY_MATCHING_DIR",
    "/Users/mingqin/Downloads/AMP/Industry Matching List(Clarity AI dataset)",
))
OUT = ROOT / "data" / "companies_master.csv"

TEAMNIFFLERS = SRC / "companies_industry_SASB(Teamnifflers_40k).csv"
TEAMAGILE = SRC / "companies_csv_SASB(Teamagile).csv"
EXTERNAL = SRC / "company_industry(external API).csv"

# Map externalAPI's vocabulary -> SASB names. Only filled for industries we
# care about right now. Anything not in this map is dropped from the
# externalAPI enrichment pass.
EXTERNAL_TO_SASB = {
    "Banking Services": "Commercial Banks",
    "Investment Banking & Investment Services": "Investment Banking & Brokerage",
    "Collective Investments": "Asset Management & Custody Activities",
    "Insurance": "Insurance",
    "Real Estate Operations": "Real Estate",
    "Residential & Commercial REITs": "Real Estate",
    "Metals & Mining": "Metals & Mining",
    "Electric Utilities & IPPs": "Electric Utilities & Power Generators",
    "Multiline Utilities": "Electric Utilities & Power Generators",
    "Renewable Energy": "Electric Utilities & Power Generators",
    "Coal": "Coal Operations",
    "Uranium": "Metals & Mining",
    "Oil & Gas": "Oil & Gas - Exploration & Production",
    "Oil & Gas Related Equipment and Services": "Oil & Gas - Services",
    "Natural Gas Utilities": "Gas Utilities & Distributors",
    "Water & Related Utilities": "Water Utilities & Services",
    "Pharmaceuticals": "Biotechnology & Pharmaceuticals",
    "Biotechnology & Medical Research": "Biotechnology & Pharmaceuticals",
    "Healthcare Providers & Services": "Health Care Delivery",
    "Healthcare Equipment & Supplies": "Medical Equipment & Supplies",
    "Software & IT Services": "Software & IT Services",
    "Semiconductors & Semiconductor Equipment": "Semiconductors",
    "Communications & Networking": "Telecommunications",
    "Telecommunications Services": "Telecommunications",
    "Aerospace & Defense": "Aerospace & Defense",
    "Automobiles & Auto Parts": "Automobiles",
    "Beverages": "Non-Alcoholic Beverages",
    "Chemicals": "Chemicals",
    "Construction Materials": "Construction Materials",
    "Containers & Packaging": "Containers & Packaging",
    "Food & Tobacco": "Processed Foods",
    "Hotels & Entertainment Services": "Hotels & Lodging",
    "Media & Publishing": "Media & Entertainment",
    "Specialty Retailers": "Multiline and Specialty Retailers & Distributors",
    "Textiles & Apparel": "Apparel, Accessories & Footwear",
}


def normalise_industry(name: str) -> str:
    return (name or "").strip().strip('"').strip()


def load_teamnifflers() -> dict[str, dict]:
    """perm_id -> row dict. Source = 'teamnifflers'."""
    out: dict[str, dict] = {}
    with TEAMNIFFLERS.open(encoding="utf-8", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pid = (row.get("perm_id") or "").strip()
            ind = normalise_industry(row.get("industry") or "")
            if not pid or not ind:
                continue
            out[pid] = {
                "perm_id": pid,
                "company_name": (row.get("company_name") or "").strip(),
                "ticker": (row.get("ticker_code") or "").strip(),
                "sasb_industry": ind,
                "source": "teamnifflers",
            }
    return out


def enrich_from_teamagile(master: dict[str, dict], known_sasb: set[str]) -> int:
    added = 0
    with TEAMAGILE.open(encoding="utf-8", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pid = (row.get("Perm ID") or "").strip()
            ind = normalise_industry(row.get("Industry") or "")
            if not pid or pid in master or not ind or ind not in known_sasb:
                continue
            master[pid] = {
                "perm_id": pid,
                "company_name": (row.get("Name") or "").strip(),
                "ticker": "",
                "sasb_industry": ind,
                "source": "teamagile",
            }
            added += 1
    return added


def enrich_from_external(master: dict[str, dict]) -> int:
    added = 0
    with EXTERNAL.open(encoding="utf-8", errors="ignore") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pid = normalise_industry(row.get("company_permid") or "")
            ext_ind = normalise_industry(row.get("industry_name") or "")
            sasb = EXTERNAL_TO_SASB.get(ext_ind)
            if not pid or pid in master or not sasb:
                continue
            master[pid] = {
                "perm_id": pid,
                "company_name": normalise_industry(row.get("company_name") or ""),
                "ticker": "",
                "sasb_industry": sasb,
                "source": "external_api",
            }
            added += 1
    return added


def main() -> None:
    print(f"Loading Teamnifflers base from {TEAMNIFFLERS.name} ...")
    master = load_teamnifflers()
    base_count = len(master)
    print(f"  {base_count} rows from Teamnifflers")

    known_sasb = {r["sasb_industry"] for r in master.values()}
    print(f"  {len(known_sasb)} unique SASB industries seen")

    print("Enriching from Teamagile (only when SASB name matches) ...")
    n = enrich_from_teamagile(master, known_sasb)
    print(f"  +{n} new companies")

    print("Enriching from external API (mapped industries only) ...")
    n2 = enrich_from_external(master)
    print(f"  +{n2} new companies")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["perm_id", "company_name", "ticker", "sasb_industry", "source"],
        )
        writer.writeheader()
        for row in sorted(master.values(), key=lambda r: r["company_name"].lower()):
            writer.writerow(row)

    by_industry: dict[str, int] = {}
    for r in master.values():
        by_industry[r["sasb_industry"]] = by_industry.get(r["sasb_industry"], 0) + 1

    print()
    print(f"Wrote {OUT} with {len(master)} rows ({len(by_industry)} industries).")
    print("Top 12 industries by count:")
    for ind, n in sorted(by_industry.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {n:>6}  {ind}")


if __name__ == "__main__":
    main()
