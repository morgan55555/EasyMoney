"""Currency precision, conversion, and string serialization (AGENTS.md §2.12;
plan cases E8/E9). No database required — the currency service is pure."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationFailed
from app.services import currency


class TestMinorDigits:
    @pytest.mark.parametrize(
        ("code", "digits"),
        [
            ("JPY", 0),
            ("KRW", 0),
            ("VND", 0),
            ("KWD", 3),
            ("BHD", 3),
            ("OMR", 3),
            ("TND", 3),
            ("USD", 2),
            ("EUR", 2),
            ("RUB", 2),
            # Unknown-but-well-formed codes fall back to the 2-digit default.
            ("XYZ", 2),
        ],
    )
    def test_minor_digits(self, code: str, digits: int) -> None:
        assert currency.minor_digits(code) == digits


class TestValidateCurrency:
    def test_uppercases_and_strips(self) -> None:
        assert currency.validate_currency(" usd ") == "USD"

    @pytest.mark.parametrize("bad", ["us", "usdx", "123", "", "US-D", "us d", "u$D"])
    def test_rejects_non_iso(self, bad: str) -> None:
        with pytest.raises(ValidationFailed):
            currency.validate_currency(bad)


class TestToMinor:
    def test_two_decimal(self) -> None:
        assert currency.to_minor(Decimal("33.34"), "USD") == 3334
        assert currency.to_minor(Decimal("0.01"), "USD") == 1

    def test_zero_decimal(self) -> None:
        assert currency.to_minor(Decimal("100"), "JPY") == 100
        assert currency.to_minor(Decimal("123"), "KRW") == 123

    def test_three_decimal(self) -> None:
        assert currency.to_minor(Decimal("12.345"), "KWD") == 12345
        assert currency.to_minor(Decimal("0.001"), "TND") == 1

    def test_rejects_finer_precision_than_currency(self) -> None:
        # Plan E8: 1.999 in a 2-decimal currency must be a 422, not a floor.
        with pytest.raises(ValidationFailed):
            currency.to_minor(Decimal("1.999"), "USD")

    def test_rejects_fractional_in_zero_decimal(self) -> None:
        with pytest.raises(ValidationFailed):
            currency.to_minor(Decimal("1.5"), "JPY")

    def test_rejects_extra_digit_in_three_decimal(self) -> None:
        with pytest.raises(ValidationFailed):
            currency.to_minor(Decimal("1.2345"), "KWD")

    def test_negative_amount_is_a_valid_minor_value(self) -> None:
        # The sign is allowed here; amount policy is enforced upstream.
        assert currency.to_minor(Decimal("-5.00"), "USD") == -500

    def test_rejects_scientific_notation_sub_precision(self) -> None:
        # H-14: "1e-7" arrives as Decimal('1E-7'); the tuple exponent carries
        # the precision, so no quantize arithmetic is needed to reject it.
        with pytest.raises(ValidationFailed):
            currency.to_minor(Decimal("1E-7"), "USD")

    def test_rejects_negative_sub_precision(self) -> None:
        # H-7: "-0.001" is finer than USD's 2 minor digits.
        with pytest.raises(ValidationFailed):
            currency.to_minor(Decimal("-0.001"), "USD")

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
    def test_rejects_non_finite(self, value: str) -> None:
        # H-16: non-finite input is a 422 at the service level too.
        with pytest.raises(ValidationFailed):
            currency.to_minor(Decimal(value), "USD")

    def test_rejects_amount_beyond_context_precision(self) -> None:
        # H-7/H-14: a huge amount whose quantized form exceeds the decimal
        # context precision used to surface as decimal.InvalidOperation (500);
        # it must now be a ValidationFailed (422).
        with pytest.raises(ValidationFailed):
            currency.to_minor(Decimal("1E+30"), "USD")


class TestRoundTripAndFormatting:
    @pytest.mark.parametrize(
        ("code", "amount"),
        [
            ("USD", Decimal("33.34")),
            ("JPY", Decimal("100")),
            ("KWD", Decimal("12.345")),
        ],
    )
    def test_from_minor_is_inverse_of_to_minor(self, code: str, amount: Decimal) -> None:
        assert currency.from_minor(currency.to_minor(amount, code), code) == amount

    def test_format_money_is_the_api_representation(self) -> None:
        # Money in JSON is always a string (§3.1) with the currency's digits.
        assert currency.format_money(Decimal("33.34"), "USD") == "33.34"
        assert currency.format_money(Decimal("100"), "JPY") == "100"
        assert currency.format_money(Decimal("12.345"), "KWD") == "12.345"

    def test_format_money_half_up(self) -> None:
        # Document the rounding mode so it can never silently change.
        assert currency.format_money(Decimal("1.005"), "USD") == "1.01"
        assert currency.format_money(Decimal("1.004"), "USD") == "1.00"

    def test_format_money_zero_has_exact_minor_digits(self) -> None:
        # SER-0 pin (§2.12): zero serializes with exactly the currency's minor
        # digits — "0" for a zero-digit currency is correct, not a bug.
        assert currency.format_money(Decimal(0), "JPY") == "0"
        assert currency.format_money(Decimal(0), "USD") == "0.00"
        assert currency.format_money(Decimal(0), "KWD") == "0.000"
