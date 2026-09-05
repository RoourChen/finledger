#!/usr/bin/env python3
"""
财会助手 —— 数据导入导出

支持三种来源：
  - CSV（第一行为表头）
  - Excel（.xlsx，多 sheet；自动识别表头行，可跳过标题行/空行）
  - Word（.docx，多表格；自动识别表头行）

进阶能力（针对真实数据）：
  - 一个 sheet / 文档里纵向堆叠多张表：自动按「表X：…」标题行拆分，可下拉选择
  - 表头支持英文列名、中文标准名、常见别名（「编号」「纳税人识别号」「合同金额（含税）」等）
  - 全角/半角括号自动归一
  - 税率统一成百分比（0.13 → 13%）；Excel 日期序列号自动转 YYYY-MM-DD
"""
from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from db import (
    fetch_all,
    insert_row,
    next_contract_code,
    next_invoice_code,
    next_partner_code,
    next_payment_code,
)

# 固定字段顺序（导出列、模板列一致）
TABLE_COLUMNS: Dict[str, List[str]] = {
    "partners": ["code", "name", "tax_no", "partner_type", "default_tax_rate", "remark"],
    "contracts": ["code", "partner_code", "name", "amount_tax", "amount_no_tax", "sign_date", "status", "remark"],
    "invoices": [
        "code", "invoice_code", "invoice_number", "invoice_date", "buy_sell_type",
        "partner_code", "contract_code", "amount_tax", "amount_no_tax", "tax_amount",
        "tax_rate", "status", "related_invoice_code", "remark",
    ],
    "payments": ["code", "pay_date", "pay_type", "partner_code", "contract_code", "amount", "pay_method", "remark"],
}

# 金额列：导入时转成 float
NUMERIC_COLUMNS = {"amount_tax", "amount_no_tax", "tax_amount", "amount"}
# 税率列：导入时统一成百分比（0.13 → 13%）
RATE_COLUMNS = {"default_tax_rate", "tax_rate"}
# 日期列：Excel 序列日期自动转 YYYY-MM-DD
DATE_COLUMNS = {"sign_date", "invoice_date", "pay_date"}

# 通用字段中文名（编号列按表单独给）
COLUMN_LABELS: Dict[str, str] = {
    "name": "名称", "tax_no": "税号", "partner_type": "类型",
    "default_tax_rate": "默认税率", "remark": "备注",
    "partner_code": "客户/供应商编号", "amount_tax": "含税金额",
    "amount_no_tax": "不含税金额", "sign_date": "签订日期", "status": "状态",
    "invoice_code": "发票代码", "invoice_number": "发票号码",
    "invoice_date": "开票日期", "buy_sell_type": "购销类型",
    "contract_code": "合同编号", "tax_amount": "税额", "tax_rate": "税率",
    "related_invoice_code": "关联原发票号", "pay_date": "日期",
    "pay_type": "收付类型", "amount": "金额", "pay_method": "付款方式",
}

CODE_LABELS: Dict[str, str] = {
    "partners": "客户/供应商编号", "contracts": "合同编号",
    "invoices": "发票编号（内部）", "payments": "回款单号",
}

