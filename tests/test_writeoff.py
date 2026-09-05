"""核销引擎 + 红冲/作废联动 + 修改金额校验。"""
import pytest

from db import fetch_one, insert_row
from writeoff import (
    auto_writeoff,
    available_invoice_amount,
    cancel_writeoff,
    manual_writeoff,
    red_lettered_amount,
    validate_invoice_amount,
    validate_payment_amount,
    validate_red_letter,
    validate_status_change,
    validate_writeoff,
    writeoff_status,
    written_off_amount,
)


# ---------------------------------------------------------------------------
# 红冲联动：可核销金额自动减少
# ---------------------------------------------------------------------------
def test_red_letter_reduces_available(seeded, add_invoice):
    add_invoice("FP101", "R0001", 40000, status="红冲", related="FP001")
    assert red_lettered_amount(seeded, "FP001") == 40000.0
    assert available_invoice_amount(seeded, "FP001") == 60000.0  # 10 万 − 4 万


def test_multiple_red_letters_sum(seeded, add_invoice):
    add_invoice("FP101", "R0001", 30000, status="红冲", related="FP001")
    add_invoice("FP102", "R0002", 20000, status="红冲", related="FP001")
    assert available_invoice_amount(seeded, "FP001") == 50000.0


def test_red_letter_over_available_blocked(seeded):
    assert validate_red_letter(seeded, "FP001", 100001) is not None


def test_red_letter_must_reference_existing_blue(seeded):
    assert "不存在" in validate_red_letter(seeded, "FP999", 1000)


def test_red_letter_cannot_target_red(seeded, add_invoice):
    add_invoice("FP101", "R0001", 40000, status="红冲", related="FP001")
    assert "不能被红冲" in validate_red_letter(seeded, "FP101", 1000)


# ---------------------------------------------------------------------------
# 红字 / 作废 发票不可核销
# ---------------------------------------------------------------------------
def test_red_invoice_not_writeoffable(seeded, add_invoice):
    add_invoice("FP101", "R0001", 40000, status="红冲", related="FP001")
    assert available_invoice_amount(seeded, "FP101") == 0.0
    assert validate_writeoff(seeded, "HK001", "FP101", 1000) is not None


def test_void_invoice_not_writeoffable(seeded, add_invoice):
    add_invoice("FP201", "V0001", 50000, status="作废")
    assert available_invoice_amount(seeded, "FP201") == 0.0
    assert validate_writeoff(seeded, "HK001", "FP201", 1000) is not None


def test_void_blocked_when_written_off(seeded):
    manual_writeoff(seeded, "HK001", "FP001", 10000, "tester")
    assert validate_status_change(seeded, "FP001", "作废") is not None


def test_void_blocked_when_red_lettered(seeded, add_invoice):
    add_invoice("FP101", "R0001", 40000, status="红冲", related="FP001")
    assert validate_status_change(seeded, "FP001", "作废") is not None


def test_void_ok_when_clean(seeded, add_invoice):
    add_invoice("FP201", "V0001", 50000)
    assert validate_status_change(seeded, "FP201", "作废") is None


# ---------------------------------------------------------------------------
# 回款状态自动推导（不手填）
# ---------------------------------------------------------------------------
def test_payment_status_pending(seeded):
    assert writeoff_status(seeded, "HK001") == "待核销"


def test_payment_status_partial(seeded):
    manual_writeoff(seeded, "HK001", "FP001", 30000, "tester")
    assert writeoff_status(seeded, "HK001") == "部分核销"


def test_payment_status_full(seeded):
    manual_writeoff(seeded, "HK001", "FP001", 80000, "tester")
    assert writeoff_status(seeded, "HK001") == "已核销"


# ---------------------------------------------------------------------------
# 核销金额不可超限
# ---------------------------------------------------------------------------
def test_writeoff_cannot_exceed_invoice_available(seeded):
    with pytest.raises(ValueError):
        manual_writeoff(seeded, "HK001", "FP001", 100001, "tester")


def test_writeoff_cannot_exceed_payment_remaining(seeded):
    with pytest.raises(ValueError):
        manual_writeoff(seeded, "HK001", "FP001", 80001, "tester")


# ---------------------------------------------------------------------------
# 修改金额校验：已关联核销的金额不允许改小，需先解除核销
# ---------------------------------------------------------------------------
def test_invoice_amount_change_blocked_when_written(seeded):
    manual_writeoff(seeded, "HK001", "FP001", 40000, "tester")
    assert written_off_amount(seeded, "FP001") == 40000.0
    assert validate_invoice_amount(seeded, "FP001", 30000) is not None


def test_invoice_amount_change_blocked_by_red(seeded, add_invoice):
    add_invoice("FP101", "R0001", 60000, status="红冲", related="FP001")
    assert validate_invoice_amount(seeded, "FP001", 50000) is not None


def test_invoice_amount_change_ok_without_commitment(seeded):
    assert validate_invoice_amount(seeded, "FP001", 90000) is None


def test_payment_amount_change_blocked_when_written(seeded):
    manual_writeoff(seeded, "HK001", "FP001", 40000, "tester")
    assert validate_payment_amount(seeded, "HK001", 30000) is not None


def test_payment_amount_change_ok(seeded):
    assert validate_payment_amount(seeded, "HK001", 70000) is None


# ---------------------------------------------------------------------------
# 先进先出自动核销 + 撤销
# ---------------------------------------------------------------------------
def test_auto_writeoff_fifo(seeded, add_invoice):
    add_invoice("FP002", "A0002", 50000, date="2026-02-10")  # 开票晚于 FP001
    insert_row(seeded, "payments", {
        "code": "HK002", "pay_date": "2026-03-01", "pay_type": "收款",
        "partner_code": "KH001", "contract_code": "HT001",
        "amount": 120000.0, "pay_method": "银行转账", "remark": "",
    }, "tester")
    res = auto_writeoff(seeded, "HK002", "tester")
    assert [(r["invoice_code"], r["amount"]) for r in res] == [
        ("FP001", 100000.0), ("FP002", 20000.0),
    ]


def test_cancel_writeoff_restores_status(seeded):
    manual_writeoff(seeded, "HK001", "FP001", 40000, "tester")
    wid = fetch_one(seeded, "writeoffs", "payment_code", "HK001")["id"]
    cancel_writeoff(seeded, wid, "tester")
    assert writeoff_status(seeded, "HK001") == "待核销"
