"""Property tests for vank.fund — Fund, AllocationRule, Unit."""
import pytest

from vank.fund import Fund, AllocationRule, Unit
from vank.portfolio import Trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fund(targets=None):
    """Return a Fund with a 100 % BTC allocation rule by default."""
    if targets is None:
        targets = {"BTC": 1.0}
    rule = AllocationRule(targets)
    return Fund(id="fund1", name="Test Fund", strategy=rule)


def _buy(asset_id, units, price, ts=1.0, fee=0.0):
    return Trade(timestamp=ts, asset_id=asset_id, units=units, price=price, fee=fee)


# ---------------------------------------------------------------------------
# AllocationRule.validate
# ---------------------------------------------------------------------------


def test_allocation_rule_validate_raises_when_over():
    rule = AllocationRule({"BTC": 0.6, "ETH": 0.6})  # 1.2 total
    with pytest.raises(ValueError):
        rule.validate()


def test_allocation_rule_validate_raises_when_under():
    rule = AllocationRule({"BTC": 0.4, "ETH": 0.4})  # 0.8 total
    with pytest.raises(ValueError):
        rule.validate()


def test_allocation_rule_validate_passes_when_exact():
    rule = AllocationRule({"BTC": 0.5, "ETH": 0.5})
    rule.validate()  # must not raise


def test_allocation_rule_validate_passes_within_tolerance():
    # 0.9999 is within ±0.001
    rule = AllocationRule({"BTC": 0.9999})
    rule.validate()  # must not raise


# ---------------------------------------------------------------------------
# AllocationRule.rebalance_trades
# ---------------------------------------------------------------------------


def test_rebalance_trades_returns_trades_toward_target():
    """Rebalance should generate a buy when underweight."""
    fund = _make_fund({"BTC": 1.0})
    # Buy a small BTC position then update price so market_value < target
    fund._cash = 1000.0
    trade = _buy("BTC", units=0.5, price=100.0)
    fund._portfolio.trade(trade)
    fund._portfolio.update_prices({"BTC": 100.0})
    fund._cash -= trade.units * trade.price  # simulate cash drawdown

    # NAV = 50 (portfolio) + 950 (cash) = 1000; target = 100 % BTC
    # current BTC value = 50; delta_units = (1000 - 50) / 100 = 9.5 (buy)
    nav = fund.nav
    trades = fund.strategy.rebalance_trades(fund._portfolio, nav)
    assert len(trades) == 1
    assert trades[0].asset_id == "BTC"
    assert trades[0].units > 0  # positive = buy to move toward target


def test_rebalance_trades_sell_when_overweight():
    """Rebalance should generate a sell when overweight."""
    rule = AllocationRule({"BTC": 0.5, "ETH": 0.5})
    fund = Fund(id="f2", name="F2", strategy=rule)

    # Give the portfolio two positions; BTC is overweight
    fund._portfolio.trade(_buy("BTC", units=8.0, price=100.0))   # 800
    fund._portfolio.trade(_buy("ETH", units=1.0, price=100.0))   # 100
    fund._portfolio.update_prices({"BTC": 100.0, "ETH": 100.0})

    total_nav = fund._portfolio.nav  # 900
    trades = rule.rebalance_trades(fund._portfolio, total_nav)

    btc_trade = next((t for t in trades if t.asset_id == "BTC"), None)
    assert btc_trade is not None
    assert btc_trade.units < 0  # sell BTC


def test_rebalance_trades_skips_unknown_assets():
    """Assets in targets but not in portfolio are silently skipped."""
    rule = AllocationRule({"BTC": 0.5, "MISSING": 0.5})
    fund = Fund(id="f3", name="F3", strategy=rule)

    fund._portfolio.trade(_buy("BTC", units=1.0, price=100.0))
    fund._portfolio.update_prices({"BTC": 100.0})

    trades = rule.rebalance_trades(fund._portfolio, 100.0)
    asset_ids = {t.asset_id for t in trades}
    assert "MISSING" not in asset_ids


# ---------------------------------------------------------------------------
# Fund.admit — unit issuance
# ---------------------------------------------------------------------------


def test_admit_first_admission_at_unit_price_one():
    """First admit must use unit_price = 1.0, so units_issued == amount."""
    fund = _make_fund()
    unit = fund.admit("alice", amount=500.0, timestamp=1.0)
    assert unit.cost_per_unit == pytest.approx(1.0)
    assert unit.units == pytest.approx(500.0)


def test_admit_issues_unit_object():
    fund = _make_fund()
    unit = fund.admit("bob", amount=200.0, timestamp=2.0)
    assert isinstance(unit, Unit)
    assert unit.holder == "bob"


def test_admit_increases_total_units():
    fund = _make_fund()
    fund.admit("alice", 100.0, 1.0)
    assert fund.total_units == pytest.approx(100.0)
    fund.admit("bob", 200.0, 2.0)
    assert fund.total_units == pytest.approx(300.0)


# ---------------------------------------------------------------------------
# Fund.nav
# ---------------------------------------------------------------------------


