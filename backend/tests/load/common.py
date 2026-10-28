"""Shared helpers for the section-6 scale/load scripts.

Run with the backend venv python against the live compose stack
(API :8000, WS :8000/ws). Evidence is written as JSON next to the run log,
under ``EASYMONEY_EVIDENCE`` (default: the opencode temp evidence dir).

    .venv/Scripts/python backend/tests/load/c1_c7.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import httpx

API = "http://localhost:8000/api"
WS = "ws://localhost:8000/ws"
REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(
    os.environ.get("EASYMONEY_EVIDENCE", r"C:\Users\alxmr\AppData\Local\Temp\opencode\easymoney-test\evidence")
)

try:  # websockets >= 13 has the asyncio client under websockets.asyncio
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - older websockets
    from websockets import connect as ws_connect  # type: ignore[assignment]


@dataclass
class Case:
    case: str
    expected: str
    observed: str
    verdict: str  # PASS / FAIL / NOTE
    evidence: str = ""


class Run:
    """Collects per-case results and writes the machine-readable evidence file."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.cases: list[Case] = []
        self.meta: dict = {}
        self.started = time.time()

    def add(
        self, case: str, expected: str, observed: str, verdict: str, evidence: str = ""
    ) -> None:
        self.cases.append(Case(case, expected, observed, verdict, evidence))
        print(f"{case:8s} | {verdict:5s} | exp={expected[:80]} | obs={observed[:140]}")

    def finish(self) -> int:
        fails = [c for c in self.cases if c.verdict == "FAIL"]
        notes = [c for c in self.cases if c.verdict == "NOTE"]
        dur = time.time() - self.started
        print(
            f"\n=== {self.name}: {len(self.cases)} cases, {len(fails)} FAIL, "
            f"{len(notes)} NOTE, {dur:.1f}s ==="
        )
        for c in fails:
            print(f"  FAIL {c.case}: {c.observed} | {c.evidence[:160]}")
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        payload = {
            "name": self.name,
            "duration_s": round(dur, 1),
            "meta": self.meta,
            "cases": [c.__dict__ for c in self.cases],
        }
        out = EVIDENCE / f"load_{self.name}.json"
        out.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        print(f"evidence -> {out}")
        return len(fails)


def pct(values: list[float]) -> dict:
    """Latency percentiles in ms (values are seconds)."""
    if not values:
        return {"n": 0}
    vs = sorted(values)

    def q(p: float) -> float:
        idx = min(len(vs) - 1, int(round(p / 100 * (len(vs) - 1))))
        return round(vs[idx] * 1000, 1)

    return {"n": len(vs), "p50": q(50), "p95": q(95), "p99": q(99), "max": q(100), "unit": "ms"}


class Api:
    """Async client with per-user token helpers."""

    def __init__(self, timeout: float = 60.0, pool: int = 256) -> None:
        self.client = httpx.AsyncClient(
            timeout=timeout, limits=httpx.Limits(max_connections=pool, max_keepalive_connections=pool)
        )
        # Transport-level retries recovered from the keep-alive race where the
        # server closes an idle connection the client is about to reuse. This is
        # a client/transport concern, NOT an application error: HTTP error
        # statuses (4xx/5xx) are returned normally and never retried here.
        self.transport_retries = 0

    async def __aenter__(self) -> "Api":
        return self

    async def __aexit__(self, *a: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self.client.aclose()

    async def req(
        self, method: str, path: str, token: str | None = None, retries: int = 3, **kw: object
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                return await self.client.request(method, API + path, headers=headers, **kw)
            except httpx.TransportError as exc:
                last = exc
                self.transport_retries += 1
                await asyncio.sleep(0.02)
        raise last  # type: ignore[misc]

    async def ensure_user(self, email: str, password: str, display_name: str | None = None) -> dict:
        """Register, falling back to login when the account already exists."""
        body: dict = {"email": email, "password": password}
        if display_name:
            body["display_name"] = display_name
        r = await self.client.post(API + "/auth/register", json=body)
        if r.status_code != 201:
            r = await self.client.post(API + "/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        return r.json()

    async def login(self, email: str, password: str) -> dict:
        r = await self.client.post(API + "/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        return r.json()

    async def user_id(self, token: str) -> int:
        r = await self.req("GET", "/me", token)
        r.raise_for_status()
        return int(r.json()["id"])


def psql(sql: str) -> str:
    """Run a psql query in the compose db; return the trimmed stdout."""
    proc = subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "easymoney", "-d", "easymoney", "-t", "-A", "-c", sql],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def recompute_balances(expenses: list[dict]) -> dict[int, Decimal]:
    """Independent §2.2 recomputation from a list of expense dicts (no settlements).

    ``expenses`` may be the API ``list[ExpenseOut]`` form: each has ``payer_id``,
    and ``participants`` of ``{user_id, share (string)}``.
    """
    out: dict[int, Decimal] = {}

    def bump(uid: int, delta: Decimal) -> None:
        out[uid] = out.get(uid, Decimal(0)) + delta

    for e in expenses:
        bump(int(e["payer_id"]), Decimal(e["amount"]))
        for p in e["participants"]:
            bump(int(p["user_id"]), -Decimal(p["share"]))
    return out


def dec_sum_zero(balances: list[dict]) -> bool:
    """True when the sum of ``balances`` (list of {balance: str}) is exactly 0."""
    total = sum((Decimal(b["balance"]) for b in balances), Decimal(0))
    return total == 0