# 常见表头别名（真实数据里的各种叫法）
GENERIC_ALIASES: Dict[str, List[str]] = {
    "code": ["编号", "序号"],
    "name": ["名称", "单位名称", "公司名称", "企业名称", "合同名称"],
    "tax_no": ["税号", "纳税人识别号", "纳税识别号", "统一社会信用代码"],
    "partner_type": ["类型", "单位类型", "往来类型"],
    "default_tax_rate": ["默认税率", "税率"],
    "remark": ["备注", "说明"],
    "partner_code": ["客户/供应商编号", "单位编号", "客户编号", "供应商编号"],
    "amount_tax": ["含税金额", "价税合计", "金额(含税)", "含税价", "合同金额(含税)", "合同金额（含税）"],
    "amount_no_tax": ["不含税金额", "金额(不含税)", "不含税价", "合同金额(不含税)", "合同金额（不含税）"],
    "tax_amount": ["税额", "税额(元)"],
    "tax_rate": ["税率"],
    "sign_date": ["签订日期", "签约日期"],
    "status": ["状态", "履约状态", "发票状态"],
    "invoice_code": ["发票代码"],
    "invoice_number": ["发票号码", "发票号"],
    "invoice_date": ["开票日期"],
    "buy_sell_type": ["购销类型", "方向", "销进项"],
    "contract_code": ["合同编号", "合同号"],
    "related_invoice_code": ["关联原发票号", "原发票号", "原蓝字发票号"],
    "pay_date": ["日期", "收付款日期"],
    "pay_type": ["收付类型", "类型"],
    "amount": ["金额", "收付金额"],
    "pay_method": ["付款方式", "方式"],
}

# 必填列（其余列可缺省，缺省用默认值 / 自动生成）
REQUIRED_COLUMNS: Dict[str, List[str]] = {
    "partners": ["name", "tax_no"],
    "contracts": ["partner_code", "name"],
    "invoices": ["invoice_number", "partner_code"],
    "payments": ["partner_code", "amount"],
}

# 缺省列的空值默认（金额列另在导入时补 0）
TABLE_DEFAULTS: Dict[str, Dict[str, str]] = {
    "partners": {"partner_type": "客户", "default_tax_rate": "13%"},
    "contracts": {"status": "未开始"},
    "invoices": {"buy_sell_type": "销项", "tax_rate": "13%", "status": "正常"},
    "payments": {"pay_type": "收款", "pay_method": "银行转账"},
}


def column_label(table: str, col: str) -> str:
    if col == "code":
        return CODE_LABELS.get(table, "编号")
    return COLUMN_LABELS.get(col, col)


def expected_columns(table: str) -> List[str]:
    return TABLE_COLUMNS[table]


def required_columns(table: str) -> List[str]:
    return REQUIRED_COLUMNS[table]


def template_csv(table: str) -> str:
    return ",".join(TABLE_COLUMNS[table]) + "\n"


def validate_header(fieldnames: List[str], table: str) -> Tuple[List[str], List[str]]:
    """校验（英文列名）表头，返回 (缺列, 多余列)。"""
    cols = set(TABLE_COLUMNS[table])
    given = set(fieldnames or [])
    missing = [c for c in TABLE_COLUMNS[table] if c not in given]
    extra = [c for c in (fieldnames or []) if c not in cols]
    return missing, extra


def _norm_text(s) -> str:
    """全角/半角归一 + 去空白。"""
    return (
        str(s).strip()
        .replace("（", "(").replace("）", ")")
        .replace("：", ":").replace("　", " ")
    )


def _label_map(table: str) -> Dict[str, str]:
    """表头 → 规范字段名 的映射（英文 + 中文标准名 + 别名，均归一化）。"""
    m: Dict[str, str] = {}
    for col in TABLE_COLUMNS[table]:
        m[_norm_text(col)] = col
        m[_norm_text(column_label(table, col))] = col
        for alias in GENERIC_ALIASES.get(col, []):
            m[_norm_text(alias)] = col
    return m


def normalize_rows(table: str, fieldnames: List[str], rows: List[Dict]) -> Tuple[List[str], List[Dict], List[str]]:
    """把表头（英文/中文/别名）映射到规范字段名。返回 (规范表头, 规范数据行, 未识别表头)。"""
    m = _label_map(table)
    norm_fields: List[str] = []
    unrecognized: List[str] = []
    for f in fieldnames:
        f = _norm_text(f)
        if f in m:
            norm_fields.append(m[f])
        elif f:
            unrecognized.append(f)

    norm_rows: List[Dict] = []
    for r in rows:
        nr = {}
        for orig_k, v in r.items():
            k = _norm_text(orig_k)
            if k in m:
                nr[m[k]] = v
        norm_rows.append(nr)
    return norm_fields, norm_rows, unrecognized


