"""Construção e validação de Position."""

from decimal import Decimal

import pytest

from exposure import Direction, Position


def make_pos(**overrides) -> Position:
    base = dict(
        asset="WIN",
        direction=Direction.WIN,
        quantity=Decimal(10),
        price=Decimal("150000"),
        point_value=Decimal("0.20"),
        stop=Decimal("149800"),
        target=Decimal("150400"),
    )
    base.update(overrides)
    return Position(**base)


class TestConstruction:
    def test_canonical_win(self):
        p = make_pos()
        assert p.quantity == Decimal(10)
        assert p.price == Decimal("150000")
        assert p.point_value == Decimal("0.20")
        assert p.signed_quantity == Decimal(10)
        assert p.direction.sign == 1

    def test_accepts_int_and_str_inputs(self):
        p = Position("WIN", Direction.WIN, 10, "150000", "0.20")
        assert p.quantity == Decimal(10)
        assert p.price == Decimal("150000")
        assert p.point_value == Decimal("0.20")

    def test_float_input_has_no_binary_error(self):
        p = Position("DOL", Direction.LOSS, 1, 0.1, 0.5)
        assert p.price == Decimal("0.1")
        assert p.point_value == Decimal("0.5")

    def test_loss_has_negative_signed_quantity(self):
        p = make_pos(direction=Direction.LOSS, stop=Decimal("150200"), target=Decimal("149600"))
        assert p.signed_quantity == Decimal(-10)
        assert p.direction.sign == -1


class TestValidation:
    def test_zero_quantity_rejected(self):
        with pytest.raises(ValueError, match="quantity"):
            make_pos(quantity=Decimal(0))

    def test_negative_quantity_rejected(self):
        with pytest.raises(ValueError, match="quantity"):
            make_pos(quantity=Decimal(-1))

    def test_zero_price_rejected(self):
        with pytest.raises(ValueError, match="price"):
            make_pos(price=Decimal(0))

    def test_zero_point_value_rejected(self):
        with pytest.raises(ValueError, match="point_value"):
            make_pos(point_value=Decimal(0))

    def test_empty_asset_rejected(self):
        with pytest.raises(ValueError, match="asset"):
            make_pos(asset="  ")

    def test_invalid_direction_type_rejected(self):
        with pytest.raises(TypeError, match="direction"):
            Position("WIN", "WIN", 1, 1, 1)

    def test_win_stop_above_entry_rejected(self):
        with pytest.raises(ValueError, match="stop do lado errado"):
            make_pos(stop=Decimal("150200"))

    def test_win_stop_equal_to_entry_rejected(self):
        with pytest.raises(ValueError, match="stop do lado errado"):
            make_pos(stop=Decimal("150000"))

    def test_loss_stop_below_entry_rejected(self):
        with pytest.raises(ValueError, match="stop do lado errado"):
            make_pos(direction=Direction.LOSS, stop=Decimal("149800"))

    def test_win_target_below_entry_rejected(self):
        with pytest.raises(ValueError, match="alvo"):
            make_pos(target=Decimal("149900"))

    def test_loss_target_above_entry_rejected(self):
        with pytest.raises(ValueError, match="alvo"):
            make_pos(
                direction=Direction.LOSS,
                stop=Decimal("150200"),
                target=Decimal("150100"),
            )

    def test_position_without_stop_is_valid(self):
        p = make_pos(stop=None, target=None)
        assert p.stop is None
        assert p.target is None
