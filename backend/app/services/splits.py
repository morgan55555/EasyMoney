"""Pure expense-splitting math (AGENTS.md §2.1).

Every function here is **pure**: no database, no I/O, no floats. Amounts are
expressed in **minor units** (int — cents/kopecks) so that ``floor`` and
remainder operate exactly; share weights are ``Decimal``. The caller is
responsible for converting a ``Decimal`` amount to minor units using the
group currency's precision (see :mod:`app.services.currency`) before calling
in, and for passing participants already sorted in remainder-tiebreak order
(``membership.joined_at`` asc, then ``user_id`` asc). The engine only decides
which positions receive the leftover minor unit.

Invariant guaranteed by every function: ``sum(shares) == amount_minor`` — no
fractional unit is ever lost or created (AGENTS.md invariants 1–2).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal, localcontext

from app.core.errors import ValidationFailed

# 50 significant digits is far more than enough for any realistic amount
# (NUMERIC(14,3) → at most ~10^11 minor units) and weight to make the
# largest-remainder floor and fractional ordering exact.
_PRECISION = 50


def _floor(value: Decimal) -> int:
    """Floor of a non-negative ``Decimal`` as an ``int``."""
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def split_equal(amount_minor: int, n: int) -> list[int]:
    """Equal split (AGENTS.md §2.1, "Equal split").

    ``base = floor(A / n)``, ``r = A - n*base`` (so ``0 <= r < n``). Every
    participant gets ``base``; the ``r`` leftover minor units are assigned one
    per participant to the first ``r`` entries (the earliest-joined). Returns
    ``n`` shares summing exactly to ``amount_minor``.
    """
    if n <= 0:
        raise ValidationFailed("At least one participant is required")
    if amount_minor < 0:
        raise ValidationFailed("Amount must be non-negative")
    base, r = divmod(amount_minor, n)
    return [base + (1 if i < r else 0) for i in range(n)]


def split_weighted(amount_minor: int, weights: Sequence[Decimal]) -> list[int]:
    """Share-weighted split via the largest-remainder method (AGENTS.md §2.1).

    ``raw_i = A * w_i / sum(w)`` (exact-enough Decimal arithmetic);
    ``base_i = floor(raw_i)``; the leftover ``r = A - sum(base_i)``
    (``0 <= r < n``) minor units go one each to the ``r`` participants with
    the largest fractional parts ``frac(raw_i) = raw_i - base_i``, ties broken
    by input (earliest-joined) order. Returns ``n`` shares summing to
    ``amount_minor``.
    """
    n = len(weights)
    if n <= 0:
        raise ValidationFailed("At least one participant is required")
    if amount_minor < 0:
        raise ValidationFailed("Amount must be non-negative")
    total = sum(weights)
    if total <= 0 or any(w <= 0 for w in weights):
        raise ValidationFailed("Share weights must be positive")

    with localcontext() as ctx:
        ctx.prec = _PRECISION
        a = Decimal(amount_minor)
        raw = [a * w / total for w in weights]
        base = [_floor(x) for x in raw]
        frac = [x - b for x, b in zip(raw, base, strict=True)]

    r = amount_minor - sum(base)
    # Largest fractional part first. Python's sort is stable, so exact ties in
    # ``frac`` keep the input (earliest-joined) order — the spec's tiebreak.
    order = sorted(range(n), key=lambda i: frac[i], reverse=True)
    shares = base[:]
    for i in order[:r]:
        shares[i] += 1
    return shares


def split_exact(amount_minor: int, amounts_minor: Sequence[int]) -> list[int]:
    """Exact amounts (AGENTS.md §2.1): validate and return as-is.

    The client supplies one amount per participant; this rejects (the caller
    maps to HTTP 422) unless ``sum(amounts) == amount_minor`` exactly. No
    recomputation — the supplied amounts are the authoritative shares.
    """
    if amount_minor < 0:
        raise ValidationFailed("Amount must be non-negative")
    if any(a < 0 for a in amounts_minor):
        raise ValidationFailed("Exact amounts must be non-negative")
    total = sum(amounts_minor)
    if total != amount_minor:
        raise ValidationFailed(
            f"Exact amounts must sum to the total "
            f"(got {total}, expected {amount_minor} minor units)"
        )
    return list(amounts_minor)
