"""Double-entry accounting ledger for the vank P2P finance system (float-friendly analytics layer)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

__all__ = [
    "AccountType",
    "Account",
    "JournalEntry",
    "Ledger",
    "accrual_entry",
    "settlement_entry",
]


class AccountType(Enum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    REVENUE = "revenue"
    EXPENSE = "expense"


_DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE}


@dataclass
class Account:
    id: str
    name: str
    account_type: AccountType
    balance: float = 0.0

    def debit(self, amount: float) -> None:
        if self.account_type in _DEBIT_NORMAL:
            self.balance += amount
        else:
            self.balance -= amount

    def credit(self, amount: float) -> None:
        if self.account_type in _DEBIT_NORMAL:
            self.balance -= amount
        else:
            self.balance += amount

    def normal_balance(self) -> float:
        return self.balance if self.account_type in _DEBIT_NORMAL else -self.balance


@dataclass
class JournalEntry:
    id: str
    timestamp: float
    description: str
    debit_account: str
    credit_account: str
    amount: float
    tags: tuple[str, ...] = ()


class Ledger:
    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}
        self.entries: list[JournalEntry] = []

    def add_account(self, account: Account) -> None:
        self.accounts[account.id] = account

    def get_account(self, id: str) -> Account:
        return self.accounts[id]

    def post(self, entry: JournalEntry) -> JournalEntry:
        if entry.debit_account not in self.accounts:
            raise KeyError(f"Unknown debit account: {entry.debit_account}")
        if entry.credit_account not in self.accounts:
            raise KeyError(f"Unknown credit account: {entry.credit_account}")
        if entry.amount <= 0:
            raise ValueError(f"Entry amount must be positive, got {entry.amount}")
        self.accounts[entry.debit_account].debit(entry.amount)
        self.accounts[entry.credit_account].credit(entry.amount)
        self.entries.append(entry)
        return entry

    def trial_balance(self) -> dict[str, float]:
        return {aid: acc.balance for aid, acc in self.accounts.items()}

    def statement(
        self,
        account_id: str,
        *,
        since: float = 0.0,
        until: float = float("inf"),
    ) -> list[JournalEntry]:
        return [
            e
            for e in self.entries
            if (e.debit_account == account_id or e.credit_account == account_id)
            and since <= e.timestamp <= until
        ]

    def net_position(self) -> float:
        assets = sum(
            acc.balance
            for acc in self.accounts.values()
            if acc.account_type is AccountType.ASSET
        )
        liabilities = sum(
            acc.balance
            for acc in self.accounts.values()
            if acc.account_type is AccountType.LIABILITY
        )
        return assets - liabilities


def accrual_entry(
    ledger: Ledger,
    ts: float,
    amount: float,
    interest_expense_id: str,
    interest_payable_id: str,
    description: str = "accrual",
) -> JournalEntry:
    import uuid
    entry = JournalEntry(
        id=str(uuid.uuid4()),
        timestamp=ts,
        description=description,
        debit_account=interest_expense_id,
        credit_account=interest_payable_id,
        amount=amount,
    )
    return ledger.post(entry)


def settlement_entry(
    ledger: Ledger,
    ts: float,
    amount: float,
    interest_payable_id: str,
    deposits_control_id: str,
    description: str = "settlement",
) -> JournalEntry:
    import uuid
    entry = JournalEntry(
        id=str(uuid.uuid4()),
        timestamp=ts,
        description=description,
        debit_account=interest_payable_id,
        credit_account=deposits_control_id,
        amount=amount,
    )
    return ledger.post(entry)
