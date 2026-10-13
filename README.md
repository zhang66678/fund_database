# 公募基金本地数据库与可视化系统

基于 Tushare Pro 的基金数据研究项目。第一阶段面向 Windows 本地单机使用，逐步实现历史数据存储、增量更新、查询分析与可视化。

## 当前状态

已建立 API 请求封装、DuckDB 数据库、数据导入与增量更新流程，以及基金查询和可视化页面。当前逐步完善正式分层模块，集中配置仍待后续整理。

[场外基金数据探索](research/README.md) 保留基金清单获取、最长存续 10 个代码筛选和样本历史数据调研。共用请求封装与字段已移入 `api/`，分页和日期分段获取已移入 `etl/extract.py`。正式程序不依赖 `research/`，探索脚本复用正式模块。

- 开发环境：Windows、VS Code、Python 虚拟环境 `.venv`。
- 当前环境 Python 版本：3.14.3。后续新增依赖时逐项验证兼容性。
- `requirements.txt` 记录当前阶段直接依赖及已安装版本，不是完整的传递依赖锁文件。
- 已安装第一阶段依赖：Tushare、Pandas、DuckDB、python-dotenv 和 pytest，并验证基本导入、配置解析与内存数据库查询。
- 已增加 Streamlit 和 Plotly, 用于本地净值曲线 demo.

## 本地环境

在项目根目录的 PowerShell 中，可直接使用虚拟环境解释器，无需依赖终端是否已激活：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
```

在新电脑上准备环境时，先创建虚拟环境并安装当前阶段依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

项目根目录的 `.env` 用于保存 `TUSHARE_TOKEN`。已有文件应保留；不要将真实 Token 写入代码、日志、截图或 Git。`api/tushare_client.py` 优先使用环境变量，否则加载项目根目录的 `.env`。

新电脑首次使用时，先建空库，再添加基金并获取数据：

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database
.\.venv\Scripts\python.exe -m scripts.add_fund 000001.OF
.\.venv\Scripts\python.exe -m scripts.update_database
```

仅建库不需要 Token，后两步需要配置 Token 和相应接口权限。已有单基金快照时，可用 `scripts.init_database --snapshot 快照目录` 直接建库并导入。数据库和快照不随 Git 克隆，详细说明见 [数据库说明](database/README.md)。

## 可视化 demo 进度

已完成 DuckDB 数据访问层, `FundService` 服务层和 Streamlit + Plotly 页面, 可查询净值曲线和七项收益风险指标. 已建立单基金数据库并导入基础资料, 净值, 分红及交易日历, 支持重复导入和事务回滚. 页面默认查询 DuckDB, CSV 模式保留用于对照. 详见 [页面启动与使用说明](app/README.md) 和 [数据库说明](database/README.md).

在项目根目录启动:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

## 分层约定

数据流：Tushare Pro → API → ETL → DuckDB → Service → Streamlit + Plotly。

| 模块（按需创建） | 职责 |
| --- | --- |
| `config/` | 集中配置、环境变量加载和日志配置 |
| `api/` | 数据请求、限频、重试与请求异常处理 |
| `etl/` | 提取、清洗、质量检查和调用数据库层加载数据 |
| `database/` | 连接、schema、参数化查询、事务与 UPSERT |
| `services/` | 查询用例、收益和风险指标、图表数据整理 |
| `app/` | 用户交互、页面布局与图表展示 |
| `scripts/` | 历史初始化与日常更新入口 |
| `tests/` | 关键行为和金融指标验证 |

页面通过服务层使用数据，不直接调用 Tushare 或操作数据库。服务层避免依赖 DuckDB 专有实现；未来迁移 PostgreSQL 时，尽量将变化限制在数据库层。当前不引入服务器框架或多人部署组件。

## 数据维护原则

- 保留当前与历史基金，基金终止只改变状态，不删除历史数据。
- 先调研接口，再确定业务表、主键和字段类型。
- 使用主键或唯一约束配合 UPSERT，保证重复运行不会产生重复记录。
- 历史回填需逐步支持失败重试、断点续跑和独立任务状态。
- 增量更新使用可配置的滚动窗口，同时考虑新基金与状态变化。窗口外历史修订需要后续补查机制，不能假设短窗口覆盖所有修订。
- 数据、日志与密钥留在本地，代码和文档进入版本管理。

