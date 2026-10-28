"""Property tests for the split engine (AGENTS.md §2.1, §5.1).

Run on the host venv (``pip install -e .[dev]``); no database required.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.errors import ValidationFailed
from app.services import splits

# Realistic ranges: NUMERIC(14,3) caps the amount at ~10^11 minor units.
amounts = st.integers(min_value=0, max_value=10**11)
people = st.integers(min_value=1, max_value=64)
# Weights are positive Decimals with up to 3 fractional digits (as the API
# will supply them), magnitude 0.001..1000.
weights = st.decimals(
    min_value=Decimal("0.001"),
    max_value=Decimal("1000"),
    places=3,
    allow_nan=False,
    allow_infinity=False,
)


class TestEqualSplit:
    @given(amounts, people)
    def test_sum_equals_amount(self, amount: int, n: int) -> None:
        """Invariant 2: no minor unit is lost or created."""
        assert sum(splits.split_equal(amount, n)) == amount

    @given(amounts, people)
    def test_spread_at_most_one_minor_unit(self, amount: int, n: int) -> None:
        """§5.1: equal-split shares differ by at most 1 minor unit."""
        shares = splits.split_equal(amount, n)
        assert max(shares) - min(shares) <= 1

    @given(amounts, people)
    def test_remainder_goes_to_earliest_joiners(self, amount: int, n: int) -> None:
        """§2.1: the first ``r`` positions (earliest-joined order) get +1,
        and only they; the order is deterministic."""
        base, r = divmod(amount, n)
        shares = splits.split_equal(amount, n)
        assert shares == [base + (1 if i < r else 0) for i in range(n)]

    @given(amounts)
    def test_single_participant_gets_everything(self, amount: int) -> None:
        assert splits.split_equal(amount, 1) == [amount]

    def test_spec_example_100_00_among_3(self) -> None:
        """AGENTS.md §2.1: 100.00 among 3 → 33.34, 33.33, 33.33 (join order)."""
        assert splits.split_equal(10_000, 3) == [3_334, 3_333, 3_333]

    def test_zero_amount(self) -> None:
        assert splits.split_equal(0, 5) == [0, 0, 0, 0, 0]

    def test_fewer_units_than_participants(self) -> None:
        assert splits.split_equal(2, 5) == [1, 1, 0, 0, 0]

    @pytest.mark.parametrize("n", [0, -1])
    def test_rejects_zero_participants(self, n: int) -> None:
        with pytest.raises(ValidationFailed):
            splits.split_equal(100, n)

    def test_rejects_negative_amount(self) -> None:
        with pytest.raises(ValidationFailed):
            splits.split_equal(-1, 2)

    def test_large_n_105_participants(self) -> None:
        """Plan C3: an equal split across 105 participants is exact — the
        remainder units go to the earliest-joined participants, one each."""
        amount = 10**9 + 7
        n = 105
        base, r = divmod(amount, n)
        shares = splits.split_equal(amount, n)
        assert len(shares) == n
        assert sum(shares) == amount
        assert shares == [base + (1 if i < r else 0) for i in range(n)]
        assert max(shares) - min(shares) <= 1
        assert r < n


class TestWeightedSplit:
    @given(amounts, st.lists(weights, min_size=1, max_size=64))
    def test_sum_equals_amount(self, amount: int, ws: list[Decimal]) -> None:
        """Invariant 2 holds for the largest-remainder method too."""
        assert sum(splits.split_weighted(amount, ws)) == amount

    @given(amounts, st.lists(weights, min_size=1, max_size=64))
    def test_each_share_is_floor_or_ceil_of_raw(self, amount: int, ws: list[Decimal]) -> None:
        """Every share is base_i or base_i + 1 for raw_i = A·w_i/Σw."""
        shares = splits.split_weighted(amount, ws)
        total = sum(ws)
        raw = [Decimal(amount) * w / total for w in ws]
        for share, r in zip(shares, raw, strict=True):
            base = int(r.to_integral_value(rounding=ROUND_FLOOR))
            assert share in (base, base + 1)

    @given(amounts, st.lists(weights, min_size=2, max_size=64))
    def test_extra_units_go_to_largest_remainders(self, amount: int, ws: list[Decimal]) -> None:
        """§2.1: every +1'd participant has a strictly larger remainder than
        every non-+1'd one, or an equal remainder with the earlier input
        position (the earliest-joined tiebreak)."""
        shares = splits.split_weighted(amount, ws)
        total = sum(ws)
        raw = [Decimal(amount) * w / total for w in ws]
        base = [int(r.to_integral_value(rounding=ROUND_FLOOR)) for r in raw]
        frac = [r - b for r, b in zip(raw, base, strict=True)]
        bumped = {i for i in range(len(shares)) if shares[i] == base[i] + 1}
        for i in bumped:
            for j in set(range(len(shares))) - bumped:
                assert frac[i] > frac[j] or (frac[i] == frac[j] and i < j)

    @given(amounts, st.integers(min_value=1, max_value=1000))
    def test_equal_weights_match_equal_split(self, amount: int, w: int) -> None:
        """Degenerate case: identical weights must reproduce the equal split."""
        n = 7
        assert splits.split_weighted(amount, [Decimal(w)] * n) == splits.split_equal(amount, n)

    def test_large_n_weighted_105(self) -> None:
        """Large participant count with uneven weights: exact sum, each share
        within 1 minor unit of its raw value."""
        amount = 99_999_991
        ws = [Decimal(i) for i in range(1, 106)]
        shares = splits.split_weighted(amount, ws)
        total = sum(ws)
        assert len(shares) == 105
        assert sum(shares) == amount
        for share, w in zip(shares, ws, strict=True):
            raw = Decimal(amount) * w / total
            base = int(raw.to_integral_value(rounding=ROUND_FLOOR))
            assert share in (base, base + 1)

    def test_spec_example_10_01_weights_1_1_1(self) -> None:
        """AGENTS.md §2.1: 10.01, weights [1,1,1] → 3.34, 3.34, 3.33
        (equal remainders → the two earliest joiners get +0.01)."""
        assert splits.split_weighted(1_001, [Decimal(1), Decimal(1), Decimal(1)]) == [
            334,
            334,
            333,
        ]

    def test_distinct_weights_largest_remainder_wins(self) -> None:
        # A = 100, weights [1, 2, 3]: raw = [16.666…, 33.333…, 50]
        # fracs  = [.666…, .333…, 0]      → +1 to participant 0.
        assert splits.split_weighted(100, [Decimal(1), Decimal(2), Decimal(3)]) == [
            17,
            33,
            50,
        ]

    @pytest.mark.parametrize("ws", [[Decimal(0)], [Decimal(1), Decimal(0)], [-Decimal(1)]])
    def test_rejects_non_positive_weights(self, ws: list[Decimal]) -> None:
        with pytest.raises(ValidationFailed):
            splits.split_weighted(100, ws)

    def test_rejects_no_participants(self) -> None:
        with pytest.raises(ValidationFailed):
            splits.split_weighted(100, [])


class TestExactSplit:
    def test_accepts_and_returns_as_is(self) -> None:
        assert splits.split_exact(1_000, [400, 350, 250]) == [400, 350, 250]

    def test_accepts_zero_shares(self) -> None:
        assert splits.split_exact(100, [100, 0]) == [100, 0]

    def test_rejects_sum_mismatch(self) -> None:
        with pytest.raises(ValidationFailed):
            splits.split_exact(1_000, [400, 350, 249])

    def test_rejects_negative_share(self) -> None:
        with pytest.raises(ValidationFailed):
            splits.split_exact(100, [150, -50])

    def test_rejects_negative_amount(self) -> None:
        with pytest.raises(ValidationFailed):
            splits.split_exact(-5, [-5])
