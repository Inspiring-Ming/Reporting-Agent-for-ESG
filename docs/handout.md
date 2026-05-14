# Grounding LLMs in a Knowledge Graph
### A working ESG reporting agent — AMP AI Expo, 12 May 2026

---

## Why this matters for AMP

AMP operates under disclosure regimes that don't tolerate "the AI made it up":
ISSB / AASB S2 climate disclosures, APRA CPS 230, ASIC RG 271, and internal
risk frameworks. Any AI that touches a disclosure, advice doc, or regulatory
filing needs to answer one question: **where did this number come from?**

This demo shows one way to make AI defensible: ground it in a knowledge graph.

---

## The pattern in three lines

| | What it does | Who owns it |
|---|---|---|
| **Ground** | Force the model to query a structured source — not its training data | Engineering |
| **Lineage** | Log every metric → source row → framework reference | Engineering + Risk |
| **Sign off** | Humans review judgement, comparison, and forward-looking claims | Subject expert |

If your team's AI workflow does those three things, you can defend it.

---

## What you saw at the booth

1. **Grounded vs Ungrounded** — same ESG question, two answers. The grounded
   agent traces every figure to an SASB metric code, a company, a year, and a
   data row. You could type *any* question — the agent showed exactly what it
   could and couldn't extract (company / metric / year), then either gave a
   number with lineage or said why it couldn't. The dashboard also showed:
   - **Coverage grid** — which (metric × year) cells actually have data
   - **Year-on-year trend** — and a warning when units aren't yet reconciled

2. **AI Myth-Busting** — six things people believe about AI. Some are true,
   some aren't. Worth knowing which.

3. **Sign-Off Workbench** — a draft AI-generated disclosure where you pick
   what to publish and what to flag for human review. The boundary isn't
   technical; it's about judgement, comparison, and accountability.

---

## Three questions to ask of any AI tool you're given at work

1. **What is it grounded in?** ("My training data" is not an answer.)
2. **Can it show me the source row?** (If not, treat its output as a draft.)
3. **What part of this needs my judgement, not its?** (Forward-looking claims, comparisons, recommendations — almost always you.)

---

## Built on

- A SASB-aligned RDF ontology (525 triples)
- ~10,000 ESG records across semiconductors and commercial banking
- A Python knowledge-graph service + deterministic calculation engine
- Reused as the backend for this booth demo — zero duplication

Talk to us at the stall, or visit `/api/health` on the demo machine.