## 开发顺序

1. Phase 1：配置、日志、Tushare 客户端和 DuckDB 连接；先验证最小基础能力。
2. Phase 2：调研基金接口的字段、历史覆盖、权限、频率和返回限制。
3. Phase 3：少量核心数据的获取、清洗与入库 MVP。
4. Phase 4–5：历史回填与增量更新。
5. Phase 6–7：业务指标与可视化。
6. Phase 8：持续完善测试、文档、错误处理和性能。

每个阶段先检查现状、说明修改范围，完成后验证并等待确认，再进入下一阶段。重要测试随功能加入，不集中拖到最后。

## Git 与本地文件

`.gitignore` 已覆盖虚拟环境、真实环境配置、Python 缓存、数据库、数据目录、日志和编辑器设置。忽略规则不会自动移除已经被 Git 跟踪的文件。

本地 Git 仓库已初始化，默认分支为 `main`，尚未创建首次提交或配置远程仓库。安装 Git 后，如果现有终端仍无法识别 `git`，请重新启动 VS Code，使终端获取新的 PATH。可在项目根目录检查状态：

```powershell
git --version
git check-ignore .env .venv/ .vscode/ data/ logs/
git status --short --untracked-files=all
```

确认敏感文件与本地数据没有出现在待提交列表后，再明确添加代码和文档。不要提交 `.env`、数据库文件或日志。

## Tushare公募基金数据接口

当前程序使用 `fund_basic`, `fund_nav`, `fund_div`, `fund_adj`. 以下输出字段列出程序实际请求的字段, 不代表接口的全部可选字段. 其余接口保留为后续调研条目, 暂未在探索程序中调用. 日期参数统一使用 `YYYYMMDD`.

### 基金列表 `fund_basic`

