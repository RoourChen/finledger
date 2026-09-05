"""建库与初始化：六表 + 参数默认值 + 审计日志。"""
import json

from db import (
    DEFAULT_PARAMS,
    delete_row,
    fetch_all,
    get_param,
    insert_row,
    update_row,
)

EXPECTED_TABLES = {
    "partners", "contracts", "invoices",
    "payments", "writeoffs", "params", "audit_log",
}


def test_all_tables_exist(conn):
    tables = {
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert EXPECTED_TABLES <= tables


def test_default_params_written(conn):
    assert get_param(conn, "默认核销规则") == "先进先出"
    assert get_param(conn, "默认税率") == "13%"
    assert get_param(conn, "是否允许预收款挂账") == "否"
    assert get_param(conn, "是否强制合同关联") == "否"
    assert get_param(conn, "部分回款进度显示方式") == "按核销金额"


def test_init_db_idempotent(conn):
    from db import init_db
    init_db(conn)  # 重复初始化不产生重复参数
    assert len(fetch_all(conn, "params")) == len(DEFAULT_PARAMS)


def test_audit_log_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)")}
    assert {
        "operation_type", "operator", "operation_time",
        "table_name", "record_id", "before_data", "after_data",
    } <= cols


def _partner(conn, code="KH001", tax_no="T1"):
    return {
        "code": code, "name": "甲客户", "tax_no": tax_no,
        "partner_type": "客户", "default_tax_rate": "13%", "remark": "",
    }


def test_audit_records_insert(conn):
    insert_row(conn, "partners", _partner(conn), "tester")
    logs = fetch_all(conn, "audit_log")
    assert len(logs) == 1
    log = logs[0]
    assert log["operation_type"] == "INSERT"
    assert log["operator"] == "tester"
    assert log["table_name"] == "partners"
    assert log["record_id"] == "KH001"
    assert json.loads(log["after_data"])["name"] == "甲客户"
    assert log["before_data"] == ""


def test_audit_captures_before_after_on_update(conn):
    insert_row(conn, "partners", _partner(conn), "tester")
    update_row(conn, "partners", "code", "KH001", {"name": "甲改名"}, "tester")
    upd = [l for l in fetch_all(conn, "audit_log") if l["operation_type"] == "UPDATE"][0]
    assert json.loads(upd["before_data"])["name"] == "甲客户"
    assert json.loads(upd["after_data"])["name"] == "甲改名"


def test_audit_records_delete(conn):
    insert_row(conn, "partners", _partner(conn), "tester")
    delete_row(conn, "partners", "code", "KH001", "tester")
    d = [l for l in fetch_all(conn, "audit_log") if l["operation_type"] == "DELETE"][0]
    assert json.loads(d["before_data"])["code"] == "KH001"
    assert d["after_data"] == ""
