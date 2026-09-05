#!/usr/bin/env python3
"""
财会助手 —— Streamlit 内网 Web 界面

启动：streamlit run app.py
密码：生产环境用 FINLEDGER_PASSWORD_HASH（python password_tool.py 生成）；
      本地开发可临时用明文 FINLEDGER_PASSWORD（会警告）。
      二者均未设置则拒绝启动（见 auth.load_password_hash）。
"""
from __future__ import annotations

import os
import sqlite3

import pandas as pd
import streamlit as st

from auth import load_password_hash, verify_password
from db import (
    delete_row,
    fetch_all,
    fetch_one,
    get_conn,
    get_param,
    init_db,
    insert_row,
    next_contract_code,
    next_invoice_code,
    next_partner_code,
    next_payment_code,
    set_param,
    update_row,
)
from rules import payment_status, reconcile, validate_direction
from writeoff import (
    auto_writeoff,
    cancel_writeoff,
    manual_writeoff,
    red_lettered_amount,
    remaining_invoice,
    remaining_payment,
    validate_invoice_amount,
    validate_payment_amount,
    validate_red_letter,
    validate_status_change,
    writeoff_status,
    written_off_amount,
)

st.set_page_config(page_title="财会助手", page_icon="📒", layout="wide")

# 密码配置：生产用 FINLEDGER_PASSWORD_HASH；本地开发可用 FINLEDGER_PASSWORD（会警告）。
# 两者都未设置则拒绝启动（见 auth.load_password_hash）。
PASSWORD_HASH = load_password_hash()

# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------
if "authed" not in st.session_state:
    st.session_state.authed = False

if not st.session_state.authed:
    st.title("📒 财会助手")
    pwd = st.text_input("访问密码", type="password")
    if st.button("登录"):
        if verify_password(pwd, PASSWORD_HASH):
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("密码错误")
    st.stop()

# 单会话复用一个连接，避免每次刷新堆积连接导致 database is locked
if "db" not in st.session_state:
    st.session_state.db = get_conn()
    init_db(st.session_state.db)
conn = st.session_state.db

st.sidebar.title("📒 财会助手")
operator = st.sidebar.text_input("操作人", value=os.getenv("FINLEDGER_OPERATOR", "admin"))
page = st.sidebar.radio(
    "功能",
    ["客户/供应商", "合同", "发票", "回款", "核销", "客户全景", "报表", "参数"],
)


# ---------------------------------------------------------------------------
# 通用控件
# ---------------------------------------------------------------------------
def show_table(df: pd.DataFrame, name: str) -> None:
    st.dataframe(df, width="stretch")
    if not df.empty:
        csv_data = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("导出 CSV", csv_data, f"{name}.csv", "text/csv")


def download_csv(df: pd.DataFrame, filename: str) -> None:
    if not df.empty:
        st.download_button(
            "导出 CSV",
            df.to_csv(index=False).encode("utf-8-sig"),
            filename,
            "text/csv",
        )


def delete_selector(table: str, pk_field: str) -> None:
    rows = fetch_all(conn, table, pk_field)
    if not rows:
        return
    confirm_key = f"delconfirm_{table}"
    with st.expander("删除记录"):
        sel = st.selectbox("选择记录", [r[pk_field] for r in rows], key=f"del_{table}")
        confirm = st.checkbox("我确认删除该记录（不可撤销）", key=confirm_key)
        if st.button("删除该记录", key=f"delbtn_{table}", disabled=not confirm):
            try:
                delete_row(conn, table, pk_field, sel, operator)
                # 用 pop 而非赋值 False：widget 在 run 结束时会用自身状态覆盖赋值，
                # 删除 key 才能让下一轮 checkbox 回到未勾选
                st.session_state.pop(confirm_key, None)
                st.success("已删除")
                st.rerun()
            except sqlite3.IntegrityError as exc:
                st.error(f"无法删除（存在关联数据）：{exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))


def edit_form(table: str, pk_field: str, fields, validator=None) -> None:
    """fields: [(列名, 标签, 类型, 可选列表)]，类型 text/num/sel/ro
    validator(conn, pk_value, payload) -> list[str] 可选，返回错误列表。
    """
    rows = fetch_all(conn, table, pk_field)
    if not rows:
        return
    with st.expander("修改记录"):
        sel = st.selectbox("选择记录", [r[pk_field] for r in rows], key=f"edit_{table}")
        row = fetch_one(conn, table, pk_field, sel)
        with st.form(f"form_edit_{table}"):
            new = {}
            for item in fields:
                col, label, kind = item[0], item[1], item[2]
                opts = item[3] if len(item) > 3 else None
                cur = row.get(col)
                key = f"e_{table}_{sel}_{col}"
                if kind == "num":
                    new[col] = st.number_input(label, value=float(cur or 0), format="%.2f", key=key)
                elif kind == "sel":
                    choices = opts or []
                    idx = choices.index(cur) if cur in choices else 0
                    new[col] = st.selectbox(label, choices, index=idx, key=key)
                elif kind == "ro":
                    st.text_input(label, value=str(cur or ""), disabled=True, key=key)
                    new[col] = cur
                else:
                    new[col] = st.text_input(label, value=str(cur or ""), key=key)
            if st.form_submit_button("保存修改"):
                payload = {c: v for c, v in new.items() if c != pk_field}
                errs = validator(conn, sel, payload) if validator else []
                if errs:
                    for e in errs:
                        st.error(e)
                else:
                    try:
                        update_row(conn, table, pk_field, sel, payload, operator)
                        st.success("已保存")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))


