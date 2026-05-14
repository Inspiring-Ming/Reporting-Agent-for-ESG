"""Flask server for the AMP AI Expo demo.

Two pages:
  1. ESG Knowledge Agent  → /api/ask
  2. ESG Analytics for Reporting → /api/company/search, /api/company/<perm_id>/report,
                                   /api/company/<perm_id>/trend, /api/generate-pdf
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS

from app.store import get_store
from app import llm
from app import pdf as pdf_render
from app.auth import (
    is_gate_enabled, login_view, login_submit, logout_view,
    rate_limit, require_auth,
)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent

load_dotenv(PROJECT_ROOT / ".env")


SUGGESTED_QUESTIONS = [
    {"id": "what_is_sasb", "label": "What is SASB and why does it matter for ESG reporting?"},
    {"id": "financed_emissions", "label": "What are 'financed emissions' for a bank, and how are they measured?"},
    {"id": "materiality_amp", "label": "Which SASB material topics matter most for AMP's industry (Asset Management)?"},
    {"id": "scope3_explain", "label": "Explain Scope 1, 2, and 3 emissions in plain language."},
    {"id": "tcfd_vs_sasb", "label": "How does TCFD differ from SASB, and where do they overlap?"},
    {"id": "greenwashing", "label": "How can an investor spot greenwashing in an ESG report?"},
]


def _build_kg_context_for_question(question: str) -> dict | None:
    """Best-effort: see if the question mentions a company we have data on."""
    store = get_store()
    text = question.lower()
    # Try ticker first (3-5 uppercase letters as a standalone token)
    import re
    tickers = re.findall(r"\b[A-Z]{2,5}\b", question)
    for t in tickers:
        hits = store.search_companies(t, limit=1)
        if hits and hits[0].ticker.upper() == t.upper():
            return _kg_context_for_company(hits[0])
    # Otherwise scan for known company names (cheap heuristic — substring of >=4 words / 6+ chars)
    candidate = None
    for token in re.findall(r"[A-Z][a-zA-Z0-9&'.-]{3,}(?:\s+[A-Z][a-zA-Z0-9&'.-]{2,}){0,3}", question):
        hits = store.search_companies(token, limit=1)
        if hits:
            candidate = hits[0]
            break
    if candidate:
        return _kg_context_for_company(candidate)
    return None


def _kg_context_for_company(company) -> dict:
    store = get_store()
    available = store.available_metric_names(company.perm_id)[:10]
    return {
        "company": company.to_dict(),
        "industry": company.sasb_industry,
        "available_metrics": available,
        "note": (
            f"Found {company.company_name} in our company database — industry is "
            f"'{company.sasb_industry}'. Reported metric families on file: "
            f"{', '.join(available[:5]) if available else 'none'}."
        ),
    }


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    CORS(app)

    store = get_store()

    # --- auth / health (no rate limit) ---
    app.route("/login", methods=["GET"])(login_view)
    app.route("/login", methods=["POST"])(login_submit)
    app.route("/logout")(logout_view)

    @app.route("/")
    @require_auth
    def index():
        return render_template("index.html", suggested=SUGGESTED_QUESTIONS)

    @app.route("/api/health")
    def health():
        return jsonify({
            "ok": True,
            "industries": store.industries(),
            "model": llm.MODEL,
            "have_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "gated": is_gate_enabled(),
        })

    # ------------------------------------------------------------------
    # Page 1 — ESG Knowledge Agent
    # ------------------------------------------------------------------

    @app.route("/api/suggested-questions")
    @require_auth
    def suggested_questions():
        return jsonify(SUGGESTED_QUESTIONS)

    @app.route("/api/ask", methods=["POST"])
    @require_auth
    @rate_limit(limit=30, per_seconds=3600)
    def ask():
        body = request.get_json(force=True) or {}
        question = (body.get("question") or "").strip()
        if not question:
            return jsonify({"error": "empty question"}), 400

        kg_context = _build_kg_context_for_question(question)
        try:
            result = llm.answer_question(question, kg_context)
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            return jsonify({"error": f"LLM call failed: {type(e).__name__}: {e}"}), 500
        return jsonify({
            "question": question,
            "kg_context": kg_context,
            "answer": result,
        })

    # ------------------------------------------------------------------
    # Page 2 — ESG Analytics for Reporting
    # ------------------------------------------------------------------

    @app.route("/api/company/search")
    @require_auth
    def company_search():
        q = (request.args.get("q") or "").strip()
        results = store.search_companies(q, limit=12)
        return jsonify({
            "query": q,
            "results": [c.to_dict() for c in results],
        })

    @app.route("/api/company/<perm_id>/snapshot")
    @require_auth
    def company_snapshot(perm_id: str):
        company = store.get_company(perm_id)
        if not company:
            return jsonify({"error": "company not found"}), 404
        coverage = store.materiality_coverage(perm_id)
        # Pick top metrics by amount of multi-year data — these are best for trend charts.
        names = store.available_metric_names(perm_id)
        metric_summaries = []
        for name in names[:30]:  # bounded
            ts = store.metric_timeseries(perm_id, name)
            if len(ts) < 2:
                continue
            metric_summaries.append({
                "metric_name": name,
                "n_years": len(ts),
                "first_year": ts[0]["metric_year"],
                "last_year": ts[-1]["metric_year"],
                "first_value": ts[0]["metric_value"],
                "last_value": ts[-1]["metric_value"],
                "unit": ts[0]["metric_unit"],
            })
        # Order by years of data, then take top 8.
        metric_summaries.sort(key=lambda m: -m["n_years"])
        metric_summaries = metric_summaries[:8]

        return jsonify({
            "company": company.to_dict(),
            "industry_materiality": store.industry_materiality(company.sasb_industry),
            "coverage": coverage,
            "metric_summaries": metric_summaries,
        })

    @app.route("/api/company/<perm_id>/trend")
    @require_auth
    def company_trend(perm_id: str):
        metric = (request.args.get("metric") or "").strip()
        if not metric:
            return jsonify({"error": "metric required"}), 400
        ts = store.metric_timeseries(perm_id, metric)
        return jsonify({
            "perm_id": perm_id,
            "metric_name": metric,
            "points": ts,
        })

    @app.route("/api/company/<perm_id>/report-section", methods=["POST"])
    @require_auth
    @rate_limit(limit=60, per_seconds=3600)
    def company_report_section(perm_id: str):
        body = request.get_json(force=True) or {}
        section_id = body.get("section_id")
        if section_id not in llm.REPORT_SECTIONS:
            return jsonify({"error": f"unknown section_id: {section_id}"}), 400

        company = store.get_company(perm_id)
        if not company:
            return jsonify({"error": "company not found"}), 404
        coverage = store.materiality_coverage(perm_id)

        # Same metric_summaries shape as /snapshot — keep this consistent so the
        # LLM sees stable inputs across requests (good for cache hits too).
        names = store.available_metric_names(perm_id)
        metric_summaries = []
        for name in names[:30]:
            ts = store.metric_timeseries(perm_id, name)
            if len(ts) < 2:
                continue
            metric_summaries.append({
                "metric_name": name,
                "n_years": len(ts),
                "first_year": ts[0]["metric_year"],
                "last_year": ts[-1]["metric_year"],
                "first_value": ts[0]["metric_value"],
                "last_value": ts[-1]["metric_value"],
                "unit": ts[0]["metric_unit"],
            })
        metric_summaries.sort(key=lambda m: -m["n_years"])
        metric_summaries = metric_summaries[:8]

        try:
            result = llm.generate_report_section(
                section_id=section_id,
                company=company.to_dict(),
                industry=company.sasb_industry,
                materiality_coverage=coverage,
                metric_summaries=metric_summaries,
            )
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 500
        except Exception as e:
            return jsonify({"error": f"LLM call failed: {type(e).__name__}: {e}"}), 500
        return jsonify(result)

    @app.route("/api/generate-pdf", methods=["POST"])
    @require_auth
    @rate_limit(limit=10, per_seconds=3600)
    def generate_pdf():
        body = request.get_json(force=True) or {}
        title = body.get("title") or "ESG Analytics Report"
        subtitle = body.get("subtitle") or ""
        sections = body.get("sections") or []
        if not sections:
            return jsonify({"error": "no sections supplied"}), 400

        # Sections come from the frontend with edited markdown. We don't trust
        # arbitrary keys — pick only what we render.
        clean = []
        for s in sections:
            clean.append({
                "section_id": s.get("section_id", ""),
                "title": s.get("title", ""),
                "markdown": s.get("markdown", ""),
                # chart_png_bytes is optional, base64-encoded if present
                "chart_png_bytes": _decode_b64(s.get("chart_png_b64")),
            })

        pdf_bytes = pdf_render.render_pdf(title=title, subtitle=subtitle, sections=clean)
        filename = (body.get("filename") or "esg_report.pdf").replace("/", "_")
        return send_file(
            __import__("io").BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )

    return app


def _decode_b64(s: str | None) -> bytes | None:
    if not s:
        return None
    import base64
    try:
        # Strip data-url prefix if present
        if "," in s and s.lstrip().startswith("data:"):
            s = s.split(",", 1)[1]
        return base64.b64decode(s)
    except Exception:
        return None


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app = create_app()
    app.run(host="0.0.0.0", port=port, debug=False)
