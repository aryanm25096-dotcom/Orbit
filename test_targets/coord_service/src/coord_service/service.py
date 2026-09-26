from __future__ import annotations
import uuid
from src.coord_service.ledger import Ledger
from src.coord_service.models import (
    Account,
    InsufficientFundsError,
    InvalidAmountError,
    TransactionRecord,
)


class PaymentService:
    def __init__(self, ledger: Ledger, fee_percent: float = 0.02) -> None:
        self.ledger = ledger
        self.fee_percent = fee_percent

    def deposit(self, account_id: str, amount: float) -> Account:
        if amount <= 0:
            raise InvalidAmountError("Deposit amount must be positive.")
        account = self.ledger.get_account(account_id)
        account.balance = round(account.balance + amount, 2)
        return account

    def withdraw(self, account_id: str, amount: float) -> Account:
        if amount <= 0:
            raise InvalidAmountError("Withdraw amount must be positive.")
        account = self.ledger.get_account(account_id)
        if account.balance < amount:
            raise InsufficientFundsError("Insufficient funds for withdrawal.")
        account.balance = round(account.balance - amount, 2)
        return account
