"""Native memory becomes within-session scratch; durable writes go to Greenhouse.

Seven code paths in Odysseus write durably to `data/memory.json`, and none of
them goes through `MemoryProvider`. Rather than edit seven upstream-owned call
sites -- each one a permanent merge conflict, and an enumeration that was wrong
the first time it was written -- every writer is handed `memory_manager` by
injection, so wrapping it once at the composition root covers all of them, plus
whatever upstream adds next.

`add_entry` builds a dict and returns it; only `save` touches the disk. `save`
is therefore the interception point.
"""

import json
from pathlib import Path

import pytest


class FakeProvider:
    provider_id = "greenhouse"

    def __init__(self, fail=False):
        self.remembered = []
        self.fail = fail

    async def remember(self, text, *, owner=None, session_id=None, category="fact",
                       source="user", metadata=None):
        if self.fail:
            raise RuntimeError("greenhouse is down")
        self.remembered.append(
            {"text": text, "category": category, "source": source, "owner": owner}
        )
        return type("R", (), {"id": f"rec-{len(self.remembered)}"})()


@pytest.fixture
def native(tmp_path):
    from src.memory import MemoryManager

    return MemoryManager(str(tmp_path))


@pytest.fixture
def memory_file(tmp_path):
    return Path(tmp_path) / "memory.json"


def wrap(native, provider):
    from src.greenhouse_backed_memory_manager import GreenhouseBackedMemoryManager

    return GreenhouseBackedMemoryManager(native, provider)


# --- the guarantee -------------------------------------------------------


def test_save_never_writes_the_native_file(native, memory_file, tmp_path):
    """The whole point. Nothing durable about the user lands on this disk."""
    before = memory_file.read_text(encoding="utf-8")
    manager = wrap(native, FakeProvider())

    entry = manager.add_entry("The return pump is a Sicce Syncra 3.0")
    manager.save([entry])

    assert memory_file.read_text(encoding="utf-8") == before


def test_a_durable_entry_reaches_greenhouse(native):
    provider = FakeProvider()
    manager = wrap(native, provider)

    manager.save([manager.add_entry("I run a UniFi Dream Machine", category="fact")])

    assert len(provider.remembered) == 1
    assert provider.remembered[0]["text"] == "I run a UniFi Dream Machine"
    assert provider.remembered[0]["category"] == "fact"


def test_a_failed_write_raises_rather_than_reporting_success(native, memory_file):
    """Invariant 13. `save` returning is what makes chat say "Saved to memory".

    A failure must not be silent, and it must not fall back to the native file:
    a silent fallback is indistinguishable from success and rebuilds the local
    model this wrapper exists to remove.
    """
    before = memory_file.read_text(encoding="utf-8")
    manager = wrap(native, FakeProvider(fail=True))

    with pytest.raises(Exception):
        manager.save([manager.add_entry("something durable")])

    assert memory_file.read_text(encoding="utf-8") == before


# --- what gets refused ---------------------------------------------------


def test_the_automatic_extractor_is_refused(native):
    """`services/memory/memory_extractor.py` writes `source="auto"` entries the
    LLM inferred from chat, every fourth message pair.

    These are uncorroborated per-turn guesses. Durability is earned by
    recurrence across evidence, which is what consolidation produces; accepting
    a single-turn inference into the durable model is the failure the ownership
    boundary exists to prevent.
    """
    provider = FakeProvider()
    manager = wrap(native, provider)

    manager.save([manager.add_entry("User seems to like tools", source="auto")])

    assert provider.remembered == []


def test_refusing_the_extractor_is_logged_not_silent(native, caplog):
    provider = FakeProvider()
    manager = wrap(native, provider)

    with caplog.at_level("INFO"):
        manager.save([manager.add_entry("inferred thing", source="auto")])

    assert any("auto" in r.message.lower() or "refus" in r.message.lower()
               for r in caplog.records)


def test_refused_entries_do_not_raise(native):
    """A refusal is not a failure. Chat carries on; nothing was lost, because
    nothing was owed."""
    manager = wrap(native, FakeProvider())
    manager.save([manager.add_entry("inferred", source="auto")])


