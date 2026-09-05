"""AppTest UI 冒烟测试：页面渲染 + 表单交互 + 核销 + 红冲/作废联动。

与数据层测试互补——数据层测规则正确性，这里测界面基本可用性，
保证今后任何 UI 改动不会悄悄破坏基本功能。
"""
import sqlite3
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def find(elems, label=None, key=None):
    """按 label 或 key 定位控件（用于区分同名控件）。"""
    for e in elems:
        if label is not None and getattr(e, "label", None) == label:
            return e
        if key is not None and getattr(e, "key", None) == key:
            return e
    return None


def query(db_path, sql):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(sql).fetchall()]
    c.close()
    return rows


def _seed(db_path):
    """预置：客户 KH001 + 合同 HT001 + 蓝字发票 FP001(10万) + 回款 HK001(8万)。"""
    from db import get_conn, init_db, insert_row
    c = get_conn(db_path)
    init_db(c)
    insert_row(c, "partners", {
        "code": "KH001", "name": "甲客户", "tax_no": "TAX001",
        "partner_type": "客户", "default_tax_rate": "13%", "remark": "",
    }, "ui-test")
    insert_row(c, "contracts", {
        "code": "HT001", "partner_code": "KH001", "name": "服务合同",
        "amount_tax": 113000.0, "amount_no_tax": 100000.0,
        "sign_date": "2026-01-01", "status": "履行中", "remark": "",
    }, "ui-test")
    insert_row(c, "invoices", {
        "code": "FP001", "invoice_code": "3100", "invoice_number": "A0001",
        "invoice_date": "2026-01-10", "buy_sell_type": "销项",
        "partner_code": "KH001", "contract_code": "HT001",
        "amount_tax": 100000.0, "amount_no_tax": 88495.58, "tax_amount": 11504.42,
        "tax_rate": "13%", "status": "正常", "related_invoice_code": "", "remark": "",
    }, "ui-test")
    insert_row(c, "payments", {
        "code": "HK001", "pay_date": "2026-02-01", "pay_type": "收款",
        "partner_code": "KH001", "contract_code": "HT001",
        "amount": 80000.0, "pay_method": "银行转账", "remark": "",
    }, "ui-test")
    c.close()


def _login_app(db_path, monkeypatch):
    from auth import hash_password
    monkeypatch.setenv("FINLEDGER_DB", db_path)
    # 生产模式：用哈希配置登录（低迭代加速测试，格式与默认一致）
    monkeypatch.setenv("FINLEDGER_PASSWORD_HASH", hash_password("test123", iterations=1000))
    monkeypatch.delenv("FINLEDGER_PASSWORD", raising=False)
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.run()
    at.text_input[0].set_value("test123")
    at.button[0].click().run()
    assert not at.exception, [str(e.value) for e in at.exception]
    at.db_path = db_path
    return at


@pytest.fixture
def app(tmp_path, monkeypatch):
    """空库 + 已登录。"""
    return _login_app(str(tmp_path / "ui.db"), monkeypatch)


@pytest.fixture
def app_seeded(tmp_path, monkeypatch):
    """预置数据 + 已登录。"""
    db = str(tmp_path / "ui_seeded.db")
    _seed(db)
    return _login_app(db, monkeypatch)


# ---------------------------------------------------------------------------
# 1. 六个台账页面（及全部页面）正常打开
# ---------------------------------------------------------------------------
def test_all_pages_render(app):
    for opt in list(app.radio[0].options):
        app.radio[0].set_value(opt).run()
        errs = [str(e.value) for e in app.exception]
        assert not errs, f"页面「{opt}」渲染异常: {errs}"


# ---------------------------------------------------------------------------
# 2. 新增 / 修改 / 删除 表单可交互
# ---------------------------------------------------------------------------
def test_add_partner_via_form(app):
    find(app.selectbox, label="类型").select("客户")
    find(app.text_input, label="名称（与营业执照一致）").set_value("冒烟客户")
    find(app.text_input, label="税号").set_value("SMOKE001")
    find(app.button, label="新增").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert query(app.db_path, "SELECT code,name,tax_no FROM partners") == [
        {"code": "KH001", "name": "冒烟客户", "tax_no": "SMOKE001"}
    ]


def test_edit_partner_via_form(app):
    # 先新增
    find(app.selectbox, label="类型").select("客户")
    find(app.text_input, label="名称（与营业执照一致）").set_value("旧名")
    find(app.text_input, label="税号").set_value("SMOKE001")
    find(app.button, label="新增").click().run()
    assert not app.exception
    # 修改（唯一一条记录，修改表单默认选中 KH001）
    find(app.text_input, label="名称").set_value("新名")
    find(app.button, label="保存修改").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert query(app.db_path, "SELECT name FROM partners WHERE code='KH001'")[0]["name"] == "新名"


