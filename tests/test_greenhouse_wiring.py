"""Tests for wiring the Greenhouse provider into startup and the chat path.

The wiring lives in its own module so the edits inside Odysseus' own files stay
at a couple of lines each. `src/app_initializer.py` and `routes/chat_helpers.py`
are upstream-owned; every line added to them is one to re-merge forever.
"""

import asyncio


def run(coro):
    return asyncio.run(coro)


class FakeProvider:
    """Stands in for GreenhouseMemoryProvider. Only the wiring is under test."""

    provider_id = "greenhouse"
    display_name = "Greenhouse"
    enabled = True

    def __init__(self, hits=None, available=True, last_error=None):
        self._hits = hits or []
        self.available = available
        self.last_error = last_error
        self.calls = []

    async def recall(self, query, *, owner=None, top_k=5):
        self.calls.append((query, top_k))
        return self._hits


class FakeHit:
    def __init__(self, text, **greenhouse):
        self.score = greenhouse.pop("score", 0.9)
        self.provider_id = "greenhouse"
        self.memory = type(
            "M", (), {"text": text, "metadata": {"greenhouse": greenhouse}}
        )()


# --- building the provider from configuration ---------------------------


def test_no_provider_when_unconfigured(monkeypatch):
    """An unconfigured Odysseus must behave exactly as it does today."""
    from src.greenhouse_wiring import build_greenhouse_provider

    monkeypatch.delenv("GREENHOUSE_URL", raising=False)
    monkeypatch.delenv("GREENHOUSE_TOKEN", raising=False)

    assert build_greenhouse_provider() is None


def test_no_provider_when_only_half_configured(monkeypatch):
    """A URL with no token would fail every call with a 401. Refuse to build it
    rather than register a provider that can only produce outages."""
    from src.greenhouse_wiring import build_greenhouse_provider

    monkeypatch.setenv("GREENHOUSE_URL", "http://greenhouse.test")
    monkeypatch.delenv("GREENHOUSE_TOKEN", raising=False)

    assert build_greenhouse_provider() is None


def test_provider_built_when_configured(monkeypatch):
    from src.greenhouse_wiring import build_greenhouse_provider

    monkeypatch.setenv("GREENHOUSE_URL", "http://greenhouse.test")
    monkeypatch.setenv("GREENHOUSE_TOKEN", "tok")

    provider = build_greenhouse_provider()

    assert provider is not None
    assert provider.provider_id == "greenhouse"


# --- recall into the chat preface ---------------------------------------


def test_recall_message_carries_memories_and_attribution():
    from src.greenhouse_wiring import greenhouse_recall_message

    provider = FakeProvider(
        hits=[
            FakeHit(
                "The return pump is a Sicce Syncra 3.0",
                kind="fact",
                authority="human",
                area_key="aquariums",
                corroboration_count=2,
            )
        ]
    )

    message = run(greenhouse_recall_message(provider, "what pump do I run?"))

    assert message is not None
    content = message["content"]
    assert "Sicce Syncra 3.0" in content
    assert "aquariums" in content
    assert "fact" in content
    assert provider.calls == [("what pump do I run?", 5)]


def test_recall_message_is_untrusted_context_not_a_system_message():
    """Retrieved text must never enter the system role — it is data, not
    instruction, and the system prefix has to stay byte-identical for KV cache
    reuse besides."""
    from src.greenhouse_wiring import greenhouse_recall_message

    provider = FakeProvider(hits=[FakeHit("something", kind="fact")])
    message = run(greenhouse_recall_message(provider, "q"))

    assert message["role"] != "system"


def test_no_message_when_nothing_is_recalled():
    """Silence about a *success* is correct. An empty recall adds nothing."""
    from src.greenhouse_wiring import greenhouse_recall_message

    provider = FakeProvider(hits=[])

    assert run(greenhouse_recall_message(provider, "q")) is None


def test_outage_produces_a_visible_notice_not_silence():
    """Invariant 13. An outage must not look like Greenhouse knowing nothing."""
    from src.greenhouse_wiring import greenhouse_recall_message

    provider = FakeProvider(hits=[], available=False, last_error="ConnectError: refused")
    message = run(greenhouse_recall_message(provider, "q"))

    assert message is not None
    assert "unavailable" in message["content"].lower()


def test_outage_notice_never_leaks_the_error_verbatim_into_instructions():
    from src.greenhouse_wiring import greenhouse_recall_message

    provider = FakeProvider(hits=[], available=False, last_error="tok=SECRET")
    message = run(greenhouse_recall_message(provider, "q"))

    assert "SECRET" not in message["content"]


def test_a_raising_provider_does_not_break_the_turn():
    """A bug in the adapter must not take chat down with it."""
    from src.greenhouse_wiring import greenhouse_recall_message

    class Exploding(FakeProvider):
        async def recall(self, query, *, owner=None, top_k=5):
            raise RuntimeError("boom")

    assert run(greenhouse_recall_message(Exploding(), "q")) is None


def test_none_provider_is_a_no_op():
    from src.greenhouse_wiring import greenhouse_recall_message

    assert run(greenhouse_recall_message(None, "q")) is None
