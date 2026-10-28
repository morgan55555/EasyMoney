"""AGENTS.md §2.3 (and the §2.4 pairwise view): property tests for the
greedy settlement optimizer. Pure functions — no database involved.

For every zero-sum balance map:
  * applying the suggested transfers leaves every balance at exactly 0;
  * at most n − 1 transfers (n = number of non-zero users);
  * the total moved equals the total debt;
  * every transfer stays within the current pairwise view.
"""

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.balances import Transfer, optimize_settlements, pairwise_debts


def _apply(balances: dict[int, Decimal], transfers: list[Transfer]) -> dict[int, Decimal]:
    """Balance map after the transfers really moved the money."""
    out = {k: v for k, v in balances.items()}
    for t in transfers:
        out[t.from_user_id] = out.get(t.from_user_id, Decimal(0)) + t.amount
        out[t.to_user_id] = out.get(t.to_user_id, Decimal(0)) - t.amount
    return out


@st.composite
def zero_sum_balances(draw):
    """Random zero-sum balance maps, in cents rendered as 2-decimal money."""
    n = draw(st.integers(min_value=2, max_value=8))
    cents = [draw(st.integers(min_value=-100_000, max_value=100_000)) for _ in range(n - 1)]
    cents.append(-sum(cents))
    return {uid: Decimal(c) / 100 for uid, c in enumerate(cents, start=1)}


@given(balances=zero_sum_balances())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_settles_all_debts(balances: dict[int, Decimal]) -> None:
    assert _apply(balances, optimize_settlements(balances)) == {k: Decimal(0) for k in balances}


@given(balances=zero_sum_balances())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_at_most_n_minus_1_transfers(balances: dict[int, Decimal]) -> None:
    non_zero = sum(1 for v in balances.values() if v != 0)
    assert len(optimize_settlements(balances)) <= max(non_zero - 1, 0)


@given(balances=zero_sum_balances())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_total_moved_equals_total_debt(balances: dict[int, Decimal]) -> None:
    transfers = optimize_settlements(balances)
    total_debt = sum((v for v in balances.values() if v > 0), Decimal(0))
    assert sum((t.amount for t in transfers), Decimal(0)) == total_debt


@given(balances=zero_sum_balances())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_every_transfer_within_pairwise_view(balances: dict[int, Decimal]) -> None:
    pairs = {(t.from_user_id, t.to_user_id): t.amount for t in pairwise_debts(balances)}
    for t in optimize_settlements(balances):
        assert Decimal(0) < t.amount <= pairs[(t.from_user_id, t.to_user_id)]


@given(balances=zero_sum_balances())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_no_self_transfers_and_positive_amounts(balances: dict[int, Decimal]) -> None:
    for t in optimize_settlements(balances):
        assert t.from_user_id != t.to_user_id
        assert t.amount > 0


def test_spec_example_and_tie_breaks() -> None:
    # debtor 1 owes 100; creditors 2 (60) then 3 (40), matched head-to-head
    assert optimize_settlements({1: Decimal("-100"), 2: Decimal("60"), 3: Decimal("40")}) == [
        Transfer(1, 2, Decimal("60")),
        Transfer(1, 3, Decimal("40")),
    ]
    # equal debts: tie-break by user_id asc (2 before 3)
    assert optimize_settlements({3: Decimal("-50"), 2: Decimal("-50"), 1: Decimal("100")}) == [
        Transfer(2, 1, Decimal("50")),
        Transfer(3, 1, Decimal("50")),
    ]
    # nothing owed -> nothing to do
    assert optimize_settlements({1: Decimal(0), 2: Decimal(0)}) == []


def test_pairwise_view() -> None:
    # user 1 owes 10 in total; creditors 2 (7) and 3 (3). The pairwise view
    # charges 1 → 2 the min(10, 7) and 1 → 3 the min(10, 3), amount desc.
    pairs = pairwise_debts({1: Decimal("-10"), 2: Decimal("7"), 3: Decimal("3")})
    assert pairs == [
        Transfer(1, 2, Decimal("7")),
        Transfer(1, 3, Decimal("3")),
    ]
    # a settled group has an empty pairwise view
    assert pairwise_debts({1: Decimal(0), 2: Decimal(0)}) == []
