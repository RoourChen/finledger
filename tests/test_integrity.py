"""数据完整性：发票代码+号码唯一、核销（回款+发票）唯一、税号/合同/回款编号唯一。"""
import sqlite3

import pytest

from db import insert_row
from writeoff import manual_writeoff


def test_invoice_code_number_unique(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(seeded, "invoices", {
            "code": "FP999", "invoice_code": "3100", "invoice_number": "A0001",  # 与 FP001 相同
            "invoice_date": "2026-03-01", "buy_sell_type": "销项",
            "partner_code": "KH001", "contract_code": None,
            "amount_tax": 0, "amount_no_tax": 0, "tax_amount": 0,
            "tax_rate": "13%", "status": "正常", "related_invoice_code": "", "remark": "",
        }, "tester")


def test_writeoff_pair_unique(seeded):
    manual_writeoff(seeded, "HK001", "FP001", 10000, "tester")
    with pytest.raises(ValueError):
        manual_writeoff(seeded, "HK001", "FP001", 1, "tester")


def test_partner_tax_no_unique(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(seeded, "partners", {
            "code": "KH999", "name": "重复税号", "tax_no": "TAX001",  # 税号重复
            "partner_type": "客户", "default_tax_rate": "13%", "remark": "",
        }, "tester")


def test_contract_code_unique(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(seeded, "contracts", {
            "code": "HT001", "partner_code": "KH001", "name": "重复合同",
            "amount_tax": 1, "amount_no_tax": 1,
            "sign_date": "2026-01-01", "status": "未开始", "remark": "",
        }, "tester")


def test_payment_code_unique(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        insert_row(seeded, "payments", {
            "code": "HK001", "pay_date": "2026-03-01", "pay_type": "收款",
            "partner_code": "KH001", "contract_code": None,
            "amount": 1, "pay_method": "银行转账", "remark": "",
        }, "tester")
