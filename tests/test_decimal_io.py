"""decimal_io: Decimal coercion + step / digit quantisation."""

from decimal import Decimal

import pytest

from cfd_skills.decimal_io import D, floor_to_step, quantize_price


class TestD:
    def test_passes_through_decimal(self):
        d = Decimal("1.23")
        assert D(d) is d

    def test_coerces_int(self):
        assert D(5) == Decimal("5")

    def test_coerces_str(self):
        # mt5-mcp's canonical "Decimal as string" output.
        assert D("1.0823") == Decimal("1.0823")

    def test_rejects_float(self):
        with pytest.raises(TypeError, match="refuses floats"):
            D(1.5)


class TestFloorToStep:
    def test_floors_to_lot_step(self):
        assert floor_to_step(Decimal("0.247"), Decimal("0.01")) == Decimal("0.24")

    def test_exact_multiple_unchanged(self):
        assert floor_to_step(Decimal("0.50"), Decimal("0.01")) == Decimal("0.50")

    def test_below_step_returns_zero(self):
        assert floor_to_step(Decimal("0.005"), Decimal("0.01")) == Decimal("0.00")

    def test_index_step_of_one(self):
        # Some index brokers use volume_step = 1.
        assert floor_to_step(Decimal("3.7"), Decimal("1")) == Decimal("3")

    def test_rejects_zero_step(self):
        with pytest.raises(ValueError):
            floor_to_step(Decimal("1"), Decimal("0"))


class TestQuantizePrice:
    def test_5_digit_fx(self):
        assert quantize_price(Decimal("1.082345"), 5) == Decimal("1.08235")

    def test_2_digit_index(self):
        assert quantize_price(Decimal("4821.273"), 2) == Decimal("4821.27")

    def test_zero_digits(self):
        assert quantize_price(Decimal("100.7"), 0) == Decimal("101")

    def test_rejects_negative_digits(self):
        with pytest.raises(ValueError):
            quantize_price(Decimal("1.5"), -1)
