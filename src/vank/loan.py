"""P2P loan matching and repayment engine for the vank finance system."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from vank.ledger import Account, AccountType, JournalEntry, Ledger

__all__ = [
    "InterestMethod",
    "RepaymentStatus",
    "Installment",
    "LoanSchedule",
    "LoanOffer",
    "LoanRequest",
    "LoanMarket",
    "ActiveLoan",
]


class InterestMethod(Enum):
    DECLINING_BALANCE = "declining_balance"  # EMI on remaining principal (Fineract progressive)
    FLAT = "flat"                            # interest on original principal throughout


class RepaymentStatus(Enum):
    CURRENT = "current"
    PAST_DUE = "past_due"
    PAID = "paid"
    DEFAULTED = "defaulted"


@dataclass
class Installment:
    due_date: float           # unix timestamp
    principal: float
    interest: float
    total: float
    paid_principal: float = 0.0
    paid_interest: float = 0.0
    status: RepaymentStatus = RepaymentStatus.CURRENT

    @property
    def outstanding(self) -> float:
        """Total remaining amount due on this installment."""
        return (self.principal - self.paid_principal) + (self.interest - self.paid_interest)

    @property
    def is_paid(self) -> bool:
        return self.paid_principal >= self.principal and self.paid_interest >= self.interest


class LoanSchedule:
    """Generate installment schedule (EMI or flat)."""

    def __init__(
        self,
        principal: float,
        annual_rate: float,
        num_installments: int,
        first_due: float,
        installment_interval_days: float = 30,
        method: InterestMethod = InterestMethod.DECLINING_BALANCE,
    ) -> None:
        self._principal = principal
        self._annual_rate = annual_rate
        self._num_installments = num_installments
        self._first_due = first_due
        self._interval_days = installment_interval_days
        self._method = method
        self._installments: list[Installment] = self._build()

    def _period_rate(self) -> float:
        """Periodic interest rate for one installment interval."""
        return self._annual_rate * self._interval_days / 365.0

    def _build(self) -> list[Installment]:
        n = self._num_installments
        interval_secs = self._interval_days * 86400.0
        installments: list[Installment] = []

        if self._method is InterestMethod.DECLINING_BALANCE:
            r = self._period_rate()
            emi = self.emi()
            balance = self._principal
            for i in range(n):
                due = self._first_due + i * interval_secs
                interest = balance * r
                if i == n - 1:
                    # Last installment: clear remaining balance
                    prin = balance
                    total = prin + interest
                else:
                    prin = emi - interest
                    total = emi
                installments.append(
                    Installment(due_date=due, principal=prin, interest=interest, total=total)
                )
                balance = max(0.0, balance - prin)

        else:  # FLAT
            per_interest = self._principal * self._annual_rate * (self._interval_days / 365.0)
            per_principal = self._principal / n
            total_per = per_principal + per_interest
            for i in range(n):
                due = self._first_due + i * interval_secs
                installments.append(
                    Installment(
                        due_date=due,
                        principal=per_principal,
                        interest=per_interest,
                        total=total_per,
                    )
                )

        return installments

    @property
    def installments(self) -> list[Installment]:
        return self._installments

    def emi(self) -> float:
        """Equal Monthly Installment amount (declining balance).

        EMI = P * r * (1+r)^n / ((1+r)^n - 1)
        Falls back to simple principal division when rate is zero.
        """
        r = self._period_rate()
        n = self._num_installments
        if r == 0.0:
            return self._principal / n
        factor = (1.0 + r) ** n
        return self._principal * r * factor / (factor - 1.0)

    def total_interest(self) -> float:
        return sum(inst.interest for inst in self._installments)

    def amortization_table(self) -> list[dict]:
        """Return [{period, principal, interest, balance}] for all installments."""
        rows = []
        balance = self._principal
        for i, inst in enumerate(self._installments, start=1):
            balance = max(0.0, balance - inst.principal)
            rows.append(
                {
                    "period": i,
                    "principal": inst.principal,
                    "interest": inst.interest,
                    "balance": balance,
                }
            )
        return rows


@dataclass
class LoanOffer:
    id: str
    lender: str
    amount: float
    annual_rate: float
    max_term_days: int
    created_at: float
    active: bool = True


@dataclass
class LoanRequest:
    id: str
    borrower: str
    amount: float
    max_rate: float
    term_days: int
    purpose: str
    created_at: float
    active: bool = True


class LoanMarket:
    """P2P loan matching: offers + requests, match on rate/amount/term."""

    def __init__(self) -> None:
        self._offers: list[LoanOffer] = []
        self._requests: list[LoanRequest] = []

    def post_offer(self, offer: LoanOffer) -> None:
        self._offers.append(offer)

    def post_request(self, request: LoanRequest) -> None:
        self._requests.append(request)

    def match(self, request: LoanRequest) -> LoanOffer | None:
        """Best match: rate <= request.max_rate, amount >= request.amount, active.

        Sorted by rate ascending (cheapest first).
        """
        candidates = [
            o
            for o in self._offers
            if o.active
            and o.annual_rate <= request.max_rate
            and o.amount >= request.amount
            and o.max_term_days >= request.term_days
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda o: o.annual_rate)

    def open_offers(self) -> list[LoanOffer]:
        return [o for o in self._offers if o.active]

    def open_requests(self) -> list[LoanRequest]:
        return [r for r in self._requests if r.active]


class ActiveLoan:
    """A matched, active loan with repayment tracking."""

    def __init__(
        self,
        id: str,
        borrower: str,
        lender: str,
        schedule: LoanSchedule,
        ledger: Ledger,
    ) -> None:
        self.id = id
        self.borrower = borrower
        self.lender = lender
        self.schedule = schedule
        self.ledger = ledger

        # Account IDs for this loan
        self._cash_id = f"{id}_cash"
        self._payable_id = f"{id}_loan_payable"
        self._interest_expense_id = f"{id}_interest_expense"

        # Register accounts in ledger if not already present
        if self._cash_id not in ledger.accounts:
            ledger.add_account(Account(self._cash_id, "Cash", AccountType.ASSET))
        if self._payable_id not in ledger.accounts:
            ledger.add_account(
                Account(self._payable_id, "Loan Payable", AccountType.LIABILITY)
            )
        if self._interest_expense_id not in ledger.accounts:
            ledger.add_account(
                Account(self._interest_expense_id, "Interest Expense", AccountType.EXPENSE)
            )

    def repay(self, amount: float, timestamp: float) -> list[JournalEntry]:
        """Apply a payment.

        Fineract allocation order: past_due first → current → future.
        Within each installment: interest first, then principal.
        Posts journal entries: debit Loan Payable / Interest Expense, credit Cash.
        """
        remaining = amount
        posted: list[JournalEntry] = []

        # Sort installments: past_due (due_date < timestamp) first, then by due_date
        installments = sorted(
            [inst for inst in self.schedule.installments if not inst.is_paid],
            key=lambda inst: (inst.due_date >= timestamp, inst.due_date),
        )

        for inst in installments:
            if remaining <= 0.0:
                break

            # Pay outstanding interest first
            interest_due = inst.interest - inst.paid_interest
            if interest_due > 0.0:
                pay_interest = min(interest_due, remaining)
                inst.paid_interest += pay_interest
                remaining -= pay_interest
                entry = JournalEntry(
                    id=str(uuid.uuid4()),
                    timestamp=timestamp,
                    description=f"Loan {self.id}: interest payment",
                    debit_account=self._interest_expense_id,
                    credit_account=self._cash_id,
                    amount=pay_interest,
                    tags=("loan_repayment", "interest", self.id),
                )
                self.ledger.post(entry)
                posted.append(entry)

            # Then pay outstanding principal
            principal_due = inst.principal - inst.paid_principal
            if principal_due > 0.0 and remaining > 0.0:
                pay_principal = min(principal_due, remaining)
                inst.paid_principal += pay_principal
                remaining -= pay_principal
                entry = JournalEntry(
                    id=str(uuid.uuid4()),
                    timestamp=timestamp,
                    description=f"Loan {self.id}: principal payment",
                    debit_account=self._payable_id,
                    credit_account=self._cash_id,
                    amount=pay_principal,
                    tags=("loan_repayment", "principal", self.id),
                )
                self.ledger.post(entry)
                posted.append(entry)

            # Update installment status
            if inst.is_paid:
                inst.status = RepaymentStatus.PAID
            elif inst.due_date < timestamp:
                inst.status = RepaymentStatus.PAST_DUE

        return posted

    def status(self, as_of: float) -> RepaymentStatus:
        """Compute repayment status as of a given timestamp."""
        installments = self.schedule.installments
        if all(inst.is_paid for inst in installments):
            return RepaymentStatus.PAID
        if any(not inst.is_paid and inst.due_date < as_of for inst in installments):
            return RepaymentStatus.PAST_DUE
        return RepaymentStatus.CURRENT

    def outstanding_principal(self) -> float:
        """Total principal remaining across all installments."""
        return sum(
            inst.principal - inst.paid_principal for inst in self.schedule.installments
        )

    def next_due(self) -> Optional[Installment]:
        """Return the earliest unpaid installment, or None if fully paid."""
        unpaid = [inst for inst in self.schedule.installments if not inst.is_paid]
        if not unpaid:
            return None
        return min(unpaid, key=lambda inst: inst.due_date)