def import_widget(table: str) -> None:
    from import_export import (
        column_label,
        expected_columns,
        grid_to_rows,
        import_rows,
        normalize_rows,
        parse_csv,
        parse_docx,
        parse_excel,
        required_columns,
        split_grid_to_blocks,
        template_csv,
    )

    st.markdown("**批量导入**（CSV / Excel / Word）")
    cols = expected_columns(table)
    req = required_columns(table)
    with st.expander("查看导入列要求 / 下载模板"):
        st.caption("支持英文列名或中文名，顺序不限；★ 为必填列，其余可缺省（自动填默认值）。")
        st.write("、".join(f"{'★' if c in req else ''}{c}（{column_label(table, c)}）" for c in cols))
        st.caption("「编号」列可留空，系统自动生成；税号等含前导零的列，请在 Excel 中设为文本格式。")
        st.download_button(
            "下载导入模板 CSV",
            template_csv(table).encode("utf-8-sig"),
            f"{table}_模板.csv",
            "text/csv",
        )

    up = st.file_uploader("上传文件（.csv / .xlsx / .docx）", type=["csv", "xlsx", "docx"], key=f"up_{table}")
    if up is None:
        return

    fname = up.name.lower()
    try:
        csv_source = None  # (来源名, 表头, 数据行)
        grids = []         # [(来源名, 原始网格)] for xlsx/docx
        if fname.endswith(".csv"):
            text = up.getvalue().decode("utf-8-sig")
            fieldnames, rows, _m, _e = parse_csv(text, table)
            csv_source = (up.name, fieldnames, rows)
        elif fname.endswith(".xlsx"):
            grids = parse_excel(up.getvalue())
        elif fname.endswith(".docx"):
            grids = parse_docx(up.getvalue())
            if not grids:
                st.error("Word 文档中未找到表格")
                return
        else:
            st.error("不支持的文件类型（仅支持 .csv / .xlsx / .docx）")
            return
    except UnicodeDecodeError:
        st.error("文件编码无法识别，请使用 UTF-8 编码的 CSV")
        return
    except Exception as exc:  # noqa: BLE001
        st.error(f"解析失败：{exc}")
        return

    # 确定表头与数据行
    if csv_source is not None:
        source_name, fieldnames, rows = csv_source
    else:
        if not grids:
            st.error("未解析到任何数据")
            return
        # 一个 sheet / 文档里可能纵向堆叠多张表，先按标题行拆块
        blocks = []  # [(sheet, 标题, 子网格)]
        for sheet_name, grid in grids:
            for title, bg in split_grid_to_blocks(grid):
                blocks.append((sheet_name, title, bg))
        if not blocks:
            st.error("未解析到任何表格")
            return

        # 计算每个块与当前表的匹配度，默认选中最佳块
        scores = []
        for _s, _t, bg in blocks:
            f, _r = grid_to_rows(table, bg)
            nf, _nr, _un = normalize_rows(table, f, [])
            scores.append(len(set(nf) & set(expected_columns(table))))
        best = scores.index(max(scores))
        labels = [f"{s}｜{t or '未命名表'}（{len(bg)} 行）" for s, t, bg in blocks]
        sel = st.selectbox("选择要导入的表格", labels, index=best, key=f"src_{table}")
        sheet_name, title, bg = blocks[labels.index(sel)]
        source_name = f"{sheet_name}｜{title or '未命名表'}"
        fieldnames, rows = grid_to_rows(table, bg)

    norm_fields, norm_rows, unrecognized = normalize_rows(table, fieldnames, rows)
    missing = [c for c in required_columns(table) if c not in set(norm_fields)]

    if missing:
        st.error(f"表头缺少列：{'、'.join(missing)}")
    if unrecognized:
        st.warning(f"存在未识别列（将被忽略）：{'、'.join(unrecognized)}")

    if not missing:
        st.caption(f"来源「{source_name}」共识别 {len(norm_rows)} 行，前 5 行预览：")
        preview = [{column_label(table, c): r.get(c, "") for c in cols} for r in norm_rows[:5]]
        st.dataframe(pd.DataFrame(preview), width="stretch")
        if st.button(f"开始导入 {len(norm_rows)} 行", key=f"imp_{table}"):
            ok, errors = import_rows(conn, table, norm_rows, operator)
            if ok:
                st.success(f"成功导入 {len(ok)} 行")
            if errors:
                st.warning(f"{len(errors)} 行失败：")
                for e in errors:
                    st.write(f"- {e}")
            if ok:
                st.rerun()


