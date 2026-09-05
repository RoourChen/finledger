#!/usr/bin/env python3
"""
财会助手 —— SQLite 数据层

六张业务表 + 一张审计日志表：
  partners  客户/供应商表
  contracts 合同表
  invoices  发票表
  payments  回款表（收付款记录，状态不落库，由核销自动推导）
  writeoffs 核销记录表
  params    参数表
  audit_log 审计日志表

需求方特别强调的约束：
  1. 发票表   (invoice_code, invoice_number) 唯一 —— 防止重复录入
  2. 核销记录表 (payment_code, invoice_code) 唯一 —— 防止重复核销
  3. audit_log 记录 操作类型 / 操作人 / 操作时间 / 变更前后数据 —— 便于追溯
  4. params 初始化默认参数（默认税率 13%、核销规则 先进先出）
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("FINLEDGER_DB", str(BASE_DIR / "finledger.db"))

# 初始化默认参数（含说明，用于参数表界面展示）
DEFAULT_PARAMS: Dict[str, Dict[str, str]] = {
    "默认核销规则": {"value": "先进先出", "description": "自动核销默认行为：先进先出 / 手工指定 / 按比例分摊"},
    "默认税率": {"value": "13%", "description": "新增发票时自动带出的税率"},
    "是否允许预收款挂账": {"value": "否", "description": "是 / 否"},
    "是否强制合同关联": {"value": "否", "description": "是：发票和回款必须选合同；否：允许留空"},
    "部分回款进度显示方式": {"value": "按核销金额", "description": "按回款金额 / 按核销金额"},
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS partners (
    code             TEXT PRIMARY KEY,                 -- 客户/供应商编号 KH001 / GYS001
    name             TEXT NOT NULL,                    -- 名称（与营业执照一致）
    tax_no           TEXT NOT NULL UNIQUE,             -- 税号（纳税人识别号，唯一）
    partner_type     TEXT NOT NULL DEFAULT '客户',      -- 类型：客户 / 供应商 / 两者
    default_tax_rate TEXT NOT NULL DEFAULT '13%',      -- 默认税率
    remark           TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS contracts (
    code           TEXT PRIMARY KEY,                   -- 合同编号
    partner_code   TEXT NOT NULL REFERENCES partners(code),
    name           TEXT NOT NULL,                      -- 合同名称
    amount_tax     REAL NOT NULL DEFAULT 0,            -- 合同金额（含税）
    amount_no_tax  REAL NOT NULL DEFAULT 0,            -- 合同金额（不含税）
    sign_date      TEXT NOT NULL DEFAULT '',           -- 签订日期 YYYY-MM-DD
    status         TEXT NOT NULL DEFAULT '未开始',      -- 履约状态：未开始/履行中/已完成/已终止
    remark         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS invoices (
    code                  TEXT PRIMARY KEY,            -- 发票编号（系统内部唯一，自动生成 FP001）
    invoice_code          TEXT NOT NULL DEFAULT '',    -- 发票代码（可空）
    invoice_number        TEXT NOT NULL,               -- 发票号码
    invoice_date          TEXT NOT NULL DEFAULT '',    -- 开票日期 YYYY-MM-DD
    buy_sell_type         TEXT NOT NULL DEFAULT '销项', -- 购销类型：销项 / 进项
    partner_code          TEXT NOT NULL REFERENCES partners(code),
    contract_code         TEXT REFERENCES contracts(code), -- 合同编号（可空）
    amount_tax            REAL NOT NULL DEFAULT 0,     -- 发票含税金额
    amount_no_tax         REAL NOT NULL DEFAULT 0,     -- 发票不含税金额
    tax_amount            REAL NOT NULL DEFAULT 0,     -- 税额
    tax_rate              TEXT NOT NULL DEFAULT '13%', -- 税率
    status                TEXT NOT NULL DEFAULT '正常', -- 发票状态：正常 / 作废 / 红冲
    related_invoice_code  TEXT NOT NULL DEFAULT '',    -- 关联原发票号（红字发票填原蓝字号码）
    remark                TEXT NOT NULL DEFAULT '',
    UNIQUE (invoice_code, invoice_number)              -- 代码+号码 唯一，防止重复录入
);

CREATE TABLE IF NOT EXISTS payments (
    code          TEXT PRIMARY KEY,                    -- 回款单号
    pay_date      TEXT NOT NULL DEFAULT '',            -- 日期 YYYY-MM-DD
    pay_type      TEXT NOT NULL DEFAULT '收款',         -- 收付类型：收款 / 付款
    partner_code  TEXT NOT NULL REFERENCES partners(code),
    contract_code TEXT REFERENCES contracts(code),     -- 合同编号（可空）
    amount        REAL NOT NULL DEFAULT 0,             -- 金额（含税）
    pay_method    TEXT NOT NULL DEFAULT '银行转账',     -- 付款方式：银行转账/现金/承兑汇票等
    remark        TEXT NOT NULL DEFAULT ''
    -- 状态（待核销/已核销/部分核销）不落库，由核销记录自动推导
);

CREATE TABLE IF NOT EXISTS writeoffs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,   -- 核销 ID
    payment_code  TEXT NOT NULL REFERENCES payments(code),
    invoice_code  TEXT NOT NULL REFERENCES invoices(code),
    amount        REAL NOT NULL DEFAULT 0,             -- 核销金额（含税）
    writeoff_date TEXT NOT NULL DEFAULT '',            -- 核销日期
    method        TEXT NOT NULL DEFAULT '手工指定',     -- 核销方式：先进先出 / 手工指定
    operator      TEXT NOT NULL DEFAULT '',            -- 操作人
    UNIQUE (payment_code, invoice_code)                -- 同一回款对同一发票只能有一条核销，防止重复核销
);

CREATE TABLE IF NOT EXISTS params (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,                      -- INSERT / UPDATE / DELETE / WRITEOFF / CANCEL_WRITEOFF
    operator       TEXT NOT NULL DEFAULT '',           -- 操作人
    operation_time TEXT NOT NULL,                      -- 操作时间（ISO）
    table_name     TEXT NOT NULL,                      -- 表名
    record_id      TEXT NOT NULL DEFAULT '',           -- 记录主键
    before_data    TEXT NOT NULL DEFAULT '',           -- 变更前数据（JSON）
    after_data     TEXT NOT NULL DEFAULT ''            -- 变更后数据（JSON）
);
"""


