"""Property tests for vank.loan — schedules, EMI formula, market matching, repayment."""

import pytest

from vank.loan import (
    ActiveLoan,
    InterestMethod,
    LoanMarket,
    LoanOffer,
    LoanRequest,
    LoanSchedule,
    RepaymentStatus,
    Installment,
)
from vank.ledger import Account, AccountType, Ledger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T0 = 1_700_000_000.0  # arbitrary unix epoch anchor


def _schedule(
    principal: float = 1000.0,
    annual_rate: float = 0.12,
    n: int = 12,
    method: InterestMethod = InterestMethod.DECLINING_BALANCE,
) -> LoanSchedule:
    return LoanSchedule(
        principal=principal,
        annual_rate=annual_rate,
        num_installments=n,
        first_due=_T0,
        method=method,
    )


def _active_loan(
    principal: float = 1000.0,
    annual_rate: float = 0.12,
    n: int = 12,
    method: InterestMethod = InterestMethod.DECLINING_BALANCE,
) -> ActiveLoan:
    schedule = _schedule(principal=principal, annual_rate=annual_rate, n=n, method=method)
    ledger = Ledger()
    return ActiveLoan(
        id="loan1",
        borrower="alice",
        lender="bob",
        schedule=schedule,
        ledger=ledger,
    )


# ---------------------------------------------------------------------------
# LoanSchedule — DECLINING_BALANCE
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_declining_balance_emi_constant():
    """All non-last installments must have the same total (EMI)."""
    sched = _schedule(n=12)
    totals = [inst.total for inst in sched.installments[:-1]]
    assert len(totals) >= 1
    assert max(totals) - min(totals) < 1e-6, "EMI is not constant across installments"


@pytest.mark.property
def test_declining_balance_has_n_installments():
    n = 6
    sched = _schedule(n=n)
    assert len(sched.installments) == n


@pytest.mark.property
def test_declining_balance_interest_decreases():
    """Under declining balance, each period's interest is less than the previous."""
    sched = _schedule(n=12)
    interests = [inst.interest for inst in sched.installments]
    for i in range(1, len(interests)):
        assert interests[i] < interests[i - 1], (
            f"Interest did not decrease at period {i}: {interests[i - 1]} -> {interests[i]}"
        )


@pytest.mark.property
def test_declining_balance_total_interest_positive():
    sched = _schedule(n=12)
    assert sched.total_interest() > 0


# ---------------------------------------------------------------------------
# LoanSchedule — FLAT
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_flat_interest_same_each_period():
    """All installments carry identical interest amounts under FLAT method."""
    sched = _schedule(n=6, method=InterestMethod.FLAT)
    interests = [inst.interest for inst in sched.installments]
    assert max(interests) - min(interests) < 1e-10, "Flat interest varies across periods"


@pytest.mark.property
def test_flat_principal_same_each_period():
    """Principal payment is identical in every installment under FLAT."""
    sched = _schedule(n=6, method=InterestMethod.FLAT)
    principals = [inst.principal for inst in sched.installments]
    assert max(principals) - min(principals) < 1e-10, "Flat principal varies across periods"


@pytest.mark.property
def test_flat_total_interest_positive():
    sched = _schedule(n=6, method=InterestMethod.FLAT)
    assert sched.total_interest() > 0


# ---------------------------------------------------------------------------
# EMI formula correctness
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_emi_formula_known_value():
    """P=1000, 12% annual, 12 monthly installments → EMI ≈ 88.85.

    Standard formula: P * r * (1+r)^n / ((1+r)^n - 1)
    r = 12% / year * 30/365 per period ≈ 0.009863...
    """
    sched = LoanSchedule(
        principal=1000.0,
        annual_rate=0.12,
        num_installments=12,
        first_due=_T0,
        installment_interval_days=30,
        method=InterestMethod.DECLINING_BALANCE,
    )
    emi = sched.emi()
    assert 88.0 < emi < 90.0, f"EMI {emi:.4f} not in expected range [88, 90]"


@pytest.mark.property
def test_emi_zero_rate_equals_principal_over_n():
    """When rate is 0, EMI degenerates to P/n."""
    sched = LoanSchedule(
        principal=1200.0,
        annual_rate=0.0,
        num_installments=12,
        first_due=_T0,
    )
    assert abs(sched.emi() - 100.0) < 1e-9


# ---------------------------------------------------------------------------
# amortization_table
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_amortization_table_has_n_rows():
    n = 10
    sched = _schedule(n=n)
    table = sched.amortization_table()
    assert len(table) == n


@pytest.mark.property
def test_amortization_table_balance_reaches_zero():
    """Final balance in amortization table must be (approx) zero."""
    sched = _schedule(n=12)
    table = sched.amortization_table()
    final_balance = table[-1]["balance"]
    assert final_balance < 1e-6, f"Final balance not ~0: {final_balance}"


@pytest.mark.property
def test_amortization_table_balance_monotonically_decreasing():
    sched = _schedule(n=12)
    table = sched.amortization_table()
    balances = [row["balance"] for row in table]
    for i in range(1, len(balances)):
        assert balances[i] <= balances[i - 1] + 1e-9, (
            f"Balance increased at period {i}: {balances[i - 1]} -> {balances[i]}"
        )


