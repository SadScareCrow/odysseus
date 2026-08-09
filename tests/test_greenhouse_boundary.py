"""The boundary must survive an upstream merge.

This fork tracks an active upstream. The risk worth defending against is not a
conflict -- a conflict is loud, and gets resolved. It is a *clean* merge that
adds an eighth durable writer to `data/memory.json`: nothing fails, no marker
appears, and Odysseus quietly starts keeping its own model of Daniel again,
which is the outcome the ownership boundary exists to prevent.

So these assert the invariant rather than the implementation. They name no
write path, because the path that matters is the one nobody has written yet.
They keep working across renames, moves (upstream relocated the whole memory
domain into `routes/memory/` once already), and additions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Constructing a MemoryManager directly bypasses the wrapper installed at the
# composition root, and therefore bypasses the whole boundary. These are the
# only places allowed to do it.
_MAY_CONSTRUCT_DIRECTLY = {
    "src/app_initializer.py",  # the composition root; wraps it immediately
    "src/greenhouse_backed_memory_manager.py",  # the wrapper itself
    # These two build their own rather than receiving one, so they ask
    # `active_memory_manager` for the wrapped instance and only fall back to a
    # raw one when Greenhouse is not configured. This guard found both.
    "src/builtin_actions.py",
    "services/memory/service.py",
    "mcp_servers/memory_server.py",
}

_CONSTRUCTION = re.compile(r"\bMemoryManager\s*\(")

# Everything shipped, not a hand-picked list. The first version of this guard
# named five directories and missed `mcp_servers/`, which contains a live
# durable writer -- exactly the blind spot the guard exists to remove.
_NOT_SHIPPED = {".venv", "venv", "node_modules", "__pycache__", "tests", "docs", "licenses"}


def _python_files():
    for path in REPO.rglob("*.py"):
        if _NOT_SHIPPED & set(path.relative_to(REPO).parts):
            continue
        yield path


def _constructors_outside_the_allowlist(extra_root: Path | None = None):
    found = []
    files = list(_python_files())
    if extra_root is not None:
        files += list(extra_root.rglob("*.py"))
    for path in files:
        try:
            rel = path.relative_to(REPO).as_posix()
        except ValueError:
            rel = path.name  # a fabricated tree outside the repo
        if rel in _MAY_CONSTRUCT_DIRECTLY:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "GreenhouseBackedMemoryManager" in stripped:
                continue
            if _CONSTRUCTION.search(stripped):
                found.append(f"{rel}: {stripped[:90]}")
    return found


def test_nothing_builds_its_own_memory_manager():
    """Every writer must receive the manager, not make one.

    That is the single assumption the boundary rests on: wrapping the injected
    object covers all seven known writers precisely because none of them
    constructs its own. An upstream addition that does would slip past the
    wrapper entirely, and would merge without a conflict.
    """
    offenders = _constructors_outside_the_allowlist()

    assert not offenders, (
        "these construct a MemoryManager directly and so bypass the Greenhouse "
        "boundary; route them through the injected manager, or add them to "
        "_MAY_CONSTRUCT_DIRECTLY if they genuinely cannot write:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_catches_a_writer_that_does_not_exist_yet(tmp_path):
    """The guard has to fail on something new, or it proves nothing.

    A test that only passes is indistinguishable from a test that cannot fail;
    three Stage 0 cards shipped green while being wrong. This fabricates the
    eighth writer, in a throwaway tree, and requires the guard to name it.
    """
    intruder = tmp_path / "src" / "some_new_upstream_feature.py"
    intruder.parent.mkdir(parents=True)
    intruder.write_text(
        "from src.memory import MemoryManager\n"
        "def remember_something(text):\n"
        "    manager = MemoryManager('data')\n"
        "    manager.save([manager.add_entry(text)])\n",
        encoding="utf-8",
    )

    offenders = _constructors_outside_the_allowlist(extra_root=tmp_path)

    assert any("some_new_upstream_feature" in entry for entry in offenders)


@pytest.mark.parametrize(
    "path",
    [
        "src/builtin_actions.py",
        "services/memory/service.py",
        "mcp_servers/memory_server.py",
    ],
)
def test_an_exempted_builder_still_asks_for_the_wrapped_manager(path: str):
    """The allowlist must not become a way to opt out of the boundary.

    These two are exempt from the construction ban only because they route
    through `active_memory_manager`. Without this, adding a name to the
    allowlist would silently reopen the hole it was meant to record.
    """
    source = (REPO / path).read_text(encoding="utf-8")

    assert "active_memory_manager" in source


def test_the_composition_root_still_wraps():
    """An upstream refactor of `initialize_managers` could drop the wrapping
    line and merge cleanly, silently reverting the whole stage."""
    source = (REPO / "src" / "app_initializer.py").read_text(encoding="utf-8")

    assert "scope_native_memory_to_scratch" in source


def test_the_recall_call_survives_in_the_chat_path():
    """Same risk on the read side: losing this line degrades chat to native
    memory without anything failing."""
    source = (REPO / "routes" / "chat_helpers.py").read_text(encoding="utf-8")

    assert "greenhouse_recall_message" in source


@pytest.mark.parametrize(
    "path,needle",
    [
        ("src/service_health.py", "greenhouse_health"),
        ("docker-compose.yml", "GREENHOUSE_URL"),
    ],
)
def test_the_remaining_upstream_touchpoints_survive(path: str, needle: str):
    """The fork's whole footprint in upstream-owned files is four files. Each
    one is a line an upstream refactor could quietly take with it."""
    assert needle in (REPO / path).read_text(encoding="utf-8")


def test_a_session_leaves_the_native_file_untouched(tmp_path, monkeypatch):
    """The behavioural half: whatever the paths are, the disk does not change."""
    monkeypatch.setenv("GREENHOUSE_URL", "http://greenhouse.test")
    monkeypatch.setenv("GREENHOUSE_TOKEN", "tok")
    monkeypatch.setattr("src.app_initializer.DATA_DIR", str(tmp_path), raising=False)

    from src.app_initializer import initialize_managers

    components = initialize_managers(str(tmp_path))
    manager = components["memory_manager"]
    memory_file = tmp_path / "memory.json"
    before = memory_file.read_text(encoding="utf-8")

    # Inferred entries are refused outright; a stated one would be attempted
    # against a Greenhouse that is not there and must fail rather than fall
    # back to disk. Either way the file does not move.
    manager.save([manager.add_entry("an inferred thing", source="auto")])
    with pytest.raises(Exception):
        manager.save([manager.add_entry("something durable")])

    assert memory_file.read_text(encoding="utf-8") == before


def test_an_unconfigured_odysseus_reports_the_stock_service_list(monkeypatch):
    """The fork must be invisible when Greenhouse is not configured.

    Upstream tests assert the exact set of service names, and adding one broke
    three of them. Editing those tests would have been the wrong fix: it would
    have made the fork's presence a permanent diff in a file upstream owns, and
    changed what a stock install reports.
    """
    import asyncio

    monkeypatch.delenv("GREENHOUSE_URL", raising=False)
    monkeypatch.delenv("GREENHOUSE_TOKEN", raising=False)

    from src.service_health import collect_service_health

    out = asyncio.run(collect_service_health())

    assert "greenhouse" not in {service["name"] for service in out["services"]}


def test_a_configured_odysseus_reports_greenhouse(monkeypatch):
    import asyncio

    monkeypatch.setenv("GREENHOUSE_URL", "http://greenhouse.test")
    monkeypatch.setenv("GREENHOUSE_TOKEN", "tok")

    from src.service_health import collect_service_health

    out = asyncio.run(collect_service_health())

    assert "greenhouse" in {service["name"] for service in out["services"]}
