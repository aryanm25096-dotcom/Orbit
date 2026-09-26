from __future__ import annotations
from dataclasses import dataclass, field
import uuid


class LedgerError(Exception):
    """Base error for ledger operations."""
    pass


class InsufficientFundsError(LedgerError):
    """Raised when an account lacks sufficient balance for debit + fees."""
    pass


class AccountNotFoundError(LedgerError):
    """Raised when an account id does not exist."""
    pass


class InvalidAmountError(LedgerError):
    """Raised when amount is zero or negative."""
    pass


@dataclass
class Account:
    id: str
    balance: float
    currency: str = "USD"


@dataclass
class TransactionRecord:
    tx_id: str
    from_id: str
    to_id: str
    amount: float
    fee: float
    net_credited: float
