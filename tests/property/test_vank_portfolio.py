"""Property tests for vank.portfolio."""

import pytest

from vank.portfolio import Portfolio, Position, Trade


def _p():
    return Portfolio(id="test-p", owner="tester")


# 1. buy trade creates a position
def test_buy_creates_position():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    positions = p.positions()
    assert len(positions) == 1
    assert positions[0].asset_id == "AAPL"
    assert positions[0].units == pytest.approx(10.0)


# 2. sell trade reduces position units
def test_sell_reduces_position():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.trade(Trade(timestamp=2.0, asset_id="AAPL", units=-3.0, price=100.0))
    assert p.positions()[0].units == pytest.approx(7.0)


# 3. position removed when units reach 0
def test_sell_all_removes_position():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=5.0, price=100.0))
    p.trade(Trade(timestamp=2.0, asset_id="AAPL", units=-5.0, price=100.0))
    assert p.positions() == []


# 4. update_prices changes market_value
def test_update_prices_changes_market_value():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.update_prices({"AAPL": 150.0})
    assert p.positions()[0].market_value == pytest.approx(1500.0)


# 5. nav = sum of market_values across all positions
def test_nav_equals_sum_of_market_values():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.trade(Trade(timestamp=2.0, asset_id="GOOG", units=5.0, price=200.0))
    p.update_prices({"AAPL": 110.0, "GOOG": 210.0})
    expected = 10 * 110.0 + 5 * 210.0
    assert p.nav == pytest.approx(expected)


# 6. unrealised_pnl = nav - total_cost
def test_unrealised_pnl_equals_nav_minus_total_cost():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.update_prices({"AAPL": 120.0})
    assert p.unrealised_pnl == pytest.approx(p.nav - p.total_cost)


# 7. allocation percentages sum to ~100.0 when portfolio has positions
def test_allocation_percentages_sum_to_100():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.trade(Trade(timestamp=2.0, asset_id="GOOG", units=5.0, price=200.0))
    p.update_prices({"AAPL": 100.0, "GOOG": 200.0})
    alloc = p.allocation()
    assert sum(alloc.values()) == pytest.approx(100.0, abs=0.001)


# 8. pnl_pct for a position: (market_value - cost_basis) / cost_basis * 100
def test_position_pnl_pct_correct():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.update_prices({"AAPL": 130.0})
    pos = p.positions()[0]
    expected = (pos.market_value - pos.cost_basis) / pos.cost_basis * 100
    assert pos.pnl_pct == pytest.approx(expected)


# 9. sharpe returns None when fewer than 2 snapshots exist
def test_sharpe_returns_none_with_fewer_than_two_snapshots():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.update_prices({"AAPL": 100.0})
    assert p.sharpe() is None
    p.snapshot(1.0)
    assert p.sharpe() is None


# 10. sharpe returns a float when there are enough snapshots with varying nav
def test_sharpe_returns_float_with_sufficient_snapshots():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0))
    p.update_prices({"AAPL": 100.0})
    p.snapshot(1.0)
    p.update_prices({"AAPL": 110.0})
    p.snapshot(2.0)
    p.update_prices({"AAPL": 90.0})
    p.snapshot(3.0)
    result = p.sharpe()
    assert isinstance(result, float)


# 11. drawdown: buy at 100, snapshot, price drops to 80, snapshot → drawdown = 0.20
def test_drawdown_after_price_drop():
    p = _p()
    p.trade(Trade(timestamp=1.0, asset_id="AAPL", units=1.0, price=100.0))
    p.update_prices({"AAPL": 100.0})
    p.snapshot(1.0)
    p.update_prices({"AAPL": 80.0})
    p.snapshot(2.0)
    assert p.drawdown() == pytest.approx(0.20)


# 12. history returns all trades in insertion order
def test_history_returns_all_trades_in_order():
    p = _p()
    t1 = Trade(timestamp=1.0, asset_id="AAPL", units=10.0, price=100.0)
    t2 = Trade(timestamp=2.0, asset_id="GOOG", units=5.0, price=200.0)
    t3 = Trade(timestamp=3.0, asset_id="AAPL", units=-3.0, price=110.0)
    p.trade(t1)
    p.trade(t2)
    p.trade(t3)
    assert p.history() == [t1, t2, t3]


# 13. FIFO/proportional cost basis: buy 10@10, buy 5@20, sell 5 → cost reduced proportionally
def test_fifo_cost_basis_buy_buy_sell():
    p = _p()
    # buy 10 units at 10 → cost_basis = 100
    p.trade(Trade(timestamp=1.0, asset_id="X", units=10.0, price=10.0))
    # buy 5 units at 20 → cost_basis += 100 → total = 200, units = 15
    p.trade(Trade(timestamp=2.0, asset_id="X", units=5.0, price=20.0))
    # sell 5 of 15 → ratio = 5/15, cost removed = 200 * (5/15)
    p.trade(Trade(timestamp=3.0, asset_id="X", units=-5.0, price=15.0))
    pos = p.positions()[0]
    assert pos.units == pytest.approx(10.0)
    # proportional: remaining cost = 200 * (10/15) = 133.333...
    assert pos.cost_basis == pytest.approx(200.0 * (10.0 / 15.0))
