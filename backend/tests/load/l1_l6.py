"""Section 6.4 - concurrency + sustained load (L-1..L-6).

L-1  50 parallel expense creates -> all 201, sum-zero holds.
L-2  20 parallel confirms of one settlement -> exactly 1x200 + 19x409.
L-3  105 sockets in one group; 20 rapid writes -> every socket gets all 20.
L-4  ~100 rps for 2 min (24 workers, token bucket, 80/20 read/write) -> 0 5xx.
L-5  50 parallel logins -> p95.
L-6  SQL invariant re-check across every load group.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from decimal import Decimal
from collections import Counter

from common import Api, Run, pct, psql, ws_connect

WS_URL = "ws://localhost:8000/ws"
ALICE = ("alice@test.local", "alicepass1")
BOB = ("bob@test.local", "bobpass123")
CAROL = ("carol@test.local", "carolpass1")
READER = ("loadread@test.local", "loadreadpass1")
CREATOR = ("biglab-creator@test.local", "biglabpass1")
BIGPW = "biglabpass1"


class _Bucket:
    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
            await asyncio.sleep(0.004)


async def main() -> int:
    run = Run("l1_l6")

    async with Api() as api:
        alice = await api.ensure_user(*ALICE)
        atok = alice["access_token"]
        aid = await api.user_id(atok)
        bob = await api.ensure_user(*BOB)
        btok = bob["access_token"]
        bid = await api.user_id(btok)
        carol = await api.ensure_user(*CAROL)
        ctok = carol["access_token"]
        cid = await api.user_id(ctok)
        readers = await api.ensure_user(*READER)
        rtok = readers["access_token"]
        rid = await api.user_id(rtok)

        # ============ L-1: 50 parallel expense creates ============
        lab = (await api.req("POST", "/groups", atok, json={"name": "Load Lab USD", "currency": "USD"})).json()
        lgid = lab["id"]
        run.meta["load_lab_id"] = lgid
        inv = (await api.req("POST", f"/groups/{lgid}/invites", atok, json={"kind": "link"})).json()
        itok = inv["token"]
        for acc, uid in ((bob, bid), (carol, cid), (readers, rid)):
            await api.req("POST", "/invites/accept", acc["access_token"], json={"token": itok})
        lm = [aid, bid, cid]  # writers (reader is read-only for L-1)
        toks = [atok, btok, ctok]

        async def _mk1(i: int) -> int:
            pi = i % 3
            oi = (pi + 1) % 3
            r = await api.req(
                "POST", f"/groups/{lgid}/expenses", toks[pi],
                json={
                    "payer_id": lm[pi], "description": f"L1 {i}", "amount": "1.00",
                    "split_method": "equal", "category": "Load", "date": "2026-10-27", "comment": "",
                    "participants": [{"user_id": lm[pi]}, {"user_id": lm[oi]}],
                },
            )
            return r.status_code

        st1 = await asyncio.gather(*[_mk1(i) for i in range(50)])
        bal1 = (await api.req("GET", f"/groups/{lgid}/balances", atok)).json()
        s1 = sum((Decimal(b["balance"]) for b in bal1["balances"]), Decimal(0))
        l1_pass = all(s == 201 for s in st1) and s1 == 0
        run.add(
            "L-1", "50 parallel expense creates -> all 201, sum-zero holds",
            f"201s={sum(1 for s in st1 if s == 201)}/50; sum(balances)={s1}",
            "PASS" if l1_pass else "FAIL",
        )

        # ============ L-2: 20 parallel confirms of one settlement ============
        sg = (await api.req("POST", "/groups", atok, json={"name": "Load Settle USD", "currency": "USD"})).json()
        sgid = sg["id"]
        sinv = (await api.req("POST", f"/groups/{sgid}/invites", atok, json={"kind": "link"})).json()
        stok = sinv["token"]
        await api.req("POST", "/invites/accept", btok, json={"token": stok})
        # alice pays 10.00 for [alice, bob] -> bob owes alice 5.00
        await api.req(
            "POST", f"/groups/{sgid}/expenses", atok,
            json={
                "payer_id": aid, "description": "L2 seed", "amount": "10.00",
                "split_method": "equal", "category": "Load", "date": "2026-10-27", "comment": "",
                "participants": [{"user_id": aid}, {"user_id": bid}],
            },
        )
        settle = (await api.req(
            "POST", f"/groups/{sgid}/settlements", btok, json={"to_user_id": aid, "amount": "5.00"}
        )).json()
        sid = settle["id"]

        async def _confirm() -> int:
            r = await api.req("POST", f"/settlements/{sid}/confirm", atok)
            return r.status_code

        st2 = await asyncio.gather(*[_confirm() for _ in range(20)])
        final = (await api.req("GET", f"/groups/{sgid}/balances", atok)).json()
        zeros = all(Decimal(b["balance"]) == 0 for b in final["balances"])
        n200 = st2.count(200)
        n409 = st2.count(409)
        l2_pass = n200 == 1 and n409 == 19 and zeros
        run.add(
            "L-2", "20 parallel confirms of one settlement -> exactly 1x200 + 19x409, balances zero",
            f"200s={n200}, 409s={n409} (mix={dict(Counter(st2))}); balances all zero: {zeros}",
            "PASS" if l2_pass else "FAIL",
        )

        # ============ L-3: 105 sockets, 20 rapid writes, full fan-out ============
        creator = await api.ensure_user(*CREATOR)
        ctok2 = creator["access_token"]
        wid = await api.user_id(ctok2)
        wsg = (await api.req("POST", "/groups", ctok2, json={"name": "Load WS USD", "currency": "USD"})).json()
        wgid = wsg["id"]
        run.meta["load_ws_id"] = wgid
        winv = (await api.req("POST", f"/groups/{wgid}/invites", ctok2, json={"kind": "link"})).json()
        wtok = winv["token"]

        big_tokens: list[str] = []
        big_sems = asyncio.Semaphore(20)

        async def _login_big(i: int) -> str:
            r = await api.req("POST", "/auth/login", json={"email": f"biglab-{i:03d}@test.local", "password": BIGPW})
            if r.status_code != 200:
                return ""
            return r.json()["access_token"]

        async def _login_big_s(i: int) -> str:
            async with big_sems:
                return await _login_big(i)

        big_tokens = await asyncio.gather(*[_login_big_s(i) for i in range(1, 106)])
        big_ok = sum(1 for t in big_tokens if t)

        async def _accept_big(tok: str) -> int:
            r = await api.req("POST", "/invites/accept", tok, json={"token": wtok})
            return r.status_code

        accept_st = await asyncio.gather(*[_accept_big(t) for t in big_tokens if t])
        joined = sum(1 for s in accept_st if s == 200)

        sockets = []
        open_sems = asyncio.Semaphore(25)

        async def _open(tok: str):
            async with open_sems:
                return await ws_connect(WS_URL + f"?token={tok}", max_size=None, open_timeout=20)

        sockets = await asyncio.gather(*[_open(t) for t in big_tokens if t])

        # start draining each socket, counting expense.created in the group
        counts = [0] * len(sockets)

        async def _drain(idx: int, ws) -> None:
            deadline = asyncio.get_event_loop().time() + 12
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=deadline - asyncio.get_event_loop().time())
                except (asyncio.TimeoutError, Exception):
                    return
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                if msg.get("type") == "expense.created" and msg.get("group_id") == wgid:
                    counts[idx] += 1

        drains = [asyncio.create_task(_drain(i, s)) for i, s in enumerate(sockets)]
        await asyncio.sleep(1.0)

        # 20 rapid single-participant expenses (creator self-paid)
        for i in range(20):
            await api.req(
                "POST", f"/groups/{wgid}/expenses", ctok2,
                json={
                    "payer_id": wid, "description": f"L3 {i}", "amount": "0.50",
                    "split_method": "equal", "category": "Load", "date": "2026-10-27", "comment": "",
                    "participants": [{"user_id": wid}],
                },
            )
        await asyncio.gather(*drains)

        full = sum(1 for c in counts if c >= 20)
        l3_pass = big_ok == 105 and joined == 105 and full == len(sockets)
        run.add(
            "L-3", "105 sockets in one group; 20 rapid writes -> every socket gets all 20",
            f"logins {big_ok}/105; joined {joined}/105; sockets {len(sockets)}; got-all-20: {full}/{len(sockets)}",
            "PASS" if l3_pass else "FAIL",
            f"min={min(counts)} max={max(counts)}" if counts else "",
        )
        for s in sockets:
            try:
                await s.close()
            except Exception:
                pass

        # ============ L-4: ~100 rps for 2 min ============
        read_paths = ["/groups", "/me", f"/groups/{lgid}/balances"]
        write_toks = [atok, btok, ctok]
        write_uids = [aid, bid, cid]
        bucket = _Bucket(rate=100.0, capacity=100.0)
        stop = asyncio.Event()
        lat: list[float] = []
        stat: Counter = Counter()
        five_xx = 0
        t_start = time.monotonic()

        async def _stopper() -> None:
            await asyncio.sleep(120)
            stop.set()

        async def _worker(idx: int) -> None:
            rng = random.Random(idx)
            w = idx % 3
            while not stop.is_set():
                await bucket.acquire()
                t0 = time.monotonic()
                try:
                    if rng.random() < 0.8:
                        r = await api.req("GET", read_paths[rng.randrange(len(read_paths))], rtok)
                    else:
                        r = await api.req(
                            "POST", f"/groups/{lgid}/expenses", write_toks[w],
                            json={
                                "payer_id": write_uids[w], "description": "L4", "amount": "1.00",
                                "split_method": "equal", "category": "Load", "date": "2026-10-27", "comment": "",
                                "participants": [{"user_id": write_uids[w]}],
                            },
                        )
                    stat[r.status_code] += 1
                    if r.status_code >= 500:
                        nonlocal five_xx
                        five_xx += 1
                    lat.append(time.monotonic() - t0)
                except Exception:
                    stat[0] += 1  # transport error

        await asyncio.gather(asyncio.create_task(_stopper()), *[_worker(i) for i in range(24)])
        elapsed = time.monotonic() - t_start
        rps = len(lat) / elapsed if elapsed else 0
        l4_pass = five_xx == 0
        run.add(
            "L-4", "~100 rps x 120s, 24 workers, 80/20 read/write -> 0 5xx",
            f"reqs={len(lat)} over {elapsed:.0f}s ({rps:.0f} rps); 5xx={five_xx}; "
            f"lat {json.dumps(pct(lat))}; status={dict(stat)}",
            "PASS" if l4_pass else "FAIL",
        )

        # ============ L-5: 50 parallel logins ============
        lats5: list[float] = []
        st5: list[int] = []
        alice_body = {"email": ALICE[0], "password": ALICE[1]}

        async def _login_timed() -> None:
            t0 = time.time()
            r = await api.req("POST", "/auth/login", json=alice_body)
            lats5.append(time.time() - t0)
            st5.append(r.status_code)

        await asyncio.gather(*[_login_timed() for _ in range(50)])
        l5_pass = all(s == 200 for s in st5)
        run.add(
            "L-5", "50 parallel logins -> all 200, p95 recorded",
            f"200s={sum(1 for s in st5 if s == 200)}/50; lat {json.dumps(pct(lats5))}",
            "PASS" if l5_pass else "FAIL",
        )

        # ============ L-6: SQL invariant re-check across all load groups ============
        patterns = ("'Big Lab%'", "'MG %'", "'Big Ledger%'", "'Load Lab%'", "'Load Settle%'", "'Load WS%'")
        where = " OR ".join(f"g.name LIKE {p}" for p in patterns)
        sql_total = (
            f"SELECT g.id, g.name, "
            f"(SELECT COALESCE(SUM(e.amount),0) FROM expenses e WHERE e.group_id=g.id AND e.deleted_at IS NULL) "
            f"- (SELECT COALESCE(SUM(ep.share),0) FROM expense_participants ep JOIN expenses e ON e.id=ep.expense_id "
            f"   WHERE e.group_id=g.id AND e.deleted_at IS NULL) AS net "
            f"FROM groups g WHERE {where} ORDER BY g.id;"
        )
        out = psql(sql_total)
        bad = []
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            if len(parts) >= 3:
                net = Decimal(parts[-1])
                if net != 0:
                    bad.append((parts[0], parts[1], str(net)))
        # pending settlements must reference group members
        pend_bad = int(psql(
            f"SELECT count(*) FROM settlements s JOIN groups g ON g.id=s.group_id "
            f"WHERE ({where}) AND s.state='pending' AND ("
            f"s.from_user_id NOT IN (SELECT user_id FROM memberships m WHERE m.group_id=g.id) "
            f"OR s.to_user_id NOT IN (SELECT user_id FROM memberships m WHERE m.group_id=g.id))"
        ))
        ngroups = sum(1 for line in out.splitlines() if line.strip())
        l6_pass = not bad and pend_bad == 0
        run.add(
            "L-6", "SQL invariant: every load group nets to 0; pending settlements reference members",
            f"groups={ngroups}; nonzero={len(bad)} {bad[:5]}; pending-with-foreign-member={pend_bad}",
            "PASS" if l6_pass else "FAIL",
        )

    run.meta["transport_retries"] = api.transport_retries
    return run.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
