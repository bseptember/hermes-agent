"""Regression tests: MoA calls must carry the turn's ``session_id`` so
OpenRouter's provider sticky routing keeps repeated reference/aggregator calls
on the same backend instance and can build automatic prompt-cache hits.

Before this fix, the non-MoA acting path sent ``session_id`` (via the OpenRouter
profile's ``build_extra_body``) and measured 75-86% cache-hit ratios, while MoA
presets sent nothing and measured ~48% (free-chat) down to ~0.4% (byok-code).
There is no ``moa`` ProviderProfile, so the standard injection point never fired
for MoA turns. These tests lock in the plumbing that closes that gap:

  * ``call_llm(session_id=...)`` folds the id into ``extra_body`` — but only for
    routes whose profile emits it (OpenRouter), never for others.
  * ``_run_reference`` / the aggregator call inside ``MoAChatCompletions.create``
    forward one stable id across the whole turn.
  * ``aggregate_moa_context`` (the ``/moa <prompt>`` one-shot) forwards it too.
  * end-to-end, the virtual ``moa`` provider carries ``agent.session_id`` from
    ``build_api_kwargs`` through to every ``call_llm`` of the turn, identically.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.auxiliary_client import call_llm


def _response(content="done", *, tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None, model="fake-model")


# ---------------------------------------------------------------------------
# call_llm: session_id -> extra_body, gated by the resolved provider's profile
# ---------------------------------------------------------------------------


def _run_call_llm(provider, model, *, session_id=None, extra_body=None):
    """Drive call_llm through a stubbed client and return the create() kwargs."""
    client = MagicMock()
    client.base_url = "https://openrouter.ai/api/v1"
    client.chat.completions.create.return_value = _response()

    with (
        patch(
            "agent.auxiliary_client._resolve_task_provider_model",
            return_value=(provider, model, None, None, None),
        ),
        patch(
            "agent.auxiliary_client._get_cached_client",
            return_value=(client, model),
        ),
        patch(
            "agent.auxiliary_client._validate_llm_response",
            side_effect=lambda resp, _task: resp,
        ),
    ):
        call_llm(
            task="moa_reference",
            messages=[{"role": "user", "content": "hi"}],
            session_id=session_id,
            extra_body=extra_body,
        )

    assert client.chat.completions.create.call_count == 1
    return client.chat.completions.create.call_args.kwargs


def test_session_id_injected_into_extra_body_for_openrouter():
    kwargs = _run_call_llm("openrouter", "deepseek/deepseek-v4-pro", session_id="sess-123")
    assert kwargs.get("extra_body", {}).get("session_id") == "sess-123"


def test_session_id_not_injected_for_non_openrouter_route():
    """A provider whose profile does not emit session_id must not receive it —
    sending an unknown extra_body field risks a 400 on strict providers."""
    kwargs = _run_call_llm("openai", "gpt-5.5", session_id="sess-123")
    assert "session_id" not in (kwargs.get("extra_body") or {})


def test_session_id_none_is_a_noop():
    """Existing callers (compression, vision, title_generation, ...) pass no
    session_id and must be byte-for-byte unaffected."""
    kwargs = _run_call_llm("openrouter", "deepseek/deepseek-v4-pro", session_id=None)
    assert "session_id" not in (kwargs.get("extra_body") or {})


def test_explicit_extra_body_session_id_is_preserved():
    """A caller-supplied session_id in extra_body wins over the injected one
    (setdefault semantics)."""
    kwargs = _run_call_llm(
        "openrouter",
        "deepseek/deepseek-v4-pro",
        session_id="injected",
        extra_body={"session_id": "explicit"},
    )
    assert kwargs["extra_body"]["session_id"] == "explicit"


# ---------------------------------------------------------------------------
# MoAChatCompletions.create: one stable id across reference + aggregator
# ---------------------------------------------------------------------------

_FACADE_CONFIG = """
moa:
  default_preset: review
  presets:
    review:
      reference_models:
        - provider: openrouter
          model: deepseek/deepseek-v4-pro
        - provider: openrouter
          model: deepseek/deepseek-v4-pro
      aggregator:
        provider: openrouter
        model: anthropic/claude-opus-4.8
