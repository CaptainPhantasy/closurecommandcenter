"""Harsh Product Owner critique session.

Drives the live app like a demanding daily user trying to find friction:
health under burst, list under load, scrollback per floyd, bulk no-op,
CRUD stress, invalid input edge cases.

Run via:
    python3 tests/po_critique.py
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:11527"


def q(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.status, r.read()


def post(path, data):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(data).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def main() -> int:
    issues: list[str] = []

    # 1. Health under repeated rapid hits (60 quick pings)
    print("\n=== Stress 1: 60 rapid /api/health pings ===")
    fail = 0
    t0 = time.time()
    for _ in range(60):
        try:
            s, _ = q("/api/health")
            if s != 200:
                fail += 1
        except Exception:
            fail += 1
    elapsed = time.time() - t0
    print(f"60 hits in {elapsed:.2f}s, {fail} failures")
    if fail > 0:
        issues.append(f"health endpoint dropped {fail}/60 under burst")

    # 2. List + status enrichment cost
    print("\n=== Stress 2: 20 sequential /api/agents calls ===")
    t0 = time.time()
    for _ in range(20):
        q("/api/agents")
    print(f"20 hits in {time.time() - t0:.2f}s")

    # 3. Performance endpoint
    print("\n=== Stress 3: Performance metrics under load ===")
    _, body = q("/api/performance")
    print(json.loads(body))

    # 4. Read scrollback for each floyd
    print("\n=== Stress 4: Scrollback download for each floyd ===")
    agents = json.loads(q("/api/agents")[1])
    for a in agents:
        if "floyd" in (a.get("tags") or []):
            s, body = q(f"/api/agents/{a['id']}/scrollback")
            print(f"  {a['name']:20s} {len(body):6d} bytes (status {s})")

    # 5. Bulk operations don't blow up under no-op
    print("\n=== Stress 5: Bulk start with all already running ===")
    s, body = post("/api/bulk/start", {})
    print(f"  status={s} body={body[:120]!r}")

    # 6. CRUD round-trip x10
    print("\n=== Stress 6: 10 rapid create/delete round-trips ===")
    t0 = time.time()
    created_fail = deleted_fail = 0
    for i in range(10):
        try:
            s, body = post(
                "/api/agents",
                {"name": f"po-stress-{i}", "directory": "/tmp", "command": "bash"},
            )
            if s != 200:
                created_fail += 1
                continue
            agent = json.loads(body)
            req = urllib.request.Request(
                f"{BASE}/api/agents/{agent['id']}", method="DELETE"
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                if r.status != 200:
                    deleted_fail += 1
        except Exception as e:
            created_fail += 1
            print(f"  iter {i}: {e}")
    print(
        f"  10 cycles in {time.time() - t0:.2f}s, "
        f"create_fail={created_fail}, delete_fail={deleted_fail}"
    )
    if created_fail or deleted_fail:
        issues.append(
            f"CRUD round-trip stress: create_fail={created_fail}, delete_fail={deleted_fail}"
        )

    # 7. Invalid input edge cases
    print("\n=== Stress 7: Invalid agent inputs ===")
    cases = [
        ({"name": "", "directory": "/tmp", "command": "bash"}, "empty name"),
        ({"name": "x" * 200, "directory": "/tmp", "command": "bash"}, "huge name"),
        (
            {"name": "ok", "directory": "/etc/passwd", "command": "bash"},
            "non-dir directory",
        ),
        ({"name": "ok2", "directory": "/tmp", "command": ""}, "empty command"),
        (
            {"name": "../etc", "directory": "/tmp", "command": "bash"},
            "name with traversal",
        ),
    ]
    for body, label in cases:
        try:
            req = urllib.request.Request(
                BASE + "/api/agents",
                data=json.dumps(body).encode(),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                print(f"  {label:25s} -> {r.status}")
        except urllib.error.HTTPError as e:
            print(f"  {label:25s} -> {e.code}")

    print("\n\nFINAL ISSUES:", issues if issues else "(none)")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())

