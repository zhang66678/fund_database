# 架构说明

## 目标

系统按“取数、存储、计算、展示”分层，每层只依赖下一层。目标是让数据源、存储和页面可以独立替换，同时保持代码量可控。

## 数据流

```text
Tushare Pro
    -> api/        请求、限频、重试、错误脱敏
    -> etl/        分页、日期分段、清洗、任务编排
    -> database/   表结构、UPSERT、只读查询
    -> services/   业务查询、日期对齐、指标计算
    -> app/        页面编排、图表、会话状态
```

## 分层规则

| 层 | 职责 | 可以依赖 |
| --- | --- | --- |
| `api/` | 数据源请求与异常分类 | 第三方 SDK |
| `etl/` | 取数、清洗、写入编排 | `api`、`database` |
| `database/` | 连接、schema、读写和在线查询适配 | DuckDB / pandas、iFinD HTTP 客户端 |
| `services/` | 查询用例、指标、对比 | `database.repository` 的接口 |
| `app/` | 页面与图表 | `services`、`app` 内部组件 |
| `scripts/` | 命令行入口 | `etl`、`database`、`api` |

约束：

- 页面不导入 DuckDB，也不调用 Tushare。
- 服务层通过 `FundRepository` Protocol 访问数据，不依赖具体存储实现。
- `etl` 可以依赖 `database`，反向不允许。
- `research/` 和 `ifind_test/` 是旁路，正式流程不依赖它们。

## 关键接口

### `FundRepository`

`database/repository.py` 定义只读查询契约：

- `list_available_funds()`：本地有净值的基金代码与名称。
- `get_nav()`：区间净值，按净值日取最新公告版本。
- `get_calendar()`：SSE 交易日历。
- `get_basic()`：当前基金档案。
- `get_managers()`：经理任职历史。
- `get_assets()`：资产披露。
- `get_dividends()`：分红披露。

实现：

- `DuckDBFundRepository`: 默认实现, 缓存少量只读结果, 主文件或 WAL 变化时失效. 未命中时打开并关闭只读连接, 单基金历史净值复用于不同区间查询.
- `CsvFundRepository`：对照实现，仅支持净值、名称、日历和基础资料。
- `IfindFundRepository`：通过 `api.ifind_client` 在线查询基金资料和净值，不入库；本地名单只供代码选择，SSE 日历供现有指标计算。

### `FundService`

`services/fund_service.py` 负责：

- 基金代码规范化与输入校验。
- 单基金净值、概览、资料查询。
- 单基金累计收益与回撤序列。
- 多基金共同区间、日期对齐、累计收益和逐只指标。
- 把读取错误整理为页面可展示的结果。

### `Source`

`api/tushare_client.py` 负责：

- 从环境变量或 `.env` 读取 Token。
- 控制最小请求间隔，有限重试。
- 把网络、权限、限频错误分类，并避免凭证进入日志。

### 名单快照

`etl/fund_list.py` 负责：

- 获取 `L`、`D`、`I` 三种状态的场外基金，保存 `data/fund_list/时间戳/` 快照。
- 写入 `fund_basic_all.csv` 和 `list_summary.json`。
- 提供 `latest_snapshot()`，供 `scripts.add_fund --from-list` 和研究脚本复用。

命令入口为 `scripts/update_fund_list.py`。

`etl/sync_fund_list.py` 校验完整快照, 复用基础字段批量新增及更新 `fund_basic`, 保留名单外基金和历史数据. `scripts/sync_fund_list.py` 默认先获取最新快照再同步, `--from-list` 可复用本地完整名单, 不逐只调用接口.

`api/ifind_client.py` 负责 HTTP 鉴权、请求、错误提示和 10 分钟内存缓存。`app/bootstrap.py` 根据会话中的数据源选择组装服务, DuckDB 服务及 iFinD 客户端在当前浏览器会话复用, 页面和服务不直接请求接口.

iFinD 离线净值链路独立为 `scripts/update_ifind_nav.py -> etl/ifind_nav.py -> api/ifind_nav.py -> database/ifind_nav_store.py`. 仅写入 `data/ifind_nav.duckdb` 的净值表, 本地 Tushare 档案只读提供代码和成立日期. 在线适配器复用同一净值解析函数, 页面来源不自动切换. `research/compare_nav_sources.py` 只读两库生成抽样核对文件.

两源日常更新复用 `scripts.sync_fund_list` 同步公共名单, `etl/update_scope.py` 共用批次 CSV 读取, 更新默认值和限制在途数量的取数线程池. iFinD 与 Tushare 一样默认按公共名单更新, 工作线程取数, 主线程写库, 净值覆盖筛选各自独立判断.

iFinD 客户端为每个线程复用独立 HTTP Session; 更新任务通过 `nav_writer` 复用主线程写库连接, 仍保留单基金事务. 日志和摘要分别记录取数与写入耗时.

## 目录职责

| 目录 | 主要文件 |
| --- | --- |
| `api/` | `tushare_client.py`、`fund_fields.py`、`ifind_client.py` |
| `etl/` | `extract.py`、`fund_list.py`、`sync_fund_list.py`、`import_snapshot.py`、`add_funds.py`、`update_funds.py` |
| `database/` | `connection.py`、`schema.py`、`writer.py`、`repository.py`、`duckdb_repository.py`、`csv_repository.py`、`ifind_repository.py` |
| `services/` | `fund_service.py`、`fund_metrics.py` |
| `app/` | `streamlit_app.py`、`comparison_page.py`、`components.py`、`bootstrap.py`、`styles.css` |
| `scripts/` | `init_database.py`、`update_fund_list.py`、`sync_fund_list.py`、`add_fund.py`、`update_database.py` |

## 扩展位置

| 需求 | 放置位置 |
| --- | --- |
| 新增 Tushare 字段或接口 | `api/fund_fields.py`、`database/schema.py`、`etl/` |
| 新增数据表 | `database/schema.py`，同步更新 `etl` 写入和 `FundRepository` |
| 新增指标 | `services/fund_metrics.py`，页面只负责展示 |
| 新增页面 | `app/`，通过 `FundService` 取数 |
| 新增数据源 | HTTP 请求放 `api/`，查询适配实现 `FundRepository`；需要持久化时再接入 `etl/` |

## 配置与路径

当前路径定义分布在：

- `database/connection.py`：`ROOT`、`DATABASE_PATH`、`IFIND_DATABASE_PATH`、连接生命周期。
- `api/tushare_client.py`：项目根目录，用于定位 `.env`。
- `app/bootstrap.py`：根据会话选择及 `DATABASE_PATH`、`NAV_CSV` 等环境变量选择实现。

后续如继续收敛，可合并为一个 `config.py`，统一 `ROOT`、`DATABASE_PATH`、日志目录和日志初始化。

## 不在范围内

- 多用户、登录、权限和服务器部署。
- 并发写入或任务队列。
- 自动发现数据库之外的新基金。
- 历史时点可得的“无前视”数据集还原。
