# 场外基金探索: 获取清单与筛选最长存续基金

基金对比开发准备: 已只读检查现有 5 只基金的历史覆盖, 复权净值和 SSE 开市日连续性, 并完成服务层 `get_comparison` 的两只及四只基金验证. 样本区间, 历史限制与调用方式见 [基金对比数据检查](comparison_readiness.md).

单基金正式开发准备: 已新增 `audit_single_fund.py`, 优先从 Tushare 获取单只基金基础数据并核查日频指标适用性. 本次结果及七项指标的拟定口径见 [华夏成长核查说明](single_fund_findings.md). 本步骤尚未实现指标计算或正式入库.

这是独立的小规模数据探索，不启动原定 Phase 1，也不创建数据库。

代码说明约定：新增或修改 Python 代码时使用中文文档字符串和关键逻辑注释，解释函数职责、参数含义、处理原因与边界情况；函数和变量保留清晰的英文命名，避免逐行复述显而易见的操作。

注释排版约定: 中文注释和文档字符串统一使用英文半角标点, 避免编辑器对中文标点显示黄色方框.

## 本轮范围

第 1 步获取 Tushare 返回的场外基金清单, 包括存续, 终止和发行中的基金.
第 2 步从最新完整快照中筛选存续时间最长的 10 个代码, 包含已终止基金.
第 3 步获取已选 10 个代码的历史净值, 独立复权因子和分红事件.

获取清单的代码分为两个文件：

- 共用请求封装已迁移至 `api/tushare_client.py`：从环境变量或项目 `.env` 加载凭证，负责请求、间隔控制、有限重试和安全的失败分类。请求字段位于 `api/fund_fields.py`，分页与日期分段获取位于 `etl/extract.py`。探索脚本调用这些正式模块，正式程序不再引用 `research`。
- `explore_funds.py`：指定基金列表字段，验证分页，合并状态清单并保存 CSV 和数量摘要。

Token 不写入代码或输出文件，不调用 `ts.set_token`。不记录原始请求体、原始 SDK 异常或异常堆栈。网络失败与权限错误分开记录。

## 为什么这样请求

