# 研究说明

`research/` 存放一次性数据探索和历史核查脚本。正式流程不依赖本目录；脚本可以复用 `api/`、`etl/` 和 `database/` 的公共能力。

基金总名单获取属于正式流程，入口为 `scripts.update_fund_list`，逻辑在 `etl/fund_list.py`。

## 脚本

| 脚本 | 作用 | 是否请求 Tushare |
| --- | --- | --- |
| `select_longest_funds.py` | 从最新完整快照筛选存续时间最长的基金 | 否 |
| `fetch_fund_history.py` | 获取选中基金的净值、复权因子和分红 | 是 |
| `audit_single_fund.py` | 单基金数据质量核查 | 是 |

## 运行

```powershell
# 1. 更新基金总名单（正式流程）
.\.venv\Scripts\python.exe -m scripts.update_fund_list

# 2. 从最新完整快照筛选最长存续基金
.\.venv\Scripts\python.exe -m research.select_longest_funds

# 3. 获取选中基金的历史净值、因子和分红
.\.venv\Scripts\python.exe -m research.fetch_fund_history

# 4. 单基金核查
.\.venv\Scripts\python.exe -m research.audit_single_fund --code 000001.OF --end-date 2026-10-12
```

`scripts.update_fund_list` 每次创建新的 `data/fund_list/时间戳/` 快照，不覆盖旧清单。`select_longest_funds.py` 默认使用目录名最新的完整快照，也可用 `--run-dir` 指定。`fetch_fund_history.py` 继承最新快照的 `top_10.csv`，输出到 `longest_lived/history/时间戳/`。

## 输出

名单快照由 `scripts.update_fund_list` 生成：

| 路径 | 内容 |
| --- | --- |
| `data/fund_list/时间戳/fund_basic_L.csv` | 存续基金 |
| `data/fund_list/时间戳/fund_basic_D.csv` | 终止基金 |
| `data/fund_list/时间戳/fund_basic_I.csv` | 发行中基金 |
| `data/fund_list/时间戳/fund_basic_all.csv` | 合并清单 |
| `data/fund_list/时间戳/list_summary.json` | 采集日期和数量摘要 |
| `longest_lived/top_10.csv` | 存续时间最长的 10 只 |
| `longest_lived/ranked_all.csv` | 全部有效候选排名 |
| `longest_lived/excluded.csv` | 被排除的记录及原因 |
| `longest_lived/history/时间戳/nav.csv` | 十只基金合并净值 |
| `longest_lived/history/时间戳/dividends.csv` | 十只基金合并分红 |
| `longest_lived/history/时间戳/summary.json` | 各接口状态与缺失统计 |

## 报告

- [单基金核查与指标口径](single_fund_findings.md)：华夏成长样本、数据质量检查、指标公式来源。
- [基金对比数据检查](comparison_readiness.md)：五只基金的历史覆盖、共同区间和对比边界。

当前指标和对比规则以 [指标与对比](../docs/metrics.md) 为准；报告保留写作时的结论和日期。

## 约定

- 快照和日志不提交 Git。
- 日期缺失不补造；接口返回空表只记录为 `empty`，不推断数据完整。
- 清单筛选使用快照日期，不使用运行当天日期。
- 终止基金优先用 `delist_date`；缺失时用 `due_date` 近似并标记。
- 不合并不同份额类别或历史转型代码。
