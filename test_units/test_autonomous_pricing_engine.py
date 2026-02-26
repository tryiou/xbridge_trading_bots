import pytest

from strategies.autonomous_pricing_engine import (
    PricingEngine,
    PriceLevel,
    SpreadMode,
)


class TestPricingEngine:
    """Tests for PricingEngine price calculations."""

    @pytest.fixture
    def engine(self):
        return PricingEngine(
            mid_price=100.0,
            max_open_orders=10,
            spread_mode="exponential",
            base_spread_percent=1.0,
            spread_multiplier=2.0,
            max_price_range_percent=50.0,
        )

    @pytest.fixture
    def linear_engine(self):
        return PricingEngine(
            mid_price=100.0,
            max_open_orders=10,
            spread_mode="linear",
            base_spread_percent=0.5,
            spread_increment=0.5,
            max_price_range_percent=50.0,
        )

    def test_initialization(self, engine):
        assert engine.mid_price == 100.0
        assert engine.max_open_orders == 10
        assert engine.spread_mode == SpreadMode.EXPONENTIAL
        assert engine.base_spread_percent == 0.01

    def test_update_mid_price(self, engine):
        engine.update_mid_price(110.0)
        assert engine.mid_price == 110.0
        assert 110.0 in engine.price_history

    def test_calculate_buy_price_level_1(self, engine):
        price = engine.calculate_buy_price(1, skew=0.0)
        spread = 0.01
        effective = 0.01
        expected = 100.0 * (1 - spread - effective)
        assert price == pytest.approx(expected)

    def test_calculate_sell_price_level_1(self, engine):
        price = engine.calculate_sell_price(1, skew=0.0)
        spread = 0.01
        effective = 0.01
        expected = 100.0 * (1 + spread + effective)
        assert price == pytest.approx(expected)

    def test_calculate_buy_price_exponential_level_2(self, engine):
        price = engine.calculate_buy_price(2, skew=0.0)
        spread = 0.01 * (2.0 ** (2 - 1))
        effective = 0.01
        expected = 100.0 * (1 - spread - effective)
        assert price == pytest.approx(expected)

    def test_calculate_sell_price_exponential_level_2(self, engine):
        price = engine.calculate_sell_price(2, skew=0.0)
        spread = 0.01 * (2.0 ** (2 - 1))
        effective = 0.01
        expected = 100.0 * (1 + spread + effective)
        assert price == pytest.approx(expected)

    def test_linear_spread_mode(self, linear_engine):
        price1 = linear_engine.calculate_buy_price(1, skew=0.0)
        price2 = linear_engine.calculate_buy_price(2, skew=0.0)
        assert price2 < price1

    def test_buy_price_with_positive_skew(self, engine):
        price_no_skew = engine.calculate_buy_price(1, skew=0.0)
        price_with_skew = engine.calculate_buy_price(1, skew=0.5)
        assert price_with_skew < price_no_skew

    def test_sell_price_with_positive_skew(self, engine):
        price_no_skew = engine.calculate_sell_price(1, skew=0.0)
        price_with_skew = engine.calculate_sell_price(1, skew=-0.5)
        assert price_with_skew > price_no_skew

    def test_validate_price_buy_valid(self, engine):
        valid = engine.validate_price(95.0, "buy")
        assert valid is True

    def test_validate_price_buy_too_low(self, engine):
        valid = engine.validate_price(10.0, "buy")
        assert valid is False

    def test_validate_price_sell_valid(self, engine):
        valid = engine.validate_price(105.0, "sell")
        assert valid is True

    def test_validate_price_sell_too_high(self, engine):
        valid = engine.validate_price(200.0, "sell")
        assert valid is False

    def test_get_price_levels(self, engine):
        levels = engine.get_price_levels(3)
        buy_levels = [l for l in levels if l.side == "buy"]
        sell_levels = [l for l in levels if l.side == "sell"]
        assert len(buy_levels) == 3
        assert len(sell_levels) == 3
        assert buy_levels[0].price > buy_levels[1].price > buy_levels[2].price
        assert sell_levels[0].price < sell_levels[1].price < sell_levels[2].price

    def test_configure_spread(self, engine):
        engine.configure_spread(
            {
                "volatility_adjustment": False,
                "volatility_multiplier": 1.5,
                "price_skew": 0.003,
            }
        )
        assert engine.volatility_adjustment is False
        assert engine.volatility_multiplier == 1.5
        assert engine.price_skew == 0.003


class TestPricingEngineVolatility:
    """Tests for volatility-adjusted spreads."""

    @pytest.fixture
    def engine(self):
        return PricingEngine(
            mid_price=100.0,
            max_open_orders=10,
            spread_mode="exponential",
            base_spread_percent=1.0,
            volatility_adjustment=True,
            min_spread=0.5,
        )

    def test_effective_spread_no_history(self, engine):
        spread = engine.get_effective_spread_percent()
        assert spread >= 0.005

    def test_effective_spread_with_history(self, engine):
        engine.update_mid_price(101.0)
        engine.update_mid_price(102.0)
        engine.update_mid_price(103.0)
        spread = engine.get_effective_spread_percent()
        assert spread > 0


class TestPriceLevel:
    """Tests for PriceLevel dataclass."""

    def test_price_level_creation(self):
        level = PriceLevel(level=1, side="buy", price=99.0, amount=0.1)
        assert level.level == 1
        assert level.side == "buy"
        assert level.price == 99.0
        assert level.amount == 0.1
