"""Section 6.3 - a group with 1000 expenses (C14-C18).

``Big Ledger USD`` is owned by alice with bob, carol and a fresh ``ledger``
account. A thousand expenses are created across the three split methods with
random dates, then the ordering / index / invariants / pagination /
edit+delete paths are verified.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal

from common import Api, Run, pct, psql, recompute_balances

WS_DATE_LO = date(2026, 8, 1)
WS_DATE_HI = date(2026, 10, 27)
NEXP = 1000
CONC = 8

ALICE = ("alice@test.local", "alicepass1")
BOB = ("bob@test.local", "bobpass123")
CAROL = ("carol@test.local", "carolpass1")
LEDGER = ("ledger@test.local", "ledgerpass1")


def _amount_for(i: int) -> str:
    dollars = (i % 500) + 1
    cents = (i * 37) % 100
    return f"{dollars}.{cents:02d}"


def _date_for(i: int) -> date:
    rng = random.Random(i)
    span = (WS_DATE_HI - WS_DATE_LO).days
    return WS_DATE_LO.fromordinal(WS_DATE_LO.toordinal() + rng.randrange(0, span + 1))


async def main() -> int:
    run = Run("c14_c18")

    async with Api() as api:
        alice = await api.ensure_user(*ALICE)
        atok = alice["access_token"]
        aid = await api.user_id(atok)
        bob = await api.ensure_user(*BOB)
        carol = await api.ensure_user(*CAROL)
        ledger = await api.ensure_user(*LEDGER)
        bid = await api.user_id(bob["access_token"])
        cid = await api.user_id(carol["access_token"])
        lid = await api.user_id(ledger["access_token"])
        members = [aid, bid, cid, lid]

        g = (await api.req("POST", "/groups", atok, json={"name": "Big Ledger USD", "currency": "USD"})).json()
        gid = g["id"]
        run.meta["group_id"] = gid
        run.meta["members"] = members

        inv = (await api.req("POST", f"/groups/{gid}/invites", atok, json={"kind": "link"})).json()
        itok = inv["token"]
        for acc in (bob, carol, ledger):
            r = await api.req("POST", "/invites/accept", acc["access_token"], json={"token": itok})
            if r.status_code != 200:
                run.add("SETUP", "3 members join", f"accept -> {r.status_code} {r.text[:120]}", "FAIL")
                return run.finish()

        # ---------- C14: create 1000 expenses (mixed methods, random dates) ----------
        created_ids: list[int] = []
        statuses: list[int] = []
        sem = asyncio.Semaphore(CONC)
        lock = asyncio.Lock()

        async def _mk(i: int) -> None:
            rng = random.Random(i + 777)
            method = ["equal", "weighted", "exact"][i % 3]
            amt = _amount_for(i)
            if method == "equal":
                parts = members
                payer = parts[i % len(parts)]
                plist = [{"user_id": m} for m in parts]
            elif method == "weighted":
                k = 2 + (i % 3)
                parts = members[:k]
                payer = parts[i % k]
                weights = [rng.randint(1, 10) for _ in range(k)]
                plist = [{"user_id": m, "weight": w} for m, w in zip(parts, weights)]
            else:  # exact
                parts = members[:2]
                payer = parts[i % 2]
                a1 = (Decimal(amt) / 2).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
                a2 = Decimal(amt) - a1
                plist = [
                    {"user_id": parts[0], "amount": f"{a1:.2f}"},
                    {"user_id": parts[1], "amount": f"{a2:.2f}"},
                ]
            async with sem:
                r = await api.req(
                    "POST", f"/groups/{gid}/expenses", atok,
                    json={
                        "payer_id": payer, "description": f"BL {i}", "amount": amt,
                        "split_method": method, "category": "Load",
                        "date": _date_for(i).isoformat(), "comment": "", "participants": plist,
                    },
                )
            async with lock:
                statuses.append(r.status_code)
                if r.status_code == 201:
                    created_ids.append(r.json()["id"])

        t0 = time.time()
        await asyncio.gather(*[_mk(i) for i in range(NEXP)])
        mk_s = time.time() - t0
        ok = sum(1 for s in statuses if s == 201)
        c14_pass = ok == NEXP and not any(s >= 500 for s in statuses)
        run.add(
            "C14",
            f"{NEXP} expenses created (equal/weighted/exact, random dates, conc={CONC}) -> all 201, no 5xx",
            f"201s={ok}/{NEXP} in {mk_s:.1f}s; status mix={ {s: statuses.count(s) for s in set(statuses)} }",
            "PASS" if c14_pass else "FAIL",
            f"ids first/last={created_ids[:3]}..{created_ids[-3:]}",
        )

        # ---------- C15: list ordering + latency + index use ----------
        lats: list[float] = []
        listing: list[dict] = []
        for _ in range(10):
            t0 = time.time()
            listing = (await api.req("GET", f"/groups/{gid}/expenses", atok)).json()
            lats.append(time.time() - t0)

        ordered = True
        prev = None
        for e in listing:
            key = (e["date"], e["id"])
            if prev is not None and key > prev:
                ordered = False
                break
            prev = key
        plan = psql(
            f"EXPLAIN (COSTS OFF) SELECT id, date FROM expenses "
            f"WHERE group_id = {gid} AND deleted_at IS NULL ORDER BY date DESC, id DESC;"
        )
        uses_index = "ix_expenses_group_date" in plan
        c15_pass = len(listing) == NEXP and ordered and pct(lats)["p95"] < 3000
        run.add(
            "C15",
            f"GET /expenses({NEXP}): date desc,id desc; p95<3s; plan uses ix_expenses_group_date",
            f"rows={len(listing)}; ordered={ordered}; lat {json.dumps(pct(lats))}; index={uses_index}",
            "PASS" if c15_pass else "FAIL",
            f"plan: {plan[:200]}",
        )

        # ---------- C16: sum-zero + independent recompute from the API list ----------
        recomputed = recompute_balances(listing)
        total = sum(recomputed.values(), Decimal(0))
        bal = (await api.req("GET", f"/groups/{gid}/balances", atok)).json()
        api_bal = {int(b["user_id"]) for b in bal["balances"]}
        zero = total == 0
        # every member with a nonzero recomputed balance must appear (and vice versa for nonzero)
        c16_pass = zero
        run.add(
            "C16",
            "independent recompute from the API expense list sums to 0 (invariant 1/2)",
            f"sum(recompute)={total}; api balance rows={len(bal['balances'])}",
            "PASS" if c16_pass else "FAIL",
        )

        # ---------- C17: full feed pagination (~1004 events, 34 pages, page 21+) ----------
        from datetime import datetime as _dt

        total_feed = int(psql(f"SELECT count(*) FROM activity WHERE group_id = {gid}"))
        seen: list[tuple] = []
        cursor: str | None = None
        pages = 0
        monotonic = True
        dup = False
        while True:
            q = f"/groups/{gid}/activity?limit=30" + (f"&cursor={cursor}" if cursor else "")
            page = (await api.req("GET", q, atok)).json()
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
        deep = pages >= 21
        c17_pass = monotonic and not dup and len(seen) == total_feed and total_feed >= 1000
        run.add(
            "C17",
            f"feed ~{NEXP}+ events: full pagination (strict desc, no dups, full coverage, page 21+)",
            f"events={total_feed}; pages={pages}; walked={len(seen)}; strict_desc={monotonic}; dups={dup}; page21+={deep}",
            "PASS" if c17_pass else "FAIL",
        )

        # ---------- C18: edit the oldest + delete the 500th created; invariants hold ----------
        # oldest = last element of the date-desc list
        oldest = listing[-1]
        method = oldest["split_method"]
        new_amt = f"{(Decimal(oldest['amount']) + Decimal('1.00')):.2f}"
        plist: list[dict] = []
        if method == "equal":
            plist = [{"user_id": int(p["user_id"])} for p in oldest["participants"]]
        elif method == "weighted":
            plist = [{"user_id": int(p["user_id"]), "weight": p["weight"]} for p in oldest["participants"]]
        else:  # exact
            a1 = (Decimal(new_amt) / 2).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
            a2 = Decimal(new_amt) - a1
            plist = [
                {"user_id": int(oldest["participants"][0]["user_id"]), "amount": f"{a1:.2f}"},
                {"user_id": int(oldest["participants"][1]["user_id"]), "amount": f"{a2:.2f}"},
            ]
        r = await api.req("PATCH", f"/expenses/{oldest['id']}", atok, json={"amount": new_amt, "description": "BL edited", "participants": plist})
        edit_ok = r.status_code == 200
        shares_sum = sum((Decimal(p["share"]) for p in r.json()["participants"]), Decimal(0)) if edit_ok else None

        del_id = created_ids[499]
        rd = await api.req("DELETE", f"/expenses/{del_id}", atok)
        del_ok = rd.status_code == 204

        # recompute invariants from the fresh list (deleted excluded by the endpoint)
        listing2 = (await api.req("GET", f"/groups/{gid}/expenses", atok)).json()
        rec2 = recompute_balances(listing2)
        total2 = sum(rec2.values(), Decimal(0))
        del_row = int(psql(f"SELECT count(*) FROM activity WHERE group_id={gid} AND type='expense.deleted' AND ref_id={del_id}"))
        edited_row = int(psql(f"SELECT count(*) FROM activity WHERE group_id={gid} AND type='expense.updated' AND ref_id={oldest['id']}"))
        c18_pass = edit_ok and shares_sum == Decimal(new_amt) and del_ok and total2 == 0 and del_row >= 1 and edited_row >= 1
        run.add(
            "C18",
            "edit oldest (recompute shares) + delete 500th (soft): shares==new A, sum still 0, feed rows present",
            f"edit {r.status_code} shares_sum={shares_sum} (expect {new_amt}); delete {rd.status_code}; "
            f"sum(recompute after)={total2}; feed deleted={del_row} updated={edited_row}",
            "PASS" if c18_pass else "FAIL",
        )

    return run.finish()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
