"""vank.fund — P2P collective investment fund.

Governance via VankDAO, positions via Portfolio.
stdlib only, floats.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from vank.dao import VankDAO, Ballot
from vank.portfolio import Portfolio, Trade

__all__ = ["Unit", "AllocationRule", "Fund"]


@dataclass
class Unit:
    """A fund unit held by a member (like a share / NAV unit)."""

    holder: str
    units: float
    cost_per_unit: float


class AllocationRule:
    """Target allocation: {asset_id: target_fraction} must sum to ~1.0."""

    def __init__(self, targets: dict[str, float]) -> None:
        self.targets = dict(targets)

    def validate(self) -> None:
        """Raise ValueError if targets do not sum to 1.0 ± 0.001."""
        total = sum(self.targets.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"AllocationRule targets sum to {total:.6f}; expected 1.0 ± 0.001"
            )

    def rebalance_trades(self, portfolio: Portfolio, total_nav: float) -> list[Trade]:
        """Compute the Trade list needed to move *portfolio* toward targets.

        Assets with unknown (zero) prices are skipped.  The returned trades
        are *unsigned* in the sense that a negative ``units`` field means sell.
        """
        trades: list[Trade] = []
        # Build position lookup from the list returned by portfolio.positions()
        pos_by_id = {p.asset_id: p for p in portfolio.positions()}

        for asset_id, fraction in self.targets.items():
            target_value = fraction * total_nav

            if asset_id in pos_by_id:
                pos = pos_by_id[asset_id]
                price = pos.current_price
                current_value = pos.market_value
            else:
                # No position held; we have no price — cannot compute trade size.
                continue

            if price <= 0.0:
                continue

            delta_value = target_value - current_value
            delta_units = delta_value / price

            if abs(delta_units) > 1e-12:
                trades.append(
                    Trade(
                        timestamp=0.0,
                        asset_id=asset_id,
                        units=delta_units,
                        price=price,
                    )
                )

        return trades


class Fund:
    """P2P collective investment fund: governance (VankDAO) + portfolio + units."""

    def __init__(self, id: str, name: str, strategy: AllocationRule) -> None:
        self.id = id
        self.name = name
        self.strategy = strategy
        self._dao: VankDAO = VankDAO()
        self._portfolio: Portfolio = Portfolio(id=id, owner="fund")
        self._units: list[Unit] = []
        self._cash: float = 0.0        # uninvested cash (subscriptions minus investments)
        self._peak_nav: float = 0.0    # high-water mark for drawdown

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    def admit(self, member: str, amount: float, timestamp: float) -> Unit:
        """Issue units at current unit_price (1.0 for first admission)."""
        price = self.unit_price          # returns 1.0 when total_units == 0
        units_issued = amount / price
        unit = Unit(holder=member, units=units_issued, cost_per_unit=price)
        self._units.append(unit)
        self._cash += amount
        self._dao.join(member, weight=units_issued)
        self._update_peak()
        return unit

    def redeem(self, holder: str, units: float, timestamp: float) -> float:
        """Burn *units* for *holder*; return the cash amount owed.

        Raises ValueError if *holder* does not have enough units.
        Note: the caller is responsible for liquidating positions if
        ``self._cash`` is insufficient to cover the returned amount.
        """
        price = self.unit_price
        amount = units * price
        remaining = units
        for u in self._units:
            if u.holder == holder and remaining > 1e-12:
                burn = min(u.units, remaining)
                u.units -= burn
                remaining -= burn
        if remaining > 1e-12:
            raise ValueError(
                f"Insufficient units for {holder!r}: "
                f"need {units:.6f}, short by {remaining:.6f}"
            )
        self._cash -= amount
        return amount

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    def apply_trade(self, trade: Trade) -> None:
        """Execute *trade* against the fund portfolio.

        Cash decreases by (units * price + fee) for buys, increases for sells.
        """
        cost = trade.units * trade.price + trade.fee
        self._cash -= cost
        self._portfolio.trade(trade)
        self._update_peak()

    def update_prices(self, prices: dict[str, float]) -> None:
        """Push new market prices into the portfolio."""
        self._portfolio.update_prices(prices)
        self._update_peak()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_peak(self) -> None:
        n = self.nav
        if n > self._peak_nav:
            self._peak_nav = n

    # ------------------------------------------------------------------
    # Core metrics
    # ------------------------------------------------------------------

    @property
    def nav(self) -> float:
        """Total fund NAV: portfolio market value + uninvested cash."""
        return self._portfolio.nav + self._cash

    @property
    def unit_price(self) -> float:
        """NAV per unit; 1.0 when no units are outstanding."""
        total = self.total_units
        if total == 0.0:
            return 1.0
        return self.nav / total

    @property
    def total_units(self) -> float:
        """Sum of all live (non-burnt) units."""
        return sum(u.units for u in self._units)

    def member_value(self, holder: str) -> float:
        """Current market value of all units held by *holder*."""
        held = sum(u.units for u in self._units if u.holder == holder)
        return held * self.unit_price

    # ------------------------------------------------------------------
    # Governance
    # ------------------------------------------------------------------

    def propose_rebalance(self, proposer: str, timestamp: float) -> list[Trade]:
        """Compute trades that would rebalance to strategy targets (read-only)."""
        return self.strategy.rebalance_trades(self._portfolio, self.nav)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def performance(self) -> dict:
        """Return a performance snapshot dict.

        Keys: nav, unit_price, total_units, member_count, drawdown,
        unrealised_pnl.  drawdown is (peak_nav - nav) / peak_nav.
        """
        current_nav = self.nav
        drawdown = 0.0
        if self._peak_nav > 0.0:
            drawdown = max(0.0, (self._peak_nav - current_nav) / self._peak_nav)
        return {
            "nav": current_nav,
            "unit_price": self.unit_price,
            "total_units": self.total_units,
            "member_count": len(
                {u.holder for u in self._units if u.units > 1e-12}
            ),
            "drawdown": drawdown,
            "unrealised_pnl": self._portfolio.unrealised_pnl,
        }

    def allocation(self) -> dict[str, float]:
        """Return {asset_id: fraction_of_nav} (0.0–1.0).

        portfolio.allocation() returns percentages; this converts to fractions.
        """
        return {k: v / 100.0 for k, v in self._portfolio.allocation().items()}
