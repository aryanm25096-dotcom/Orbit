from __future__ import annotations
from typing import Dict, List
from src.coord_service.models import Account, AccountNotFoundError, TransactionRecord


class Ledger:
    def __init__(self) -> None:
        self.accounts: Dict[str, Account] = {}
        self.transactions: List[TransactionRecord] = []

    def add_account(self, account: Account) -> None:
        self.accounts[account.id] = account

    def get_account(self, account_id: str) -> Account:
        if account_id not in self.accounts:
            raise AccountNotFoundError(f"Account '{account_id}' not found.")
        return self.accounts[account_id]

    def record_transaction(self, record: TransactionRecord) -> None:
        self.transactions.append(record)

    def get_history(self, account_id: str) -> List[TransactionRecord]:
        return [
            tx for tx in self.transactions
            if tx.from_id == account_id or tx.to_id == account_id
        ]
