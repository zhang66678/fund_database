# 数据流程

本文说明基金如何进入数据库、如何增量更新，以及失败时会发生什么。表结构和写入细节见 [数据库说明](../database/README.md)，接口字段见 [数据源](data-sources.md)。

## 命令总览

| 命令 | 作用 | 是否请求 Tushare |
| --- | --- | --- |
| `python -m scripts.init_database` | 创建数据库和缺失的表 | 否 |
| `python -m scripts.init_database --snapshot <目录>` | 从单基金快照建库并导入 | 否 |
| `python -m scripts.update_fund_list` | 更新场外基金总名单快照 | 是 |
| `python -m scripts.add_fund <代码>` | 添加一只场外基金的基础资料 | 是 |
| `python -m scripts.add_fund --file <csv>` | 批量添加基础资料 | 是（仅新代码） |
| `python -m scripts.add_fund --from-list` | 从本地总名单生成批次 CSV | 否 |
| `python -m scripts.update_database` | 更新全库数据 | 是 |

添加和更新数据库前应停止 Streamlit，避免 DuckDB 写锁冲突。

## 更新基金名单

名单是批量添加的输入，需定期更新：

```powershell
.\.venv\Scripts\python.exe -m scripts.update_fund_list
```

- 请求 `fund_basic` 的 `L`、`D`、`I` 三种状态，固定 `market=O`。
- 每页 1000 行，先用小页探测确认 `offset` 生效。
- 输出到 `data/fund_list/时间戳/`：三个状态 CSV、`fund_basic_all.csv`、`list_summary.json`。
- 只有三种状态全部获取并校验完成后才写摘要；有摘要表示快照完整。
- `--run-dir` 可指定输出目录，便于复现或重跑。
- 不读取数据库，不写数据库；日志在 `logs/fund_list.log`。
- `scripts.add_fund --from-list` 和研究脚本默认使用最新完整快照。

## 初始化

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database
```

- 仅创建缺失的表，重复运行不会清空已有数据。
- 不需要 Token。
- 字段变更需要单独迁移；初始化不会修改旧表结构。

## 添加基金

### 单只

```powershell
.\.venv\Scripts\python.exe -m scripts.add_fund 000001
.\.venv\Scripts\python.exe -m scripts.add_fund 040001.OF
```

六位代码会自动补 `.OF`。程序获取并校验场外基金资料，写入 `fund_basic`；重复添加只更新变化字段。此步骤不下载净值，需再运行 `update_database`。

### 固定批次

批次 CSV 只需 `ts_code` 列：

```csv
ts_code
000001.OF
040001.OF
```

```powershell
.\.venv\Scripts\python.exe -m scripts.add_fund --file data/fund_batches/batch_001.csv
```

- 批次内按代码去重；完整 `fund_basic` 中已有的代码跳过，不请求接口。
- 每只基金单独提交事务；一只失败不影响其他基金。
- 重跑同一文件时，已成功的跳过，失败的重新尝试。
- 不要直接传入全市场名单；先用 `--from-list` 生成小批次。
- 摘要写入 `logs/add_funds_时间戳.json`，包含新增、已存在、重复、失败及逐条原因。

### 从总名单生成批次

```powershell
# 默认选择存续场外基金，最多 20 只
.\.venv\Scripts\python.exe -m scripts.add_fund --from-list

# 指定类型和数量
.\.venv\Scripts\python.exe -m scripts.add_fund --from-list --fund-types 股票型 混合型 债券型 --limit 30
```

规则：

- 先运行 `scripts.update_fund_list` 生成最新完整快照。
- 不传路径时，取 `data/fund_list/` 下目录名最新的完整快照。
- 候选为场外 `market=O`，状态默认 `L`，可指定 `L`、`D`、`I`。
- 先排除已入库基金，再按成立日期和代码排序取前 `limit` 只。
- 输出到 `data/fund_batches/batch_时间戳.csv`；已有文件不覆盖。
- 只生成清单，不请求接口、不写数据库。

确认 CSV 后，用 `--file` 指向同一文件添加，再用同一文件更新数据。

## 更新数据库

```powershell
# 全库
.\.venv\Scripts\python.exe -m scripts.update_database

# 只更新指定基金
.\.venv\Scripts\python.exe -m scripts.update_database --codes 000001 040001.OF

# 只更新指定批次
.\.venv\Scripts\python.exe -m scripts.update_database --file data/fund_batches/batch_001.csv

# 只更新部分数据
.\.venv\Scripts\python.exe -m scripts.update_database --datasets nav asset
```

`--file` 与 `--codes` 二选一。指定的基金必须已经在库中，否则报错，不会自动添加。

### 更新条目

| `--datasets` | 内容 |
| --- | --- |
| `basic` | 基金基础资料 |
| `nav` | 单位净值、累计净值、复权净值、累计分红 |
| `dividend` | 分红披露 |
| `manager` | 基金经理任职历史 |
| `asset` | 资产净值与合计资产净值 |
| `calendar` | SSE 交易日历 |

不传时更新全部。交易日历是共享数据，每轮只更新一次。

### 大库日常更新与首次回填

全量更新会逐只请求基础资料、净值、分红、经理和资产，即使已有净值也会重复请求其他条目。两万只以上基金建议将日常净值更新、首次历史回填、低频资料更新分开运行：

```powershell
# 日常：只更新已有净值的基金，沿用各自末日前回看 7 天的增量窗口
.\.venv\Scripts\python.exe -m scripts.update_database --datasets nav --nav-state existing --workers 4

