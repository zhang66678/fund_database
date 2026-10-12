# 数据库与数据访问层

## DuckDB 单基金 MVP

已建立 `data/fund_database.duckdb`, 导入华夏成长核查快照 `data/single_fund_audit/20261012_093903/`. 页面默认已通过 `DuckDBFundRepository` 查询数据库.

| 表 | 当前行数 | 主键 |
| --- | --- | --- |
| fund_basic | 1 | ts_code |
| fund_nav | 6027 | ts_code, nav_date, ann_date |
| fund_dividend | 25 | ts_code, ann_date, ex_date, div_proc |
| trade_calendar | 9065 | exchange, cal_date |

保留快照中已请求的字段, 代码和文本使用 VARCHAR, 日期使用 DATE, 数值使用 DECIMAL(24,10), 日历开闭市标记使用 INTEGER. 空值保留为 NULL. Decimal 从源字符串转换, 避免先转浮点造成额外精度损失.

- 基础资料为当前状态, 更新不删除历史净值或分红.
- 净值保留不同公告日期的版本; 同代码, 净值日和公告日的修订更新原记录, 暂不另建变更审计表.
- 分红主键是当前样本的披露记录标识, 不是供应商提供的永久事件 ID. 公告日期, 除息日或方案进度变化会形成新记录. 后续多基金与增量更新时需进一步确认事件关联, 不能直接把各披露版本的金额汇总为总分红.
- 主键字段缺失或同主键存在冲突源记录时拒绝导入, 不补造日期. 当前样本的 29 行分红中有 4 行完全重复, 入库为 25 条, 原始 CSV 不变.
- 四张表在同一事务中写入, 任一写入失败则全部回滚. 不删除已有记录. 日历标记限制为 0 或 1.
- 重复运行仅更新变化记录, 日志分别统计新增, 更新, 未变化及源数据重复行数. 当前单基金导入器校验净值和分红代码归属, 暂未添加跨表外键.
- 建表仅使用 CREATE TABLE IF NOT EXISTS. 后续字段变更需要明确迁移, 不能靠重复运行初始化修改旧表结构.

实现文件:

- `connection.py`: 默认路径与连接生命周期.
- `schema.py`: 四张表的字段和约束.
- `writer.py`: 数据库 UPSERT 和写入数量统计.
- `etl/import_snapshot.py`: CSV 类型转换, 去重, 质量检查与事务导入.
- `scripts/init_database.py`: 命令行入口及日志配置.

在项目根目录重复导入:

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database
```

可以通过 `--snapshot` 和 `--database` 指定其他快照或数据库路径. 当前入口仅接受单基金快照, 不会请求 Tushare. 日志位于 `logs/database_import.log`, 数据库和日志不提交 Git.

启动 Python 后只读查看:

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

验证记录: 首次导入完成, 二次导入四张表新增和更新均为 0; 全部入库字段与清洗后的 CSV 一致, 净值覆盖 2001-12-18 至 2026-10-09. 仅新增一个集成测试验证幂等性, 修订更新和事务回滚:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_import.py -q
```

## 清单驱动的增量更新

先添加一只场外基金的基本资料:

```powershell
.\.venv\Scripts\python.exe -m scripts.add_fund 040001.OF
```

也支持 `040001` 和小写后缀. 程序从 Tushare 获取名称, 成立日期等资料, 写入 `fund_basic`, 不需要手动编辑数据库. 重复添加只更新变化的资料, 不重复插入, 也不删除历史数据. 当前仅支持场外基金. 日志在 `logs/add_fund.log`.

然后运行下面的更新命令. 新基金没有净值时会从成立日回填; 有净值后才出现在页面下拉列表里. 添加和更新均可用 `--database` 指定数据库, 两次需指向同一文件. 添加基金前也应停止网站以避免写入锁冲突.

在项目根目录运行, 更新前先停止 Streamlit, 避免独立进程的 DuckDB 文件锁冲突:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database
```

任务从数据库 `fund_basic` 读取代码, 名称, 市场和成立日期, 并关联每只基金的最新净值日. 名称用于日志, 代码用于 API 请求, 不使用固定清单或 `top_10.csv`, 不按存续状态排除基金. 今后将基金资料加入此表后, 同一程序即可处理, 无须修改基金代码列表.

- 净值从最后净值日前回看 7 个日历日 (含该日), 获取至任务截止日. 停更后会覆盖中间未更新的时间, 长区间继续分段请求. 无净值的基金从成立日获取.
- 基础资料重新获取并更新名称和状态; 分红记录量小, 每只基金重新分页获取后去重并 UPSERT. 不删除历史记录.
- SSE 日历每轮更新一次, 从库内日历末日回看, 不为每只基金重复请求.
- 每只基金的基础资料, 净值和分红共同提交; 某只失败则不提交该基金的修改, 继续其他基金. 下次重跑重试失败基金.
- 净值返回空表时保留原数据, 日志标记 `empty_nav`, 不能据此证明该基金没有遗漏或已经完整. 当前不做自动停运判定.
- 历史窗口外的净值修订仍需扩大回看区间另行补查. 回看窗口不是完整历史质量保证.
- 当前分红表要求公告日期和除息日等主键非空. 若接口返回未明确日期的方案, 本轮该基金会记录失败, 等后续明确事件模型后再处理, 不补造主键.

可调整窗口, 截止日和数据库:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database --refresh-days 30 --end-date 2026-10-12 --database data/fund_database.duckdb
```

