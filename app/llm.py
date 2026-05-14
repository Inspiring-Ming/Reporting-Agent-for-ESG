"""LLM layer.

Calls Claude Sonnet 4.6 with prompt caching:
  - Tier 1 (cached, ~5 min TTL): the SASB materiality JSON for all 6 industries
    + the system prompt. ~6-8K tokens, paid once, then read for ~0.1x.
  - Tier 2 (per-request): the user's question / company snapshot (small).

Two surfaces:
  * answer_question(question, kg_context) — Knowledge Agent page.
    Returns blocks: kg_summary (if any), llm_answer, caveats.
  * generate_report_sections(company, industry, materiality_coverage,
                              metric_summaries) — Analytics page.
    Returns 5 sections suitable for editable cards + PDF export.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic

ROOT = Path(__file__).resolve().parent.parent
MATERIALITY_PATH = ROOT / "data" / "sasb_materiality.json"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# How big each report section can get. Generous — these are fully-edited PDFs.
MAX_TOKENS = 4096


@dataclass
class LLMResult:
    text: str
    cached_tokens_read: int
    cached_tokens_written: int
    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "usage": {
                "cache_read": self.cached_tokens_read,
                "cache_write": self.cached_tokens_written,
                "input": self.input_tokens,
                "output": self.output_tokens,
            },
        }


def _client() -> anthropic.Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to .env (see .env.example)."
        )
    return anthropic.Anthropic()


_SYSTEM_PROMPT = """You are an ESG reporting assistant for AMP, an Australian wealth-management firm. You help non-technical AMP staff understand ESG concepts, SASB industry materiality, and how to interpret real ESG disclosures.

You have two roles:

1. **Knowledge Agent** — answer ESG questions clearly for a non-technical audience. When the user provides "KG-grounded context" from our internal knowledge graph, lead with that and clearly mark it as `[KG-grounded]`. Then extend with broader context, frameworks, methodology. Mark extensions as `[LLM-extended]`. Be honest when the KG has no data on the question — say so, then offer the wider context.

2. **Analytics Reporter** — generate sections of a company-level ESG analytics report. Each section must be self-contained (the user may include or exclude individual sections), use plain language, and be free of confident-sounding fabrications. When you don't know a number, say so explicitly rather than inventing one.

Always:
- Cite SASB industry, topic name, or metric code when relevant (e.g. "FN-CB-410b.1 financed emissions").
- Distinguish what the data shows from what it doesn't show.
- Use Australian-relevant framing where natural (e.g. APRA, ASX, AASB S2).
- Write in concise, well-structured Markdown. Use ## for section headings, **bold** for emphasis, and - for bullets. No emojis.
- Keep paragraphs short (3-4 sentences max).

