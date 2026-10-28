"""Section 6.1 - a group with 100+ members (C1-C7, C9; C8 is the UI walk in section 9).

Against the live compose stack. Users carry the dedicated ``biglab-`` email
prefix so a re-run logs in instead of re-registering. The group is recreated
fresh each run (named with a short run stamp) so membership math stays exact.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from decimal import Decimal

from common import Api, Run, dec_sum_zero, pct, psql, ws_connect

N = 105  # joiners (plus the creator = 106 members)
PW = "biglabpass1"
WS_URL = "ws://localhost:8000/ws"


async def main() -> int:
    run = Run("c1_c7")
    stamp = uuid.uuid4().hex[:6]

    async with Api() as api:
        # ---------- C1: register 105 users, one link invite, sequential accepts ----------
        t0 = time.time()
        creator = await api.ensure_user("biglab-creator@test.local", PW, "BigLab Creator")
        ctok = creator["access_token"]
        creator_id = await api.user_id(ctok)

        reg_tasks = [
            api.ensure_user(f"biglab-{i:03d}@test.local", PW, f"BL {i:03d}") for i in range(1, N + 1)
        ]
        sem = asyncio.Semaphore(10)

        async def _reg(t: asyncio.Task) -> dict:
            async with sem:
                return await t

        tokens = await asyncio.gather(*[_reg(t) for t in reg_tasks])
        reg_s = time.time() - t0

        group = (await api.req("POST", "/groups", ctok, json={"name": f"Big Lab {stamp} USD", "currency": "USD"})).json()
        gid = group["id"]
        run.meta["group_id"] = gid

        inv = (await api.req("POST", f"/groups/{gid}/invites", ctok, json={"kind": "link"})).json()
        token = inv["token"]

        statuses: list[int] = []
        t0 = time.time()
        for tok in tokens:  # sequential, per plan
            r = await api.req("POST", "/invites/accept", tok["access_token"], json={"token": token})
            statuses.append(r.status_code)
        accept_s = time.time() - t0
        ok200 = sum(1 for s in statuses if s == 200)
        members = (await api.req("GET", f"/groups/{gid}/members", ctok)).json()
        c1_pass = ok200 == N and not any(s >= 500 for s in statuses) and len(members) == N + 1
        run.add(
            "C1",
            f"105 registered, one link invite, {N} sequential accepts -> all join, memberships correct, no 5xx",
            f"register {N} in {reg_s:.1f}s; accepts {ok200}/{N} 200 in {accept_s:.1f}s; members={len(members)} (expect {N + 1})",
            "PASS" if c1_pass else "FAIL",
            f"status mix={ {s: statuses.count(s) for s in set(statuses)} }",
        )

        # ---------- C2: 50 concurrent accepts on one link token ----------
        g2 = (await api.req("POST", "/groups", ctok, json={"name": f"Big Lab C2 {stamp} USD", "currency": "USD"})).json()
        gid2 = g2["id"]
        run.meta["group2_id"] = gid2
        inv2 = (await api.req("POST", f"/groups/{gid2}/invites", ctok, json={"kind": "link"})).json()
        tok2 = inv2["token"]

        async def _accept(tok: str) -> int:
            r = await api.req("POST", "/invites/accept", tok, json={"token": tok2})
            return r.status_code

        c2_status = await asyncio.gather(*[_accept(t["access_token"]) for t in tokens[:50]])
        c2_ok = sum(1 for s in c2_status if s == 200)
        members2 = (await api.req("GET", f"/groups/{gid2}/members", ctok)).json()
        # duplicate-membership check: one of them accepts again -> 409, not a second row
        dup = await api.req("POST", "/invites/accept", tokens[0]["access_token"], json={"token": tok2})
        c2_pass = c2_ok == 50 and not any(s >= 500 for s in c2_status) and len(members2) == 51 and dup.status_code == 409
        run.add(
            "C2",
            "50 concurrent accepts, same link token -> all 200, no duplicate memberships, no 5xx",
            f"200s={c2_ok}/50; members={len(members2)} (expect 51); re-accept -> {dup.status_code}",
            "PASS" if c2_pass else "FAIL",
            f"status mix={ {s: c2_status.count(s) for s in set(c2_status)} }",
        )

        # ---------- C3: equal split across all 105 joiners ----------
        # payer must be a participant: BL 001 pays; all 105 joiners participate.
        uid = {i: await api.user_id(t["access_token"]) for i, t in enumerate(tokens, start=1)}
        payer_id = uid[1]
        amount = "100.00"
        t0 = time.time()
        r = await api.req(
            "POST", f"/groups/{gid}/expenses", ctok,
            json={
                "payer_id": payer_id, "description": "C3 big split", "amount": amount,
                "split_method": "equal", "category": "Food", "date": "2026-10-27", "comment": "",
                "participants": [{"user_id": uid[i]} for i in range(1, N + 1)],
            },
        )
        c3_s = time.time() - t0
        exp = r.json()
        shares = [Decimal(p["share"]) for p in exp["participants"]]
        total = sum(shares, Decimal(0))
        by_uid = {int(p["user_id"]): Decimal(p["share"]) for p in exp["participants"]}
        base = Decimal("0.95")  # floor(100.00/105) in minor units = 95
        big = [u for u, s in by_uid.items() if s == base + Decimal("0.01")]
        small = [u for u, s in by_uid.items() if s == base]
        # the r earliest-joined participants get the extra unit
        # (members includes the creator; restrict to participants, keep join order)
        members_by_join = sorted(members, key=lambda m: (m["joined_at"], m["id"]))
        participants_ordered = [m["id"] for m in members_by_join if m["id"] in by_uid]
        expected_big = set(participants_ordered[: len(big)])
        c3_pass = (
            r.status_code == 201
            and total == Decimal(amount)
            and len(big) == 25
            and len(small) == N - 25
            and set(big) == expected_big
        )
        run.add(
            "C3",
            f"equal split 100.00 among {N}: sum == A; 25 x 0.96 to the earliest joiners, rest 0.95",
            f"status={r.status_code} sum={total} (expect {amount}); 0.96 x{len(big)}, 0.95 x{len(small)}; "
            f"earliest-joiners match={set(big) == expected_big}; {c3_s * 1000:.0f}ms",
            "PASS" if c3_pass else "FAIL",
            f"expense id={exp['id']}",
        )

        # ---------- C4: balances endpoint at 106 members ----------
        lats: list[float] = []
        bal: list[dict] = []
        for _ in range(20):
            t0 = time.time()
            b = (await api.req("GET", f"/groups/{gid}/balances", ctok)).json()
            lats.append(time.time() - t0)
            bal = b["balances"]
        zero = dec_sum_zero(bal)
        c4_pass = zero and len(bal) == N + 1 and pct(lats)["p95"] < 1000
        run.add(
            "C4",
            f"GET /balances with {N + 1} members: sum == 0, p95 < 1s",
            f"rows={len(bal)} sum==0: {zero}; lat {json.dumps(pct(lats))}",
            "PASS" if c4_pass else "FAIL",
        )

        # ---------- C5: optimizer with ~50 debtors / ~50 creditors ----------
        # 50 joiners (BL 002..051) each pay a 2.00 self-expense -> they flip to creditors.
        async def _self(i: int) -> int:
            r = await api.req(
                "POST", f"/groups/{gid}/expenses", tokens[i - 1]["access_token"],
                json={
                    "payer_id": uid[i], "description": f"C5 self {i}", "amount": "2.00",
                    "split_method": "exact", "category": "Food", "date": "2026-10-27", "comment": "",
                    "participants": [{"user_id": uid[i], "amount": "2.00"}],
                },
            )
            return r.status_code

        c5_status = await asyncio.gather(*[_self(i) for i in range(2, 52)])
        snap = (await api.req("GET", f"/groups/{gid}/balances", ctok)).json()
        balances = {int(b["user_id"]): Decimal(b["balance"]) for b in snap["balances"]}
        nonzero = {u: b for u, b in balances.items() if b != 0}
        debt_total = sum((-b for b in nonzero.values() if b < 0), Decimal(0))
        transfers = snap["transfers"]
        pairs = {(t["from_user_id"], t["to_user_id"]): Decimal(t["amount"]) for t in snap["pairs"]}
        t_sum = sum((Decimal(t["amount"]) for t in transfers), Decimal(0))
        n_nonzero = len(nonzero)
        max_ok = all(
            Decimal(t["amount"]) <= pairs.get((t["from_user_id"], t["to_user_id"]), Decimal(0)) for t in transfers
        )
        c5_pass = (
            sum(1 for s in c5_status if s == 201) == 50
            and 50 <= n_nonzero <= N
            and len(transfers) <= n_nonzero - 1
            and t_sum == debt_total
            and max_ok
        )
        run.add(
            "C5",
            "optimizer: <= n-1 transfers, total == total debt, each transfer within the pairwise debt",
            f"nonzero={n_nonzero} (debtors={sum(1 for b in nonzero.values() if b < 0)}, "
            f"creditors={sum(1 for b in nonzero.values() if b > 0)}); transfers={len(transfers)} "
            f"(limit {n_nonzero - 1}); sum {t_sum} == debt {debt_total}: {t_sum == debt_total}; within pairs: {max_ok}",
            "PASS" if c5_pass else "FAIL",
        )

        # ---------- C6: WS fan-out to 105 sockets ----------
        sockets = []
        t0 = time.time()

        async def _open(i: int):
            ws = await ws_connect(WS_URL + f"?token={tokens[i - 1]['access_token']}", max_size=None, open_timeout=15)
            return ws

        sockets = await asyncio.gather(*[_open(i) for i in range(1, N + 1)])
        open_s = time.time() - t0

        creator_exp = await api.req(
            "POST", f"/groups/{gid}/expenses", ctok,
            json={
                "payer_id": creator_id, "description": "C6 fanout", "amount": "5.00",
                "split_method": "equal", "category": "Food", "date": "2026-10-27", "comment": "",
                "participants": [{"user_id": creator_id}],
            },
        )
        received = [0] * N
        snapshot_ok = 0

        async def _reader(idx: int, ws) -> None:
            nonlocal snapshot_ok
            try:
                deadline = asyncio.get_event_loop().time() + 10
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - asyncio.get_event_loop().time())
                    except asyncio.TimeoutError:
                        break
                    except Exception:
                        break
                    try:
                        msg = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    if msg.get("group_id") == gid and msg.get("type") == "expense.created":
                        if msg.get("data", {}).get("group_balances"):
                            snapshot_ok += 1
                        received[idx] += 1
                        break
            finally:
                try:
                    await ws.close()
                except Exception:
                    pass

        readers = [asyncio.create_task(_reader(i, s)) for i, s in enumerate(sockets)]
        await asyncio.gather(*readers)
        got = sum(1 for c in received if c >= 1)
        c6_pass = got == N and snapshot_ok >= N and creator_exp.status_code == 201
        run.add(
            "C6",
            f"{N} sockets open -> one expense.created reaches all {N} with a balance snapshot",
            f"opened {N} sockets in {open_s:.1f}s; received {got}/{N}; snapshots {snapshot_ok}/{N}",
            "PASS" if c6_pass else "FAIL",
            f"min recv={min(received)} max={max(received)}",
        )
        for s in sockets:
            try:
                await s.close()
            except Exception:
                pass

        # ---------- C7: one expense involving 105 participants -> 105 notifications ----------
        eid = exp["id"]
        n_rows = psql(
            f"SELECT count(*) FROM notifications WHERE type='expense.created' "
            f"AND (payload->>'id')::bigint = {eid}"
        )
        sample_ok = 0
        for i in (1, 53, 105):
            nl = (await api.req("GET", "/notifications", tokens[i - 1]["access_token"])).json()
            hit = any(
                n["type"] == "expense.created" and (n.get("payload") or {}).get("id") == eid
                for n in nl["items"]
            )
            sample_ok += 1 if (hit and (nl.get("unread_count") or 0) >= 1) else 0
        c7_pass = int(n_rows) == N and sample_ok == 3
        run.add(
            "C7",
            f"expense with {N} participants -> {N} notification rows; per-user unread counts",
            f"db rows={n_rows} (expect {N}); sample unread+present {sample_ok}/3",
            "PASS" if c7_pass else "FAIL",
        )

        # ---------- C9: feed with 150+ events, cursor pagination over all pages ----------
        from datetime import datetime as _dt

        total_feed = int(psql(f"SELECT count(*) FROM activity WHERE group_id = {gid}"))
        seen: list[tuple] = []
        cursor: str | None = None
        pages = 0
        monotonic = True
        dup = False
        while True:
            q = f"/groups/{gid}/activity?limit=30" + (f"&cursor={cursor}" if cursor else "")
            page = (await api.req("GET", q, ctok)).json()
            pages += 1
            for it in page["items"]:
                when = _dt.fromisoformat(it["created_at"].replace("Z", "+00:00"))
                key = (when, it["id"])
                if seen and key >= seen[-1]:
                    monotonic = False
                if any(key == s for s in seen[-100:]):
                    dup = True
                seen.append(key)
            cursor = page.get("next_cursor")
            if cursor is None:
                break
            if pages > 200:
                break
        deep_page_21 = pages >= 21
        c9_pass = monotonic and not dup and len(seen) == total_feed
        run.add(
            "C9",
            "feed 150+ events: cursor pagination correct at every page (strict desc, no dups, full coverage)",
            f"events={total_feed}; pages={pages}; walked={len(seen)}; strict_desc={monotonic}; dups={dup}; page21={deep_page_21}",
            "PASS" if c9_pass else "FAIL",
        )

    return run.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
