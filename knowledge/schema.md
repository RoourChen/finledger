# 六张表字段说明（schema）

> 与 SQLite 实际表结构一致，见 `../db.py` 的 `SCHEMA_SQL`。

## partners 客户/供应商表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| code | TEXT | PRIMARY KEY | 客户/供应商编号，如 KH001 / GYS001 |
| name | TEXT | NOT NULL | 名称（与营业执照一致） |
| tax_no | TEXT | NOT NULL UNIQUE | 税号（纳税人识别号，唯一） |
| partner_type | TEXT | 默认 '客户' | 类型：客户 / 供应商 / 两者 |
| default_tax_rate | TEXT | 默认 '13%' | 默认税率 |
| remark | TEXT | | 备注 |

## contracts 合同表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| code | TEXT | PRIMARY KEY | 合同编号 |
| partner_code | TEXT | NOT NULL FK→partners | 客户/供应商编号 |
| name | TEXT | NOT NULL | 合同名称 |
| amount_tax | REAL | | 合同金额（含税） |
| amount_no_tax | REAL | | 合同金额（不含税） |
| sign_date | TEXT | | 签订日期 YYYY-MM-DD |
| status | TEXT | 默认 '未开始' | 履约状态：未开始/履行中/已完成/已终止 |
| remark | TEXT | | 备注 |

## invoices 发票表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| code | TEXT | PRIMARY KEY | 发票编号（系统内部唯一，FP001） |
| invoice_code | TEXT | | 发票代码（可空） |
| invoice_number | TEXT | NOT NULL | 发票号码 |
| invoice_date | TEXT | | 开票日期 |
| buy_sell_type | TEXT | 默认 '销项' | 购销类型：销项/进项 |
| partner_code | TEXT | NOT NULL FK→partners | 客户/供应商编号 |
| contract_code | TEXT | FK→contracts（可空） | 合同编号 |
| amount_tax | REAL | | 发票含税金额 |
| amount_no_tax | REAL | | 发票不含税金额 |
| tax_amount | REAL | | 税额 |
| tax_rate | TEXT | 默认 '13%' | 税率 |
| status | TEXT | 默认 '正常' | 发票状态：正常/作废/红冲 |
| related_invoice_code | TEXT | | 关联原蓝字发票的内部编号（红字发票填） |
| remark | TEXT | | 备注 |

> **唯一约束**：`UNIQUE (invoice_code, invoice_number)` —— 代码+号码重复即拦截。
> **可核销金额**（不落库，实时计算）：正常发票 = 含税金额 − 已核销 − 已红冲；作废/红冲发票 = 0。
> 红字发票金额以正数登记（红冲金额量）。

## payments 回款表（收付款记录）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| code | TEXT | PRIMARY KEY | 回款单号 |
| pay_date | TEXT | | 日期 |
| pay_type | TEXT | 默认 '收款' | 收付类型：收款/付款 |
| partner_code | TEXT | NOT NULL FK→partners | 客户/供应商编号 |
| contract_code | TEXT | FK→contracts（可空） | 合同编号 |
| amount | REAL | | 金额（含税） |
| pay_method | TEXT | 默认 '银行转账' | 付款方式 |
| remark | TEXT | | 备注 |

> 无 `状态` 列：状态由核销记录自动推导（待核销/部分核销/已核销）。

## writeoffs 核销记录表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | INTEGER | PRIMARY KEY AUTOINCREMENT | 核销 ID |
| payment_code | TEXT | NOT NULL FK→payments | 回款单号 |
| invoice_code | TEXT | NOT NULL FK→invoices | 发票编号 |
| amount | REAL | | 核销金额（含税） |
| writeoff_date | TEXT | | 核销日期 |
| method | TEXT | 默认 '手工指定' | 核销方式：先进先出/手工指定 |
| operator | TEXT | | 操作人 |

> **唯一约束**：`UNIQUE (payment_code, invoice_code)` —— 同一回款对同一发票只能有一条核销。

## params 参数表

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| key | TEXT | PRIMARY KEY | 参数名 |
| value | TEXT | NOT NULL | 参数值 |
| description | TEXT | | 说明 |

默认值：默认核销规则=先进先出、默认税率=13%、是否允许预收款挂账=否、是否强制合同关联=否、部分回款进度显示方式=按核销金额。

## audit_log 审计日志表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PRIMARY KEY AUTOINCREMENT | 自增 |
| operation_type | TEXT | INSERT/UPDATE/DELETE/WRITEOFF/CANCEL_WRITEOFF |
| operator | TEXT | 操作人 |
| operation_time | TEXT | 操作时间（ISO） |
| table_name | TEXT | 表名 |
| record_id | TEXT | 记录主键 |
| before_data | TEXT | 变更前数据（JSON） |
| after_data | TEXT | 变更后数据（JSON） |
