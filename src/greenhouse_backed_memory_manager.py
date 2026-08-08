"""Routes durable memory writes to Greenhouse and keeps native memory as scratch.

Greenhouse owns the durable model of the user. Odysseus renders it. Seven code
paths here write durably to `data/memory.json` and none of them goes through
`MemoryProvider`, so left alone they each keep a second model — the outcome the
ownership boundary exists to prevent.

Rather than edit those seven call sites, this wraps the one object they are all
handed. Every writer receives `memory_manager` by injection — a constructor
argument, a function argument, or `set_memory_manager` — so a single wrapper
installed at the composition root covers all of them, covers the ones nobody
found (the first enumeration of them was wrong), and covers whatever upstream
adds next. It also keeps this fork's edit to upstream files at one line.

`add_entry` builds a dict and returns it; only `save` writes the disk. `save`
is therefore where durability is decided, and the only method that has to do
anything interesting.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Sources whose entries are machine inferences rather than something the user
# said. `auto` is `services/memory/memory_extractor.py`, which asks an LLM to
# invent facts from a chat every fourth message pair.
INFERRED_SOURCES = {"auto"}

# How long a durable write may hold up the caller. `save` is synchronous and
# its return is what makes chat claim the memory was stored, so it has to know
# the answer before returning — but not at the cost of hanging the UI.
WRITE_TIMEOUT_SECONDS = 15.0


class GreenhouseWriteFailed(RuntimeError):
    """A durable write did not land, and nothing was written anywhere else."""


class GreenhouseBackedMemoryManager:
    """A `MemoryManager` whose durable writes go to Greenhouse instead of disk."""

    def __init__(self, native, provider):
        self._native = native
        self._provider = provider
        self._scratch: List[Dict[str, Any]] = []
        self._seen_ids: set = set()
        self._lock = threading.Lock()
        # One worker with its own event loop. `save` is called from inside a
        # running loop on the chat paths, so the coroutine cannot be awaited
        # here and must not be run on the caller's loop.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="greenhouse-write"
        )

    # -- the interception point -------------------------------------------

    def save(self, entries: List[Dict[str, Any]]) -> None:
        """Accept a memory set. Never writes `data/memory.json`.

        Entries the caller has not saved before are classified: an inference is
        refused, anything else is written to Greenhouse. A write that fails
        raises, because returning normally is what makes the caller report
        success, and a durable failure that looks like success is the defect
        this whole boundary is about.
        """
        with self._lock:
            fresh = [e for e in entries or [] if e.get("id") not in self._seen_ids]
            for entry in entries or []:
                if entry.get("id"):
                    self._seen_ids.add(entry["id"])
            self._scratch = list(entries or [])

        for entry in fresh:
            source = (entry.get("source") or "user").lower()
            if source in INFERRED_SOURCES:
                logger.info(
                    "Refused an inferred memory (source=%s): durability is earned by "
                    "corroboration, not by a single turn. Text withheld from logs.",
                    source,
                )
                continue
            self._remember(entry)

    def _remember(self, entry: Dict[str, Any]) -> None:
        text = (entry.get("text") or "").strip()
        if not text:
            return
        if self._provider is None:
            raise GreenhouseWriteFailed(
                "no Greenhouse provider is configured, so nothing durable can be saved"
            )

        async def write():
            return await self._provider.remember(
                text,
                owner=entry.get("owner"),
                session_id=entry.get("session_id"),
                category=entry.get("category", "fact"),
                source=entry.get("source", "user"),
            )

        try:
            self._executor.submit(lambda: asyncio.run(write())).result(
                timeout=WRITE_TIMEOUT_SECONDS
            )
        except Exception as exc:
            logger.warning("Greenhouse write failed: %s", type(exc).__name__)
            raise GreenhouseWriteFailed(f"the memory was not saved: {exc}") from exc

    # -- reads ------------------------------------------------------------

    def load_all(self) -> List[Dict[str, Any]]:
        """Scratch only.

        The pre-existing `data/memory.json` is the user's and is not deleted,
        but it stops reaching the prompt — otherwise it goes on serving as the
        second model regardless of what writes are blocked.
        """
        with self._lock:
            return list(self._scratch)

    def load(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        entries = self.load_all()
        if owner is None:
            return entries
        return [e for e in entries if e.get("owner") in (None, owner)]

    def increment_uses(self, ids: List[str]) -> None:
        """No-op. Usage counters are a property of the native file's ranking."""

    def claim_ownerless(self, owner: str) -> None:
        """No-op. Ownership is Greenhouse's, and it is a single-person model."""

    def ensure_file_exists(self) -> None:
        self._native.ensure_file_exists()

    # -- pure helpers, passed straight through ----------------------------

    def __getattr__(self, name: str) -> Any:
        """Anything not overridden is the native manager's.

        The methods that remain are computation — duplicate detection,
        relevance ranking, command parsing, chat scraping. They read what they
        are given and persist nothing, so they are safe to delegate, and
        delegating means an upstream addition keeps working rather than
        vanishing silently.
        """
        return getattr(self._native, name)
