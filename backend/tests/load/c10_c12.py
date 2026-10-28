"""Section 6.2 - one user in 50 groups (C10-C12; C13 is the UI walk in section 9).

``manylab`` is the creator of all 50 groups so it is a member of each. The
groups span three currencies (25 USD / 15 EUR / 10 JPY) so the cross-group
balance view must return one zero row per currency, and JPY must be
serialized with 0 minor digits (AGENTS.md 2.12).
"""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal

from common import Api, Run, pct, ws_connect

WS_URL = "ws://localhost:8000/ws"
MANYY = "manylab@test.local"
MANYPW = "manylabpass1"
ALICE = "alice@test.local"
ALICEPW = "alicepass1"

# (currency, count) -> group name pattern
PLAN = [("USD", 25), ("EUR", 15), ("JPY", 10)]
CURS = ("USD", "EUR", "JPY")


async def _drain(ws, sink, duration: float = 12.0) -> None:
    """Read messages until timeout; append every expense.created to sink."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + duration
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - loop.time())
        except (asyncio.TimeoutError, Exception):
            return
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if msg.get("type") == "expense.created":
            sink.append(msg)


async def main() -> int:
    run = Run("c10_c12")

    async with Api() as api:
        man = await api.ensure_user(MANYY, MANYPW, "ManyLab")
        mtok = man["access_token"]
        mid = await api.user_id(mtok)

        # ---------- create the 50 groups ----------
        groups: list[dict] = []
        t0 = time.time()
        for cur, count in PLAN:
            for i in range(1, count + 1):
                g = (await api.req("POST", "/groups", mtok, json={"name": f"MG {cur} {i}", "currency": cur})).json()
                groups.append(g)
        create_s = time.time() - t0
        run.meta["group_ids"] = [g["id"] for g in groups]
        run.meta["creator_id"] = mid

        # ---------- C10: all memberships present ----------
        mine = (await api.req("GET", "/groups", mtok)).json()
        c10_pass = len(mine) == 50
        run.add(
            "C10",
            "one user in 50 groups -> GET /groups returns all 50 memberships",
            f"created 50 in {create_s:.1f}s; /groups rows={len(mine)} (expect 50)",
            "PASS" if c10_pass else "FAIL",
        )

        # ---------- C11: /groups + /balances/all latency & correctness ----------
        glats: list[float] = []
        blats: list[float] = []
        allrows: list[dict] = []
        for _ in range(10):
            t0 = time.time()
            (await api.req("GET", "/groups", mtok)).json()
            glats.append(time.time() - t0)
            t0 = time.time()
            allrows = (await api.req("GET", "/balances/all", mtok)).json()
            blats.append(time.time() - t0)

        by_cur = {r["currency"]: r["balance"] for r in allrows}
        c11_cur = set(by_cur) == {"USD", "EUR", "JPY"}
        c11_zero = all(Decimal(v) == 0 for v in by_cur.values())
        c11_jpy = by_cur.get("JPY") == "0"  # 0 minor digits -> "0" not "0.00"
        c11_pass = c11_cur and c11_zero and c11_jpy and pct(blats)["p95"] < 1000
        run.add(
            "C11",
            "GET /groups(50) & GET /balances/all: 3 per-currency zero rows, JPY='0', p95<1s",
            f"groups p95={pct(glats)['p95']}ms; balances p95={pct(blats)['p95']}ms; "
            f"rows={json.dumps(by_cur)}; 3cur={c11_cur} zero={c11_zero} jpy='0'={c11_jpy}",
            "PASS" if c11_pass else "FAIL",
        )

        # ---------- C12: WS handshake subscribes to all 50; event routes to that group ----------
        man_ws = await ws_connect(WS_URL + f"?token={mtok}", max_size=None, open_timeout=15)
        alice = await api.ensure_user(ALICE, ALICEPW)
        al_ws = await ws_connect(WS_URL + f"?token={alice['access_token']}", max_size=None, open_timeout=15)

        man_hits: list[dict] = []
        alice_hits: list[dict] = []
        man_task = asyncio.create_task(_drain(man_ws, man_hits, duration=14.0))
        alice_task = asyncio.create_task(_drain(al_ws, alice_hits, duration=14.0))
        await asyncio.sleep(1.0)  # let drains settle

        # fire one expense per currency group (self-paid by manylab)
        fired: dict[str, int] = {}
        for cur in CURS:
            g = next(x for x in groups if x["currency"] == cur)
            r = await api.req(
                "POST", f"/groups/{g['id']}/expenses", mtok,
                json={
                    "payer_id": mid, "description": f"C12 {cur}", "amount": "1.00",
                    "split_method": "equal", "category": "Food", "date": "2026-10-27", "comment": "",
                    "participants": [{"user_id": mid}],
                },
            )
            fired[cur] = r.status_code

        await man_task
        await alice_task

        got = {cur: sum(1 for m in man_hits if m.get("data", {}).get("group_balances", {}).get("currency") == cur) for cur in CURS}
        c12_pass = all(got[c] >= 1 for c in CURS) and len(alice_hits) == 0
        run.add(
            "C12",
            "manylab WS (subscribed to 50) gets 3 events (one per currency); alice WS gets none",
            f"fired={fired}; manylab got={got} (total {len(man_hits)}); alice got={len(alice_hits)}",
            "PASS" if c12_pass else "FAIL",
        )

        for ws in (man_ws, al_ws):
            try:
                await ws.close()
            except Exception:
                pass

    return run.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