# ---------------------------------------------------------------------------
# 解析：CSV / Excel / Word
# ---------------------------------------------------------------------------

def parse_csv(text: str, table: str) -> Tuple[List[str], List[Dict], List[str], List[str]]:
    """解析 CSV 文本（第一行为表头），返回 (表头, 数据行, 缺列, 多余列)。"""
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = list(reader.fieldnames or [])
    missing, extra = validate_header(fieldnames, table)
    rows = list(reader)
    return fieldnames, rows, missing, extra


def _cell_str(v) -> str:
    """把 Excel 单元格值转成干净的字符串（处理 numpy 标量、日期、NaN）。"""
    if v is None:
        return ""
    try:
        if hasattr(v, "item"):
            v = v.item()
    except (ValueError, AttributeError):
        pass
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return str(int(v))
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    return "" if s in ("NaT", "nan", "None") else s


def parse_excel(data_bytes: bytes) -> List[Tuple[str, List[List[str]]]]:
    """解析 .xlsx，返回 [(sheet 名, 原始网格), ...]，网格为字符串二维数组（不假设表头位置）。"""
    import pandas as pd  # 惰性导入

    sheets = pd.read_excel(io.BytesIO(data_bytes), sheet_name=None, header=None)
    result = []
    for sheet_name, df in sheets.items():
        grid = [[_cell_str(v) for v in row] for row in df.values.tolist()]
        result.append((str(sheet_name), grid))
    return result


def parse_docx(data_bytes: bytes) -> List[Tuple[str, List[List[str]]]]:
    """解析 .docx，返回 [(表名, 原始网格), ...]。"""
    from docx import Document  # 惰性导入

    doc = Document(io.BytesIO(data_bytes))
    result = []
    for idx, table in enumerate(doc.tables, start=1):
        grid = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        result.append((f"表 {idx}", grid))
    return result


def find_header_row(table: str, grid: List[List[str]], max_scan: int = 10) -> int:
    """在网格前若干行中找表头行（匹配列名最多的一行），返回行索引。"""
    cands = _label_map(table)
    best_idx, best_score = 0, -1
    for i in range(min(max_scan, len(grid))):
        score = sum(1 for cell in grid[i] if _norm_text(cell) in cands)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def grid_to_rows(table: str, grid: List[List[str]]) -> Tuple[List[str], List[Dict]]:
    """从原始网格中识别表头并提取数据行，返回 (表头, 数据行)。"""
    hi = find_header_row(table, grid)
    if hi >= len(grid):
        return [], []
    fieldnames = [str(c).strip() for c in grid[hi]]
    rows: List[Dict] = []
    for r in grid[hi + 1:]:
        row = {}
        for j, h in enumerate(fieldnames):
            if h:
                row[h] = r[j] if j < len(r) else ""
        rows.append(row)
    return fieldnames, rows


def _title_of(row: List[str]) -> str:
    """识别「表X：…」标题行，返回标题文本；否则返回空串。"""
    for c in row:
        s = str(c).strip()
        if s.startswith("表") and ("：" in s or ":" in s):
            return s
    return ""


def split_grid_to_blocks(grid: List[List[str]]) -> List[Tuple[str, List[List[str]]]]:
    """把一个 sheet / 文档网格按「表X：…」标题行拆成多个表块。

    返回 [(标题, 子网格), ...]；无标题时返回 [("", 整个网格)]。
    """
    blocks: List[Tuple[str, List[List[str]]]] = []
    current_title = ""
    current: List[List[str]] = []
    for row in grid:
        t = _title_of(row)
        if t:
            if current:
                blocks.append((current_title, current))
            current_title = t
            current = []
        else:
            current.append(row)
    if current:
        blocks.append((current_title, current))

    # 只保留有内容的块
    return [
        (t, rows) for t, rows in blocks
        if any(any(_norm_text(c) for c in r) for r in rows)
    ]


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def _normalize_rate(v) -> str:
    """把 '0.13' / '13' / '13%' 统一成 '13%'。"""
    s = str(v).strip()
    if s == "" or s.endswith("%"):
        return s
    try:
        f = float(s)
    except ValueError:
        return s
    if f > 1:
        return f"{f:g}%"
    return f"{f * 100:g}%"


