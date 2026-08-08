"""Tests for the Greenhouse memory provider adapter.

The adapter is a translation shell: it builds a request, sends it, and maps the
response onto the provider dataclasses. It holds no Greenhouse domain logic --
kind vocabulary, category mapping and error semantics all live server-side, so
that this AGPL-licensed file stays free of Greenhouse's model. See
`docs/plans/2026-08-07-stage-1-odysseus-memory-provider.md` in the Greenhouse
repository, decision 2.
"""

import asyncio
import json

import httpx


def run(coro):
    return asyncio.run(coro)


def make_provider(handler, **kwargs):
    """Build a provider whose HTTP calls are served by `handler`."""
    from src.greenhouse_memory_provider import GreenhouseMemoryProvider

    provider = GreenhouseMemoryProvider(
        base_url="http://greenhouse.test",
        token="test-token",
        device_id="odysseus-test",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )
    run(provider.initialize())
    return provider


MEMORY_PAYLOAD = {
    "id": "rec-1",
    "kind": "fact",
    "authority": "human",
    "corroboration_count": 2,
    "source_block_address": "aquariums.md#major-systems/180-gallon-display:4",
    "content": "The return pump is a Sicce Syncra 3.0",
    "presentation": "Aquariums > Major systems: The return pump is a Sicce Syncra 3.0",
    "area_key": "aquariums",
    "freshness": "current",
    "created_at": "2026-08-08T10:30:00-05:00",
    "score": 0.87,
}


# --- the contract itself -------------------------------------------------


def test_provider_implements_every_abstract_method():
    """A provider missing `list_memories` cannot be constructed at all.

    The MVP plan described the contract as three methods and omitted
    `list_memories`, which is `@abstractmethod`. That mistake surfaces only at
    construction time, so it gets its own test.
    """
    from src.memory_provider import MemoryProvider

    provider = make_provider(lambda request: httpx.Response(200, json={}))

    assert isinstance(provider, MemoryProvider)
    assert provider.provider_id == "greenhouse"
    assert provider.provider_id != "native"
    assert provider.display_name
    assert provider.enabled is True


def test_provider_registers_alongside_native():
    from src.memory_provider import MemoryProviderRegistry, NativeMemoryProvider

    provider = make_provider(lambda request: httpx.Response(200, json={}))
    registry = MemoryProviderRegistry([NativeMemoryProvider(None, None), provider])

    assert {p.provider_id for p in registry.active()} == {"native", "greenhouse"}


# --- recall --------------------------------------------------------------


def test_recall_queries_search_and_returns_hits():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"memories": [MEMORY_PAYLOAD]})

    provider = make_provider(handler)
    hits = run(provider.recall("return pump", top_k=7))

    assert "/v1/memories/search" in seen["url"]
    assert "q=return+pump" in seen["url"] or "q=return%20pump" in seen["url"]
    assert "limit=7" in seen["url"]
    assert seen["auth"] == "Bearer test-token"

    assert len(hits) == 1
    assert hits[0].provider_id == "greenhouse"
    assert hits[0].score == 0.87
    assert hits[0].memory.id == "rec-1"
    assert hits[0].memory.text == "The return pump is a Sicce Syncra 3.0"


def test_recall_passes_through_a_null_score():
    """`MemorySearchHit.score` is Optional; the server may omit it."""
    payload = dict(MEMORY_PAYLOAD)
    payload.pop("score")
    provider = make_provider(lambda r: httpx.Response(200, json={"memories": [payload]}))

    hits = run(provider.recall("anything"))

    assert hits[0].score is None


def test_rich_typing_rides_in_metadata_and_is_not_flattened_into_category():
    """Greenhouse's typing must survive the crossing.

    `category` carries Odysseus's own vocabulary so existing scoring keeps
    working; everything Greenhouse knows that Odysseus has no field for rides
    in `metadata` instead of being lost.
    """
    provider = make_provider(
        lambda r: httpx.Response(200, json={"memories": [MEMORY_PAYLOAD]})
    )

    memory = run(provider.recall("pump"))[0].memory
    greenhouse = memory.metadata["greenhouse"]

    assert greenhouse["kind"] == "fact"
    assert greenhouse["authority"] == "human"
    assert greenhouse["corroboration_count"] == 2
    assert greenhouse["area_key"] == "aquariums"
    assert greenhouse["freshness"] == "current"
    assert greenhouse["source_block_address"].endswith(":4")
    assert greenhouse["presentation"].startswith("Aquariums > ")
    # The offset-carrying original survives; `timestamp` is the lossy convenience.
    assert greenhouse["created_at"] == "2026-08-08T10:30:00-05:00"
    assert memory.category == "fact"
    assert isinstance(memory.timestamp, int)
    assert memory.timestamp > 0


# --- remember ------------------------------------------------------------