# --- scratch -------------------------------------------------------------


def test_load_does_not_expose_the_pre_existing_durable_file(native, memory_file):
    """Existing content is the user's and is not deleted -- but it stops
    reaching the prompt, so it cannot go on acting as a second model."""
    memory_file.write_text(
        json.dumps([{ "id": "old-1", "text": "a memory from before", "category": "fact"}]),
        encoding="utf-8",
    )
    manager = wrap(native, FakeProvider())

    assert manager.load() == []
    assert manager.load_all() == []
    assert memory_file.read_text(encoding="utf-8")  # still on disk, untouched


def test_scratch_survives_within_the_process_and_is_never_persisted(native, memory_file):
    before = memory_file.read_text(encoding="utf-8")
    manager = wrap(native, FakeProvider())

    manager.save([manager.add_entry("scratch note", source="scratch")])

    assert memory_file.read_text(encoding="utf-8") == before


# --- pass-through --------------------------------------------------------


def test_pure_helpers_still_work(native):
    """`find_duplicates`, `get_relevant_memories` and friends compute; they do
    not persist. They must keep working or callers break."""
    manager = wrap(native, FakeProvider())

    entries = [manager.add_entry("I use a UniFi Dream Machine")]
    assert manager.find_duplicates("I use a UniFi Dream Machine", entries)
    assert manager.get_relevant_memories("UniFi", entries) is not None
    ok, text = manager.process_inline_memory_command("remember: buy a return pump")
    assert ok and "return pump" in text


def test_wrapper_covers_the_managers_whole_public_surface(native):
    """An upstream method added to MemoryManager must fail here, loudly, rather
    than at runtime in whichever path first calls it."""
    manager = wrap(native, FakeProvider())

    for name in dir(native):
        if name.startswith("_"):
            continue
        assert hasattr(manager, name), f"wrapper is missing {name}"


# --- the real composition root ------------------------------------------


def test_the_real_initializer_hands_the_wrapper_to_every_consumer(tmp_path, monkeypatch):
    """The whole design rests on one claim: every writer receives the manager
    by injection, so wrapping it once covers all of them.

    This asserts it against the real `initialize_managers` rather than against
    a description of it, because that claim is what makes editing seven call
    sites unnecessary — and if it ever stops being true, this is the only place
    that would notice.
    """
    monkeypatch.setenv("GREENHOUSE_URL", "http://greenhouse.test")
    monkeypatch.setenv("GREENHOUSE_TOKEN", "tok")
    monkeypatch.setattr("src.app_initializer.DATA_DIR", str(tmp_path), raising=False)

    from src.app_initializer import initialize_managers
    from src.greenhouse_backed_memory_manager import GreenhouseBackedMemoryManager

    components = initialize_managers(str(tmp_path))
    manager = components["memory_manager"]

    assert isinstance(manager, GreenhouseBackedMemoryManager)
    # The same object, not merely another wrapper: a second wrapper would keep
    # its own scratch and the two would disagree.
    assert components["chat_processor"].memory_manager is manager
    assert components["chat_handler"].memory_manager is manager
    native = components["memory_provider_registry"].get("native")
    assert native.memory_manager is manager
    assert components["memory_provider_registry"].get("greenhouse") is not None


def test_the_initializer_is_untouched_without_greenhouse(tmp_path, monkeypatch):
    monkeypatch.delenv("GREENHOUSE_URL", raising=False)
    monkeypatch.delenv("GREENHOUSE_TOKEN", raising=False)
    monkeypatch.setattr("src.app_initializer.DATA_DIR", str(tmp_path), raising=False)

    from src.app_initializer import initialize_managers
    from src.greenhouse_backed_memory_manager import GreenhouseBackedMemoryManager
    from src.memory import MemoryManager

    components = initialize_managers(str(tmp_path))

    assert isinstance(components["memory_manager"], MemoryManager)
    assert not isinstance(components["memory_manager"], GreenhouseBackedMemoryManager)
