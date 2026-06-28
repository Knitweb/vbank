"""P2P asset management portfolio tracker. stdlib only, floats."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["Position", "Trade", "Portfolio"]


@dataclass
class Position:
    asset_id: str
    units: float
    cost_basis: float       # total cost paid (for P&L)
    current_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.units * self.current_price

    @property
    def unrealised_pnl(self) -> float:
        return self.market_value - self.cost_basis

    @property
    def pnl_pct(self) -> float:
        return (self.unrealised_pnl / self.cost_basis * 100) if self.cost_basis else 0.0


@dataclass
class Trade:
    timestamp: float
    asset_id: str
    units: float           # positive = buy, negative = sell
    price: float
    fee: float = 0.0

    @property
    def notional(self) -> float:
        return abs(self.units) * self.price


class Portfolio:
    def __init__(self, id: str, owner: str) -> None:
        self.id = id
        self.owner = owner
        self._positions: dict[str, Position] = {}
        self._trades: list[Trade] = []
        self._snapshots: list[dict] = []  # [{timestamp, nav}]

    def trade(self, t: Trade) -> None:
        """Update Position (create if new, remove if units -> 0).

        FIFO cost basis: buying adds cost, selling reduces proportionally.
        """
        self._trades.append(t)
        aid = t.asset_id

        if t.units > 0:
            # Buy: add units and cost (notional + fee)
            cost_added = t.units * t.price + t.fee
            if aid in self._positions:
                pos = self._positions[aid]
                pos.units += t.units
                pos.cost_basis += cost_added
            else:
                self._positions[aid] = Position(
                    asset_id=aid,
                    units=t.units,
                    cost_basis=cost_added,
                    current_price=t.price,
                )
        elif t.units < 0:
            # Sell: reduce units and cost basis proportionally
            sold = abs(t.units)
            if aid not in self._positions:
                raise ValueError(f"No position for {aid}")
            pos = self._positions[aid]
            if sold > pos.units + 1e-12:
                raise ValueError(
                    f"Cannot sell {sold} units of {aid}; only {pos.units} held"
                )
            ratio = sold / pos.units if pos.units else 0.0
            pos.cost_basis -= pos.cost_basis * ratio
            pos.units -= sold
            # Remove dust positions
            if pos.units < 1e-12:
                del self._positions[aid]

    def update_prices(self, prices: dict[str, float]) -> None:
        for aid, price in prices.items():
            if aid in self._positions:
                self._positions[aid].current_price = price

    @property
    def nav(self) -> float:
        return sum(p.market_value for p in self._positions.values())

    @property
    def total_cost(self) -> float:
        return sum(p.cost_basis for p in self._positions.values())

    @property
    def unrealised_pnl(self) -> float:
        return self.nav - self.total_cost

    @property
    def pnl_pct(self) -> float:
        tc = self.total_cost
        return (self.unrealised_pnl / tc * 100) if tc else 0.0

    def allocation(self) -> dict[str, float]:
        """Return {asset_id: pct_of_nav}."""
        n = self.nav
        if n == 0:
            return {aid: 0.0 for aid in self._positions}
        return {aid: p.market_value / n * 100 for aid, p in self._positions.items()}

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def history(self) -> list[Trade]:
        return list(self._trades)

    def snapshot(self, timestamp: float) -> dict:
        """Record a NAV snapshot for Sharpe/drawdown calculation."""
        snap = {"timestamp": timestamp, "nav": self.nav}
        self._snapshots.append(snap)
        return snap

    def sharpe(self, risk_free_rate: float = 0.0) -> Optional[float]:
        """Compute Sharpe ratio from NAV snapshots.

        Requires at least 2 snapshots.
        Returns (mean_period_return - risk_free) / std_period_return, or None.
        """
        if len(self._snapshots) < 2:
            return None
        navs = [s["nav"] for s in self._snapshots]
        returns = []
        for i in range(1, len(navs)):
            prev = navs[i - 1]
            if prev == 0:
                continue
            returns.append((navs[i] - prev) / prev)
        if len(returns) < 2:
            return None
        n = len(returns)
        mean_r = sum(returns) / n
        variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        std_r = math.sqrt(variance)
        if std_r == 0:
            return None
        return (mean_r - risk_free_rate) / std_r

    def drawdown(self) -> float:
        """Max drawdown from peak NAV in snapshot history.

        Returns (peak - trough) / peak, or 0.0 if fewer than 2 snapshots.
        """
        if len(self._snapshots) < 2:
            return 0.0
        peak = self._snapshots[0]["nav"]
        max_dd = 0.0
        for snap in self._snapshots[1:]:
            nav = snap["nav"]
            if nav > peak:
                peak = nav
            elif peak > 0:
                dd = (peak - nav) / peak
                if dd > max_dd:
                    max_dd = dd
        return max_dd