# ---------------------------------------------------------------------------
# LoanMarket matching
# ---------------------------------------------------------------------------


def _offer(id: str, rate: float, amount: float = 5000.0, term: int = 365) -> LoanOffer:
    return LoanOffer(
        id=id,
        lender="bank",
        amount=amount,
        annual_rate=rate,
        max_term_days=term,
        created_at=_T0,
    )


def _request(max_rate: float = 0.12, amount: float = 1000.0, term: int = 180) -> LoanRequest:
    return LoanRequest(
        id="req1",
        borrower="alice",
        amount=amount,
        max_rate=max_rate,
        term_days=term,
        purpose="working capital",
        created_at=_T0,
    )


@pytest.mark.property
def test_market_match_returns_cheapest():
    """Among multiple qualifying offers, cheapest (lowest rate) wins."""
    market = LoanMarket()
    market.post_offer(_offer("o1", rate=0.10))
    market.post_offer(_offer("o2", rate=0.08))
    market.post_offer(_offer("o3", rate=0.09))

    req = _request(max_rate=0.12)
    result = market.match(req)
    assert result is not None
    assert result.id == "o2"


@pytest.mark.property
def test_market_match_none_when_all_too_expensive():
    """No offer qualifies when all rates exceed max_rate."""
    market = LoanMarket()
    market.post_offer(_offer("o1", rate=0.20))
    market.post_offer(_offer("o2", rate=0.18))

    req = _request(max_rate=0.10)
    assert market.match(req) is None


@pytest.mark.property
def test_market_match_none_when_amount_too_small():
    """Offer must cover the full requested amount."""
    market = LoanMarket()
    market.post_offer(_offer("o1", rate=0.05, amount=500.0))  # not enough

    req = _request(amount=1000.0, max_rate=0.12)
    assert market.match(req) is None


@pytest.mark.property
def test_market_match_none_when_term_too_short():
    """Offer term must accommodate the requested loan term."""
    market = LoanMarket()
    market.post_offer(_offer("o1", rate=0.05, term=60))  # offer max 60d

    req = _request(term=180)
    assert market.match(req) is None


@pytest.mark.property
def test_market_match_ignores_inactive_offers():
    """Inactive offers must never be matched."""
    market = LoanMarket()
    cheap = _offer("o1", rate=0.05)
    cheap.active = False
    market.post_offer(cheap)
    market.post_offer(_offer("o2", rate=0.10))

    req = _request(max_rate=0.12)
    result = market.match(req)
    assert result is not None
    assert result.id == "o2"


# ---------------------------------------------------------------------------
# ActiveLoan — repayment
# ---------------------------------------------------------------------------


@pytest.mark.property
def test_repay_past_due_allocated_first():
    """A payment applied after the first due date should clear the past-due
    installment before touching the current one."""
    loan = _active_loan(n=2)
    installments = loan.schedule.installments
    first_due = installments[0].due_date
    second_due = installments[1].due_date

    # Pay just enough to cover first installment, well past its due date
    payment = installments[0].total
    loan.repay(payment, timestamp=second_due + 1)

    assert installments[0].is_paid, "Past-due installment should be paid first"
    assert not installments[1].is_paid, "Current installment should still be outstanding"


@pytest.mark.property
def test_full_repayment_clears_outstanding_principal():
    """Paying all installments in full must zero out outstanding_principal."""
    loan = _active_loan(n=6)
    total_due = sum(inst.total for inst in loan.schedule.installments)
    # Pay far in the future so all are considered past-due / current
    loan.repay(total_due + 1.0, timestamp=_T0 + 86400 * 365)

    assert loan.outstanding_principal() < 1e-6, (
        f"outstanding_principal should be ~0, got {loan.outstanding_principal()}"
    )


@pytest.mark.property
def test_status_past_due_when_due_date_passed():
    """Loan is PAST_DUE when an installment's due date has passed and it's not paid."""
    loan = _active_loan(n=3)
    first_due = loan.schedule.installments[0].due_date
    # Don't repay anything; check status after first due date
    assert loan.status(as_of=first_due + 1) is RepaymentStatus.PAST_DUE


@pytest.mark.property
def test_status_paid_after_full_repayment():
    """Status is PAID once all installments are paid."""
    loan = _active_loan(n=3)
    total = sum(inst.total for inst in loan.schedule.installments)
    loan.repay(total + 1.0, timestamp=_T0 + 86400 * 365)

    assert loan.status(as_of=_T0 + 86400 * 365) is RepaymentStatus.PAID


@pytest.mark.property
def test_repay_posts_journal_entries():
    """repay() must generate at least one journal entry per payment applied."""
    loan = _active_loan(n=2)
    first_inst = loan.schedule.installments[0]
    entries = loan.repay(first_inst.total, timestamp=_T0 + 86400 * 400)
    assert len(entries) > 0, "Expected journal entries after repayment"
