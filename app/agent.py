"""Agent layer — tool-calling loop over the ESG data store.

Differs from llm.answer_question() in who decides what to retrieve.

  llm.answer_question()  server.py resolves the company up front, passes a
                         fixed context blob, one model call. Retrieval is
                         decided by our code.

  agent.run()            the model is given tool definitions and decides
                         which to call, in what order, and when it has
                         enough to answer. Multi-turn: call -> observe
                         result -> decide again.

The tools are thin wrappers over the same store.py methods the rest of the
app uses, so there is one source of truth for data access.

Design notes:
  * Hard iteration cap (MAX_TURNS). A model that loops is a bug, not a
    feature, and an uncapped while-loop bills you for it.
  * Every tool call is recorded in a trace, so the caller can show what the
    agent actually did. Opaque agents are not auditable agents.
  * Tool failures are returned to the model as tool_result content with
    is_error set, rather than raised. The model can then recover (try a
    different query) instead of the whole run dying.
  * The cached system prefix from llm.py is reused unchanged, so the
    materiality reference stays on the cheap path.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .llm import MODEL, _client, _full_materiality_block
from .store import get_store

MAX_TURNS = 8
MAX_TOKENS = 4096

AGENT_SYSTEM_PROMPT = """You are an ESG analysis agent with access to an internal
company and metrics database.

Work by retrieving before you assert. When a question names a company, look it up
first; do not answer from memory about specific companies or figures. Chain calls
when you need to: resolve the company, then pull its metrics, then pull a
time series or materiality coverage if the question calls for it.

Rules:
1. Any company-specific number you state must come from a tool result. If the
   tools return nothing for a company, say so plainly rather than estimating.
2. Mark content from tool results as [retrieved]. Mark wider ESG or framework
   context you are adding from your own knowledge as [general]. The reader needs
   to see which is which.
3. Stop calling tools once you can answer. Do not re-fetch data you already have.
4. If a tool errors or returns nothing useful twice, explain what you could not
   find instead of trying indefinitely.