# ---------------------------------------------------------------------------
# 连接与初始化
# ---------------------------------------------------------------------------

def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    # 环境变量在调用时读取，而非导入时固化（测试会动态切换 DB 路径）
    resolved = db_path or os.getenv("FINLEDGER_DB") or DB_PATH
    conn = sqlite3.connect(resolved, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL：读写不互相阻塞；busy_timeout：锁忙时等待而非立即报错
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表 + 初始化默认参数（幂等）。"""
    conn.executescript(SCHEMA_SQL)
    conn.executemany(
        "INSERT OR IGNORE INTO params (key, value, description) VALUES (?, ?, ?)",
        [(k, v["value"], v["description"]) for k, v in DEFAULT_PARAMS.items()],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------

def audit(
    conn: sqlite3.Connection,
    operation_type: str,
    operator: str,
    table_name: str,
    record_id: Any = "",
    before: Optional[Dict] = None,
    after: Optional[Dict] = None,
) -> None:
    """记录一次操作：类型 / 操作人 / 时间 / 变更前后数据。"""
    conn.execute(
        "INSERT INTO audit_log (operation_type, operator, operation_time, table_name, record_id, before_data, after_data)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            operation_type,
            operator or "",
            now(),
            table_name,
            str(record_id or ""),
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else "",
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else "",
        ),
    )


# ---------------------------------------------------------------------------
# 通用读写（带审计）
# ---------------------------------------------------------------------------

def fetch_all(conn: sqlite3.Connection, table: str, order_by: str = "1") -> List[Dict]:
    cur = conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
    return [dict(r) for r in cur.fetchall()]


def fetch_one(conn: sqlite3.Connection, table: str, pk_field: str, pk_value: Any) -> Optional[Dict]:
    cur = conn.execute(f"SELECT * FROM {table} WHERE {pk_field} = ?", (pk_value,))
    row = cur.fetchone()
    return dict(row) if row else None


def insert_row(conn: sqlite3.Connection, table: str, data: Dict, operator: str = "") -> None:
    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
        [data[c] for c in cols],
    )
    record_id = data.get("code", data.get("id", ""))
    audit(conn, "INSERT", operator, table, record_id, after=data)
    conn.commit()


def update_row(conn: sqlite3.Connection, table: str, pk_field: str, pk_value: Any, data: Dict, operator: str = "") -> None:
    before = fetch_one(conn, table, pk_field, pk_value)
    sets = ", ".join(f"{c} = ?" for c in data)
    conn.execute(
        f"UPDATE {table} SET {sets} WHERE {pk_field} = ?",
        [data[c] for c in data] + [pk_value],
    )
    after = fetch_one(conn, table, pk_field, pk_value)
    audit(conn, "UPDATE", operator, table, pk_value, before=before, after=after)
    conn.commit()


def delete_row(conn: sqlite3.Connection, table: str, pk_field: str, pk_value: Any, operator: str = "") -> None:
    before = fetch_one(conn, table, pk_field, pk_value)
    conn.execute(f"DELETE FROM {table} WHERE {pk_field} = ?", (pk_value,))
    audit(conn, "DELETE", operator, table, pk_value, before=before)
    conn.commit()


# ---------------------------------------------------------------------------
# 参数读写
# ---------------------------------------------------------------------------

def get_param(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM params WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_param(conn: sqlite3.Connection, key: str, value: str, operator: str = "") -> None:
    before = fetch_one(conn, "params", "key", key)
    conn.execute(
        "INSERT INTO params (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    after = fetch_one(conn, "params", "key", key)
    audit(conn, "UPDATE", operator, "params", key, before=before, after=after)
    conn.commit()


# ---------------------------------------------------------------------------
# 编号生成（合同可手工编制或系统生成，此处提供系统生成）
# ---------------------------------------------------------------------------

def _next_code(conn: sqlite3.Connection, table: str, column: str, prefix: str, width: int = 3) -> str:
    rows = conn.execute(f"SELECT {column} FROM {table}").fetchall()
    max_n = 0
    for (val,) in rows:
        if isinstance(val, str) and val.startswith(prefix):
            try:
                max_n = max(max_n, int(val[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{max_n + 1:0{width}d}"


def next_partner_code(conn: sqlite3.Connection, partner_type: str) -> str:
    prefix = {"客户": "KH", "供应商": "GYS", "两者": "WD"}.get(partner_type, "KH")
    return _next_code(conn, "partners", "code", prefix)


def next_contract_code(conn: sqlite3.Connection) -> str:
    return _next_code(conn, "contracts", "code", "HT")


def next_invoice_code(conn: sqlite3.Connection) -> str:
    return _next_code(conn, "invoices", "code", "FP")


def next_payment_code(conn: sqlite3.Connection) -> str:
    return _next_code(conn, "payments", "code", "HK")
