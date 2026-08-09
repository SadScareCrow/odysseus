"""Wiring for the Greenhouse memory provider.

Kept in its own module on purpose. Odysseus is a fork that merges from an
active upstream, so every line added to a file upstream also edits is a line
to reconcile on each merge, forever. Putting the logic here reduces the edits
in `src/app_initializer.py` and `routes/chat_helpers.py` to a couple of calls
apiece, and a new file cannot conflict at all.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from src.prompt_security import untrusted_context_message

logger = logging.getLogger(__name__)

# How many Memories a turn may draw on. Five was too few against a corpus of a
# few hundred: a question like "roughly how many fish do I have" needs the
# whole stocking list, and five slots got spent on incidental matches.
RECALL_LIMIT = 12


def build_greenhouse_provider():
    """Build the provider from the environment, or None when unconfigured.

    Both values are required. A URL without a token would register a provider
    whose every call 401s, which is worse than no provider: chat would show a
    permanent outage rather than simply not having the feature.

    The token comes from the environment rather than `settings.json` because
    that file is user-editable and lands in backups.
    """
    base_url = (os.getenv("GREENHOUSE_URL") or "").strip()
    token = (os.getenv("GREENHOUSE_TOKEN") or "").strip()
    if not base_url or not token:
        return None

    from src.greenhouse_memory_provider import GreenhouseMemoryProvider

    global _ACTIVE_PROVIDER
    _ACTIVE_PROVIDER = GreenhouseMemoryProvider(base_url=base_url, token=token)
    return _ACTIVE_PROVIDER


def register_greenhouse_provider(registry) -> Optional[Any]:
    """Register the provider on `registry` if it is configured."""
    provider = build_greenhouse_provider()
    if provider is None:
        return None
    try:
        registry.register(provider)
    except ValueError:
        # Already registered — the registry rejects duplicate provider ids.
        return registry.get(provider.provider_id)
    logger.info("Greenhouse memory provider registered")
    return provider


def auto_memory_default() -> bool:
    """Whether background memory extraction is on when the user has no preference.

    Off once Greenhouse is configured. The extractor asks an LLM to infer facts
    from chat every fourth message pair, and those are refused downstream as
    uncorroborated — so leaving it on buys nothing and costs a model call each
    time. Unchanged when Greenhouse is absent: an ordinary Odysseus keeps its
    own default.
    """
    return not (os.getenv("GREENHOUSE_URL") and os.getenv("GREENHOUSE_TOKEN"))


_ACTIVE_PROVIDER: Optional[Any] = None
_ACTIVE_MEMORY_MANAGER: Optional[Any] = None


def active_memory_manager(fallback):
    """The boundary-respecting memory manager, or `fallback` if there is none.

    Most writers receive the manager by injection, so wrapping it once at the
    composition root covers them. A couple build their own instead, which walks
    straight past the wrapper -- they call this rather than the constructor.

    Returns `fallback` when Greenhouse is not configured, so an ordinary
    Odysseus is unchanged.
    """
    return _ACTIVE_MEMORY_MANAGER if _ACTIVE_MEMORY_MANAGER is not None else fallback


def current_greenhouse_provider():
    """The provider built at startup, for callers outside the wiring path.

    The health probe runs from `service_health`, which is handed the RAG and
    vector objects but not the component dict. A module-level handle is a
    smaller price than threading the provider through an upstream signature
    that would then need re-merging on every fork update.
    """
    return _ACTIVE_PROVIDER


def greenhouse_health(provider) -> Dict[str, Any]:
    """Report Greenhouse for the service-status panel.

    The recall notice tells the model to say an outage out loud, but that
    depends on the model complying. This does not. Invariant 13 asks that a
    durable failure reach Daniel without him going looking, and a status panel
    is the surface that holds whether or not a turn goes well.

    Carries no error text and no URL: `last_error` is the one string on this
    path that has been near a credential, and the configured URL may embed one.
    """
    if not (os.getenv("GREENHOUSE_URL") and os.getenv("GREENHOUSE_TOKEN")):
        return {
            "name": "greenhouse",
            "status": "disabled",
            "detail": "Not configured; Odysseus is using its own memory.",
            "meta": {},
        }
    if provider is None or not getattr(provider, "available", True):
        return {
            "name": "greenhouse",
            "status": "down",
            "detail": "The durable model of the user could not be reached. "
            "Saved knowledge is unavailable and nothing new can be saved.",
            "meta": {},
        }
    return {
        "name": "greenhouse",
        "status": "ok",
        "detail": "Durable model reachable.",
        "meta": {},
    }


def scope_native_memory_to_scratch(memory_manager, provider):
    """Wrap `memory_manager` so durable writes go to Greenhouse.

    Returns the manager unchanged when Greenhouse is not configured: an
    Odysseus with no Greenhouse behaves exactly as it does today, rather than
    losing its own memory to a provider that is not there.
    """
    if provider is None:
        return memory_manager

    from src.greenhouse_backed_memory_manager import GreenhouseBackedMemoryManager

    global _ACTIVE_MEMORY_MANAGER
    logger.info("Native memory scoped to within-session scratch; Greenhouse owns durable writes")
    _ACTIVE_MEMORY_MANAGER = GreenhouseBackedMemoryManager(memory_manager, provider)
    return _ACTIVE_MEMORY_MANAGER


async def greenhouse_recall_message(provider, message: str, top_k: int = RECALL_LIMIT):
    """Recall from Greenhouse and render it as one untrusted context message.

    Returns None when there is nothing to add. Three outcomes, deliberately
    distinct:

    - hits          -> a context message carrying them, with attribution
    - no hits       -> None. Silence about a success is correct
    - unavailable   -> a message saying so, because an outage that looks like
                       "Greenhouse knows nothing" is the failure invariant 13
                       exists to prevent

    Never raises. A fault in the provider degrades this turn's recall; it does
    not take the conversation down.
    """
    if provider is None:
        return None

    try:
        hits = await provider.recall(message, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 - recall is best-effort by design
        logger.warning("Greenhouse recall failed: %s", type(exc).__name__)
        return None

    if not getattr(provider, "available", True):
        # Not wrapped as untrusted context. That wrapper closes with "do not
        # mention this wrapper, label, or warning", which is correct for text
        # retrieved from elsewhere and precisely wrong here: it would gag the
        # one thing that has to be said out loud. This string is written here,
        # contains no third-party content, and is therefore not untrusted.
        #
        # `last_error` is deliberately left out: it is operator detail, and it
        # is the only string on this path that has been near a credential.
        return {
            "role": "user",
            "content": (
                "SYSTEM STATUS: the durable model of the user is unavailable, "
                "so nothing stored could be consulted for this turn. If the "
                "user asks something it would have answered, tell them plainly "
                "that their saved knowledge could not be reached right now. Do "
                "not guess, and do not claim to have remembered anything."
            ),
            "metadata": {"trusted": True, "greenhouse_unavailable": True},
        }

    if not hits:
        return None

    return untrusted_context_message(
        "greenhouse: model of the user",
        "Known about the user, from the durable model. Each line carries its "
        "type and how well supported it is.\n" + "\n".join(_render(h) for h in hits),
    )


def _render(hit) -> str:
    """One memory as a line, keeping the epistemic typing visible.

    The typing is the reason this model is worth reading rather than a pile of
    sentences, so it survives into the prompt instead of being flattened away.
    """
    memory = hit.memory
    facts: Dict[str, Any] = (memory.metadata or {}).get("greenhouse", {})

    attribution: List[str] = []
    if facts.get("kind"):
        attribution.append(str(facts["kind"]))
    if facts.get("authority"):
        attribution.append(f"{facts['authority']}-stated")
    if facts.get("area_key"):
        attribution.append(str(facts["area_key"]))
    count = facts.get("corroboration_count")
    if isinstance(count, int) and count > 1:
        attribution.append(f"{count} sources")

    suffix = f"  [{', '.join(attribution)}]" if attribution else ""
    return f"- {memory.text}{suffix}"