Format answers as Markdown: a short summary first, then the evidence, then any
caveats about coverage or data gaps."""


# --------------------------------------------------------------------------
# Tool definitions — what the model sees.
#
# The description field is the entire basis on which the model chooses a tool,
# so it is written for the model, not for a human reader. Say when to use it,
# not just what it does.
# --------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_companies",
        "description": (
            "Find companies by name or ticker. Call this FIRST whenever a question "
            "mentions a specific company, to resolve it to a perm_id. Returns "
            "matching companies with perm_id, company_name, ticker and sasb_industry. "
            "Returns an empty list if the company is not in the database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Company name or ticker, e.g. 'BHP' or 'Westpac'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 5).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_company_metrics",
        "description": (
            "Get reported ESG metrics for one company, given the perm_id from "
            "search_companies. Returns metric name, value, unit, year and pillar. "
            "Use when asked what a company reports or how it performs on a topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "perm_id": {
                    "type": "string",
                    "description": "Company identifier from search_companies.",
                },
            },
            "required": ["perm_id"],
        },
    },
    {
        "name": "get_metric_timeseries",
        "description": (
            "Get the year-by-year series for ONE metric for ONE company. Use for "
            "trend, trajectory or 'has it improved' questions. Call "
            "get_company_metrics first if you do not know the exact metric_name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "perm_id": {"type": "string"},
                "metric_name": {
                    "type": "string",
                    "description": "Exact metric name as returned by get_company_metrics.",
                },
            },
            "required": ["perm_id", "metric_name"],
        },
    },
    {
        "name": "get_materiality_coverage",
        "description": (
            "Check a company's disclosure against the SASB material topics for its "
            "industry. Returns which topics are covered and which are gaps. Use for "
            "questions about disclosure completeness, gaps, or SASB alignment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "perm_id": {"type": "string"},
            },
            "required": ["perm_id"],
        },
    },
    {
        "name": "get_industry_materiality",
        "description": (
            "Get the SASB material topics and metrics for an INDUSTRY, with no "
            "company involved. Use when asked what matters in a sector generally."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "industry": {
                    "type": "string",
                    "description": "SASB industry name, e.g. 'Commercial Banks'.",
                },
            },
            "required": ["industry"],
        },
    },
]


# --------------------------------------------------------------------------
# Dispatch — maps a tool name to the real store.py call.
# --------------------------------------------------------------------------

def run_tool(name: str, args: dict[str, Any]) -> Any:
    """Execute one tool call. Raises ValueError on an unknown name."""
    store = get_store()

    if name == "search_companies":
        limit = int(args.get("limit") or 5)
        hits = store.search_companies(args["query"], limit=limit)
        return [c.to_dict() for c in hits]

    if name == "get_company_metrics":
        rows = store.metrics_for_company(args["perm_id"])
        # Trim to keep the context manageable: a company can report hundreds
        # of metric-years, and the model does not need all of them to answer.
        return rows[:60]

    if name == "get_metric_timeseries":
        return store.metric_timeseries(args["perm_id"], args["metric_name"])

    if name == "get_materiality_coverage":
        return store.materiality_coverage(args["perm_id"])

    if name == "get_industry_materiality":
        return store.industry_materiality(args["industry"])

    raise ValueError(f"unknown tool: {name}")


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

@dataclass
class ToolCall:
    """One tool invocation and its outcome, for the audit trace."""
    name: str
    args: dict[str, Any]
    ok: bool
    result_preview: str
    ms: int


@dataclass
class AgentResult:
    text: str
    trace: list[ToolCall] = field(default_factory=list)
    turns: int = 0
    stopped_early: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens_read: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer_markdown": self.text,
            "tool_calls": [
                {
                    "name": c.name,
                    "args": c.args,
                    "ok": c.ok,
                    "result_preview": c.result_preview,
                    "ms": c.ms,
                }
                for c in self.trace
            ],
            "turns": self.turns,
            "stopped_early": self.stopped_early,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cached_tokens_read": self.cached_tokens_read,
            },
        }


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------

def run(question: str, max_turns: int = MAX_TURNS) -> AgentResult:
    """Answer a question, letting the model drive retrieval.

    Returns when the model stops requesting tools, or when max_turns is hit
    (stopped_early=True). The trace records every call made along the way.
    """
    client = _client()

    # Same cached prefix strategy as llm.py: the stable block carries the
    # cache_control marker, so repeat runs read it at ~0.1x cost.
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": AGENT_SYSTEM_PROMPT + "\n\n" + _full_materiality_block(),
            "cache_control": {"type": "ephemeral"},
        }
    ]

    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    result = AgentResult(text="")

    for turn in range(max_turns):
        result.turns = turn + 1

        msg = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            messages=messages,
            tools=TOOLS,
        )

        result.input_tokens += msg.usage.input_tokens
        result.output_tokens += msg.usage.output_tokens
        result.cached_tokens_read += getattr(msg.usage, "cache_read_input_tokens", 0) or 0

        messages.append({"role": "assistant", "content": msg.content})

        if msg.stop_reason != "tool_use":
            # Model is done: collect the text it produced.
            result.text = "".join(
                b.text for b in msg.content if getattr(b, "type", "") == "text"
            ).strip()
            return result

        # Model asked for one or more tools. Run them, feed results back.
        tool_results: list[dict[str, Any]] = []
        for block in msg.content:
            if getattr(block, "type", "") != "tool_use":
                continue

            started = time.time()
            try:
                output = run_tool(block.name, block.input)
                ok = True
                payload = json.dumps(output, default=str)
            except Exception as e:
                # Hand the failure back to the model rather than raising:
                # it can try a different query instead of the run dying.
                ok = False
                payload = f"Tool failed: {type(e).__name__}: {e}"

            elapsed_ms = int((time.time() - started) * 1000)
            result.trace.append(
                ToolCall(
                    name=block.name,
                    args=dict(block.input),
                    ok=ok,
                    result_preview=payload[:200],
                    ms=elapsed_ms,
                )
            )

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": payload[:20000],  # guard against flooding context
                    **({"is_error": True} if not ok else {}),
                }
            )

        messages.append({"role": "user", "content": tool_results})

    # Ran out of turns. Return whatever text we have with the flag set, so the
    # caller can surface that the answer may be incomplete.
    result.stopped_early = True
    result.text = (
        result.text
        or "I reached the maximum number of retrieval steps before completing "
           "this answer. The tool calls attempted are listed in the trace."
    )
    return result
