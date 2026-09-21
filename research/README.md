# 场外基金探索：第 1 步，获取清单

这是独立的小规模数据探索，不启动原定 Phase 1，也不创建数据库。

代码说明约定：新增或修改 Python 代码时使用中文文档字符串和关键逻辑注释，解释函数职责、参数含义、处理原因与边界情况；函数和变量保留清晰的英文命名，避免逐行复述显而易见的操作。

注释排版约定: 中文注释和文档字符串统一使用英文半角标点, 避免编辑器对中文标点显示黄色方框.

## 本轮范围

只获取 Tushare 返回的场外基金清单，包括存续、终止和发行中的基金。
暂不筛选 10 只基金，不请求历史净值、分红或复权因子。

代码分为两个文件：

- `tushare_source.py`：从环境变量或项目 `.env` 加载凭证，通过已安装的 Tushare SDK 请求数据。只负责请求、间隔控制、有限重试和安全的失败分类。
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

每次执行创建新的 `data/exploration/时间戳/`，不覆盖以前的清单快照。成功后有：

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

## 后续逐步进行，需确认后再实现

2026-09-16 实测结果（本地快照 `data/exploration/20260916_154011/`）：

- `L` 25,021 条，`D` 4,276 条，`I` 0 条；合计 29,297 个代码、25 列，无重复代码。
- 全部代码的 `found_date` 可解析。
- 4,276 条终止记录都有可解析的 `due_date`，但 `delist_date` 全为空；存续记录中也有 6 条非空 `due_date`。
- 因此下一步必须验证 `due_date` 在终止记录中能否代表实际终止日期，不能仅凭字段非空就直接计算实际寿命。
- 三个分页离线测试通过；真实请求验证了本账号的清单权限与分页行为。其他基金接口尚未实测。

1. 检查清单实际结果，重点查看 `found_date`、`due_date`、`delist_date` 的覆盖情况。
2. 按用户确定的口径，包含终止基金，选择实际存续时长最长的 10 个基金代码。存续基金算至采集日，终止基金需确认可靠的终止日期；合同到期日不直接当作实际终止日，日期缺失时不能默认活到今天。不能直接按 `duration_year` 排序。
3. 先取其中 1 只的短区间净值和分红，解释字段，再扩展至 10 只的全历史。核实独立复权因子是否覆盖场外代码。保留源数据披露频率，不用填充制造每日记录。
4. 完成公募基金板块接口与字段统计，区分“官方列出的字段”与“当前账号实测可获取的字段”。

已初步核查的相关官方文档：[净值](https://tushare.pro/document/2?doc_id=119)、[分红](https://tushare.pro/document/2?doc_id=120)、[复权因子](https://tushare.pro/document/2?doc_id=199)。完整板块字段清单尚未完成。
