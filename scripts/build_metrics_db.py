"""Filter the 5M-row Clarity AI ESG dataset down to the 6 chosen industries
and build a fast SQLite index for the demo.

Output: data/esg_metrics.sqlite

Tables:
  companies(perm_id PK, company_name, ticker, sasb_industry, source)
  metrics(id PK, perm_id, metric_name, metric_description, metric_unit,
          metric_year, metric_value, pillar, data_type, disclosure)

Indexes:
  metrics(perm_id, metric_year)
  metrics(metric_name)

Run once. Re-run when you change the industry whitelist.
"""
from __future__ import annotations

import csv
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
COMPANY_CSV = ROOT / "data" / "companies_master.csv"
DB_OUT = ROOT / "data" / "esg_metrics.sqlite"

# Source ESG dataset (the seven Clarity AI chunks). Override via env var on a
# fresh clone; default points to the original path on the author's laptop.
ESG_DIR = Path(os.environ.get(
    "AMP_ESG_METRICS_DIR",
    "/Users/mingqin/Downloads/2.ESG Metrics Datasets Sorted(Clarity AI)",
))

TARGET_INDUSTRIES = {
    "Asset Management & Custody Activities",
    "Commercial Banks",
    "Insurance",
    "Real Estate",
    "Metals & Mining",
    "Electric Utilities & Power Generators",
}


def load_target_companies() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with COMPANY_CSV.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["sasb_industry"] in TARGET_INDUSTRIES and row["perm_id"]:
                out[row["perm_id"]] = row
    return out


def reset_db(path: Path) -> sqlite3.Connection:
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE companies (
            perm_id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            ticker TEXT,
            sasb_industry TEXT NOT NULL,
            source TEXT
        );
        CREATE INDEX idx_companies_industry ON companies(sasb_industry);
        CREATE INDEX idx_companies_name ON companies(company_name);

        CREATE TABLE metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            perm_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_description TEXT,
            metric_unit TEXT,
            metric_year INTEGER,
            metric_value REAL,
            pillar TEXT,
            data_type TEXT,
            disclosure TEXT,
            FOREIGN KEY (perm_id) REFERENCES companies(perm_id)
        );
        CREATE INDEX idx_metrics_perm_year ON metrics(perm_id, metric_year);
        CREATE INDEX idx_metrics_name ON metrics(metric_name);
        CREATE INDEX idx_metrics_perm_name ON metrics(perm_id, metric_name);
        """
    )
    return conn


def parse_year(value: str) -> int | None:
    if not value:
        return None
    s = value.strip()
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4])
    return None


def parse_float(value: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def main() -> None:
    if not COMPANY_CSV.exists():
        print(f"ERROR: {COMPANY_CSV} not found. Run build_company_master.py first.")
        sys.exit(1)
    if not ESG_DIR.exists():
        print(f"ERROR: {ESG_DIR} not found.")
        sys.exit(1)

    print("Loading target companies...")
    target = load_target_companies()
    print(f"  {len(target)} companies in our 6 industries")
    target_ids = set(target.keys())

    print(f"\nResetting {DB_OUT} ...")
    conn = reset_db(DB_OUT)
    cur = conn.cursor()

    cur.executemany(
        "INSERT INTO companies (perm_id, company_name, ticker, sasb_industry, source) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (r["perm_id"], r["company_name"], r["ticker"], r["sasb_industry"], r["source"])
            for r in target.values()
        ],
    )
    conn.commit()
    print(f"  inserted {len(target)} companies")

    chunks = sorted(ESG_DIR.glob("esg_cleaned_data_chunk*.csv"))
    print(f"\nProcessing {len(chunks)} ESG chunks...")

    total_seen = 0
    total_kept = 0
    batch: list[tuple] = []

    start = datetime.now()
    for chunk_path in chunks:
        chunk_seen = 0
        chunk_kept = 0
        with chunk_path.open(encoding="utf-8", errors="ignore") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                chunk_seen += 1
                pid = (row.get("perm_id") or "").strip()
                if pid not in target_ids:
                    continue
                year = parse_year(row.get("metric_year") or row.get("metric_period") or "")
                value = parse_float(row.get("metric_value") or "")
                batch.append((
                    pid,
                    (row.get("metric_name") or "").strip(),
                    (row.get("metric_description") or "").strip() or None,
                    (row.get("metric_unit") or "").strip() or None,
                    year,
                    value,
                    (row.get("pillar") or "").strip() or None,
                    (row.get("data_type") or "").strip() or None,
                    (row.get("disclosure") or "").strip() or None,
                ))
                chunk_kept += 1
                if len(batch) >= 5000:
                    cur.executemany(
                        "INSERT INTO metrics (perm_id, metric_name, metric_description, "
                        "metric_unit, metric_year, metric_value, pillar, data_type, disclosure) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        batch,
                    )
                    batch.clear()
        total_seen += chunk_seen
        total_kept += chunk_kept
        elapsed = (datetime.now() - start).total_seconds()
        print(f"  {chunk_path.name}: scanned {chunk_seen:>9,} | kept {chunk_kept:>7,} | "
              f"running total kept {total_kept:>8,} | elapsed {elapsed:.1f}s")

    if batch:
        cur.executemany(
            "INSERT INTO metrics (perm_id, metric_name, metric_description, "
            "metric_unit, metric_year, metric_value, pillar, data_type, disclosure) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM metrics")
    n_metrics = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT perm_id) FROM metrics")
    n_companies_with_data = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT metric_name) FROM metrics")
    n_unique_metrics = cur.fetchone()[0]

    print()
    print(f"Done. {DB_OUT}")
    print(f"  scanned {total_seen:,} rows total; kept {total_kept:,} (~"
          f"{100*total_kept/max(total_seen,1):.1f}%)")
    print(f"  {n_metrics:,} metric rows in DB")
    print(f"  {n_companies_with_data:,} companies have at least one metric")
    print(f"  {n_unique_metrics:,} distinct metric names")

    print("\nPer-industry coverage:")
    cur.execute(
        """SELECT c.sasb_industry, COUNT(DISTINCT c.perm_id) as cs,
                  COUNT(m.id) as ms
           FROM companies c LEFT JOIN metrics m ON m.perm_id = c.perm_id
           GROUP BY c.sasb_industry ORDER BY ms DESC"""
    )
    for ind, cs, ms in cur.fetchall():
        print(f"  {ms:>9,} metric rows | {cs:>5,} companies | {ind}")

    conn.close()


if __name__ == "__main__":
    main()
