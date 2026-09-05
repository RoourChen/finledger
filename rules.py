#!/usr/bin/env python3
"""
财会助手 —— 业务规则引擎（纯函数，不依赖数据库）

  - 金额勾稽：含税 = 不含税 + 税额；税额 ≈ 不含税 × 税率；缺项自动补算
  - 方向校验：销项发票只挂客户，进项发票只挂供应商
  - 回款状态推导：0 → 待核销；部分 → 部分核销；全额 → 已核销
"""
from __future__ import annotations

from typing import List, Optional, Tuple

# 购销类型 ↔ 往来单位类型 的合法映射（「两者」两者都允许）
BUY_SELL_TO_PARTNER = {"销项": "客户", "进项": "供应商"}
# 收付类型 ↔ 应核销的发票购销类型
PAY_TYPE_TO_BUY_SELL = {"收款": "销项", "付款": "进项"}


def _num(value) -> Optional[float]:
    """把空值 / 字符串转成 float，无法转换返回 None。"""
    if value is None or value == "":
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def parse_rate(rate) -> float:
    """把 '13%' / 13 / 0.13 统一成小数 0.13。"""
    if isinstance(rate, str):
        rate = rate.strip()
        if rate.endswith("%"):
            rate = rate[:-1]
        rate = float(rate or 0)
    else:
        rate = float(rate or 0)
    if rate > 1:
        rate = rate / 100.0
    return round(rate, 4)


def reconcile(
    amount_tax=None,
    amount_no_tax=None,
    tax_amount=None,
    rate=None,
    tol: float = 0.01,
) -> Tuple[Optional[float], Optional[float], Optional[float], List[str]]:
    """自动补算缺失金额 + 勾稽校验。

    返回 (含税, 不含税, 税额, 错误列表)。错误列表为空表示勾稽通过。
    """
    errors: List[str] = []
    at = _num(amount_tax)
    ant = _num(amount_no_tax)
    ta = _num(tax_amount)
    r = parse_rate(rate) if rate not in (None, "") else None

    # 缺项自动补算（已知两项 / 已知一项+税率）
    if at is None and ant is not None and r is not None:
        ta = round(ant * r, 2)
        at = round(ant + ta, 2)
    elif ant is None and at is not None and r is not None:
        ant = round(at / (1 + r), 2)
        ta = round(at - ant, 2)
    elif ta is None and at is not None and ant is not None:
        ta = round(at - ant, 2)
    elif ant is None and at is not None and ta is not None:
        ant = round(at - ta, 2)
    elif at is None and ant is not None and ta is not None:
        at = round(ant + ta, 2)

    # 勾稽校验
    if at is not None and ant is not None and ta is not None:
        if abs(at - ant - ta) > tol:
            errors.append(f"含税({at}) ≠ 不含税({ant}) + 税额({ta})")
        if r is not None:
            expected_tax = round(ant * r, 2)
            if abs(ta - expected_tax) > max(tol, abs(expected_tax) * 0.02):
                errors.append(f"税额({ta}) 与 不含税×税率({expected_tax}) 偏差过大")

    return at, ant, ta, errors


def validate_direction(partner_type: str, buy_sell_type: str) -> Optional[str]:
    """销项发票只挂客户，进项发票只挂供应商。「两者」单位都允许。"""
    if partner_type == "两者":
        return None
    expected = BUY_SELL_TO_PARTNER.get(buy_sell_type)
    if expected and partner_type != expected:
        return f"{buy_sell_type}发票应挂「{expected}」，当前单位为「{partner_type}」"
    return None


def payment_status(amount: float, paid_total: float, tol: float = 0.01) -> str:
    """由已核销金额自动推导回款状态（不手填）。"""
    if paid_total <= tol:
        return "待核销"
    if paid_total + tol >= amount:
        return "已核销"
    return "部分核销"
