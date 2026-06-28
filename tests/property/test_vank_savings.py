"""Property tests for vank.savings module."""

from __future__ import annotations

import pytest

from vank.savings import (
    PERIODS_PER_YEAR,
    PostingPeriod,
    SavingsAccount,
    SavingsProduct,
)
from vank.ledger import Account, AccountType, Ledger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_TS: float = 0.0        # 1970-01-01 00:00:00 UTC (epoch)
ONE_DAY: float = 86_400.0
# 35 days: safely crosses the Jan→Feb calendar boundary from epoch
ONE_MONTH: float = 35 * ONE_DAY
# 95 days: crosses a quarterly boundary (Q1→Q2 from Jan)
ONE_QUARTER: float = 95 * ONE_DAY


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------

def _make(
    nominal_rate: float = 0.12,
    posting_period: PostingPeriod = PostingPeriod.MONTHLY,
    compounding: bool = True,
    min_balance: float = 0.0,
) -> tuple[Ledger, SavingsProduct, SavingsAccount]:
    """Return (ledger, product, account).

    SavingsAccount.__init__ adds 4 accounts to the ledger:
      - sav_acc1_cash          (ASSET)
      - sav_acc1_deposits      (LIABILITY  — deposits control)
      - sav_acc1_int_exp       (EXPENSE    — interest expense)
      - sav_acc1_int_pay       (LIABILITY  — interest payable)
    """
    ledger = Ledger()
    product = SavingsProduct(
        id="prod1",
        name="Test Product",
        nominal_rate=nominal_rate,
        posting_period=posting_period,
        compounding=compounding,
        min_balance=min_balance,
    )
    account = SavingsAccount(
        id="acc1",
        product=product,
        opened_at=BASE_TS,
        ledger=ledger,
    )
    return ledger, product, account


# ---------------------------------------------------------------------------
# Tests — basic balance behaviour
# ---------------------------------------------------------------------------

def test_balance_zero_with_no_deposits():
    """Initial balance is 0 before any deposits."""
    _, _, acc = _make()
    assert acc.balance() == 0.0


def test_deposit_increases_balance():
    _, _, acc = _make()
    acc.deposit(1_000.0, BASE_TS + 1.0)
    assert acc.balance() == pytest.approx(1_000.0)


def test_deposit_multiple_accumulates():
    _, _, acc = _make()
    acc.deposit(500.0, BASE_TS + 1.0)
    acc.deposit(300.0, BASE_TS + 2.0)
    assert acc.balance() == pytest.approx(800.0)


def test_withdraw_decreases_balance():
    _, _, acc = _make()
    acc.deposit(1_000.0, BASE_TS + 1.0)
    acc.withdraw(400.0, BASE_TS + 2.0)
    assert acc.balance() == pytest.approx(600.0)


def test_withdraw_raises_if_insufficient():
    _, _, acc = _make()
    acc.deposit(100.0, BASE_TS + 1.0)
    with pytest.raises(ValueError):
        acc.withdraw(500.0, BASE_TS + 2.0)


# ---------------------------------------------------------------------------
# Tests — interest accrual
# ---------------------------------------------------------------------------

def test_accrue_interest_posts_two_leg_entry():
    """accrue_interest returns a JournalEntry whose debit and credit accounts
    both exist in the ledger (i.e. a complete two-leg entry was posted)."""
    ledger, _, acc = _make()
    acc.deposit(1_000.0, BASE_TS + 1.0)
    entry = acc.accrue_interest(BASE_TS + ONE_MONTH)
    assert entry is not None
    assert entry.debit_account in ledger.accounts
    assert entry.credit_account in ledger.accounts


def test_accrue_interest_account_types():
    """Accrual entry: debit account must be EXPENSE, credit account LIABILITY."""
    ledger, _, acc = _make()
    acc.deposit(1_000.0, BASE_TS + 1.0)
    entry = acc.accrue_interest(BASE_TS + ONE_MONTH)
    assert entry is not None
    debit_acct = ledger.get_account(entry.debit_account)
    credit_acct = ledger.get_account(entry.credit_account)
    assert debit_acct.account_type is AccountType.EXPENSE
    assert credit_acct.account_type is AccountType.LIABILITY


def test_after_accrual_accrued_interest_positive():
    _, _, acc = _make()
    acc.deposit(1_000.0, BASE_TS + 1.0)
    acc.accrue_interest(BASE_TS + ONE_MONTH)
    assert acc.accrued_interest() > 0.0


# ---------------------------------------------------------------------------
# Tests — rate arithmetic
# ---------------------------------------------------------------------------

def test_monthly_interest_rate():
    """MONTHLY posting_period means 12 periods/year → period rate = annual/12."""
    nominal = 0.12
    assert PERIODS_PER_YEAR[PostingPeriod.MONTHLY.value] == 12
    period_rate = nominal / PERIODS_PER_YEAR[PostingPeriod.MONTHLY.value]
    assert period_rate == pytest.approx(0.01)


def test_effective_rate_greater_than_nominal_when_compounding():
    _, _, acc = _make(nominal_rate=0.12, posting_period=PostingPeriod.MONTHLY, compounding=True)
    assert acc.effective_rate() > 0.12


def test_effective_rate_equals_nominal_when_not_compounding():
    _, _, acc = _make(nominal_rate=0.12, compounding=False)
    assert acc.effective_rate() == pytest.approx(0.12)


# ---------------------------------------------------------------------------
# Tests — PostingPeriod periods-per-year
# ---------------------------------------------------------------------------

def test_daily_posting_period_uses_365():
    assert PERIODS_PER_YEAR[PostingPeriod.DAILY.value] == 365


def test_quarterly_posting_period_uses_4():
    assert PERIODS_PER_YEAR[PostingPeriod.QUARTERLY.value] == 4


# ---------------------------------------------------------------------------
# Tests — statement
# ---------------------------------------------------------------------------

def test_statement_returns_entries_since_timestamp():
    """statement(since=t) excludes entries before t."""
    _, _, acc = _make()
    t1 = BASE_TS + 1.0
    t2 = BASE_TS + ONE_MONTH + 1.0
    acc.deposit(1_000.0, t1)
    acc.deposit(200.0, t2)

    late_entries = acc.statement(since=t2)
    assert all(e.timestamp >= t2 for e in late_entries)
    assert len(late_entries) >= 1

    all_entries = acc.statement(since=0.0)
    assert len(all_entries) >= 2