def test_remember_posts_a_stated_memory_and_returns_the_record():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"memory": MEMORY_PAYLOAD, "seed": {"capture_id": "cap-1"}}
        )

    provider = make_provider(handler)
    record = run(
        provider.remember(
            "The return pump is a Sicce Syncra 3.0",
            session_id="session-9",
            category="fact",
        )
    )

    assert "/v1/memories/stated" in seen["url"]
    assert seen["body"]["text"] == "The return pump is a Sicce Syncra 3.0"
    assert seen["body"]["category"] == "fact"
    assert seen["body"]["device_id"] == "odysseus-test"
    assert seen["body"]["client_capture_id"]
    assert seen["body"]["client_timestamp"]
    assert record.id == "rec-1"
    assert record.session_id == "session-9"
    assert record.metadata["greenhouse"]["seed_capture_id"] == "cap-1"


def test_remember_sends_the_raw_category_without_mapping_it():
    """The category mapping is Greenhouse's, not the adapter's.

    Putting it here would place Greenhouse vocabulary in an AGPL file and
    create a second copy that drifts.
    """
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201, json={"memory": MEMORY_PAYLOAD, "seed": {"capture_id": "cap-1"}}
        )

    provider = make_provider(handler)
    run(provider.remember("something", category="not_a_known_category"))

    assert seen["body"]["category"] == "not_a_known_category"


def test_remember_reuses_its_idempotency_key_on_retry():
    """`device_id + client_capture_id` is the retry boundary. A retry must not
    generate a fresh id, or the same statement lands twice."""
    keys = []

    def handler(request):
        keys.append(json.loads(request.content)["client_capture_id"])
        if len(keys) == 1:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(
            200, json={"memory": MEMORY_PAYLOAD, "seed": {"capture_id": "cap-1"}}
        )

    provider = make_provider(handler, max_retries=1)
    run(provider.remember("The return pump is a Sicce Syncra 3.0"))

    assert len(keys) == 2
    assert keys[0] == keys[1]


# --- list_memories -------------------------------------------------------


def test_list_memories_reads_the_list_route():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"memories": [MEMORY_PAYLOAD]})

    provider = make_provider(handler)
    memories = run(provider.list_memories(limit=25))

    assert "/v1/memories" in seen["url"]
    assert "/search" not in seen["url"]
    assert "limit=25" in seen["url"]
    assert [m.id for m in memories] == ["rec-1"]


# --- delete --------------------------------------------------------------


def test_delete_archives_and_never_hard_deletes():
    """Invariant 8: AI cannot hard-delete. The native provider removes rows;
    this one must not."""
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"memory": MEMORY_PAYLOAD})

    provider = make_provider(handler)
    assert run(provider.delete("rec-1")) is True

    assert seen["method"] == "POST"
    assert seen["url"].endswith("/v1/memories/rec-1/archive")
    assert "DELETE" != seen["method"]


def test_delete_returns_false_for_a_missing_record():
    provider = make_provider(lambda r: httpx.Response(404, json={"message": "nope"}))

    assert run(provider.delete("ghost")) is False


# --- outage behaviour ----------------------------------------------------


def test_recall_degrades_to_empty_and_records_the_outage():
    """Invariant 13: silence about a failure is a defect. Recall returns
    nothing so the turn survives, but the outage becomes visible."""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    provider = make_provider(handler)
    hits = run(provider.recall("sump"))

    assert hits == []
    assert provider.available is False
    assert "refused" in provider.last_error.lower() or provider.last_error


def test_remember_raises_on_outage_and_never_claims_success():
    from src.greenhouse_memory_provider import GreenhouseUnavailable

    def handler(request):
        raise httpx.ConnectError("connection refused")

    provider = make_provider(handler, max_retries=0)

    try:
        run(provider.remember("something durable"))
    except GreenhouseUnavailable:
        pass
    else:
        raise AssertionError("remember must not report success when the write failed")

    assert provider.available is False


def test_recovery_clears_the_outage_state():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"memories": [MEMORY_PAYLOAD]})

    provider = make_provider(handler)
    run(provider.recall("sump"))
    assert provider.available is False

    run(provider.recall("sump"))
    assert provider.available is True
    assert provider.last_error is None


def test_errors_never_leak_the_credential():
    def handler(request):
        return httpx.Response(401, json={"message": "unauthorized"})

    provider = make_provider(handler)
    run(provider.recall("sump"))

    assert "test-token" not in (provider.last_error or "")


# --- the licensing boundary ---------------------------------------------


def test_adapter_imports_nothing_from_greenhouse():
    """ADR-0005: the licensing boundary and the ownership boundary are the same
    line. This file is AGPL-3.0 by virtue of subclassing Odysseus; Greenhouse
    is a separate service reached over HTTP and must not be imported into it."""
    import pathlib

    source = pathlib.Path("src/greenhouse_memory_provider.py").read_text(
        encoding="utf-8"
    )

    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "greenhouse." not in stripped.replace(
                "src.greenhouse_memory_provider", ""
            ), f"adapter must not import from greenhouse: {stripped}"
