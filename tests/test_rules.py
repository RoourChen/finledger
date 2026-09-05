"""业务规则引擎（纯函数）：金额勾稽 / 方向校验 / 回款状态推导。"""
from rules import parse_rate, reconcile, validate_direction, payment_status


def test_parse_rate_variants():
    assert parse_rate("13%") == 0.13
    assert parse_rate(13) == 0.13
    assert parse_rate(0.13) == 0.13


def test_reconcile_from_tax_and_rate():
    at, ant, ta, errs = reconcile(113, None, None, "13%")
    assert (at, ant, ta) == (113.0, 100.0, 13.0)
    assert errs == []


def test_reconcile_from_no_tax_and_rate():
    at, ant, ta, errs = reconcile(None, 100, None, "13%")
    assert (at, ant, ta) == (113.0, 100.0, 13.0)


def test_reconcile_detects_mismatch():
    _, _, _, errs = reconcile(100, 90, 5, "13%")
    assert errs  # 含税 ≠ 不含税 + 税额


def test_validate_direction():
    assert validate_direction("客户", "销项") is None
    assert validate_direction("供应商", "进项") is None
    assert validate_direction("两者", "进项") is None
    assert "客户" in validate_direction("供应商", "销项")


def test_payment_status_derivation():
    assert payment_status(100, 0) == "待核销"
    assert payment_status(100, 40) == "部分核销"
    assert payment_status(100, 100) == "已核销"
