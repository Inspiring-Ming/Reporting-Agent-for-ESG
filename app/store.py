"""Read-only data access for the demo.

Wraps three sources:
  - companies_master.csv  → company → SASB industry lookup
  - sasb_materiality.json → industry → material topics + metrics
  - esg_metrics.sqlite    → indexed Clarity AI ESG values per company/metric/year

Loaded once at app start. Thread-safe for read.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

DB_PATH = DATA / "esg_metrics.sqlite"
MATERIALITY_PATH = DATA / "sasb_materiality.json"


@dataclass
class Company:
    perm_id: str
    company_name: str
    ticker: str
    sasb_industry: str
    source: str

    def to_dict(self) -> dict:
        return {
            "perm_id": self.perm_id,
            "company_name": self.company_name,
            "ticker": self.ticker,
            "sasb_industry": self.sasb_industry,
            "source": self.source,
        }


class Store:
    """Single shared instance — Flask creates it once at startup."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._conn: sqlite3.Connection | None = None
        self._materiality: dict | None = None

    # ---- lazy initialisation ----
    def _ensure_open(self) -> None:
        if self._conn is None:
            if not DB_PATH.exists():
                raise RuntimeError(
                    f"Missing {DB_PATH}. Run scripts/build_metrics_db.py first."
                )
            self._conn = sqlite3.connect(
                DB_PATH, check_same_thread=False, isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row
        if self._materiality is None:
            if not MATERIALITY_PATH.exists():
                raise RuntimeError(f"Missing {MATERIALITY_PATH}")
            self._materiality = json.loads(MATERIALITY_PATH.read_text())

    # ---- materiality ----
    def materiality(self) -> dict:
        self._ensure_open()
        assert self._materiality is not None
        return self._materiality

    def industries(self) -> list[str]:
        return list(self.materiality()["industries"].keys())

    def industry_materiality(self, industry: str) -> dict | None:
        return self.materiality()["industries"].get(industry)

    # ---- company lookup ----
    def search_companies(self, query: str, limit: int = 12) -> list[Company]:
        """Match by name (case-insensitive substring) or ticker (exact)."""
        self._ensure_open()
        assert self._conn is not None
        q = (query or "").strip()
        if not q:
            return []
        like = f"%{q}%"
        rows = self._conn.execute(
            """SELECT perm_id, company_name, ticker, sasb_industry, source
               FROM companies
               WHERE company_name LIKE ? COLLATE NOCASE
                  OR ticker = ? COLLATE NOCASE
               ORDER BY
                 CASE WHEN ticker = ? COLLATE NOCASE THEN 0
                      WHEN company_name LIKE ? COLLATE NOCASE THEN 1
                      ELSE 2 END,
                 length(company_name)
               LIMIT ?""",
            (like, q, q, q + "%", limit),
        ).fetchall()
        return [Company(**dict(r)) for r in rows]

    def get_company(self, perm_id: str) -> Company | None:
        self._ensure_open()
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT perm_id, company_name, ticker, sasb_industry, source "
            "FROM companies WHERE perm_id = ?",
            (perm_id,),
        ).fetchone()
        return Company(**dict(row)) if row else None

    def companies_in_industry(self, industry: str, limit: int = 50) -> list[Company]:
        self._ensure_open()
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT perm_id, company_name, ticker, sasb_industry, source "
            "FROM companies WHERE sasb_industry = ? "
            "ORDER BY company_name LIMIT ?",
            (industry, limit),
        ).fetchall()
        return [Company(**dict(r)) for r in rows]

    # ---- metrics ----
    def metrics_for_company(self, perm_id: str) -> list[dict]:
        """All reported (metric_name, year) → value rows for one company."""
        self._ensure_open()
        assert self._conn is not None
        rows = self._conn.execute(
            """SELECT metric_name, metric_description, metric_unit, metric_year,
                      metric_value, pillar, data_type, disclosure
               FROM metrics WHERE perm_id = ? AND metric_value IS NOT NULL
               ORDER BY metric_name, metric_year""",
            (perm_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def metric_timeseries(self, perm_id: str, metric_name: str) -> list[dict]:
        self._ensure_open()
        assert self._conn is not None
        rows = self._conn.execute(
            """SELECT metric_year, metric_value, metric_unit
               FROM metrics
               WHERE perm_id = ? AND metric_name = ? AND metric_value IS NOT NULL
                     AND metric_year IS NOT NULL
               ORDER BY metric_year""",
            (perm_id, metric_name),
        ).fetchall()
        return [dict(r) for r in rows]

    def available_metric_names(self, perm_id: str) -> list[str]:
        self._ensure_open()
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT DISTINCT metric_name FROM metrics WHERE perm_id = ? "
            "AND metric_value IS NOT NULL ORDER BY metric_name",
            (perm_id,),
        ).fetchall()
        return [r["metric_name"] for r in rows]

    # ---- materiality coverage analysis ----
    def materiality_coverage(self, perm_id: str) -> dict:
        """For a company, return which SASB material topics it has any data on.

        Coverage is determined by *fuzzy keyword match* — Clarity AI metric
        names (e.g. CO2DIRECTSCOPE1) don't perfectly align with SASB metric
        codes, so we map by keyword presence in the metric_description and
        metric_name.
        """
        company = self.get_company(perm_id)
        if not company:
            return {"error": "company not found"}
        ind_data = self.industry_materiality(company.sasb_industry)
        if not ind_data:
            return {"error": "industry has no materiality reference"}

        available = self.metrics_for_company(perm_id)
        # Build a search corpus per metric: name + description, lowercased.
        haystack: list[tuple[str, str]] = []
        for r in available:
            name = (r.get("metric_name") or "").lower()
            desc = (r.get("metric_description") or "").lower()
            haystack.append((name, desc))

        topics_out = []
        for topic in ind_data["topics"]:
            keywords = _topic_keywords(topic["name"])
            matched_metrics: list[str] = []
            for r, (name, desc) in zip(available, haystack):
                if any(k in name or k in desc for k in keywords):
                    if r["metric_name"] not in matched_metrics:
                        matched_metrics.append(r["metric_name"])
            topics_out.append({
                "topic": topic["name"],
                "summary": topic.get("summary", ""),
                "sasb_metrics": [m["code"] for m in topic["metrics"]],
                "covered": bool(matched_metrics),
                "available_metrics": matched_metrics[:6],
                "match_count": len(matched_metrics),
            })

        covered = sum(1 for t in topics_out if t["covered"])
        return {
            "company": company.to_dict(),
            "industry": company.sasb_industry,
            "topics_total": len(topics_out),
            "topics_covered": covered,
            "topics": topics_out,
        }


_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Greenhouse Gas Emissions": ["co2", "ghg", "scope 1", "scope 2", "scope 3", "emission", "greenhouse"],
    "Greenhouse Gas Emissions & Energy Resource Planning": ["co2", "ghg", "scope 1", "scope 2", "scope 3", "emission", "greenhouse"],
    "Air Quality": ["nox", "sox", "particulate", "voc", "air pollut", "mercury", "lead"],
    "Energy Management": ["energy", "renewable", "electricity"],
    "Water Management": ["water", "withdraw"],
    "Waste & Hazardous Materials Management": ["waste", "tailing", "hazardous", "recycl"],
    "Coal Ash Management": ["coal", "ash", "ccp"],
    "Biodiversity Impacts": ["biodiv", "habitat", "species", "acid rock"],
    "Security, Human Rights & Rights of Indigenous Peoples": ["human right", "indigenous", "conflict"],
    "Workforce Health & Safety": ["injury", "fatal", "safety", "trir", "incident rate", "near miss", "lost time"],
    "Labour Practices": ["strike", "lockout", "collective bargain", "union"],
    "Tailings Storage Facilities Management": ["tailing", "dam"],
    "Energy Affordability": ["disconnect", "retail rate", "affordab"],
    "End-Use Efficiency & Demand": ["smart grid", "efficiency", "demand"],
    "Nuclear Safety & Emergency Management": ["nuclear", "radioact"],
    "Grid Resiliency": ["saidi", "saifi", "caidi", "outage", "interruption", "cybersecur"],
    "Data Security": ["breach", "cyber", "data security"],
    "Financial Inclusion & Capacity Building": ["unbank", "underserv", "community development", "small business loan", "financial inclus", "literacy"],
    "Incorporation of ESG Factors in Credit Analysis": ["esg credit", "esg factor"],
    "Incorporation of ESG Factors in Investment Management & Advisory": ["esg invest", "esg factor"],
    "Incorporation of ESG Factors in Investment Management": ["esg invest", "esg factor"],
    "Financed Emissions": ["financed emission", "scope 3", "portfolio emission"],
    "Business Ethics": ["fraud", "antitrust", "anti-competitive", "monetary loss", "whistleblow", "bribe", "corrupt", "ethics"],
    "Systemic Risk Management": ["g-sib", "systemic", "stress test", "capital adequacy"],
    "Transparent Information & Fair Advice for Customers": ["complaint", "customer retention", "customer satisfaction", "mis-sell"],
    "Employee Diversity & Inclusion": ["divers", "gender", "women", "minority"],
    "Policies Designed to Incentivise Responsible Behaviour": ["green premium", "sustainable product", "low-carbon"],
    "Physical Risk Exposure": ["catastroph", "physical climate", "weather-related", "pml", "flood"],
    "Management of Tenant Sustainability Impacts": ["tenant", "lease"],
    "Climate Change Adaptation": ["flood", "climate adapt", "physical climate"],
}


def _topic_keywords(topic_name: str) -> list[str]:
    return _TOPIC_KEYWORDS.get(topic_name, [topic_name.lower()])


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
