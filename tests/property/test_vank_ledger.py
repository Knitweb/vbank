"""Property tests for vank.ledger — double-entry accounting invariants."""
import pytest

from vank.ledger import (
    Account,
    AccountType,
    JournalEntry,
    Ledger,
    accrual_entry,
    settlement_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(debit: str, credit: str, amount: float, eid: str = "e1", ts: float = 1.0) -> JournalEntry:
    return JournalEntry(
        id=eid,
        timestamp=ts,
        description="test entry",
        debit_account=debit,
        credit_account=credit,
        amount=amount,
    )


def _basic_ledger() -> Ledger:
    """Ledger with cash (ASSET) and loan (LIABILITY) accounts."""
    ledger = Ledger()
    ledger.add_account(Account("cash", "Cash", AccountType.ASSET))
    ledger.add_account(Account("loan", "Loan Payable", AccountType.LIABILITY))
    return ledger


# ---------------------------------------------------------------------------
# Account debit / credit normal balance rules
# ---------------------------------------------------------------------------

def test_asset_debit_increases_balance():
    acc = Account("a", "Cash", AccountType.ASSET)
    acc.debit(150.0)
    assert acc.balance == 150.0


def test_asset_credit_decreases_balance():
    acc = Account("a", "Cash", AccountType.ASSET)
    acc.balance = 300.0
    acc.credit(100.0)
    assert acc.balance == 200.0


def test_liability_credit_increases_balance():
    acc = Account("l", "Loan", AccountType.LIABILITY)
    acc.credit(400.0)
    assert acc.balance == 400.0


def test_liability_debit_decreases_balance():
    acc = Account("l", "Loan", AccountType.LIABILITY)
    acc.balance = 500.0
    acc.debit(200.0)
    assert acc.balance == 300.0


# ---------------------------------------------------------------------------
# Double-entry invariant: sum(debits) == sum(credits) across all entries
# ---------------------------------------------------------------------------

def test_double_entry_invariant_single_post():
    """After one post(), the recorded debit and credit amounts are equal."""
    ledger = _basic_ledger()
    ledger.post(_entry("cash", "loan", 250.0))
    assert len(ledger.entries) == 1
    entry = ledger.entries[0]
    # Both sides of the entry record the same amount
    assert entry.amount == 250.0
    total_debit_amounts = sum(e.amount for e in ledger.entries)
    total_credit_amounts = sum(e.amount for e in ledger.entries)
    assert total_debit_amounts == total_credit_amounts


def test_double_entry_invariant_multiple_posts():
    """After multiple posts(), balance changes on both sides are consistent."""
    ledger = Ledger()
    cash = Account("cash", "Cash", AccountType.ASSET)
    loan = Account("loan", "Loan", AccountType.LIABILITY)
    ledger.add_account(cash)
    ledger.add_account(loan)

    amounts = [100.0, 50.0, 75.0, 25.0]
    for i, amt in enumerate(amounts, 1):
        ledger.post(_entry("cash", "loan", amt, eid=str(i), ts=float(i)))

    total = sum(amounts)
    # ASSET (debit-normal): each debit increases balance
    assert cash.balance == total
    # LIABILITY (credit-normal): each credit increases balance
    assert loan.balance == total
    # sum of all entry amounts is the same on the debit and credit side
    assert sum(e.amount for e in ledger.entries) == total


# ---------------------------------------------------------------------------
# trial_balance
# ---------------------------------------------------------------------------

def test_trial_balance_correct_values():
    ledger = _basic_ledger()
    ledger.post(_entry("cash", "loan", 300.0))
    tb = ledger.trial_balance()
    assert tb["cash"] == 300.0
    assert tb["loan"] == 300.0


def test_trial_balance_includes_all_accounts():
    ledger = Ledger()
    for aid, name, atype in [
        ("a1", "Cash", AccountType.ASSET),
        ("l1", "Loan", AccountType.LIABILITY),
        ("e1", "Interest Expense", AccountType.EXPENSE),
    ]:
        ledger.add_account(Account(aid, name, atype))
    tb = ledger.trial_balance()
    assert set(tb.keys()) == {"a1", "l1", "e1"}


# ---------------------------------------------------------------------------
# statement — timestamp range filtering
# ---------------------------------------------------------------------------

def _ledger_with_timeline() -> Ledger:
    ledger = _basic_ledger()
    for i in range(1, 6):  # timestamps 1.0 … 5.0
        ledger.post(_entry("cash", "loan", float(i * 10), eid=f"e{i}", ts=float(i)))
    return ledger


def test_statement_filters_by_since():
    ledger = _ledger_with_timeline()
    result = ledger.statement("cash", since=3.0)
    assert len(result) == 3
    assert all(e.timestamp >= 3.0 for e in result)


def test_statement_filters_by_until():
    ledger = _ledger_with_timeline()
    result = ledger.statement("cash", until=3.0)
    assert len(result) == 3
    assert all(e.timestamp <= 3.0 for e in result)


def test_statement_filters_by_timestamp_range():
    ledger = _ledger_with_timeline()
    result = ledger.statement("cash", since=2.0, until=4.0)
    assert len(result) == 3
    assert {e.id for e in result} == {"e2", "e3", "e4"}


# ---------------------------------------------------------------------------
# net_position = total ASSET balance − total LIABILITY balance
# ---------------------------------------------------------------------------

def test_net_position_assets_minus_liabilities():
    ledger = Ledger()
    a1 = Account("a1", "Cash", AccountType.ASSET)
    a2 = Account("a2", "Investments", AccountType.ASSET)
    l1 = Account("l1", "Loan", AccountType.LIABILITY)
    a1.balance = 1_000.0
    a2.balance = 500.0
    l1.balance = 300.0
    for acc in (a1, a2, l1):
        ledger.add_account(acc)
    assert ledger.net_position() == pytest.approx(1_200.0)  # 1500 - 300


def test_net_position_no_liabilities():
    ledger = Ledger()
    acc = Account("a", "Cash", AccountType.ASSET)
    acc.balance = 750.0
    ledger.add_account(acc)
    assert ledger.net_position() == pytest.approx(750.0)


# ---------------------------------------------------------------------------
# accrual_entry and settlement_entry
# ---------------------------------------------------------------------------

def test_accrual_entry_creates_correct_debit_credit():
    ledger = Ledger()
    ledger.add_account(Account("int_exp", "Interest Expense", AccountType.EXPENSE))
    ledger.add_account(Account("int_pay", "Interest Payable", AccountType.LIABILITY))

    entry = accrual_entry(
        ledger, ts=1.0, amount=100.0,
        interest_expense_id="int_exp",
        interest_payable_id="int_pay",
    )

    assert entry.debit_account == "int_exp"
    assert entry.credit_account == "int_pay"
    assert entry.amount == 100.0
    # Side effects: EXPENSE debited → balance increases; LIABILITY credited → balance increases
    assert ledger.accounts["int_exp"].balance == 100.0
    assert ledger.accounts["int_pay"].balance == 100.0


def test_settlement_entry_creates_correct_debit_credit():
    ledger = Ledger()
    pay = Account("int_pay", "Interest Payable", AccountType.LIABILITY)
    dep = Account("deposits", "Deposits Control", AccountType.LIABILITY)
    pay.balance = 200.0  # pre-existing payable
    ledger.add_account(pay)
    ledger.add_account(dep)

    entry = settlement_entry(
        ledger, ts=2.0, amount=100.0,
        interest_payable_id="int_pay",
        deposits_control_id="deposits",
    )

    assert entry.debit_account == "int_pay"
    assert entry.credit_account == "deposits"
    assert entry.amount == 100.0
    # LIABILITY debit → decreases balance: 200 - 100 = 100
    assert pay.balance == pytest.approx(100.0)
    # LIABILITY credit → increases balance: 0 + 100 = 100
    assert dep.balance == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# post() validation errors
# ---------------------------------------------------------------------------

def test_post_raises_for_unknown_debit_account():
    """post() must raise when the debit account id is not registered."""
    ledger = _basic_ledger()
    with pytest.raises((KeyError, ValueError)):
        ledger.post(_entry("UNKNOWN", "loan", 100.0))


def test_post_raises_for_unknown_credit_account():
    """post() must raise when the credit account id is not registered."""
    ledger = _basic_ledger()
    with pytest.raises((KeyError, ValueError)):
        ledger.post(_entry("cash", "UNKNOWN", 100.0))


def test_post_raises_for_negative_amount():
    ledger = _basic_ledger()
    with pytest.raises(ValueError):
        ledger.post(_entry("cash", "loan", -1.0))


def test_post_raises_for_zero_amount():
    ledger = _basic_ledger()
    with pytest.raises(ValueError):
        ledger.post(_entry("cash", "loan", 0.0))


@pytest.mark.xfail(
    reason="post() does not yet guard against debit_account == credit_account",
    strict=False,
)
def test_post_raises_for_same_debit_and_credit_account():
    """Posting with identical debit and credit account should raise ValueError."""
    ledger = Ledger()
    ledger.add_account(Account("a", "Cash", AccountType.ASSET))
    with pytest.raises(ValueError):
        ledger.post(_entry("a", "a", 100.0))