You will be given a SASB Industry Materiality reference for the user's industry. Use it as the authoritative source for which topics are material.
"""


_FULL_MATERIALITY_CACHE: str | None = None


def _full_materiality_block() -> str:
    """All 6 industries' SASB materiality, deterministic.

    Cached as a string at module level so the bytes are stable across
    requests — that's what lets Anthropic's prompt cache hit."""
    global _FULL_MATERIALITY_CACHE
    if _FULL_MATERIALITY_CACHE is not None:
        return _FULL_MATERIALITY_CACHE
    data = json.loads(MATERIALITY_PATH.read_text())
    parts = [
        "# SASB Industry Materiality Reference",
        "Authoritative source for which ESG topics are financially material per industry.",
        "",
    ]
    for industry in sorted(data["industries"].keys()):
        body = data["industries"][industry]
        parts.append(f"\n## {industry}")
        parts.append(f"Sector: {body['sector']} | Code prefix: {body['sasb_code_prefix']}")
        parts.append(body["description"])
        parts.append("\nMaterial topics:")
        for t in body["topics"]:
            parts.append(f"\n### {t['name']}")
            parts.append(t.get("summary", ""))
            parts.append("Metrics:")
            for m in t["metrics"]:
                parts.append(f"  - {m['code']}: {m['name']} ({m.get('unit', '')})")
        parts.append("")
    _FULL_MATERIALITY_CACHE = "\n".join(parts)
    return _FULL_MATERIALITY_CACHE


def _call_claude(
    *,
    user_text: str,
    industry: str | None = None,
    extra_system: str = "",
) -> LLMResult:
    """Single Claude call with cached system + materiality prefix."""
    client = _client()

    # Stable, deterministic prefix: system prompt + ALL industries' materiality.
    # Combined into one large cached block so the prefix is well over the 2048-
    # token Sonnet caching threshold and shared across every request regardless
    # of which industry the user asks about.
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _SYSTEM_PROMPT + "\n\n" + _full_materiality_block(),
            "cache_control": {"type": "ephemeral"},
        },
    ]
    if industry:
        # Per-request hint about which industry to focus on (uncached, after the
        # cache breakpoint, so it doesn't invalidate the prefix).
        system_blocks.append({
            "type": "text",
            "text": f"For this request, focus on industry: **{industry}**.",
        })
    if extra_system:
        system_blocks.append({"type": "text", "text": extra_system})

    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_blocks,
        messages=[{"role": "user", "content": user_text}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return LLMResult(
        text=text.strip(),
        cached_tokens_read=getattr(msg.usage, "cache_read_input_tokens", 0) or 0,
        cached_tokens_written=getattr(msg.usage, "cache_creation_input_tokens", 0) or 0,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
    )


# ============================================================================
# Surface 1 — Knowledge Agent
# ============================================================================

def answer_question(question: str, kg_context: dict | None = None) -> dict:
    """Answer an ESG question.

    kg_context shape (all optional):
      {
        "company": {"company_name": "...", "sasb_industry": "..."},
        "industry": "Commercial Banks",
        "metric_hits": [{"metric_name": "...", "year": 2022, "value": ..., "unit": "..."}],
        "available_metrics": ["CO2DIRECTSCOPE1", ...],
        "note": "free-text describing what we did/didn't find",
      }
    """
    industry = (kg_context or {}).get("industry")

    if kg_context and any(kg_context.get(k) for k in ("metric_hits", "company", "available_metrics")):
        kg_text = "## KG-grounded context\n" + json.dumps(kg_context, indent=2, default=str)
        kg_text += (
            "\n\nUse the KG-grounded data above as the primary source where it answers "
            "the question. Be explicit about what the KG provides vs what you are "
            "extending from your wider knowledge. Format your answer as Markdown with "
            "clear sections: a one-paragraph summary first, then a 'KG evidence' "
            "section if the KG had data, then a 'Wider context' section, and finally "
            "'Caveats' if applicable."
        )
    else:
        kg_text = (
            "Our internal knowledge graph has no specific data on this question. "
            "Answer from your general ESG/SASB knowledge, but say so up front."
        )

    user_text = f"# User question\n{question}\n\n{kg_text}"
    res = _call_claude(user_text=user_text, industry=industry)
    return {
        "answer_markdown": res.text,
        "industry": industry,
        "kg_used": bool(kg_context and (kg_context.get("metric_hits") or kg_context.get("company"))),
        "usage": res.to_dict()["usage"],
    }


# ============================================================================
# Surface 2 — Analytics report sections
# ============================================================================

REPORT_SECTIONS = [
    "executive_summary",
    "industry_materiality",
    "disclosure_coverage",
    "trend_analysis",
    "gaps_and_recommendations",
]


def generate_report_section(
    *,
    section_id: str,
    company: dict,
    industry: str,
    materiality_coverage: dict,
    metric_summaries: list[dict],
) -> dict:
    """Generate one report section. Each section is independent so the user
    can edit and include/exclude them individually."""
    if section_id not in REPORT_SECTIONS:
        raise ValueError(f"unknown section_id: {section_id}")

    snapshot = {
        "company": company,
        "industry": industry,
        "materiality_coverage_summary": {
            "topics_total": materiality_coverage.get("topics_total"),
            "topics_covered": materiality_coverage.get("topics_covered"),
            "topics": [
                {
                    "topic": t["topic"],
                    "covered": t["covered"],
                    "match_count": t["match_count"],
                    "available_metrics": t["available_metrics"],
                }
                for t in materiality_coverage.get("topics", [])
            ],
        },
        "metric_summaries": metric_summaries,  # short list of top reported metrics
    }

    instructions = {
        "executive_summary": (
            "Write a one-section **Executive Summary** (~150-200 words) for an "
            "AMP audience. Cover: who the company is, its SASB industry, how broad "
            "its ESG disclosure is in our dataset (use the coverage numbers), and "
            "the single most material topic for its industry. End with a plain-"
            "language 'what to watch' line for an AMP analyst."
        ),
        "industry_materiality": (
            "Write an **Industry Materiality** section explaining the SASB "
            "material topics for this industry — what they are and *why* each "
            "matters financially for this kind of business. Use a short bulleted "
            "list of topics, each with a one-line rationale. Include a brief "
            "Australian/AMP-relevant framing where useful."
        ),
        "disclosure_coverage": (
            "Write a **Disclosure Coverage** section. Using the materiality "
            "coverage data, group topics into 'Disclosed' (covered: true) and "
            "'Gaps' (covered: false). For each disclosed topic, name an example "
            "metric we have data on. For gaps, briefly explain what's missing and "
            "why it matters for investors."
        ),
        "trend_analysis": (
            "Write a **Trend Analysis** section. Look at the metric_summaries — "
            "for each metric with multiple years of data, comment on direction "
            "(improving / worsening / flat). Be honest if the data is too thin "
            "to call a trend. Keep it factual, no over-interpretation."
        ),
        "gaps_and_recommendations": (
            "Write a **Gaps & Recommendations** section for an AMP analyst. "
            "Three short bulleted recommendations: (1) which material topic the "
            "company should disclose next, (2) what data quality issue to flag "
            "for engagement, (3) a suggested peer or industry benchmark to compare "
            "against. Keep each recommendation concrete and actionable."
        ),
    }[section_id]

    user_text = (
        f"# Task\n{instructions}\n\n"
        f"# Company snapshot\n```json\n{json.dumps(snapshot, indent=2, default=str)}\n```\n"
        f"\nWrite ONLY the section content (no preamble, no 'Sure, here is...'). "
        f"Use Markdown. Lead with a `## ` heading."
    )
    res = _call_claude(user_text=user_text, industry=industry)
    return {
        "section_id": section_id,
        "markdown": res.text,
        "usage": res.to_dict()["usage"],
    }
