"""Greenhouse memory provider — Odysseus reads and writes the durable model.

Greenhouse owns the durable, provider-independent model of its user. Odysseus
renders it and hosts chat; it does not keep a copy. This adapter is the seam
between the two, and it is deliberately thin.

**It holds no Greenhouse domain logic.** The kind vocabulary, the category
mapping, the review rules and the error semantics all live server-side and are
reached over HTTP. That is a licensing boundary as much as a design one: this
file subclasses Odysseus' `MemoryProvider` and is therefore AGPL-3.0, while
Greenhouse is a separate service that is not derived from it. Keeping the
model's vocabulary out of this file keeps the two apart, and stops a second
copy of that vocabulary existing to drift out of step with the first.

The whole job is: build a request, send it, map the response onto
`MemoryRecord` / `MemorySearchHit`. Anything that requires knowing what a kind
*means* belongs on the other side of the wire.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from src.memory_provider import MemoryProvider, MemoryRecord, MemorySearchHit

logger = logging.getLogger(__name__)

# Fields the server sends that Odysseus has a home for. Everything else on the
# payload rides in `metadata` rather than being dropped.
_DIRECT_FIELDS = {"id", "content", "score"}

# Who an archive is attributed to. An archive is reversible and attributable;
# the record keeps who retired it.
ARCHIVE_ACTOR = "odysseus-memory-provider"


class GreenhouseUnavailable(RuntimeError):
    """A durable write could not be completed.

    Raised rather than swallowed. Silence about a failure is a defect: chat
    must never report that something was remembered when the write did not
    land, and it must never quietly fall back to native memory, because a
    silent fallback is indistinguishable from success and recreates the local
    model this provider exists to remove.
    """


class GreenhouseMemoryProvider(MemoryProvider):
    """Reads and writes the durable model over Greenhouse's HTTP API."""

    provider_id = "greenhouse"
    display_name = "Greenhouse"

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        device_id: Optional[str] = None,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 10.0,
        max_retries: int = 1,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.device_id = device_id or f"odysseus-{uuid.getnode():x}"
        self._transport = transport
        self._timeout = timeout
        self._max_retries = max_retries
        self._client: Optional[httpx.AsyncClient] = None

        # Health, read by the chat surface so an outage is visible rather than
        # looking like "Greenhouse knows nothing about that".
        self.available: bool = True
        self.last_error: Optional[str] = None

    # -- lifecycle --------------------------------------------------------

    async def initialize(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                transport=self._transport,
                headers={"Authorization": f"Bearer {self._token}"},
            )

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- provider contract ------------------------------------------------

    async def remember(
        self,
        text: str,
        *,
        owner: Optional[str] = None,
        session_id: Optional[str] = None,
        category: str = "fact",
        source: str = "user",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryRecord:
        """Write a stated memory: a Seed and its derived Memory, in one call.

        Greenhouse writes both in one transaction. Two calls from here would
        leave a Seed with no Memory whenever this process died in between, and
        that durability rule belongs to the system that owns the record.

        `category` is sent exactly as Odysseus supplied it. Resolving it to a
        Greenhouse kind is the server's job.
        """
        # Generated once and reused across retries: `device_id +
        # client_capture_id` is the idempotency boundary, so a fresh id on
        # retry would land the same statement twice.
        client_capture_id = str(uuid.uuid4())
        body = {
            "text": text,
            "category": category,
            "source": source,
            "device_id": self.device_id,
            "client_capture_id": client_capture_id,
            "client_timestamp": datetime.now(timezone.utc).astimezone().isoformat(),
        }
        if session_id:
            body["session_id"] = session_id
        if metadata:
            body["client_metadata"] = metadata

        response = await self._send(
            "POST", "/v1/memories/stated", json=body, retries=self._max_retries
        )
        if response is None or response.status_code >= 400:
            raise GreenhouseUnavailable(
                f"the statement was not saved: {self.last_error or 'unknown cause'}"
            )

        payload = response.json()
        record = self._to_record(payload.get("memory") or {}, session_id=session_id)
        seed = payload.get("seed") or {}
        if seed.get("capture_id"):
            record.metadata["greenhouse"]["seed_capture_id"] = seed["capture_id"]
        return record

    async def recall(
        self,
        query: str,
        *,
        owner: Optional[str] = None,
        top_k: int = 5,
    ) -> List[MemorySearchHit]:
        """Return Memories relevant to the query.

        On an outage this returns nothing so the chat turn survives, and marks
        the provider unavailable so the surface can say so. Returning empty
        *quietly* would be the defect: it is indistinguishable from Greenhouse
        genuinely knowing nothing.
        """
        response = await self._send(
            "GET", "/v1/memories/search", params={"q": query, "limit": top_k}
        )
        if response is None or response.status_code >= 400:
            return []

        return [
            MemorySearchHit(
                memory=self._to_record(item),
                provider_id=self.provider_id,
                score=item.get("score"),
            )
            for item in response.json().get("memories", [])
        ]

    async def list_memories(
        self,
        *,
        owner: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        response = await self._send("GET", "/v1/memories", params={"limit": limit})
        if response is None or response.status_code >= 400:
            return []

        return [self._to_record(item) for item in response.json().get("memories", [])]

    async def delete(self, memory_id: str, *, owner: Optional[str] = None) -> bool:
        """Archive the Memory. Never hard-delete it.

        The caller wants the record to stop coming back, which archiving
        achieves. Destroying it is not on offer: an AI cannot hard-delete, and
        the Seed and evidence behind a Memory outlive the Memory itself.
        """
        response = await self._send(
            "POST",
            f"/v1/memories/{quote(memory_id, safe='')}/archive",
            json={"actor": ARCHIVE_ACTOR},
        )
        if response is None:
            return False
        return response.status_code < 400

    # -- internals --------------------------------------------------------

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        retries: int = 0,
    ) -> Optional[httpx.Response]:
        """Send a request, recording health. Returns None when nothing arrived."""
        if self._client is None:
            await self.initialize()

        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method, path, params=params, json=json
                )
            except httpx.HTTPError as exc:
                if attempt < retries:
                    attempt += 1
                    continue
                self._record_failure(f"{type(exc).__name__}: {exc}")
                return None

            if response.status_code >= 500 and attempt < retries:
                attempt += 1
                continue

            if response.status_code >= 400:
                self._record_failure(
                    f"HTTP {response.status_code} from {path}: "
                    f"{self._detail(response)}"
                )
                return response

            self.available = True
            self.last_error = None
            return response

    def _record_failure(self, message: str) -> None:
        # The token is never interpolated into a message, but redact anyway:
        # a message that has been through a formatter is not one you can prove
        # clean by reading it, and credentials must never reach a log.
        safe = message.replace(self._token, "[redacted]") if self._token else message
        self.available = False
        self.last_error = safe
        logger.warning("Greenhouse unavailable: %s", safe)

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(body, dict):
            return str(body.get("message") or body.get("detail") or body)[:200]
        return str(body)[:200]

    def _to_record(
        self, payload: Dict[str, Any], *, session_id: Optional[str] = None
    ) -> MemoryRecord:
        """Map a Greenhouse memory payload onto a provider-neutral record.

        Everything Greenhouse knows that Odysseus has no field for is kept
        under `metadata["greenhouse"]` rather than being flattened into
        `category`. Flattening would discard the epistemic typing that is the
        entire reason the model is worth reading.

        `category` carries the server's `kind` verbatim so Odysseus' existing
        relevance scoring keeps working. It is a passthrough, not a
        translation: this adapter does not know what a kind means.
        """
        greenhouse = {
            key: value for key, value in payload.items() if key not in _DIRECT_FIELDS
        }
        if session_id:
            greenhouse["session_id"] = session_id

        return MemoryRecord(
            id=payload.get("id", ""),
            text=payload.get("content", ""),
            timestamp=self._epoch(payload.get("created_at")),
            category=payload.get("kind", "fact"),
            source=self.provider_id,
            owner=None,
            session_id=session_id,
            metadata={"greenhouse": greenhouse},
        )

    @staticmethod
    def _epoch(created_at: Optional[str]) -> int:
        """Epoch seconds for `MemoryRecord.timestamp`, which is an int.

        Lossy by the contract's design — the offset-carrying original is kept
        in metadata, and that is the one to trust.
        """
        if not created_at:
            return int(datetime.now(timezone.utc).timestamp())
        try:
            return int(datetime.fromisoformat(created_at).timestamp())
        except ValueError:
            return int(datetime.now(timezone.utc).timestamp())
