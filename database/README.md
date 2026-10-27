# 数据库说明

系统使用 DuckDB 单文件存储，默认路径 `data/fund_database.duckdb`。表结构、UPSERT 和查询实现都在 `database/`。

## 表结构

| 表 | 字段 | 主键 |
| --- | --- | --- |
| `fund_basic` | `ts_code, name, management, custodian, fund_type, found_date, due_date, list_date, issue_date, delist_date, issue_amount, m_fee, c_fee, duration_year, p_value, min_amount, exp_return, benchmark, status, invest_type, type, trustee, purc_startdate, redm_startdate, market` | `ts_code` |
| `fund_nav` | `ts_code, ann_date, nav_date, unit_nav, accum_nav, adj_nav, accum_div` | `ts_code, nav_date, ann_date` |
| `fund_dividend` | `ts_code, ann_date, div_proc, record_date, ex_date, pay_date, div_cash, net_ex_date, account_date` | `ts_code, ann_date, ex_date, div_proc` |
| `trade_calendar` | `exchange, cal_date, is_open` | `exchange, cal_date` |
| `fund_manager` | `ts_code, ann_date, name, begin_date, end_date, edu, resume` | `ts_code, name, begin_date, ann_date` |
| `fund_asset` | `ts_code, ann_date, nav_date, net_asset, total_netasset` | `ts_code, nav_date, ann_date` |

类型约定：

- 代码和文本：`VARCHAR`。
- 日期：`DATE`。
- 净值、金额和费率：`DECIMAL(24,10)`。
- 日历开闭市：`INTEGER`，限制为 0 或 1。

## 写入

### 清洗

`etl/import_snapshot.py::normalize` 被快照导入、添加和更新流程共用：

- 仅保留 schema 中定义的列。
- 空字符串和空格转为 NULL。
- 日期按 `YYYYMMDD` 解析。
- Decimal 从源字符串转换，避免先转浮点损失精度。
- 去除完全重复行。
- 主键缺失或同主键冲突时拒绝导入。

### UPSERT

`database/writer.py`：

- 按主键比较，仅写入新增或变化记录。
- 先按 schema 类型转换到临时表，再统计新增、更新、未变化数量。
- 使用 `ON CONFLICT ... DO UPDATE`，重复运行保持幂等。
- 标识符只来自固定 schema，用户数据通过注册表参数传入。

### 事务

- 单基金快照导入：四张表同一事务，失败全部回滚。
- 日常更新：`basic + nav` 同批提交；`dividend`、`manager`、`asset` 各自提交。
- 不删除历史记录。

### 建表

只使用 `CREATE TABLE IF NOT EXISTS`。字段变更需要单独迁移，不能依赖重复初始化修改旧表。

## 查询

`database/repository.py` 定义 `FundRepository` Protocol，DuckDB、CSV 和 iFinD 查询实现向服务层提供统一格式。iFinD 适配器只在线取数，不创建数据库；其范围和公告版本差异见 [数据源](../docs/data-sources.md#ifind)。

| 方法 | 返回 |
| --- | --- |
| `list_available_funds()` | 有净值的代码与名称 |
| `get_nav(code, start, end)` | `nav_date, unit_nav, accum_nav, adj_nav` |
| `get_calendar()` | SSE `cal_date, is_open` |
| `get_basic(code)` | `found_date, fund_type, benchmark` |
| `get_managers(code)` | 任职历史，按任职日期倒序 |
| `get_assets(code)` | 资产披露，按净值日期升序 |
| `get_dividends(code)` | 分红披露，按除息日、公告日倒序 |

iFinD 经理返回中附带 `position_status`，任职公告日期保持缺失，学历与简历仅补充现任经理。iFinD 资产返回中附带 `total_asset`（资产总值），不写入含义不同的 `total_netasset`；页面分别标注报告期与季度报告披露日期。字段和参数统一见 [iFinD 数据源](../docs/data-sources.md#ifind)。

约定：

- 日期区间包含两端，任意一端可省略。
- 同一净值日取 `ann_date` 最新的版本；无公告日期的版本优先级最低。
- 最新公告存在冲突值时抛错，不任意选择。
- 无记录返回同结构空表；不区分“代码不存在”和“本地未下载”。
- 返回结果是独立副本，修改不会影响后续查询。
- 金额在查询时转浮点供展示和计算，数据库仍保留 DECIMAL。

## 初始化与导入

```powershell
# 建空库
.\.venv\Scripts\python.exe -m scripts.init_database

# 导入单基金快照
.\.venv\Scripts\python.exe -m scripts.init_database --snapshot data/single_fund_audit/20261012_093903
```

快照需要 `fund_basic.csv`、`fund_nav.csv`、`fund_div.csv`、`trade_cal.csv`。当前只接受单基金快照。完整流程见 [数据流程](../docs/etl.md)。

## 只读查看

```python
from database.connection import connect

with connect(read_only=True) as db:
    print(db.execute("SHOW TABLES").fetchdf())
    print(db.execute(
        "SELECT ts_code, nav_date, unit_nav, accum_nav, adj_nav "
        "FROM fund_nav WHERE ts_code = ? ORDER BY nav_date DESC LIMIT 5",
        ["000001.OF"],
    ).fetchdf())
```

## DuckDB 与 CSV

| 方面 | DuckDB | CSV |
| --- | --- | --- |
| 默认 | 是 | 设置 `NAV_CSV` 时启用 |
| 净值、名称、日历 | 支持 | 支持 |
| 分红、经理、资产 | 支持 | 不支持 |
| 公告版本 | SQL 取最新 | 内存取最新 |
| 适用场景 | 日常使用 | 历史快照对照 |

CSV 实现每次实例化读取一次文件，文件更新后需重新创建实例。数据库读取失败不会自动回退到 CSV。

## 并发与锁

- 每次查询打开并关闭只读连接，页面不持有长期连接。
- DuckDB 单文件写入与页面读取可能冲突；更新前停止 Streamlit，更新后重启。
- 当前不做并发写入、任务队列或多进程调度。