def test_delete_partner_via_form(app):
    # 先新增两条（删一条后还剩一条，用于验证确认状态是否自动清除）
    for name, tax in [("待删除", "SMOKE001"), ("保留", "SMOKE002")]:
        find(app.selectbox, label="类型").select("客户")
        find(app.text_input, label="名称（与营业执照一致）").set_value(name)
        find(app.text_input, label="税号").set_value(tax)
        find(app.button, label="新增").click().run()
        assert not app.exception
    # 删除：未勾选确认时按钮禁用，勾选后才可删
    find(app.selectbox, key="del_partners").select("KH001")
    del_btn = find(app.button, label="删除该记录")
    assert del_btn.disabled, "未勾选确认时删除按钮应禁用"
    find(app.checkbox, key="delconfirm_partners").check().run()
    del_btn = find(app.button, label="删除该记录")
    assert not del_btn.disabled, "勾选确认后删除按钮应可用"
    del_btn.click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert query(app.db_path, "SELECT COUNT(*) AS n FROM partners")[0]["n"] == 1
    # 删除成功后确认状态自动清除，按钮回到禁用
    app.run()
    assert app.session_state["delconfirm_partners"] is False, "删除成功后确认状态应自动清除"
    assert find(app.button, label="删除该记录").disabled, "确认清除后删除按钮应回到禁用"


def test_add_invoice_auto_computes(app):
    # 先加一个客户
    find(app.selectbox, label="类型").select("客户")
    find(app.text_input, label="名称（与营业执照一致）").set_value("客户A")
    find(app.text_input, label="税号").set_value("T001")
    find(app.button, label="新增").click().run()
    assert not app.exception
    # 发票：只填含税 113000，自动补算不含税与税额
    app.radio[0].set_value("发票").run()
    find(app.selectbox, label="客户/供应商").select("KH001")
    find(app.text_input, key="add_inv_number").set_value("88888888")
    find(app.text_input, label="含税金额（可空，自动补算）").set_value("113000")
    find(app.button, label="新增发票").click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    assert query(app.db_path, "SELECT amount_tax,amount_no_tax,tax_amount FROM invoices") == [
        {"amount_tax": 113000.0, "amount_no_tax": 100000.0, "tax_amount": 13000.0}
    ]


# ---------------------------------------------------------------------------
# 3. 核销页：执行核销并刷新状态
# ---------------------------------------------------------------------------
def test_manual_writeoff_via_ui(app_seeded):
    app_seeded.radio[0].set_value("核销").run()
    pay_opts = find(app_seeded.selectbox, key="m_pay").options
    inv_opts = find(app_seeded.selectbox, key="m_inv").options
    hk_opt = next(o for o in pay_opts if o.startswith("HK001"))
    fp_opt = next(o for o in inv_opts if o.startswith("FP001"))

    find(app_seeded.selectbox, key="m_pay").select(hk_opt)
    find(app_seeded.selectbox, key="m_inv").select(fp_opt)
    find(app_seeded.number_input, label="核销金额").set_value(30000.0)
    find(app_seeded.button, label="提交核销").click().run()
    assert not app_seeded.exception, [str(e.value) for e in app_seeded.exception]

    assert query(app_seeded.db_path, "SELECT payment_code,invoice_code,amount FROM writeoffs") == [
        {"payment_code": "HK001", "invoice_code": "FP001", "amount": 30000.0}
    ]
    from db import get_conn
    from writeoff import writeoff_status
    assert writeoff_status(get_conn(app_seeded.db_path), "HK001") == "部分核销"


# ---------------------------------------------------------------------------
# 4. 红冲 / 作废 联动在界面上的表现
# ---------------------------------------------------------------------------
def test_red_letter_via_ui(app_seeded):
    app_seeded.radio[0].set_value("发票").run()
    find(app_seeded.selectbox, key="add_inv_status").select("红冲").run()

    blue_opts = find(app_seeded.selectbox, label="关联原蓝字发票").options
    assert blue_opts, "应能列出可红冲的原蓝字发票"
    find(app_seeded.selectbox, label="关联原蓝字发票").select(blue_opts[0])
    find(app_seeded.text_input, key="add_inv_number").set_value("R99999")
    find(app_seeded.text_input, label="红冲含税金额（正数，可空自动补算）").set_value("40000")
    find(app_seeded.button, label="新增发票").click().run()
    assert not app_seeded.exception, [str(e.value) for e in app_seeded.exception]

    from db import get_conn
    from writeoff import available_invoice_amount
    assert available_invoice_amount(get_conn(app_seeded.db_path), "FP001") == 60000.0  # 10万 − 4万


def test_void_invoice_via_ui(app_seeded):
    app_seeded.radio[0].set_value("发票").run()
    find(app_seeded.selectbox, key="edit_invoices_sel").select("FP001｜A0001｜正常").run()
    find(app_seeded.selectbox, key="ei_s").select("作废")
    find(app_seeded.button, label="保存修改").click().run()
    assert not app_seeded.exception, [str(e.value) for e in app_seeded.exception]

    from db import get_conn
    from writeoff import available_invoice_amount
    assert available_invoice_amount(get_conn(app_seeded.db_path), "FP001") == 0.0
