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

RECALL_LIMIT = 5


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

    return GreenhouseMemoryProvider(base_url=base_url, token=token)


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
        # Deliberately does not include `last_error`: it is operator detail,
        # and it is the one string in this path that has touched a credential.
        return untrusted_context_message(
            "greenhouse: model unavailable",
            "The durable model of the user is currently unavailable, so no "
            "stored knowledge could be consulted for this turn. Say so if the "
            "user asks something it would have answered. Do not guess, and do "
            "not claim to have remembered anything.",
        )

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
