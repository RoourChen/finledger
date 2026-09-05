# 财会助手（finledger）

内网 Web、多人共用的财务表格工具：六张表（客户/供应商、合同、发票、回款、核销记录、参数）跑通「合同—发票—回款—核销」最小业务闭环。

> 需求文档：`../agent-blueprint/tools/caiwuzhushou.md`

## 目录结构

```
finledger/
  app.py            # Streamlit 内网 Web 界面（入口）
  db.py             # SQLite 数据层：六表 + 审计日志 + 默认参数
  rules.py          # 业务规则引擎：金额勾稽 / 方向校验 / 回款状态推导
  writeoff.py       # 核销引擎：先进先出 / 手工指定 / 撤销
  import_export.py  # 导入导出：CSV / Excel(.xlsx) / Word(.docx)，表头校验 + 编号自动生成
  knowledge/
    schema.md       # 六张表字段说明
    rules.md        # 业务规则说明
  requirements.txt
```

## 快速开始

```bash
cd finledger
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 先配置密码（见下方“访问密码”），否则应用会拒绝启动
cp .env.example .env   # 编辑 .env，填入生成的 FINLEDGER_PASSWORD_HASH
chmod 600 .env         # 限制仅当前账号可读，防止同机其他账号读取哈希
set -a; source .env; set +a

.venv/bin/streamlit run app.py
```

访问密码：**生产环境必须设置 `FINLEDGER_PASSWORD_HASH`**（未设置则拒绝启动）；本地开发可临时用明文 `FINLEDGER_PASSWORD`（启动时明确警告）。
操作人：环境变量 `FINLEDGER_OPERATOR`（默认 `admin`），也可在侧边栏临时填写。

```bash
# 1. 生成密码哈希（交互式输入，不回显）
.venv/bin/python password_tool.py

# 2. 把输出的 FINLEDGER_PASSWORD_HASH='...' 写入 .env（已被 .gitignore 忽略，勿提交）
#    切勿直接粘贴到命令行：会进入 shell 历史，且进程启动期间可能被同机用户看到
cp .env.example .env   # 然后编辑，填入生成的哈希
chmod 600 .env         # 限制仅当前账号可读

# 3. 从 .env 加载并启动（哈希不出现在命令行 / shell 历史）
set -a; source .env; set +a
.venv/bin/streamlit run app.py
```

> **.env 安全红线**：`.env` 必须 `chmod 600`，且只由可信运维人员维护；
> `source .env` 会按 shell 脚本执行其内容，切勿让不可信内容进入该文件。

数据库文件默认生成在 `finledger.db`（可用环境变量 `FINLEDGER_DB` 指定路径），单文件易备份。

## 核心约束（已内建）

| 约束 | 位置 |
|---|---|
| 发票 `(发票代码, 发票号码)` 唯一，防重复录入 | `db.py` invoices 表 `UNIQUE` |
| 核销 `(回款单号, 发票编号)` 唯一，防重复核销 | `db.py` writeoffs 表 `UNIQUE` |
| 审计日志：操作类型/操作人/时间/变更前后数据 | `db.py` audit_log + `audit()` |
| 参数表默认值：默认税率 13%、核销规则 先进先出 | `db.py` `DEFAULT_PARAMS` + `init_db()` |
| 回款「状态」不落库，由核销记录自动推导 | `writeoff.py` `writeoff_status()` |

## 使用提示（一期打磨）

- **批量导入**：每张台账页底部有「批量导入」，支持 **CSV / Excel(.xlsx) / Word(.docx)** 三种格式。
  - 自动识别表头行（可跳过标题行/空行）；一个 sheet/文档里纵向堆叠多张表时自动按「表X：…」标题拆分，可下拉选择。
  - 表头支持英文列名/中文名/常见别名（如「编号」「纳税人识别号」「合同金额（含税）」），全角半角括号自动归一。
  - 税率自动归一为百分比（0.13 → 13%），Excel 日期序列号自动转 YYYY-MM-DD。
  - ★ 为必填列、其余缺省自动填默认值；上传后先校验并预览前 5 行；「编号」列留空会自动生成（KH001 / HT001 / FP001 / HK001）。
- **报表导出**：「报表」页三张报表（应收未回款 / 合同执行进度 / 开票未核销）均可一键导出 CSV。
- **台账导出**：每张台账页右上可导出当前表为 CSV。

## 运行测试

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -v
```

测试覆盖（财务准确性底线，回归必跑）：
- 建库与初始化：六表 + 参数默认值 + 审计日志（操作类型/操作人/时间/变更前后）
- 业务规则：红冲联动减少可核销金额、红字/作废不可核销、回款状态自动推导
- 数据完整性：发票代码+号码唯一、核销（回款+发票）唯一、税号/合同/回款编号唯一
- 修改金额校验：已关联核销的发票/回款金额不允许改小，需先撤销核销
- UI 冒烟（AppTest）：八页面渲染、新增/修改/删除表单、核销页交互、红冲/作废界面联动

## 无界面快速验证核心层

核心层（`db.py` / `rules.py` / `writeoff.py`，以及 `import_export.py` 的 CSV 部分）只依赖标准库，可脱离 Streamlit 单独测试；Excel/Word 解析需 pandas + openpyxl + python-docx：

```bash
python3 -c "
from db import get_conn, init_db, get_param, fetch_all
from writeoff import auto_writeoff, manual_writeoff, writeoff_status
conn = get_conn(':memory:')
init_db(conn)
print('默认税率 =', get_param(conn, '默认税率'))
print('默认核销规则 =', get_param(conn, '默认核销规则'))
print('审计日志表字段 =', [r['name'] for r in conn.execute('PRAGMA table_info(audit_log)')])
"
```

## 下一步（待办）

- 二期：乱格式表格自动识别（LLM + 脱敏）、自然语言查询、认证到期提醒