def test_nav_equals_cash_when_no_positions():
    """With no portfolio positions, all NAV is uninvested cash."""
    fund = _make_fund()
    fund.admit("alice", 1000.0, 1.0)
    # portfolio.nav == 0, so fund.nav == fund._cash
    assert fund.nav == pytest.approx(fund._portfolio.nav + fund._cash)
    assert fund.nav == pytest.approx(1000.0)


def test_nav_reflects_portfolio_and_cash():
    """NAV = portfolio market value + uninvested cash."""
    fund = _make_fund()
    fund.admit("alice", 1000.0, 1.0)
    fund.apply_trade(_buy("BTC", 5.0, 100.0, ts=2.0))
    fund._portfolio.update_prices({"BTC": 120.0})  # 5 * 120 = 600

    expected = fund._portfolio.nav + fund._cash
    assert fund.nav == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Fund.unit_price
# ---------------------------------------------------------------------------


def test_unit_price_is_one_when_no_units():
    fund = _make_fund()
    assert fund.unit_price == pytest.approx(1.0)


def test_unit_price_equals_nav_over_total_units():
    fund = _make_fund()
    fund.admit("alice", 400.0, 1.0)
    fund.admit("bob", 600.0, 2.0)
    expected = fund.nav / fund.total_units
    assert fund.unit_price == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Fund.member_value
# ---------------------------------------------------------------------------


def test_member_value_equals_units_times_unit_price():
    fund = _make_fund()
    fund.admit("alice", 300.0, 1.0)
    # At this point unit_price == 1.0, so member_value == 300
    assert fund.member_value("alice") == pytest.approx(300.0)


def test_member_value_reflects_price_appreciation():
    fund = _make_fund()
    fund.admit("alice", 200.0, 1.0)
    # Double the NAV by injecting cash directly (simulate portfolio gain)
    fund._cash += 200.0  # NAV = 400, total_units = 200, unit_price = 2.0
    expected = fund.total_units * fund.unit_price  # alice holds all units
    assert fund.member_value("alice") == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Fund.redeem
# ---------------------------------------------------------------------------


def test_redeem_returns_correct_amount():
    fund = _make_fund()
    fund.admit("alice", 500.0, 1.0)
    amount = fund.redeem("alice", 100.0, 2.0)
    # unit_price == 1.0 initially (no portfolio gains yet)
    assert amount == pytest.approx(100.0)


def test_redeem_burns_units():
    fund = _make_fund()
    fund.admit("alice", 500.0, 1.0)
    before = fund.total_units
    fund.redeem("alice", 200.0, 2.0)
    assert fund.total_units == pytest.approx(before - 200.0)


def test_redeem_raises_when_insufficient_units():
    fund = _make_fund()
    fund.admit("alice", 100.0, 1.0)
    with pytest.raises(ValueError):
        fund.redeem("alice", 999.0, 2.0)


# ---------------------------------------------------------------------------
# Fund.total_units
# ---------------------------------------------------------------------------


def test_total_units_zero_after_full_redeem():
    """Redeeming all units must bring total_units to 0."""
    fund = _make_fund()
    fund.admit("alice", 300.0, 1.0)
    fund.admit("bob", 700.0, 2.0)
    total = fund.total_units
    fund.redeem("alice", 300.0, 3.0)
    fund.redeem("bob", 700.0, 4.0)
    assert fund.total_units == pytest.approx(0.0, abs=1e-10)


# ---------------------------------------------------------------------------
# Fund.performance
# ---------------------------------------------------------------------------


def test_performance_returns_expected_keys():
    fund = _make_fund()
    fund.admit("alice", 100.0, 1.0)
    perf = fund.performance()
    for key in ("nav", "unit_price", "total_units", "member_count"):
        assert key in perf, f"missing key: {key}"


def test_performance_member_count():
    fund = _make_fund()
    fund.admit("alice", 100.0, 1.0)
    fund.admit("bob", 200.0, 2.0)
    assert fund.performance()["member_count"] == 2


def test_performance_values_consistent():
    fund = _make_fund()
    fund.admit("alice", 500.0, 1.0)
    perf = fund.performance()
    assert perf["nav"] == pytest.approx(fund.nav)
    assert perf["unit_price"] == pytest.approx(fund.unit_price)
    assert perf["total_units"] == pytest.approx(fund.total_units)


# ---------------------------------------------------------------------------
# Fund.allocation mirrors portfolio allocation
# ---------------------------------------------------------------------------


def test_allocation_mirrors_portfolio_allocation():
    """fund.allocation() values == portfolio.allocation() / 100."""
    fund = _make_fund({"BTC": 0.6, "ETH": 0.4})
    fund.admit("alice", 1000.0, 1.0)
    fund.apply_trade(_buy("BTC", 3.0, 100.0, ts=2.0))
    fund.apply_trade(_buy("ETH", 2.0, 100.0, ts=3.0))
    fund._portfolio.update_prices({"BTC": 100.0, "ETH": 100.0})

    port_alloc = fund._portfolio.allocation()
    fund_alloc = fund.allocation()

    for asset_id, pct in port_alloc.items():
        assert fund_alloc[asset_id] == pytest.approx(pct / 100.0)


def test_allocation_empty_portfolio_returns_zeros():
    fund = _make_fund()
    # No trades → no positions → empty dict
    assert fund.allocation() == {}
