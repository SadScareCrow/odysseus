"""Drive the real Odysseus adapter against a real Greenhouse over a real socket.

The harness proves Greenhouse's side of the contract. This proves the two
sides agree, which is the thing neither repository's test suite can check:
Greenhouse cannot import the AGPL adapter, and Odysseus has no Greenhouse to
talk to.
"""

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

GREENHOUSE = Path(r"C:\Git\greenhouse")
ODYSSEUS = Path(r"C:\Git\odysseus")
TOKEN = "odysseus-adapter-pair-probe-token"

sys.path.insert(0, str(ODYSSEUS))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_greenhouse(tmp):
    db = Path(tmp) / "pair.db"
    attachments = Path(tmp) / "attachments"
    attachments.mkdir(mode=0o700, exist_ok=True)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({
        "GREENHOUSE_DATABASE_URL": f"sqlite:///{db}",
        "GREENHOUSE_ATTACHMENT_ROOT": str(attachments),
        "GREENHOUSE_AUTH_VERIFIERS": json.dumps([{
            "principal_id": "pair-probe",
            "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
            "scopes": ["captures:read", "captures:write"],
        }]),
    })
    subprocess.run(
        ["uv", "run", "python", "-m", "alembic", "upgrade", "head"],
        cwd=GREENHOUSE, env=env, check=True, capture_output=True, text=True,
    )
    port = free_port()
    server = subprocess.Popen(
        ["uv", "run", "python", "-m", "uvicorn", "greenhouse.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=GREENHOUSE, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(f"{base}/healthz", timeout=2).status_code < 500:
                return server, base
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    server.kill()
    log = server.stdout.read() if server.stdout else ""
    raise RuntimeError("Greenhouse did not start:\n" + log[-3000:])


async def probe(base):
    from src.greenhouse_memory_provider import GreenhouseMemoryProvider

    p = GreenhouseMemoryProvider(base_url=base, token=TOKEN, device_id="pair-probe")
    await p.initialize()
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))

    try:
        text = "The return pump is a Sicce Syncra 3.0"
        rec = await p.remember(text, category="fact", session_id="probe-session")
        check("remember returns a record", bool(rec.id), f"id={rec.id}")
        check("remember content round-trips verbatim", rec.text == text, repr(rec.text))
        gh = rec.metadata.get("greenhouse", {})
        check("seed capture id present", bool(gh.get("seed_capture_id")))

        hits = await p.recall("Sicce", top_k=5)
        check("recall finds the written memory", any(h.memory.id == rec.id for h in hits),
              f"{len(hits)} hit(s)")
        if hits:
            f = hits[0].memory.metadata.get("greenhouse", {})
            missing = [k for k in ("kind", "authority", "corroboration_count",
                                   "area_key", "freshness", "created_at",
                                   "source_block_address", "presentation")
                       if k not in f]
            check("rich typing rides in metadata", not missing, f"missing={missing}")
            check("score is higher-is-better (positive)",
                  hits[0].score is None or hits[0].score >= 0, f"score={hits[0].score}")
            check("timestamp parsed to epoch int",
                  isinstance(hits[0].memory.timestamp, int) and hits[0].memory.timestamp > 0,
                  f"ts={hits[0].memory.timestamp}")
            check("category populated", bool(hits[0].memory.category),
                  f"category={hits[0].memory.category!r}")

        listed = await p.list_memories(limit=10)
        check("list_memories returns records", any(m.id == rec.id for m in listed),
              f"{len(listed)} record(s)")

        check("delete archives and returns True", await p.delete(rec.id) is True)
        after = await p.recall("Sicce", top_k=5)
        check("archived memory leaves search", not any(h.memory.id == rec.id for h in after))
        check("provider healthy throughout", p.available is True, str(p.last_error))
    finally:
        await p.shutdown()

    return results


def main():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        server, base = start_greenhouse(tmp)
        try:
            results = asyncio.run(probe(base))
        finally:
            server.kill()
            server.wait(timeout=10)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