用途: 获取场外基金基础清单, 包括存续, 已终止和发行中的基金. 调用程序: `research/explore_funds.py`. [官方文档](https://tushare.pro/document/2?doc_id=19).

输入

`ts_code` 基金代码

`market` 交易市场 E场内（默认） O场外

`status` 存续状态 D摘牌/已到期 I发行 L已上市/存续中

`fields` 指定返回字段, 程序显式请求下列 25 个字段

`limit` 每页请求行数, 正式获取时为 1000

`offset` 分页起始行数, 从 0 开始, 按实际返回行数递增

当前调用: 固定 `market='O'`, 分别请求 `status='L'`, `'D'`, `'I'`, 不指定单只 `ts_code`. 官方文档未在本接口参数表中列出 `limit/offset`, 程序先实测分页行为后再使用, 不能把单次返回结果直接当作全量清单.

输出

`ts_code` 基金代码, 不同份额类别可能对应不同代码

`name` 基金简称

`management` 基金管理人

`custodian` 基金托管人

`fund_type` 投资类型

`found_date` 成立日期

`due_date` 到期日期, 未独立核实前不能直接等同于实际终止日期

`list_date` 上市日期

`issue_date` 发行日期

`delist_date` 退市日期, 场外样本可能为空

`issue_amount` 发行份额, 单位为亿份

`m_fee` 管理费

`c_fee` 托管费

`duration_year` 存续期, 不直接作为已实际存续年数参与排名

`p_value` 面值

`min_amount` 起点金额, 单位为万元

`exp_return` 预期收益率

`benchmark` 业绩比较基准描述

`status` 存续状态, D摘牌/已到期, I发行, L已上市/存续中

`invest_type` 投资风格

`type` 基金类型

`trustee` 受托人

`purc_startdate` 日常申购起始日

`redm_startdate` 日常赎回起始日

`market` 交易市场, E场内, O场外

### 基金管理人 `fund_company`
输入

无

输出

### 基金经理 `fund_manager`
输入


### 基金业绩基准 `mkt_idx_bmk`

### 基金规模 `fund_share`

### 基金净值 `fund_nav`

用途: 获取所选基金的历史净值. 调用程序: `research/fetch_fund_history.py`. [官方文档](https://tushare.pro/document/2?doc_id=119).

输入

`ts_code` 基金代码, 当前程序逐只请求

`nav_date` 指定净值日期, 官方支持与基金代码二选一查询, 当前程序不传

`market` 交易市场 E场内 O场外, 当前程序按基金代码查询, 不额外传入

`start_date` 净值开始日期, 从该基金成立日开始分段

`end_date` 净值结束日期, 默认至运行当天, 可通过程序的 `--end-date` 指定

`fields` 指定返回字段, 当前请求下列 7 个字段

当前调用: 每只基金按两年窗口请求, 不一次请求全部历史. 单段达到 2000 行会触发疑似截断检查; 这是程序的保护阈值, 不是该接口文档已确认的最大行数.

输出

`ts_code` 基金代码

`ann_date` 公告日期, 与净值日期可能不同

`nav_date` 净值日期, 用于排列历史净值序列

`unit_nav` 单位净值

`accum_nav` 累计净值

`adj_nav` 复权单位净值, 不等同于复权因子

`accum_div` 累计分红, 空值不能解释为没有分红

官方还提供 `net_asset` 资产净值和 `total_netasset` 合计资产净值, 本次为精简数据未请求. 保存至 `nav.csv`, 不补齐非披露日期. 本次 10 只样本的 `accum_div` 大量缺失.

### 基金复权因子 `fund_adj`

用途: 尝试获取独立复权因子. 调用程序: `research/fetch_fund_history.py`. [官方文档](https://tushare.pro/document/2?doc_id=199).

输入

`ts_code` 基金代码, 官方支持多只输入, 当前程序逐只请求

`trade_date` 指定交易日期, 当前程序不传

`start_date` 开始日期, 当前使用基金成立日

`end_date` 结束日期, 与净值请求截止日期一致

`limit` 每页请求行数, 当前为 1000

`offset` 分页起始行数, 从 0 开始, 直到返回空页

`fields` 指定返回字段, 当前请求下列 3 个字段

输出

`ts_code` 基金代码

`trade_date` 因子对应的交易日期

`adj_factor` 独立复权因子

程序将 `trade_date` 对齐至 `nav_date`, 按基金代码和日期合并到 `nav.csv`. 2026-09-21 本次 10 个场外样本的请求均返回空表, 因此保存的 `adj_factor` 全为空. 未填 1, 未用 `adj_nav / unit_nav` 替代. 此结果不能推广为所有场外基金都没有因子数据.

### 基金分红 `fund_div`

用途: 获取所选基金的分红事件. 调用程序: `research/fetch_fund_history.py`. [官方文档](https://tushare.pro/document/2?doc_id=120).

输入

`ts_code` 基金代码, 当前程序逐只请求

`ann_date` 公告日期, 当前程序不传

`ex_date` 除息日期, 当前程序不传

`pay_date` 派息日期, 当前程序不传

`fields` 指定返回字段, 当前请求下列 9 个字段

`limit` 每页请求行数, 当前为 1000

`offset` 分页起始行数, 当前从 0 开始请求至空页

官方输入条件为基金代码, 公告日, 除息日, 派息日四选一. 当前按基金代码请求全部事件, 不受程序的 `--end-date` 限制. 本接口官方参数表未列出 `limit/offset`, 程序使用通用分页封装并检查重复页; 当前样本事件数少于一页, 尚未验证多页非空结果的行为.

输出

`ts_code` 基金代码

`ann_date` 公告日期

`div_proc` 分红方案进度, 不同进度不能直接作为多次分红相加

`record_date` 权益登记日

`ex_date` 除息日

`pay_date` 派息日

`div_cash` 每份派现金额, 单位为元, 官方字段说明为每股派息

`net_ex_date` 净值除权日

`account_date` 红利再投资到账日

分红按事件保存到 `dividends.csv`, 不展开为每日记录. 本次数据的 `net_ex_date` 和 `account_date` 部分缺失, 不自动用其他日期替代. 接口还支持实施公告日等其他输出字段, 本次未请求.

### 基金持仓 `fund_portfolio`

### 场内基金技术因子（专业版）`fund_factor_pro`