日志为 `logs/database_update.log`, 每次运行另存一个 `database_update_时间戳.json` 结果摘要, 含各基金状态及新增, 更新, 未变化数量. `partial` 返回非零退出码. 暂不自动发现数据库之外的新基金, 不做并发请求或持久化任务队列.

实现: `etl/update_funds.py` 负责更新流程, `scripts/update_database.py` 提供命令入口. `api/tushare_client.py` 统一处理限频, 重试和凭证脱敏, `api/fund_fields.py` 维护请求字段, `etl/extract.py` 负责分页和日期分段获取. 复用快照导入的类型转换和数据库 UPSERT, 正式运行不依赖 research 目录.

## DuckDB 查询实现

`duckdb_repository.py` 实现既有的基金列表, 区间净值和交易日历接口. 日期和代码全部参数化; 数据库金额字段保留 DECIMAL, 查询净值时转换为浮点数, 供现有服务层计算. 按净值日选择公告日期最新的版本, 主键保证同版本唯一. 每次查询打开并关闭只读连接, 不共享全局连接.

页面默认读取 `data/fund_database.duckdb`, 可用 `DATABASE_PATH` 环境变量指定其他文件. 显式设置 `NAV_CSV` 时优先使用 CSV 模式, 不会在数据库读取失败时偷偷回退 CSV. 数据库模式无需配置名单或日历 CSV.

验证: 6027 条净值与 CSV 一致, 2020-2025 年七项指标差异小于 1e-12, 数据库驱动的页面显示正常. 查询本身不建表, 初次运行须先执行初始化命令.

当前是本机单进程查询方案. 另一个进程写入 DuckDB 时可能与网站查询的文件锁冲突, 更新前先停止网站, 完成后重启. 本步不实现并发更新调度.

## 原有 CSV 查询

当前调用方向为:

```text
Streamlit 页面 -> FundService -> FundRepository -> DuckDB (默认) / CSV (对照)
```

## 查询约定

新增 `list_available_funds()`, 返回本地有净值的代码与名称映射, 供页面下拉选择. `CsvFundRepository` 可选接收 `names_path`, 仅查询选择列表时读取名单的 `ts_code,name`, 不影响原有净值查询. 未提供名单时使用名称缺失提示.

- 输入完整代码, 如 `000001.OF`. 日期区间包含两端, 可以省略边界.
- 返回四列: `nav_date`, `unit_nav`, `accum_nav`, `adj_nav`.
- 日期为 pandas 日期类型, 净值为浮点数, 按日期升序, 缺失值不填充.
- 同日取最新公告版本, 无公告日期的版本优先级最低. 最新公告存在冲突值时抛出异常.
- 无记录返回同结构空表. 不区分代码不存在和本地尚未下载.
- 返回独立结果, 修改结果不会影响后续查询.

## CSV 实现

CSV 实现接收净值路径, 可选接收名称与日历文件路径. 净值文件需有 `ts_code`, `ann_date` 和上述四列. 代码用于筛选, 公告日期仅用于内部版本选择, 其他列忽略. 无需分红或复权因子字段.

保留必要字段, 非空代码, 日期和数值格式检查. 文件不存在或格式错误时抛出异常. 

每个实例读一次文件, CSV 更新后需要重新创建实例. 当前适合小样本 demo, 全量数据阶段改用数据库查询.

## 手动验证

在项目根目录 PowerShell 中启动 Python:

```powershell
.\.venv\Scripts\python.exe
```

然后执行:

```python
from datetime import date
from pathlib import Path
from database.csv_repository import CsvFundRepository

nav_path = Path(
    "data/exploration/20260921_095101/longest_lived/history/20260921_102854/nav.csv"
)
repo = CsvFundRepository(nav_path)
nav = repo.get_nav("000001.OF", date(2020, 1, 1), date(2020, 12, 31))
nav.head()
len(nav)
```

输入 `exit()` 退出 Python 后, 可运行离线测试:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_csv_repository.py -q
```

## 服务层

已增加 `services/fund_service.py`, 负责规范基金代码, 校验查询输入并选择展示列. 服务只依赖 `FundRepository` 接口, 不读取文件, 不重复排序或选择公告版本.

接着上面的示例执行:

```python
from services.fund_service import FundService

service = FundService(repo)
nav = service.get_nav(
    "000001", date(2020, 1, 1), date(2020, 12, 31),
    nav_types=["unit_nav", "adj_nav"],
)
nav.head()
```

`000001` 会补为 `000001.OF`, 首尾空格和小写后缀也会处理. `nav_types` 省略时返回全部三种净值, 传入空列表时提示至少选择一种. 支持的字段为 `unit_nav`, `accum_nav`, `adj_nav`, 返回顺序与选择顺序一致, 重复选择只保留一次. 无数据返回空表, 查询错误向调用方传递.

已接入 Streamlit + Plotly 页面, 见 [启动说明](../app/README.md). 未来数据库实现遵守相同查询约定, 页面不需要理解文件路径或 SQL.