# 首次回填：只处理尚无净值的基金，从各自成立日下载历史
.\.venv\Scripts\python.exe -m scripts.update_database --datasets nav --nav-state missing --workers 4

# 基础资料、分红、经理和资产按需要另行更新，例如每周运行
.\.venv\Scripts\python.exe -m scripts.update_database --datasets basic dividend manager asset calendar --workers 4
```

- `--workers`：1～16，默认 1；并发取数、清洗，数据库始终由主线程写入。最多预取约两倍线程数的任务，限制历史数据的内存占用。
- `--nav-state`：`all`（默认，全部）、`existing`（已有至少一条净值）、`missing`（没有净值），按任务开始时的数据库记录筛选；可与 `--file`、`--codes` 组合。
- 这两个净值分组不代表历史完整性；`existing` 仍可能存在更早的缺口，需另用 `--full-history` 复查。没有返回可入库净值的基金仍属于 `missing`，下次会重新尝试。
- 所有线程共用一个请求限频器，包含分页与重试；默认 `--request-interval 0.15`，理论上限约 400 次/分钟。参数最小为 `0.12`，程序也会保证间隔至少 0.12 秒，遵守本账号 500 次/分钟上限。遇到限频后所有线程共同冷却 30 秒。
- 并发用于重叠网络等待，不能突破账号限频。另开的更新进程或其他程序会共享账号配额，应避免同时运行，或增大 `--request-interval` 为其预留配额。
- 不按工作日过滤日期，不因终止状态或空返回自动停止补查；保留原有增量回看、历史修订和异常行隔离规则。
- 不加新参数时仍更新全部基金、全部条目；新参数和本轮筛选数量会写入更新摘要。

### 增量窗口

- `nav`：从最后净值日前回看 7 个日历日，补上停更期间的数据；无净值时从成立日回填。
- `asset`：使用资产表自己的最后日期回看，不能用净值末日代替。
- `calendar`：从日历末日回看。
- `basic`、`dividend`、`manager`：每次重新获取。

```powershell
# 扩大回看窗口
.\.venv\Scripts\python.exe -m scripts.update_database --refresh-days 30

# 全历史复查净值和资产
.\.venv\Scripts\python.exe -m scripts.update_database --full-history

# 只复查资产
.\.venv\Scripts\python.exe -m scripts.update_database --datasets asset --full-history
```

增量窗口用于日常更新；窗口外的历史修订需要 `--full-history` 或扩大回看天数。

### 事务与失败

- `basic` + `nav` 同一次提交，失败则一起回滚。
- `dividend`、`manager`、`asset` 各自提交，单项失败不影响其他数据。
- 单只基金失败后继续处理下一只。
- 存在失败时整体状态为 `partial`，命令返回非零退出码，下次运行会重新尝试。

### 异常行隔离

净值和资产缺少公告日期时：

- 将这些原始行写入数据库同目录的 `rejected_rows/运行时间/`，文件名为 `<表名>_<代码>.csv`。
- 合格行继续校验和入库。
- 该基金和本轮任务标记 `partial`。
- 不补造公告日期，也不替换主键。

数据源补全后，用 `--full-history` 复查对应历史。

### 日志与摘要

| 文件 | 内容 |
| --- | --- |
| `logs/database_update.log` | 本轮更新过程 |
| `logs/database_update_时间戳.json` | 每只基金、每张表的新增 / 更新 / 未变化数量及失败原因 |
| `logs/fund_list.log` | 名单快照获取过程 |
| `logs/add_fund.log` | 添加过程 |
| `logs/add_funds_时间戳.json` | 批量添加摘要 |
| `logs/fund_batch_时间戳.json` | 批次筛选条件与输出路径 |

## 快照导入

已有单基金核查快照时，可以跳过 Tushare 直接建库：

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database --snapshot data/single_fund_audit/20261012_093903
```

快照需要包含 `fund_basic.csv`、`fund_nav.csv`、`fund_div.csv`、`trade_cal.csv`。导入会：

- 统一字段类型，空字符串转为 NULL。
- 去除完全重复行。
- 主键缺失或冲突时拒绝导入。
- 四张表在同一事务中提交，失败全部回滚。

## 注意事项

- 更新时网站必须停止；DuckDB 单文件不能同时被页面读取和命令写入。
- `--end-date` 限制净值、资产和日历；分红和经理获取接口返回的全部记录。
- 空结果只表示接口没有返回数据，不能证明该基金没有遗漏。
- 详细字段和权限见 [数据源](data-sources.md)。