# ---------------------------------------------------------------------------
# 页面：客户/供应商
# ---------------------------------------------------------------------------
def page_partners() -> None:
    st.header("客户/供应商表")
    st.caption("统一管理往来单位基础信息，保证抬头和税号一致")
    show_table(pd.DataFrame(fetch_all(conn, "partners", "code")), "partners")

    with st.form("add_partner", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            partner_type = st.selectbox("类型", ["客户", "供应商", "两者"])
            name = st.text_input("名称（与营业执照一致）")
            tax_no = st.text_input("税号")
        with c2:
            default_rate = st.text_input("默认税率", value=get_param(conn, "默认税率", "13%"))
            remark = st.text_input("备注")
        if st.form_submit_button("新增"):
            if not name or not tax_no:
                st.error("名称和税号必填")
            else:
                code = next_partner_code(conn, partner_type)
                try:
                    insert_row(conn, "partners", {
                        "code": code, "name": name, "tax_no": tax_no,
                        "partner_type": partner_type, "default_tax_rate": default_rate,
                        "remark": remark,
                    }, operator)
                    st.success(f"已新增 {code} {name}")
                    st.rerun()
                except sqlite3.IntegrityError as exc:
                    st.error(f"唯一性冲突（税号可能重复）：{exc}")

    edit_form("partners", "code", [
        ("name", "名称", "text"),
        ("tax_no", "税号", "text"),
        ("partner_type", "类型", "sel", ["客户", "供应商", "两者"]),
        ("default_tax_rate", "默认税率", "text"),
        ("remark", "备注", "text"),
    ])
    delete_selector("partners", "code")
    import_widget("partners")


# ---------------------------------------------------------------------------
# 页面：合同
# ---------------------------------------------------------------------------
def page_contracts() -> None:
    st.header("合同表")
    show_table(pd.DataFrame(fetch_all(conn, "contracts", "code")), "contracts")

    partners = fetch_all(conn, "partners", "code")
    pmap = {r["code"]: r for r in partners}
    with st.form("add_contract", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("合同名称")
            partner_code = st.selectbox("客户/供应商", list(pmap.keys()) if pmap else ["（先新增单位）"])
            sign_date = st.date_input("签订日期").strftime("%Y-%m-%d")
        with c2:
            amount_tax = st.text_input("合同金额（含税，可空）")
            amount_no_tax = st.text_input("合同金额（不含税，可空）")
            status = st.selectbox("履约状态", ["未开始", "履行中", "已完成", "已终止"])
        remark = st.text_input("备注")
        code_input = st.text_input("合同编号（留空自动生成）")
        if st.form_submit_button("新增合同"):
            if not name or partner_code == "（先新增单位）":
                st.error("合同名称和客户/供应商必填")
            else:
                rate = pmap[partner_code]["default_tax_rate"]
                at, ant, _, errs = reconcile(amount_tax, amount_no_tax, None, rate)
                if errs:
                    for e in errs:
                        st.error(e)
                else:
                    code = code_input.strip() or next_contract_code(conn)
                    try:
                        insert_row(conn, "contracts", {
                            "code": code, "partner_code": partner_code, "name": name,
                            "amount_tax": at or 0, "amount_no_tax": ant or 0,
                            "sign_date": sign_date, "status": status, "remark": remark,
                        }, operator)
                        st.success(f"已新增合同 {code}")
                        st.rerun()
                    except sqlite3.IntegrityError as exc:
                        st.error(f"合同编号重复或关联错误：{exc}")

    edit_form("contracts", "code", [
        ("partner_code", "客户/供应商编号", "text"),
        ("name", "合同名称", "text"),
        ("amount_tax", "合同金额（含税）", "num"),
        ("amount_no_tax", "合同金额（不含税）", "num"),
        ("sign_date", "签订日期", "text"),
        ("status", "履约状态", "sel", ["未开始", "履行中", "已完成", "已终止"]),
        ("remark", "备注", "text"),
    ])
    delete_selector("contracts", "code")
    import_widget("contracts")


# ---------------------------------------------------------------------------
# 页面：发票
# ---------------------------------------------------------------------------
def page_invoices() -> None:
    st.header("发票表")
    st.caption("跟踪销项/进项发票；作废与红冲会联动锁定原蓝字发票的可核销金额")

    invoices = fetch_all(conn, "invoices", "code")
    for i in invoices:
        i["可核销金额"] = remaining_invoice(conn, i["code"])
        i["已核销"] = written_off_amount(conn, i["code"])
        i["已红冲"] = red_lettered_amount(conn, i["code"])
    show_table(pd.DataFrame(invoices), "invoices")

    partners = fetch_all(conn, "partners", "code")
    pmap = {r["code"]: r for r in partners}
    contracts = fetch_all(conn, "contracts", "code")
    cmap = {r["code"]: r for r in contracts}
    normal_invoices = [i for i in invoices if i["status"] == "正常"]

    with st.form("add_invoice", clear_on_submit=True):
        status = st.selectbox("发票状态", ["正常", "作废", "红冲"], key="add_inv_status")

        if status == "红冲":
            blue_options = {
                f"{i['code']}｜{i['invoice_number']}｜{i['buy_sell_type']}｜{i['partner_code']}｜可核销 {remaining_invoice(conn, i['code']):.2f}": i["code"]
                for i in normal_invoices
            }
            if blue_options:
                related_blue = blue_options[st.selectbox("关联原蓝字发票", list(blue_options.keys()))]
                blue = fetch_one(conn, "invoices", "code", related_blue)
                buy_sell = blue["buy_sell_type"]
                partner_code = blue["partner_code"]
                contract_code = blue["contract_code"]
                default_rate = blue["tax_rate"]
            else:
                related_blue = ""
                buy_sell, partner_code, contract_code, default_rate = "销项", "", "", get_param(conn, "默认税率", "13%")
                st.warning("暂无「正常」状态蓝字发票可红冲")
            st.text_input("购销类型（跟随原蓝字）", value=buy_sell, disabled=True)
            st.text_input("客户/供应商（跟随原蓝字）", value=partner_code, disabled=True)
            c1, c2, c3 = st.columns(3)
            with c1:
                invoice_date = st.date_input("开票日期").strftime("%Y-%m-%d")
                invoice_code = st.text_input("发票代码（可空）")
            with c2:
                invoice_number = st.text_input("发票号码", key="add_inv_number")
                tax_rate = st.text_input("税率", value=default_rate, key="add_inv_tax_rate")
            with c3:
                amount_tax = st.text_input("红冲含税金额（正数，可空自动补算）")
                amount_no_tax = st.text_input("红冲不含税金额（可空）")
                tax_amount = st.text_input("红冲税额（可空）")
            remark = st.text_input("备注")
        else:
            c1, c2, c3 = st.columns(3)
            with c1:
                buy_sell = st.selectbox("购销类型", ["销项", "进项"])
                partner_code = st.selectbox("客户/供应商", list(pmap.keys()) if pmap else ["（先新增单位）"])
                invoice_date = st.date_input("开票日期").strftime("%Y-%m-%d")
            with c2:
                invoice_code = st.text_input("发票代码（可空）")
                invoice_number = st.text_input("发票号码", key="add_inv_number")
                tax_rate = st.text_input("税率", value=get_param(conn, "默认税率", "13%"), key="add_inv_tax_rate")
            with c3:
                amount_tax = st.text_input("含税金额（可空，自动补算）")
                amount_no_tax = st.text_input("不含税金额（可空）")
                tax_amount = st.text_input("税额（可空）")
            contract_code = st.selectbox("合同编号（可空）", ["（无）"] + list(cmap.keys()))
            related_blue = ""
            remark = st.text_input("备注")

        if st.form_submit_button("新增发票"):
            if not invoice_number:
                st.error("发票号码必填")
            else:
                at, ant, ta, errs = reconcile(amount_tax, amount_no_tax, tax_amount, tax_rate)
                if errs:
                    for e in errs:
                        st.error(e)
                elif status == "红冲":
                    if not related_blue:
                        st.error("请先选择要红冲的原蓝字发票")
                    else:
                        verr = validate_red_letter(conn, related_blue, at or 0)
                        if verr:
                            st.error(verr)
                        else:
                            code = next_invoice_code(conn)
                            try:
                                insert_row(conn, "invoices", {
                                    "code": code, "invoice_code": invoice_code or "",
                                    "invoice_number": invoice_number, "invoice_date": invoice_date,
                                    "buy_sell_type": buy_sell, "partner_code": partner_code,
                                    "contract_code": contract_code or None,
                                    "amount_tax": at or 0, "amount_no_tax": ant or 0,
                                    "tax_amount": ta or 0, "tax_rate": tax_rate,
                                    "status": "红冲", "related_invoice_code": related_blue,
                                    "remark": remark,
                                }, operator)
                                st.success(f"已新增红字发票 {code}，原蓝字可核销金额已相应减少")
                                st.rerun()
                            except sqlite3.IntegrityError as exc:
                                st.error(f"唯一性冲突（发票代码+号码可能重复）：{exc}")
                else:
                    if partner_code == "（先新增单位）":
                        st.error("客户/供应商必填")
                    else:
                        derr = validate_direction(pmap[partner_code]["partner_type"], buy_sell)
                        if derr:
                            st.error(derr)
                        else:
                            code = next_invoice_code(conn)
                            try:
                                insert_row(conn, "invoices", {
                                    "code": code, "invoice_code": invoice_code or "",
                                    "invoice_number": invoice_number, "invoice_date": invoice_date,
                                    "buy_sell_type": buy_sell, "partner_code": partner_code,
                                    "contract_code": None if contract_code == "（无）" else contract_code,
                                    "amount_tax": at or 0, "amount_no_tax": ant or 0,
                                    "tax_amount": ta or 0, "tax_rate": tax_rate,
                                    "status": status, "related_invoice_code": "",
                                    "remark": remark,
                                }, operator)
                                st.success(f"已新增发票 {code}")
                                st.rerun()
                            except sqlite3.IntegrityError as exc:
                                st.error(f"唯一性冲突（发票代码+号码可能重复）：{exc}")

    _edit_invoices()
    delete_selector("invoices", "code")
    import_widget("invoices")


def _edit_invoices() -> None:
    """发票修改：金额/状态变更时联动校验可核销金额。"""
    invoices = fetch_all(conn, "invoices", "code")
    if not invoices:
        return
    with st.expander("修改记录"):
        sel = st.selectbox(
            "选择发票",
            [f"{r['code']}｜{r['invoice_number']}｜{r['status']}" for r in invoices],
            key="edit_invoices_sel",
        )
        code = sel.split("｜")[0]
        row = fetch_one(conn, "invoices", "code", code)
        is_red = row["status"] == "红冲"
        status_options = ["红冲"] if is_red else ["正常", "作废"]
        with st.form("form_edit_invoices"):
            c1, c2 = st.columns(2)
            with c1:
                invoice_code = st.text_input("发票代码", value=row["invoice_code"] or "", key="ei_icode")
                invoice_number = st.text_input("发票号码", value=row["invoice_number"], key="ei_inum")
                invoice_date = st.text_input("开票日期", value=row["invoice_date"], key="ei_idate")
                buy_sell = st.selectbox("购销类型", ["销项", "进项"], index=0 if row["buy_sell_type"] == "销项" else 1, key="ei_bs")
            with c2:
                partner_code = st.text_input("客户/供应商编号", value=row["partner_code"], key="ei_p")
                contract_code = st.text_input("合同编号（可空）", value=row["contract_code"] or "", key="ei_c")
                tax_rate = st.text_input("税率", value=row["tax_rate"], key="ei_r")
                status = st.selectbox("发票状态", status_options, index=status_options.index(row["status"]), key="ei_s")
            amount_tax = st.number_input("含税金额", value=float(row["amount_tax"] or 0), format="%.2f", key="ei_at")
            amount_no_tax = st.number_input("不含税金额", value=float(row["amount_no_tax"] or 0), format="%.2f", key="ei_ant")
            tax_amount = st.number_input("税额", value=float(row["tax_amount"] or 0), format="%.2f", key="ei_ta")
            related = st.text_input("关联原蓝字发票编号（红冲）", value=row["related_invoice_code"] or "", disabled=not is_red, key="ei_rel")
            remark = st.text_input("备注", value=row["remark"] or "", key="ei_rm")
            if st.form_submit_button("保存修改"):
                errors = []
                _, _, _, rerrs = reconcile(amount_tax, amount_no_tax, tax_amount, tax_rate)
                errors.extend(rerrs)
                p = fetch_one(conn, "partners", "code", partner_code)
                if p is None:
                    errors.append("客户/供应商编号不存在")
                else:
                    derr = validate_direction(p["partner_type"], buy_sell)
                    if derr:
                        errors.append(derr)
                if is_red:
                    verr = validate_red_letter(conn, related, amount_tax, exclude_red_code=code)
                    if verr:
                        errors.append(verr)
                else:
                    if status == "作废":
                        serr = validate_status_change(conn, code, "作废")
                        if serr:
                            errors.append(serr)
                    aerr = validate_invoice_amount(conn, code, amount_tax)
                    if aerr:
                        errors.append(aerr)
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    try:
                        update_row(conn, "invoices", "code", code, {
                            "invoice_code": invoice_code or "",
                            "invoice_number": invoice_number,
                            "invoice_date": invoice_date,
                            "buy_sell_type": buy_sell,
                            "partner_code": partner_code,
                            "contract_code": contract_code or None,
                            "amount_tax": amount_tax,
                            "amount_no_tax": amount_no_tax,
                            "tax_amount": tax_amount,
                            "tax_rate": tax_rate,
                            "status": status,
                            "related_invoice_code": related or "",
                            "remark": remark,
                        }, operator)
                        st.success("已保存")
                        st.rerun()
                    except sqlite3.IntegrityError as exc:
                        st.error(f"唯一性冲突（发票代码+号码可能重复）：{exc}")


# ---------------------------------------------------------------------------
# 页面：回款
# ---------------------------------------------------------------------------
def page_payments() -> None:
    st.header("回款表（收付款记录）")
    st.caption("「状态」由核销记录自动推导，不手填")

    payments = fetch_all(conn, "payments", "code")
    for p in payments:
        paid = round(p["amount"] - remaining_payment(conn, p["code"]), 2)
        p["已核销金额"] = paid
        p["状态"] = payment_status(p["amount"], paid)
    show_table(pd.DataFrame(payments), "payments")

    partners = fetch_all(conn, "partners", "code")
    pmap = {r["code"]: r for r in partners}
    contracts = fetch_all(conn, "contracts", "code")
    cmap = {r["code"]: r for r in contracts}

    with st.form("add_payment", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            pay_type = st.selectbox("收付类型", ["收款", "付款"])
            partner_code = st.selectbox("客户/供应商", list(pmap.keys()) if pmap else ["（先新增单位）"])
            pay_date = st.date_input("日期").strftime("%Y-%m-%d")
        with c2:
            amount = st.number_input("金额", min_value=0.0, format="%.2f")
            pay_method = st.selectbox("付款方式", ["银行转账", "现金", "承兑汇票", "其他"])
            contract_code = st.selectbox("合同编号（可空）", ["（无）"] + list(cmap.keys()))
        remark = st.text_input("备注")
        if st.form_submit_button("新增回款"):
            if partner_code == "（先新增单位）" or amount <= 0:
                st.error("客户/供应商和金额必填（金额大于 0）")
            else:
                code = next_payment_code(conn)
                try:
                    insert_row(conn, "payments", {
                        "code": code, "pay_date": pay_date, "pay_type": pay_type,
                        "partner_code": partner_code,
                        "contract_code": None if contract_code == "（无）" else contract_code,
                        "amount": amount, "pay_method": pay_method, "remark": remark,
                    }, operator)
                    st.success(f"已新增回款 {code}")
                    st.rerun()
                except sqlite3.IntegrityError as exc:
                    st.error(str(exc))

    def _validate_payment(c, code, payload):
        if payload.get("contract_code") == "":
            payload["contract_code"] = None
        if payload.get("amount") is not None:
            aerr = validate_payment_amount(c, code, payload["amount"])
            return [aerr] if aerr else []
        return []

    edit_form("payments", "code", [
        ("pay_date", "日期", "text"),
        ("pay_type", "收付类型", "sel", ["收款", "付款"]),
        ("partner_code", "客户/供应商编号", "text"),
        ("contract_code", "合同编号", "text"),
        ("amount", "金额", "num"),
        ("pay_method", "付款方式", "sel", ["银行转账", "现金", "承兑汇票", "其他"]),
        ("remark", "备注", "text"),
    ], validator=_validate_payment)
    delete_selector("payments", "code")
    import_widget("payments")


# ---------------------------------------------------------------------------
# 页面：核销
# ---------------------------------------------------------------------------
def page_writeoff() -> None:
    st.header("核销")
    st.caption("把「回款」与「发票」对应起来；一笔回款可核销多张发票")

    payments = fetch_all(conn, "payments", "code")
    invoices = fetch_all(conn, "invoices", "code")
    wo_rows = conn.execute(
        "SELECT id, payment_code, invoice_code, amount, writeoff_date, method, operator"
        " FROM writeoffs ORDER BY id"
    ).fetchall()
    wo_list = [dict(r) for r in wo_rows]

    st.subheader("核销记录")
    st.dataframe(pd.DataFrame(wo_list), width="stretch")

    if not payments:
        st.info("暂无回款记录，先去「回款」页新增")
        return

    pay_options = {
        f"{p['code']}｜{p['pay_type']}｜{p['amount']:.2f}元｜{writeoff_status(conn, p['code'])}": p["code"]
        for p in payments
    }

    # 自动核销
    st.subheader("自动核销（先进先出）")
    auto_sel = st.selectbox("选择回款", list(pay_options.keys()), key="auto_pay")
    if st.button("执行先进先出核销", key="btn_auto"):
        try:
            res = auto_writeoff(conn, pay_options[auto_sel], operator)
            if res:
                total = sum(r["amount"] for r in res)
                st.success(f"核销 {len(res)} 笔，共 {total:,.2f} 元")
                st.rerun()
            else:
                st.info("没有可核销的发票（无匹配方向/单位，或已核销完毕）")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    # 手工核销
    st.subheader("手工指定核销")
    normal_inv = [i for i in invoices if i["status"] == "正常"]
    if normal_inv:
        inv_options = {
            f"{i['code']}｜{i['buy_sell_type']}｜{i['invoice_number']}｜剩余 {remaining_invoice(conn, i['code']):.2f}": i["code"]
            for i in normal_inv
        }
        with st.form("manual_wo"):
            m_pay = st.selectbox("选择回款", list(pay_options.keys()), key="m_pay")
            m_inv = st.selectbox("选择发票", list(inv_options.keys()), key="m_inv")
            m_amt = st.number_input("核销金额", min_value=0.0, step=100.0, format="%.2f")
            if st.form_submit_button("提交核销"):
                try:
                    manual_writeoff(conn, pay_options[m_pay], inv_options[m_inv], m_amt, operator)
                    st.success("核销成功")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
    else:
        st.info("暂无「正常」状态发票可核销")

    # 撤销核销
    if wo_list:
        st.subheader("撤销核销")
        cancel_sel = st.selectbox(
            "选择核销记录",
            [f"{r['id']}｜{r['payment_code']}→{r['invoice_code']}｜{r['amount']:.2f}" for r in wo_list],
            key="c_id",
        )
        confirm = st.checkbox("确认撤销（高危操作，会记审计日志）", key="c_confirm")
        if st.button("撤销该核销", key="c_btn") and confirm:
            try:
                cancel_writeoff(conn, int(cancel_sel.split("｜")[0]), operator)
                st.success("已撤销")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))


# ---------------------------------------------------------------------------
# 页面：客户全景
# ---------------------------------------------------------------------------
def page_panorama() -> None:
    st.header("客户全景查询")
    partners = fetch_all(conn, "partners", "code")
    if not partners:
        st.info("暂无单位")
        return
    pmap = {r["code"]: r for r in partners}
    sel = st.selectbox("选择客户/供应商", [f"{r['code']}｜{r['name']}" for r in partners])
    code = sel.split("｜")[0]
    p = pmap[code]
    st.subheader(f"{p['code']} {p['name']}（{p['partner_type']}，税号 {p['tax_no']}）")

    contracts = [r for r in fetch_all(conn, "contracts", "code") if r["partner_code"] == code]
    invoices = [r for r in fetch_all(conn, "invoices", "code") if r["partner_code"] == code]
    payments = [r for r in fetch_all(conn, "payments", "code") if r["partner_code"] == code]

    for i in invoices:
        i["已核销"] = round(i["amount_tax"] - remaining_invoice(conn, i["code"]), 2)
        i["未核销"] = remaining_invoice(conn, i["code"])
    for py in payments:
        paid = round(py["amount"] - remaining_payment(conn, py["code"]), 2)
        py["已核销金额"] = paid
        py["状态"] = payment_status(py["amount"], paid)

    total_contract = sum(c["amount_tax"] for c in contracts)
    total_paid = sum(py["已核销金额"] for py in payments)

    m1, m2, m3 = st.columns(3)
    m1.metric("合同总额（含税）", f"{total_contract:,.2f}")
    m2.metric("已核销金额", f"{total_paid:,.2f}")
    m3.metric("未回款金额", f"{total_contract - total_paid:,.2f}")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**合同**")
        st.dataframe(pd.DataFrame(contracts), width="stretch")
        st.markdown("**发票**")
        st.dataframe(pd.DataFrame(invoices), width="stretch")
    with c2:
        st.markdown("**回款**")
        st.dataframe(pd.DataFrame(payments), width="stretch")


# ---------------------------------------------------------------------------
# 页面：报表
# ---------------------------------------------------------------------------
def page_reports() -> None:
    st.header("报表")
    tab1, tab2, tab3 = st.tabs(["应收未回款", "合同执行进度", "开票未核销"])

    with tab1:
        rows = conn.execute(
            "SELECT i.partner_code AS 客户,"
            "       SUM(i.amount_tax) AS 开票含税,"
            "       SUM(i.amount_tax"
            "           - COALESCE((SELECT SUM(w.amount) FROM writeoffs w WHERE w.invoice_code = i.code), 0)"
            "           - COALESCE((SELECT SUM(r.amount_tax) FROM invoices r WHERE r.status = '红冲' AND r.related_invoice_code = i.code), 0)"
            "       ) AS 未回款"
            " FROM invoices i"
            " WHERE i.status = '正常' AND i.buy_sell_type = '销项'"
            " GROUP BY i.partner_code"
        ).fetchall()
        df1 = pd.DataFrame([dict(r) for r in rows])
        st.dataframe(df1, width="stretch")
        download_csv(df1, "应收未回款.csv")

    with tab2:
        rows = conn.execute(
            "SELECT c.code AS 合同编号, c.name AS 合同名称, c.amount_tax AS 合同含税,"
            "       COALESCE(SUM(CASE WHEN i.status = '正常' THEN i.amount_tax ELSE 0 END), 0)"
            "       - COALESCE(SUM(CASE WHEN i.status = '红冲' THEN i.amount_tax ELSE 0 END), 0) AS 已开票含税,"
            "       CASE WHEN c.amount_tax > 0"
            "            THEN ROUND((COALESCE(SUM(CASE WHEN i.status = '正常' THEN i.amount_tax ELSE 0 END), 0)"
            "                        - COALESCE(SUM(CASE WHEN i.status = '红冲' THEN i.amount_tax ELSE 0 END), 0)) / c.amount_tax * 100, 1)"
            "            ELSE 0 END AS 开票进度_百分比"
            " FROM contracts c LEFT JOIN invoices i ON i.contract_code = c.code"
            " GROUP BY c.code ORDER BY c.code"
        ).fetchall()
        df2 = pd.DataFrame([dict(r) for r in rows])
        st.dataframe(df2, width="stretch")
        download_csv(df2, "合同执行进度.csv")

    with tab3:
        data = []
        for i in fetch_all(conn, "invoices", "code"):
            if i["status"] != "正常":
                continue
            rem = remaining_invoice(conn, i["code"])
            if rem > 0.01:
                data.append({
                    "发票编号": i["code"], "发票号码": i["invoice_number"],
                    "购销类型": i["buy_sell_type"], "客户/供应商": i["partner_code"],
                    "含税金额": i["amount_tax"], "未核销": rem,
                })
        df3 = pd.DataFrame(data)
        st.dataframe(df3, width="stretch")
        download_csv(df3, "开票未核销.csv")


# ---------------------------------------------------------------------------
# 页面：参数
# ---------------------------------------------------------------------------
def page_params() -> None:
    st.header("参数设置")
    params = fetch_all(conn, "params", "key")
    st.dataframe(pd.DataFrame(params), width="stretch")

    with st.form("set_param"):
        key = st.selectbox("参数", [p["key"] for p in params])
        value = st.text_input("值", value=get_param(conn, key) or "")
        if st.form_submit_button("保存"):
            set_param(conn, key, value, operator)
            st.success("已保存")
            st.rerun()


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
ROUTES = {
    "客户/供应商": page_partners,
    "合同": page_contracts,
    "发票": page_invoices,
    "回款": page_payments,
    "核销": page_writeoff,
    "客户全景": page_panorama,
    "报表": page_reports,
    "参数": page_params,
}

ROUTES[page]()
