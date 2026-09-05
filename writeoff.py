#!/usr/bin/env python3
"""
财会助手 —— 核销引擎 + 发票可核销金额（红冲/作废联动）

可核销金额口径（含税，与核销口径一致）：
  蓝字发票可核销金额 = 含税金额 − 已核销金额 − 已红冲金额（不得为负）

规则：
  - 红字发票（status='红冲'）本身不允许被核销（红字发票无收款义务）
  - 作废发票（status='作废'）金额全部不可核销，且不参与任何统计
  - 蓝字发票被部分/全部红冲后，可核销金额自动减少对应红冲金额
  - 强制校验：可核销金额不得为负
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from db import audit, fetch_one
from rules import PAY_TYPE_TO_BUY_SELL, payment_status


def written_off_amount(conn, invoice_code: str) -> float:
    """该发票已核销的总金额。"""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM writeoffs WHERE invoice_code = ?",
        (invoice_code,),
    ).fetchone()
    return round(row[0] or 0, 2)


def red_lettered_amount(conn, invoice_code: str) -> float:
    """该蓝字发票已被红冲的总金额（红字发票金额以正数登记）。"""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_tax), 0) FROM invoices"
        " WHERE status = '红冲' AND related_invoice_code = ?",
        (invoice_code,),
    ).fetchone()
    return round(row[0] or 0, 2)


def available_invoice_amount(conn, invoice_code: str) -> float:
    """发票可核销金额（含税口径）。

    正常发票 = 含税金额 − 已核销 − 已红冲；作废 / 红冲发票 = 0。
    """
    inv = fetch_one(conn, "invoices", "code", invoice_code)
    if not inv:
        return 0.0
    if inv["status"] in ("作废", "红冲"):
        return 0.0
    return round(
        inv["amount_tax"] - written_off_amount(conn, invoice_code) - red_lettered_amount(conn, invoice_code),
        2,
    )


# 兼容旧命名：可核销金额
def remaining_invoice(conn, invoice_code: str) -> float:
    return available_invoice_amount(conn, invoice_code)


def remaining_payment(conn, payment_code: str) -> float:
    """回款剩余可核销金额 = 回款金额 − 已核销金额。"""
    p = fetch_one(conn, "payments", "code", payment_code)
    if not p:
        return 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM writeoffs WHERE payment_code = ?",
        (payment_code,),
    ).fetchone()
    return round(p["amount"] - (row[0] or 0), 2)


def written_off_payment_amount(conn, payment_code: str) -> float:
    """该回款已核销的总金额。"""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM writeoffs WHERE payment_code = ?",
        (payment_code,),
    ).fetchone()
    return round(row[0] or 0, 2)


def writeoff_status(conn, payment_code: str) -> str:
    """回款状态自动推导（不落库、不手填）。"""
    p = fetch_one(conn, "payments", "code", payment_code)
    if not p:
        return "待核销"
    paid = round(p["amount"] - remaining_payment(conn, payment_code), 2)
    return payment_status(p["amount"], paid)


# ---------------------------------------------------------------------------
# 校验：红冲 / 作废 / 金额变更
# ---------------------------------------------------------------------------

def validate_red_letter(conn, blue_code: str, red_amount_tax: float, exclude_red_code: Optional[str] = None) -> Optional[str]:
    """新增/修改红字发票前校验。

    red_amount_tax 为正数（红冲金额量，含税口径）。exclude_red_code 用于修改时排除自身。
    """
    if not blue_code:
        return "红字发票必须关联原蓝字发票"
    blue = fetch_one(conn, "invoices", "code", blue_code)
    if not blue:
        return "关联的原蓝字发票不存在"
    if blue["status"] != "正常":
        return f"原蓝字发票状态为「{blue['status']}」，不能被红冲"
    if red_amount_tax is None or red_amount_tax <= 0:
        return "红冲金额必须大于 0"

    if exclude_red_code:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_tax), 0) FROM invoices"
            " WHERE status = '红冲' AND related_invoice_code = ? AND code != ?",
            (blue_code, exclude_red_code),
        ).fetchone()
        red_existing = round(row[0] or 0, 2)
    else:
        red_existing = red_lettered_amount(conn, blue_code)

    available = blue["amount_tax"] - written_off_amount(conn, blue_code) - red_existing
    if available - red_amount_tax < -0.01:
        return f"红冲金额({red_amount_tax:.2f})超过原蓝字发票可核销金额({max(available, 0):.2f})"
    return None


def validate_invoice_amount(conn, invoice_code: str, new_amount_tax: float) -> Optional[str]:
    """修改发票含税金额时，保证可核销金额不为负。"""
    if new_amount_tax is None or new_amount_tax < 0:
        return "含税金额不能为负"
    written = written_off_amount(conn, invoice_code)
    red = red_lettered_amount(conn, invoice_code)
    if new_amount_tax - written - red < -0.01:
        return f"含税金额({new_amount_tax:.2f})不能小于 已核销({written:.2f}) + 已红冲({red:.2f})"
    return None


def validate_payment_amount(conn, payment_code: str, new_amount: float) -> Optional[str]:
    """修改回款金额时，保证不小于已核销金额（避免可核销金额为负）。"""
    if new_amount is None or new_amount < 0:
        return "金额不能为负"
    paid = written_off_payment_amount(conn, payment_code)
    if new_amount - paid < -0.01:
        return f"金额({new_amount:.2f})不能小于已核销({paid:.2f})，请先撤销核销再修改"
    return None


def validate_status_change(conn, invoice_code: str, new_status: str) -> Optional[str]:
    """状态变更校验（主要为 作废 前检查关联核销/红冲）。"""
    inv = fetch_one(conn, "invoices", "code", invoice_code)
    if not inv:
        return "发票不存在"
    if new_status == "作废" and inv["status"] != "作废":
        written = written_off_amount(conn, invoice_code)
        red = red_lettered_amount(conn, invoice_code)
        if written > 0:
            return f"该发票已有核销 {written:.2f} 元，请先撤销核销再作废"
        if red > 0:
            return f"该发票已被红冲 {red:.2f} 元，请先处理红字发票再作废"
    return None


# ---------------------------------------------------------------------------
# 核销
# ---------------------------------------------------------------------------

def validate_writeoff(conn, payment_code: str, invoice_code: str, amount: float) -> Optional[str]:
    """核销前置校验，返回错误信息；None 表示通过。"""
    p = fetch_one(conn, "payments", "code", payment_code)
    if not p:
        return "回款单号不存在"
    inv = fetch_one(conn, "invoices", "code", invoice_code)
    if not inv:
        return "发票编号不存在"
    if inv["status"] != "正常":
        return f"发票状态为「{inv['status']}」，不参与核销"
    if p["partner_code"] != inv["partner_code"]:
        return "回款与发票的往来单位不一致"
    expected_bs = PAY_TYPE_TO_BUY_SELL.get(p["pay_type"])
    if expected_bs and inv["buy_sell_type"] != expected_bs:
        return f"{p['pay_type']}应核销「{expected_bs}」发票，而该发票为「{inv['buy_sell_type']}」"
    if amount <= 0:
        return "核销金额必须大于 0"
    exists = conn.execute(
        "SELECT 1 FROM writeoffs WHERE payment_code = ? AND invoice_code = ?",
        (payment_code, invoice_code),
    ).fetchone()
    if exists:
        return "该回款已核销过该发票（唯一约束），请调整已有核销记录"
    if amount > remaining_payment(conn, payment_code) + 0.01:
        return "核销金额超过回款剩余可核销金额"
    if amount > available_invoice_amount(conn, invoice_code) + 0.01:
        return "核销金额超过发票可核销金额（含税 − 已核销 − 已红冲）"
    return None


def manual_writeoff(conn, payment_code: str, invoice_code: str, amount: float, operator: str = "") -> Dict:
    """手工指定核销：指定回款 + 发票 + 金额。"""
    err = validate_writeoff(conn, payment_code, invoice_code, amount)
    if err:
        raise ValueError(err)
    data = {
        "payment_code": payment_code,
        "invoice_code": invoice_code,
        "amount": round(float(amount), 2),
        "writeoff_date": datetime.now().strftime("%Y-%m-%d"),
        "method": "手工指定",
        "operator": operator or "",
    }
    try:
        conn.execute(
            "INSERT INTO writeoffs (payment_code, invoice_code, amount, writeoff_date, method, operator)"
            " VALUES (:payment_code, :invoice_code, :amount, :writeoff_date, :method, :operator)",
            data,
        )
    except sqlite3.IntegrityError:
        raise ValueError("该回款已核销过该发票（唯一约束）") from None
    wid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    audit(conn, "WRITEOFF", operator, "writeoffs", wid, after=data)
    conn.commit()
    return {**data, "id": wid}


def auto_writeoff(conn, payment_code: str, operator: str = "") -> List[Dict]:
    """先进先出自动核销：按发票开票日期升序核销，直到回款核销完毕。"""
    p = fetch_one(conn, "payments", "code", payment_code)
    if not p:
        raise ValueError("回款单号不存在")

    expected_bs = PAY_TYPE_TO_BUY_SELL.get(p["pay_type"])
    sql = "SELECT * FROM invoices WHERE status = '正常' AND partner_code = :partner"
    params = {"partner": p["partner_code"]}
    if expected_bs:
        sql += " AND buy_sell_type = :bs"
        params["bs"] = expected_bs
    if p["contract_code"]:
        sql += " AND contract_code = :contract"
        params["contract"] = p["contract_code"]
    sql += " ORDER BY invoice_date, code"  # 先进先出：开票早的先核销

    candidates = [dict(r) for r in conn.execute(sql, params).fetchall()]

    results: List[Dict] = []
    remaining = remaining_payment(conn, payment_code)
    for inv in candidates:
        if remaining <= 0.01:
            break
        inv_remaining = available_invoice_amount(conn, inv["code"])
        if inv_remaining <= 0.01:
            continue
        amt = round(min(remaining, inv_remaining), 2)
        data = {
            "payment_code": payment_code,
            "invoice_code": inv["code"],
            "amount": amt,
            "writeoff_date": datetime.now().strftime("%Y-%m-%d"),
            "method": "先进先出",
            "operator": operator or "",
        }
        conn.execute(
            "INSERT INTO writeoffs (payment_code, invoice_code, amount, writeoff_date, method, operator)"
            " VALUES (:payment_code, :invoice_code, :amount, :writeoff_date, :method, :operator)",
            data,
        )
        wid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        audit(conn, "WRITEOFF", operator, "writeoffs", wid, after=data)
        results.append({**data, "id": wid})
        remaining = round(remaining - amt, 2)

    conn.commit()
    return results


def cancel_writeoff(conn, writeoff_id: int, operator: str = "") -> None:
    """撤销一条核销记录（高危操作，界面需二次确认）。"""
    row = fetch_one(conn, "writeoffs", "id", int(writeoff_id))
    if not row:
        raise ValueError("核销记录不存在")
    conn.execute("DELETE FROM writeoffs WHERE id = ?", (int(writeoff_id),))
    audit(conn, "CANCEL_WRITEOFF", operator, "writeoffs", writeoff_id, before=row)
    conn.commit()
