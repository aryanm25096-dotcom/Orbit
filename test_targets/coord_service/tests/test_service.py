import pytest
from src.coord_service.models import Account, InsufficientFundsError, InvalidAmountError
from src.coord_service.ledger import Ledger
from src.coord_service.service import PaymentService


@pytest.fixture
def service():
    ledger = Ledger()
    acc_a = Account(id="acc_a", balance=100.0)
    acc_b = Account(id="acc_b", balance=50.0)
    ledger.add_account(acc_a)
    ledger.add_account(acc_b)
    return PaymentService(ledger=ledger, fee_percent=0.02)


def test_deposit(service):
    acc = service.deposit("acc_a", 25.0)
    assert acc.balance == 125.0


def test_withdraw(service):
    acc = service.withdraw("acc_a", 40.0)
    assert acc.balance == 60.0


def test_withdraw_insufficient(service):
    with pytest.raises(InsufficientFundsError):
        service.withdraw("acc_a", 150.0)