def _excel_date_to_str(v) -> str:
    """Excel 日期序列号（如 45397）→ YYYY-MM-DD；非序列号原样返回。"""
    s = str(v).strip()
    if s == "":
        return s
    try:
        n = float(s)
    except ValueError:
        return s
    if 20000 < n < 80000:  # Excel 序列日期范围（约 1954 ~ 2118 年）
        return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
    return s


def _auto_code(conn, table: str, data: Dict) -> str:
    if table == "partners":
        return next_partner_code(conn, data.get("partner_type", "客户"))
    if table == "contracts":
        return next_contract_code(conn)
    if table == "invoices":
        return next_invoice_code(conn)
    if table == "payments":
        return next_payment_code(conn)
    return ""


def import_rows(conn, table: str, rows: List[Dict], operator: str = "") -> Tuple[List[str], List[str]]:
    """导入规范数据行，返回 (成功编号列表, 错误列表)。每行独立，单行失败不中断。"""
    cols = TABLE_COLUMNS[table]
    ok: List[str] = []
    errors: List[str] = []
    for i, raw in enumerate(rows, start=2):  # 第 1 行是表头
        data = {c: (raw.get(c) or "").strip() for c in cols}

        # 跳过空数据行（必填列全空，通常是备注/标题行）
        if all(str(data.get(c) or "").strip() == "" for c in REQUIRED_COLUMNS[table]):
            continue

        # 缺省列填默认值
        for col, default in TABLE_DEFAULTS.get(table, {}).items():
            if data.get(col) == "":
                data[col] = default

        # 税率统一成百分比
        for rc in RATE_COLUMNS:
            if rc in data and data[rc] != "":
                data[rc] = _normalize_rate(data[rc])

        # 日期序列号转 YYYY-MM-DD
        for dc in DATE_COLUMNS:
            if dc in data and data[dc] != "":
                data[dc] = _excel_date_to_str(data[dc])

        # 编号留空自动生成
        if data.get("code") == "":
            data["code"] = _auto_code(conn, table, data)

        # 金额列转换
        bad = False
        for num_col in NUMERIC_COLUMNS:
            if num_col not in data:
                continue
            val = str(data[num_col] or "").strip()
            if val == "":
                data[num_col] = 0.0
                continue
            try:
                data[num_col] = float(val)
            except (TypeError, ValueError):
                errors.append(f"第 {i} 行：列「{num_col}」不是数字（值：{val!r}）")
                bad = True
                break
        if bad:
            continue

        # 外键可空列：空字符串 -> NULL，避免 FK 约束失败
        if table in ("invoices", "payments") and data.get("contract_code") == "":
            data["contract_code"] = None

        try:
            insert_row(conn, table, data, operator)
            ok.append(data.get("code", str(i)))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"第 {i} 行：{exc}")
    return ok, errors


def import_csv(conn, table: str, path: str, operator: str = "") -> Tuple[List[str], List[str]]:
    """按路径导入 CSV（先校验表头再导入）。"""
    with open(path, newline="", encoding="utf-8-sig") as f:
        text = f.read()
    _fieldnames, rows, missing, _extra = parse_csv(text, table)
    if missing:
        return [], [f"表头缺少列：{missing}"]
    return import_rows(conn, table, rows, operator)


def export_csv(conn, table: str, path: str) -> str:
    """把一张表导出为 CSV（UTF-8 with BOM，Excel 可直接打开）。"""
    cols = TABLE_COLUMNS[table]
    rows = fetch_all(conn, table)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({c: r.get(c, "") for c in cols})
    return path
