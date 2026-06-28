"""Float-friendly time-deposit / savings module. stdlib only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from vank.ledger import (
    Account,
    AccountType,
    JournalEntry,
    Ledger,
    accrual_entry,
    settlement_entry,
)

__all__ = [
    "PostingPeriod",
    "SavingsProduct",
    "SavingsAccount",
]

_SECS_PER_DAY: float = 86_400.0
_SECS_PER_ANIV_MONTH: float = 365.25 / 12 * _SECS_PER_DAY  # ~30.4375 days

PERIODS_PER_YEAR: dict[str, int] = {
    "daily": 365,
    "monthly": 12,
    "quarterly": 4,
    "annual": 1,
    "anniversary_monthly": 12,
}


class PostingPeriod(Enum):
    DAILY = "daily"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    ANNIVERSARY_MONTHLY = "anniversary_monthly"  # anchored to activation timestamp


@dataclass
class SavingsProduct:
    id: str
    name: str
    nominal_rate: float   # annual rate, e.g. 0.05 for 5 %
    posting_period: PostingPeriod
    compounding: bool = True   # compound or simple interest
    min_balance: float = 0.0


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _periods_elapsed(
    posting_period: PostingPeriod,
    last_posted: float,
    timestamp: float,
    opened_at: float,
) -> int:
    """Return the number of complete posting periods elapsed between last_posted and timestamp."""
    pp = posting_period

    if pp is PostingPeriod.ANNIVERSARY_MONTHLY:
        n_now = int((timestamp - opened_at) / _SECS_PER_ANIV_MONTH)
        n_last = int((last_posted - opened_at) / _SECS_PER_ANIV_MONTH)
        return max(0, n_now - n_last)

    if pp is PostingPeriod.DAILY:
        return max(0, int(timestamp / _SECS_PER_DAY) - int(last_posted / _SECS_PER_DAY))

    dt_now = _utc(timestamp)
    dt_last = _utc(last_posted)

    if pp is PostingPeriod.MONTHLY:
        return max(0, (dt_now.year * 12 + dt_now.month) - (dt_last.year * 12 + dt_last.month))

    if pp is PostingPeriod.QUARTERLY:
        def _q(dt: datetime) -> int:
            return dt.year * 4 + (dt.month - 1) // 3
        return max(0, _q(dt_now) - _q(dt_last))

    if pp is PostingPeriod.ANNUAL:
        return max(0, dt_now.year - dt_last.year)

    raise ValueError(f"Unknown PostingPeriod: {pp}")


class SavingsAccount:
    """Double-entry savings account backed by a shared Ledger."""

    def __init__(
        self,
        id: str,
        product: SavingsProduct,
        opened_at: float,
        ledger: Ledger,
    ) -> None:
        self._id = id
        self._product = product
        self._opened_at = opened_at
        self._ledger = ledger
        self._last_posted = opened_at
        self._total_interest: float = 0.0

        # Ledger account IDs scoped to this savings account
        pfx = f"sav_{id}"
        self._cash_id = f"{pfx}_cash"
        self._deposits_id = f"{pfx}_deposits"
        self._int_exp_id = f"{pfx}_int_exp"
        self._int_pay_id = f"{pfx}_int_pay"

        ledger.add_account(Account(self._cash_id, f"Cash [{id}]", AccountType.ASSET))
        ledger.add_account(Account(self._deposits_id, f"Deposits [{id}]", AccountType.LIABILITY))
        ledger.add_account(Account(self._int_exp_id, f"Interest Expense [{id}]", AccountType.EXPENSE))
        ledger.add_account(Account(self._int_pay_id, f"Interest Payable [{id}]", AccountType.LIABILITY))

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def deposit(self, amount: float, timestamp: float) -> JournalEntry:
        """Record a customer deposit. Debit cash, credit deposits-control."""
        entry = JournalEntry(
            id=str(uuid.uuid4()),
            timestamp=timestamp,
            description=f"Deposit {amount:.4f}",
            debit_account=self._cash_id,
            credit_account=self._deposits_id,
            amount=amount,
            tags=("deposit",),
        )
        return self._ledger.post(entry)

    def withdraw(self, amount: float, timestamp: float) -> JournalEntry:
        """Record a customer withdrawal. Debit deposits-control, credit cash."""
        if amount > self.balance():
            raise ValueError(
                f"Insufficient balance: have {self.balance():.4f}, requested {amount:.4f}"
            )
        entry = JournalEntry(
            id=str(uuid.uuid4()),
            timestamp=timestamp,
            description=f"Withdrawal {amount:.4f}",
            debit_account=self._deposits_id,
            credit_account=self._cash_id,
            amount=amount,
            tags=("withdrawal",),
        )
        return self._ledger.post(entry)

    def accrue_interest(self, timestamp: float) -> JournalEntry | None:
        """Compute and post interest since last_posted.

        Returns the accrual JournalEntry, or None if no period has elapsed
        or balance is below min_balance.

        period_rate = annual_rate / periods_per_year(posting_period)

        For ANNIVERSARY_MONTHLY the period boundary is anchored to
        opened_at, not the calendar month.

        When multiple periods have elapsed (e.g. accrue_interest called
        infrequently), the interest is compounded (or summed) over all
        elapsed periods before posting a single pair of entries.
        """
        n = _periods_elapsed(
            self._product.posting_period,
            self._last_posted,
            timestamp,
            self._opened_at,
        )
        if n <= 0:
            return None

        bal = self.balance()
        if bal <= self._product.min_balance:
            self._last_posted = timestamp
            return None

        period_rate = self._product.nominal_rate / PERIODS_PER_YEAR[self._product.posting_period.value]

        if self._product.compounding:
            # compound: balance * ((1 + r)^n - 1)
            interest = bal * ((1.0 + period_rate) ** n - 1.0)
        else:
            # simple: balance * r * n
            interest = bal * period_rate * n

        self._total_interest += interest

        # Post accrual: debit interest expense, credit interest payable
        accrual = accrual_entry(
            self._ledger,
            timestamp,
            interest,
            self._int_exp_id,
            self._int_pay_id,
            description=f"interest accrual ({n} period(s))",
        )

        # Settle immediately: debit interest payable, credit deposits control
        # (capitalises interest into the deposit balance for compound accounts,
        #  or credits it for simple-interest accounts — behaviour is identical)
        settlement_entry(
            self._ledger,
            timestamp,
            interest,
            self._int_pay_id,
            self._deposits_id,
            description=f"interest settlement ({n} period(s))",
        )

        self._last_posted = timestamp
        return accrual

    def balance(self) -> float:
        """Current deposit balance (customer-facing)."""
        # deposits_control is a LIABILITY; its internal .balance grows on credit
        return self._ledger.get_account(self._deposits_id).balance

    def accrued_interest(self) -> float:
        """Total interest credited to this account since opening."""
        return self._total_interest

    def effective_rate(self) -> float:
        """APY (Annual Percentage Yield) accounting for compounding frequency."""
        pp = self._product.posting_period
        n = PERIODS_PER_YEAR[pp.value]
        r = self._product.nominal_rate
        if self._product.compounding:
            return (1.0 + r / n) ** n - 1.0
        return r  # simple interest: APY == nominal rate

    def statement(self, since: float = 0.0) -> list[JournalEntry]:
        """All journal entries touching this account's deposits control ledger."""
        return self._ledger.statement(self._deposits_id, since=since)
