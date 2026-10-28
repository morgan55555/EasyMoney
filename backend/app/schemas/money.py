"""Shared input validation for money/weight Decimal fields (AGENTS.md §2.12, §3.1).

Money and weights arrive as strings or JSON numbers and must be *finite*
``Decimal``s. Sub-precision strings (``"-0.001"``, ``"1e-7"``) and non-finite
values (raw JSON ``NaN``) are rejected here so the API answers a clean 422
instead of blowing up deeper in the split engine (H-7/H-14/H-16). The checks
run after Pydantic's type coercion, so ``value`` is already a ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal

# Upper bound for a currency amount. NUMERIC(14,3) holds at most
# 99,999,999,999.999, so anything at/above this is out of range (422, not a
# database error).
MAX_MONEY = Decimal("100000000000")

# Finest currency precision in the §2.12 map (KWD/BHD/OMR/TND). More than this
# many decimal places is not valid money in any supported currency.
MAX_DIGITS = 3


def _require_finite(value: Decimal, label: str) -> Decimal:
    if not value.is_finite():
        raise ValueError(f"{label} must be a finite number")
    return value


def check_money(value: Decimal) -> Decimal:
    """Validate a currency amount: finite, bounded, at most MAX_DIGITS decimals.

    Use for fields that hold a money amount (expense/settlement amounts,
    exact-split participant amounts).
    """
    _require_finite(value, "Amount")
    if value >= MAX_MONEY:
        raise ValueError("Amount is too large")
    # The tuple exponent is int for every finite value (the non-int spellings
    # mean NaN/Inf, rejected above).
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -MAX_DIGITS:
        raise ValueError(f"Amount has more than {MAX_DIGITS} decimal places")
    return value


def check_weight(value: Decimal) -> Decimal:
    """Validate a split weight: finite and bounded.

    Weights are relative, so they carry no currency precision constraint —
    only finiteness and a sane magnitude matter.
    """
    _require_finite(value, "Weight")
    if value >= MAX_MONEY:
        raise ValueError("Weight is too large")
    return value