官方 [fund_basic 文档](https://tushare.pro/document/2?doc_id=19) 中，`market='O'` 表示场外，默认市场却是场内，因此必须显式指定。

分三组请求 `status`：`L` 为存续，`D` 为摘牌或到期，`I` 为发行。三组均保留，防止只看到存续基金。基金份额类别可能对应不同代码，本轮不擅自合并。

文档给出单次 15,000 行上限，却未在该接口参数表明确列出分页参数。因此先用 `limit=2`、`offset=0/2` 和一次 `limit=4` 的结果对照，实测服务器是否接受分页。测试不通过就报错，不能继续声称已取得完整清单。

正式请求每页 1,000 行，持续移动 offset，直到空页。如果服务器重复返回同一页则停止报错。合并后检查基金代码非空、代码不重复，以及市场和状态是否符合请求。

这里的“全量”指 Tushare 针对上述三种状态返回的全部分页结果，不代表已和监管机构名录核对，不能据此证明源数据没有遗漏。

## 如何运行与查看结果

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m research.explore_funds list
```

每次执行创建新的 `data/fund_list/时间戳/`，不覆盖以前的清单快照。成功后有：

- `fund_basic_L.csv`、`fund_basic_D.csv`、`fund_basic_I.csv`：三种状态的清单。
- `fund_basic_all.csv`：合并清单。
- `list_summary.json`：采集日期、分状态数量和日期缺失检查。

日志在 `logs/fund_exploration.log`。中途失败可能只有部分文件，只有生成最终摘要才表示本次清单步骤完成。CSV 使用 UTF-8 BOM 方便 Windows 查看；用 Pandas 读取时建议 `dtype=str`，避免日期或代码被自动转换。

离线验证：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_fund_list.py -q
```

## 如何理解这次清单的字段

此次显式请求文档列出的 25 个字段。它们属于基金档案，没有每日净值：

| 用途 | 字段 |
| --- | --- |
| 识别与机构 | `ts_code`, `name`, `management`, `custodian`, `trustee` |
| 分类与状态 | `fund_type`, `invest_type`, `type`, `market`, `status` |
| 生命周期与交易日期 | `found_date`, `due_date`, `list_date`, `issue_date`, `delist_date`, `purc_startdate`, `redm_startdate` |
| 发行与费用等 | `issue_amount`, `m_fee`, `c_fee`, `duration_year`, `p_value`, `min_amount`, `exp_return` |
| 基准描述 | `benchmark` |

字段含义与单位以链接文档为准；返回列存在不等于每只基金都有值。

## 第 2 步: 筛选存续时间最长的 10 个基金代码

程序为 `research/select_longest_funds.py`, 只读取本地清单, 不使用 Token 或请求 Tushare.

在项目根目录运行:

```powershell
.\.venv\Scripts\python.exe -m research.select_longest_funds
```

程序按 `data/fund_list/YYYYMMDD_HHMMSS/` 的目录时间戳选择最新快照, 要求同时存在 `fund_basic_all.csv` 和 `list_summary.json`. 没有摘要的未完成任务会跳过. 选中后会核对摘要日期, 总行数和状态数量; 数据损坏时失败, 不静默改用旧数据.

也可以指定某次快照, 复现当时的结果:

```powershell
.\.venv\Scripts\python.exe -m research.select_longest_funds --run-dir data/fund_list/20260921_095101
```

计算规则:

- 候选为 `market=O` 且 `status=L/D`. 不要求前 10 名必须包含终止基金.
- 存续基金: 采集日期减去成立日期. 使用摘要的 `as_of`, 不使用运行当天日期, 避免旧清单被误当成实时状态.
- 已终止基金: 优先以 `delist_date` 为截止日; 缺失时以 `due_date` 近似, 标记 `due_date_proxy`. 该字段尚未独立核实为实际终止日, 因此结果是源数据口径排名.
- 日期缺失, 非法, 截止日早于成立日或晚于采集日的记录排除并记录原因. 不能把日期缺失的终止基金计算至今天.
- 按 `age_days` 从大到小排序, 并列时按 `ts_code` 升序. 年数以天数除以 365.2425 近似展示. 不使用档案中的 `duration_year` 排名.
- 不合并不同份额类别或历史转型代码. 这是代码层面的存续时长, 不代表合并前身后的产品完整寿命.

输出位于所选快照的 `longest_lived/` 子目录:

| 文件 | 内容 |
| --- | --- |
| `top_10.csv` | 前 10 名, 保留全部原始基础字段及计算字段 |
| `ranked_all.csv` | 全部有效候选的排名, 可核对终止基金是否参与 |
| `excluded.csv` | 排除的记录及 `exclusion_reason`, 无异常时只有表头 |
| `selection_summary.json` | 输入快照, 计算口径, 候选与选中状态数量, 近似日期数量 |

新增字段包括 `rank`, `as_of`, `calculation_end_date`, `end_date_source`, `uses_due_date_proxy`, `age_days`, `age_years_approx`. 原始 CSV 不修改. 重跑同一快照覆盖该快照的筛选输出, 不追加重复行. 日志位于 `logs/fund_selection.log`.

2026-09-21 实测: 自动选择 `20260921_095101`, 输入 29,355 条, 包含存续 25,069 条和终止 4,286 条. 无排除记录. 终止候选均使用 `due_date_proxy`, 前 10 名均为存续基金, 前 10 名本身没有使用近似终止日期. 名次仍受源清单日期准确性限制.

验证命令:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_fund_selection.py tests/test_fund_list.py -q
```

## 第 3 步: 获取 10 只基金的历史净值和分红

```powershell
.\.venv\Scripts\python.exe -m research.fetch_fund_history
```

程序读取最新完整快照的 `longest_lived/top_10.csv`, 不重新挑选基金. 也可传入 `--run-dir data/fund_list/20260921_095101` 固定名单, 或用 `--end-date 2026-09-21` 指定净值和因子截止日期. 默认截止日为运行当天, 数据仍可能有披露延迟.

净值从各代码的 `found_date` 开始, 按两年窗口请求. 因子和分红复用分页函数, 请求至空页并检查重复页. 某接口失败不影响其他基金, 失败原因写入摘要. 本轮为简单探索程序, 未实现断点续跑; 重跑会重新请求全部历史并生成新的时间戳目录.

仅保存三个文件, 位于 `longest_lived/history/运行时间戳/`:

| 文件 | 内容 |
| --- | --- |
| `nav.csv` | 十只基金合并净值表, 按代码和净值日期排序 |
| `dividends.csv` | 十只基金合并分红事件表 |
| `summary.json` | 各基金接口状态, 行数, 日期范围, 缺失数量和失败原因 |

`nav.csv` 字段:

| 字段 | 含义 |
| --- | --- |
| `ts_code` | 基金代码, 名称可从已选清单查询 |
| `nav_date` | 净值日期 |
| `ann_date` | 公告日期, 用于识别披露延迟或不同公告版本 |
| `unit_nav` | 单位净值 |
| `accum_nav` | 累计净值 |
| `adj_nav` | 接口返回的复权单位净值 |
| `accum_div` | 接口返回的累计分红 |
| `adj_factor` | `fund_adj` 返回的独立复权因子, 按代码和日期匹配 |

三个净值字段与复权因子不是同一概念. 因子缺失不填 1, 不用 `adj_nav / unit_nav` 冒充独立接口因子. 日期缺失不填充, 周末和停披露日期不会人为补行. 只去除完全重复行, 不擅自丢弃同日不同公告或不同数值记录, 摘要会统计重复净值日期. 因子日期无法对应净值时在摘要记录数量, 不另存无净值的因子行.

`dividends.csv` 保留代码, 公告日, 方案进度, 登记日, 除息日, 派息日, 每份派现金额 `div_cash`, 净值除权日和红利再投资到账日. 分红是事件数据, 不会强行展开为每日记录或汇总不同方案进度. 按代码获取接口返回的全部事件, 不受 `--end-date` 限制, 因而不是历史时点可见数据集, 不能直接用于无前视偏差回测.

`summary.json` 中 `completed` 仅表示所有请求流程结束且未失败, 不代表字段无缺失或完整性已由外部核实. `empty` 表示请求成功但返回空表, `failed` 表示获取失败, `partial` 表示本轮存在失败. 运行中每处理完一只基金保存一次进度, 不把中断任务当作完成. 日志为 `logs/fund_history.log`.

官方字段说明: [净值](https://tushare.pro/document/2?doc_id=119), [分红](https://tushare.pro/document/2?doc_id=120), [复权因子](https://tushare.pro/document/2?doc_id=199).

2026-09-21 实测结果位于 `20260921_095101/longest_lived/history/20260921_102854/`: 10 只基金共 59,182 条净值, 226 条分红事件. 总体日期范围为 2001-09-21 至 2026-09-18. 单位净值, 累计净值及复权单位净值均无缺失, 没有重复代码与净值日期组合, 未发现非正单位净值. 累计分红有 58,714 条缺失. `fund_adj` 对 10 个场外代码全部返回空表, 因而 `adj_factor` 全为空; 本次没有获取到独立复权因子, 不能把请求成功视为因子覆盖成功. 此结论只针对本次样本和请求.

## 历史检查记录与后续步骤

2026-09-16 实测结果（本地快照 `data/fund_list/20260916_154011/`）：

- `L` 25,021 条，`D` 4,276 条，`I` 0 条；合计 29,297 个代码、25 列，无重复代码。
- 全部代码的 `found_date` 可解析。
- 4,276 条终止记录都有可解析的 `due_date`，但 `delist_date` 全为空；存续记录中也有 6 条非空 `due_date`。
- 因此下一步必须验证 `due_date` 在终止记录中能否代表实际终止日期，不能仅凭字段非空就直接计算实际寿命。
- 三个分页离线测试通过；真实请求验证了本账号的清单权限与分页行为。其他基金接口尚未实测。

1. 清单字段检查及源数据口径的前 10 名筛选已完成. 如需严格的实际存续时长, 继续核实终止日期与转型关系.
2. 已新增 10 只基金的历史获取程序, 后续根据实测缺失情况决定是否补充数据来源和质量检查.
3. 完成公募基金板块接口与字段统计, 区分官方列出的字段和当前账号实测可获取的字段.

已初步核查的相关官方文档：[净值](https://tushare.pro/document/2?doc_id=119)、[分红](https://tushare.pro/document/2?doc_id=120)、[复权因子](https://tushare.pro/document/2?doc_id=199)。完整板块字段清单尚未完成。