""".strip()


@pytest.fixture
def _moa_home(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text(_FACADE_CONFIG, encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def test_facade_forwards_identical_session_id_to_all_calls(_moa_home, monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return _response("ok")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    from agent.moa_loop import MoAChatCompletions

    facade = MoAChatCompletions("review")
    facade.create(
        session_id="turn-session-9",
        messages=[{"role": "user", "content": "question"}],
        tools=[{"type": "function"}],
    )

    ref_calls = [c for c in calls if c["task"] == "moa_reference"]
    agg_calls = [c for c in calls if c["task"] == "moa_aggregator"]
    assert len(ref_calls) == 2  # two references in the preset
    assert len(agg_calls) == 1
    # Every call in the turn carries the SAME session_id so OpenRouter pins them
    # to one backend/cache.
    assert {c["session_id"] for c in calls} == {"turn-session-9"}


def test_facade_does_not_leak_session_id_into_forwarded_kwargs(_moa_home, monkeypatch):
    """The Hermes-internal session_id key must be consumed by create(), never
    forwarded to a real chat-completions field (messages/tools/extra_body)."""
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return _response("ok")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    from agent.moa_loop import MoAChatCompletions

    facade = MoAChatCompletions("review")
    facade.create(
        session_id="turn-session-9",
        messages=[{"role": "user", "content": "question"}],
        tools=[{"type": "function"}],
    )

    agg = next(c for c in calls if c["task"] == "moa_aggregator")
    # session_id rides as call_llm's dedicated param, NOT inside extra_body/tools/
    # messages built for the aggregator.
    assert (agg.get("extra_body") or {}).get("session_id") is None


def test_facade_without_session_id_is_a_noop(_moa_home, monkeypatch):
    """Omitting session_id (e.g. a caller that never set one) must still work
    and forward session_id=None."""
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return _response("ok")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    from agent.moa_loop import MoAChatCompletions

    facade = MoAChatCompletions("review")
    facade.create(
        messages=[{"role": "user", "content": "question"}],
        tools=[{"type": "function"}],
    )

    assert {c["session_id"] for c in calls} == {None}


# ---------------------------------------------------------------------------
# aggregate_moa_context: the /moa <prompt> one-shot path
# ---------------------------------------------------------------------------


def test_aggregate_moa_context_forwards_session_id(monkeypatch):
    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        return _response("synth")

    from agent import moa_loop

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)
    monkeypatch.setattr(
        moa_loop,
        "_slot_runtime",
        lambda slot: {
            "provider": slot.get("provider"),
            "model": slot.get("model"),
            "base_url": "",
            "api_mode": "chat_completions",
        },
    )

    moa_loop.aggregate_moa_context(
        user_prompt="what next?",
        api_messages=[{"role": "user", "content": "help me plan"}],
        reference_models=[{"provider": "openrouter", "model": "deepseek/deepseek-v4-pro"}],
        aggregator={"provider": "openrouter", "model": "anthropic/claude-opus-4.8"},
        session_id="oneshot-session",
    )

    assert calls, "expected reference + aggregator calls"
    assert {c["session_id"] for c in calls} == {"oneshot-session"}


# ---------------------------------------------------------------------------
# end-to-end: the virtual `moa` provider carries agent.session_id through
# build_api_kwargs into every call_llm of the turn
# ---------------------------------------------------------------------------


def test_session_id_flows_from_agent_through_moa_turn(_moa_home, monkeypatch):
    from run_agent import AIAgent

    calls = []

    def fake_call_llm(**kwargs):
        calls.append(kwargs)
        if kwargs["task"] == "moa_reference":
            return _response("reference advice")
        return _response("aggregator acted")

    monkeypatch.setattr("agent.moa_loop.call_llm", fake_call_llm)

    agent = AIAgent(
        api_key="moa-virtual-provider",
        base_url="http://127.0.0.1/v1",
        model="review",
        provider="moa",
        session_id="agent-session-42",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        enabled_toolsets=["file"],
        max_iterations=1,
    )
    # MoA must never build a real request OpenAI client.
    monkeypatch.setattr(
        agent,
        "_create_request_openai_client",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("MoA calls must use MoAClient, not a request OpenAI client")
        ),
    )

    agent.run_conversation("solve this")

    assert agent.session_id == "agent-session-42"
    tasks = {c["task"] for c in calls}
    assert {"moa_reference", "moa_aggregator"} <= tasks
    # The live agent session_id reaches every MoA call, identically.
    assert {c["session_id"] for c in calls} == {"agent-session-42"}
