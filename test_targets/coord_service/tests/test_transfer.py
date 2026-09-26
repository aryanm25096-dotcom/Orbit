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
    return PaymentService(ledger=ledger, fee_percent=0.05)


def test_transfer_success(service):
    # transfer 40 from acc_a to acc_b. Fee is 5% = 2.0. Total debit from acc_a = 42.0.
    # acc_a new balance = 58.0. acc_b new balance = 90.0.
    tx = service.transfer("acc_a", "acc_b", 40.0, fx_rate=1.0)
    assert tx.amount == 40.0
    assert tx.fee == 2.0
    assert tx.net_credited == 40.0
    assert service.ledger.get_account("acc_a").balance == 58.0
    assert service.ledger.get_account("acc_b").balance == 90.0
    assert len(service.ledger.get_history("acc_a")) == 1


def test_transfer_with_fx(service):
    # transfer 20 with fx_rate 1.5 -> credit 30.0. Fee is 5% = 1.0. Total debit = 21.0.
    tx = service.transfer("acc_a", "acc_b", 20.0, fx_rate=1.5)
    assert tx.net_credited == 30.0
    assert tx.fee == 1.0
    assert service.ledger.get_account("acc_a").balance == 79.0
    assert service.ledger.get_account("acc_b").balance == 80.0


def test_transfer_insufficient_balance_for_amount_plus_fee(service):
    # balance is 100. transfer 98.0 -> fee is 4.90 -> total 102.90 -> raises InsufficientFundsError
    with pytest.raises(InsufficientFundsError):
        service.transfer("acc_a", "acc_b", 98.0)
    # balances must remain unchanged
    assert service.ledger.get_account("acc_a").balance == 100.0


def test_transfer_negative_amount_raises(service):
    with pytest.raises(InvalidAmountError):
        service.transfer("acc_a", "acc_b", -10.0)
