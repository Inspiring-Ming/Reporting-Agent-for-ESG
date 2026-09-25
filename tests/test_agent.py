"""Tests for the agent layer.

These cover the parts that do not need a live model: tool schemas, the
dispatcher, and the loop's control flow. The loop is exercised against a fake
client so we can assert on turn counts, tracing and error handling without
spending tokens or depending on model behaviour.

What is deliberately NOT tested here: whether the model chooses sensible tools.
That is an evaluation problem, not a unit-test problem, and it needs a fixed
question set scored against expected retrieval paths.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from app import agent


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

def test_every_tool_has_required_schema_fields():
    """A malformed schema fails at call time with an opaque API error, so it is
    worth catching here instead."""
    for tool in agent.TOOLS:
        assert tool["name"], "tool missing name"
        assert tool["description"], f"{tool['name']} missing description"
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        for req in schema.get("required", []):
            assert req in schema["properties"], (
                f"{tool['name']} requires '{req}' but does not define it"
            )


def test_tool_names_are_unique():
    names = [t["name"] for t in agent.TOOLS]
    assert len(names) == len(set(names))


def test_descriptions_say_when_to_use_the_tool():
    """The description is the only thing the model uses to choose a tool, so a
    bare restatement of the name is a latent bug. Cheap proxy: it should be a
    real sentence, not three words."""
    for tool in agent.TOOLS:
        assert len(tool["description"].split()) >= 10, (
            f"{tool['name']} description is too thin to guide selection"
        )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def test_unknown_tool_raises():
    with pytest.raises(ValueError, match="unknown tool"):
        agent.run_tool("definitely_not_a_tool", {})


def test_every_declared_tool_is_dispatchable():
    """Guards against declaring a tool to the model that run_tool cannot execute.
    We call each with empty args and accept anything except the 'unknown tool'
    ValueError: a KeyError or TypeError means the name IS routed."""
    for tool in agent.TOOLS:
        try:
            agent.run_tool(tool["name"], {})
        except ValueError as e:
            if "unknown tool" in str(e):
                pytest.fail(f"{tool['name']} is declared but not dispatchable")
        except Exception:
            pass  # wrong-args errors are fine; the name routed correctly


# ---------------------------------------------------------------------------
# Loop control flow, against a fake client
# ---------------------------------------------------------------------------

@dataclass
class FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_read_input_tokens: int = 0


@dataclass
class FakeText:
    text: str
    type: str = "text"


@dataclass
class FakeToolUse:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list[Any]
    stop_reason: str
    usage: FakeUsage = None

    def __post_init__(self):
        if self.usage is None:
            self.usage = FakeUsage()


class FakeClient:
    """Replays a scripted list of responses, one per turn."""

    def __init__(self, responses: list[FakeMessage]):
        self._responses = responses
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("fake client ran out of scripted responses")
        return self._responses.pop(0)


@pytest.fixture
def patch_client(monkeypatch):
    def _install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr(agent, "_client", lambda: client)
        return client
    return _install


def test_returns_immediately_when_model_does_not_call_tools(patch_client):
    patch_client([FakeMessage(content=[FakeText("Direct answer.")],
                              stop_reason="end_turn")])
    result = agent.run("a question needing no lookup")

    assert result.text == "Direct answer."
    assert result.turns == 1
    assert result.trace == []
    assert result.stopped_early is False


def test_executes_tool_then_answers(patch_client, monkeypatch):
    monkeypatch.setattr(agent, "run_tool",
                        lambda name, args: [{"company_name": "Test Co"}])
    patch_client([
        FakeMessage(content=[FakeToolUse("search_companies", {"query": "Test"})],
                    stop_reason="tool_use"),
        FakeMessage(content=[FakeText("Found Test Co.")], stop_reason="end_turn"),
    ])

    result = agent.run("tell me about Test Co")

    assert result.turns == 2
    assert result.text == "Found Test Co."
    assert len(result.trace) == 1
    assert result.trace[0].name == "search_companies"
    assert result.trace[0].ok is True


def test_tool_failure_is_returned_to_model_not_raised(patch_client, monkeypatch):
    """A failing tool must not kill the run: the model should get the error back
    as a tool_result so it can recover."""
    def boom(name, args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(agent, "run_tool", boom)
    client = patch_client([
        FakeMessage(content=[FakeToolUse("search_companies", {"query": "X"})],
                    stop_reason="tool_use"),
        FakeMessage(content=[FakeText("I could not retrieve that.")],
                    stop_reason="end_turn"),
    ])

    result = agent.run("a question")

    assert result.text == "I could not retrieve that."
    assert result.trace[0].ok is False
    assert "database unavailable" in result.trace[0].result_preview

    # The second call must carry the error back to the model.
    sent_messages = client.calls[1]["messages"]
    tool_result_msgs = [
        m for m in sent_messages
        if isinstance(m.get("content"), list)
        and m["content"] and isinstance(m["content"][0], dict)
        and m["content"][0].get("type") == "tool_result"
    ]
    assert tool_result_msgs, "no tool_result was sent back to the model"
    assert tool_result_msgs[-1]["content"][0]["is_error"] is True


def test_turn_cap_stops_a_model_that_never_finishes(patch_client, monkeypatch):
    """Without a cap this is an unbounded billing loop."""
    monkeypatch.setattr(agent, "run_tool", lambda name, args: {"ok": True})
    patch_client([
        FakeMessage(content=[FakeToolUse("search_companies", {"query": "x"})],
                    stop_reason="tool_use")
        for _ in range(10)
    ])

    result = agent.run("a question", max_turns=3)

    assert result.turns == 3
    assert result.stopped_early is True
    assert len(result.trace) == 3
    assert result.text  # must still return something explanatory


def test_tool_results_are_truncated(patch_client, monkeypatch):
    """Unbounded tool output can blow the context window mid-run."""
    monkeypatch.setattr(agent, "run_tool", lambda name, args: "x" * 50_000)
    client = patch_client([
        FakeMessage(content=[FakeToolUse("search_companies", {"query": "x"})],
                    stop_reason="tool_use"),
        FakeMessage(content=[FakeText("done")], stop_reason="end_turn"),
    ])

    agent.run("a question")

    sent_messages = client.calls[1]["messages"]
    tool_result_msgs = [
        m for m in sent_messages
        if isinstance(m.get("content"), list)
        and m["content"] and isinstance(m["content"][0], dict)
        and m["content"][0].get("type") == "tool_result"
    ]
    assert tool_result_msgs, "no tool_result was sent back to the model"
    sent = tool_result_msgs[-1]["content"][0]["content"]
    assert len(sent) <= 20_000


def test_cached_prefix_is_marked_on_the_system_block(patch_client):
    """The materiality reference is large and stable; if the cache_control
    marker goes missing, every call pays full price."""
    client = patch_client([FakeMessage(content=[FakeText("hi")],
                                       stop_reason="end_turn")])
    agent.run("a question")

    system = client.calls[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_tools_are_passed_on_every_call(patch_client, monkeypatch):
    monkeypatch.setattr(agent, "run_tool", lambda name, args: {})
    client = patch_client([
        FakeMessage(content=[FakeToolUse("search_companies", {"query": "x"})],
                    stop_reason="tool_use"),
        FakeMessage(content=[FakeText("done")], stop_reason="end_turn"),
    ])

    agent.run("a question")

    for call in client.calls:
        assert call["tools"] is agent.TOOLS


def test_usage_accumulates_across_turns(patch_client, monkeypatch):
    monkeypatch.setattr(agent, "run_tool", lambda name, args: {})
    patch_client([
        FakeMessage(content=[FakeToolUse("search_companies", {"query": "x"})],
                    stop_reason="tool_use"),
        FakeMessage(content=[FakeText("done")], stop_reason="end_turn"),
    ])

    result = agent.run("a question")

    assert result.input_tokens == 20   # 10 per turn, two turns
    assert result.output_tokens == 10


def test_result_serialises_for_the_api(patch_client):
    patch_client([FakeMessage(content=[FakeText("answer")],
                              stop_reason="end_turn")])
    payload = agent.run("q").to_dict()

    assert payload["answer_markdown"] == "answer"
    assert payload["tool_calls"] == []
    assert "usage" in payload
    json.dumps(payload)  # must be JSON-serialisable for the Flask response
