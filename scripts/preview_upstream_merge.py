"""Report what an upstream merge would do to the Greenhouse boundary.

This fork tracks an active upstream. Merging is routine; the risk is not.

A conflict is the good case -- git stops and someone looks. The bad case is a
clean merge that adds a durable writer to `data/memory.json`, or quietly drops
one of the few lines this fork adds to upstream-owned files. Nothing fails, no
marker appears, and Odysseus goes back to keeping its own model of the user.

Read-only. Fetches upstream refs and reports; never merges, never checks
anything out, never touches the working tree.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UPSTREAM = "origin"
UPSTREAM_BRANCH = "dev"

# The fork's entire footprint in files upstream also owns. Each line here is
# one an upstream refactor could take with it without conflicting.
TOUCHED = {
    "src/app_initializer.py": "scope_native_memory_to_scratch",
    "routes/chat_helpers.py": "greenhouse_recall_message",
    "src/service_health.py": "greenhouse_health",
    "src/builtin_actions.py": "active_memory_manager",
    "services/memory/service.py": "active_memory_manager",
    "docker-compose.yml": "GREENHOUSE_URL",
}


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="use the refs already present")
    args = parser.parse_args()

    if not args.no_fetch:
        subprocess.run(
            ["git", "fetch", UPSTREAM, UPSTREAM_BRANCH],
            cwd=REPO,
            capture_output=True,
            check=False,
        )

    target = f"{UPSTREAM}/{UPSTREAM_BRANCH}"
    base = git("merge-base", "HEAD", target)
    if not base:
        print(f"cannot compare against {target}; is it fetched?")
        return 1

    incoming = [line for line in git("log", "--oneline", f"HEAD..{target}").splitlines() if line]
    print(f"upstream {target}: {len(incoming)} commit(s) not in this branch")
    if not incoming:
        print("nothing to merge.")
        return 0

    changed = set(git("diff", "--name-only", f"{base}..{target}").splitlines())

    print("\nfiles this fork edits that upstream also changed:")
    collisions = sorted(changed & set(TOUCHED))
    for path in collisions:
        print(f"  ! {path}   (keep: {TOUCHED[path]})")
    if not collisions:
        print("  none")

    # The quiet failure: new code that writes native memory and merges clean.
    suspects = sorted(
        path
        for path in changed
        if path.endswith(".py") and ("memory" in path.lower() or "memor" in path.lower())
    )
    print("\nupstream files touching memory (check for new durable writers):")
    for path in suspects:
        print(f"  ? {path}")
    if not suspects:
        print("  none")

    print(
        "\nAfter merging, run:  pytest tests/test_greenhouse_boundary.py\n"
        "That is the check that matters. A clean merge proves nothing on its own:\n"
        "upstream can add a writer without touching a single line this fork edits."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
