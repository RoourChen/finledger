"""pytest 公共夹具：内存库 + 种子数据 + 发票工厂。"""
import sys
from pathlib import Path

import pytest

# 让测试能 import 项目根目录下的 db / rules / writeoff 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import get_conn, init_db, insert_row  # noqa: E402


@pytest.fixture
def conn():
    """全新内存数据库（六表 + 参数 + 审计日志已初始化）。"""
    c = get_conn(":memory:")
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    """种子数据：
    客户 KH001 + 供应商 GYS001 + 合同 HT001
    + 蓝字发票 FP001（含税 10 万）+ 回款 HK001（收款 8 万）。
    """
    insert_row(conn, "partners", {
        "code": "KH001", "name": "甲客户", "tax_no": "TAX001",
        "partner_type": "客户", "default_tax_rate": "13%", "remark": "",
    }, "tester")
    insert_row(conn, "partners", {
        "code": "GYS001", "name": "乙供应商", "tax_no": "TAX002",
        "partner_type": "供应商", "default_tax_rate": "13%", "remark": "",
    }, "tester")
    insert_row(conn, "contracts", {
        "code": "HT001", "partner_code": "KH001", "name": "服务合同",
        "amount_tax": 113000.0, "amount_no_tax": 100000.0,
        "sign_date": "2026-01-01", "status": "履行中", "remark": "",
    }, "tester")
    insert_row(conn, "invoices", {
        "code": "FP001", "invoice_code": "3100", "invoice_number": "A0001",
        "invoice_date": "2026-01-10", "buy_sell_type": "销项",
        "partner_code": "KH001", "contract_code": "HT001",
        "amount_tax": 100000.0, "amount_no_tax": 88495.58, "tax_amount": 11504.42,
        "tax_rate": "13%", "status": "正常", "related_invoice_code": "", "remark": "",
    }, "tester")
    insert_row(conn, "payments", {
        "code": "HK001", "pay_date": "2026-02-01", "pay_type": "收款",
        "partner_code": "KH001", "contract_code": "HT001",
        "amount": 80000.0, "pay_method": "银行转账", "remark": "",
    }, "tester")
    return conn


@pytest.fixture
def add_invoice(seeded):
    """发票工厂：add(code, number, amount_tax, status=..., related=...)。"""
    def _add(code, number, amount_tax, status="正常", related="", partner="KH001",
             bs="销项", date="2026-01-10"):
        amount_tax = float(amount_tax)
        insert_row(seeded, "invoices", {
            "code": code, "invoice_code": "3100", "invoice_number": number,
            "invoice_date": date, "buy_sell_type": bs, "partner_code": partner,
            "contract_code": "HT001", "amount_tax": amount_tax,
            "amount_no_tax": round(amount_tax / 1.13, 2),
            "tax_amount": round(amount_tax - amount_tax / 1.13, 2),
            "tax_rate": "13%", "status": status,
            "related_invoice_code": related, "remark": "",
        }, "tester")
    return _add
