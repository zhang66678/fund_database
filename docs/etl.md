# 数据更新指南

在项目根目录的 PowerShell 中运行以下命令. **写入数据库前先停止 Streamlit, 同一时间只运行一个更新任务.** 默认数据库为 `data/fund_database.duckdb`.

Tushare 和 iFinD 的配置见 [数据源](data-sources.md), 表结构与写入规则见 [数据库说明](../database/README.md).

## 日常同步入库

日常维护按顺序执行两步: **同步公共名单, 再更新所需来源的数据.** 两源共用 Tushare 库中的名单, iFinD 库只保存净值, 无需另外导入一份名单.

### 1. 同步公共名单

```powershell
# 获取最新名单, 添加新基金, 更新已有基金的基础资料和状态
.\.venv\Scripts\python.exe -m scripts.sync_fund_list
```

**今天已下载过名单**, 第一步可替换为:

```powershell
.\.venv\Scripts\python.exe -m scripts.sync_fund_list --from-list
```

`--from-list` 默认取最新完整快照, 也可在后面指定目录, 如 `data/fund_list/20261103_091133`.

名单同步直接复用名单中的基础字段, 无需逐只请求接口:

| 名单变化 | 数据库处理 |
| --- | --- |
| 新增基金 | 写入基础资料, 纳入后续数据更新 |
| 已有资料或状态变化, 如 L 转 D | 更新基础资料, 保留历史数据 |
| 基金从名单中消失 | 保留原记录, 在摘要的 `missing_from_snapshot` 中列出 |
| 非六位代码, 名称缺失, 成立日期无效 | 跳过该记录, 在 `skipped_records` 中列出原因; 已有资料保持原样 |

同步覆盖 L(存续), D(终止), I(发行) 三种状态, 入库代码格式为六位数字加 `.OF`. 名单同步只写基础资料; 净值等数据和 iFinD 分类分别由对应命令更新.

### 2. 更新数据

按使用的数据源选择命令, 需要同时维护两库时依次执行:

```powershell
# Tushare: 更新全库净值和 SSE 日历, 使用 8 并发
.\.venv\Scripts\python.exe -m scripts.update_database --datasets nav calendar --workers 8

# iFinD: 更新公共名单中的全库净值, 使用 4 并发
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --workers 4
```

已有净值的基金按各自数据库的最新日期增量更新; 尚无净值的新基金从成立日回填. 首次全库回填通常比日常更新耗时更长. iFinD 的 `--all` 保留为默认全库行为的别名.

两源均支持 `--codes`, `--file`, `--nav-state`, `--refresh-days`, `--end-date`, `--full-history`, `--workers`, `--request-interval` 和 `--database`. `existing/missing` 按目标来源自己的净值库判断, 请求间隔下限按来源分别设置.

## 按需更新其他数据

### 分红, 经理和资产

这些资料可按需要更新, 例如每周运行一次:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database --datasets dividend manager asset --workers 8
```

已同步名单时, 基础资料已刷新. 单独刷新基础资料可使用 `--datasets basic`.

### iFinD 基金细分类

分类用于页面的同类涨幅比较, 独立存入 `fund_category`:

```powershell
# 更新全部已入库基金的分类
.\.venv\Scripts\python.exe -m scripts.update_fund_categories

# 只更新指定基金, 适合补充新基金分类
.\.venv\Scripts\python.exe -m scripts.update_fund_categories --codes 000001 000003
```

默认每次请求 20 只基金; `--batch-size` 可设为 1 至 100. 添加 `--snapshot-only` 时仅保存分类快照. `--run-dir` 可指定尚未存在的输出目录.

分组采用 iFinD 的 `一级 / 二级` 投资类型, 同时保留原始三级分类. 每只基金使用最近采集的分类; 同日重跑更新同日记录. 一级或二级缺失时记录为空分组, 不沿用旧分类.

### iFinD 独立净值库

只保存三种净值和获取时间, 默认文件为 `data/ifind_nav.duckdb`. 不写入 Tushare 库, 不采集基础资料, 分红, 经理, 资产或分类, 也不切换页面来源.

```powershell
# 只建库, 不请求接口或读取凭证
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --init-only

# 日常全库更新, 使用 4 并发
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --workers 4

# 只更新 iFinD 库已有净值的基金, 暂不回填新基金
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --nav-state existing --workers 4

# 只采集 iFinD 库尚无净值的基金
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --nav-state missing --workers 4

# 指定基金或 CSV 批次, 与 Tushare 使用同一格式
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --codes 000001 000216
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --file data/fund_batches/batch_001.csv

# 按指定区间核对历史, 已采集过的基金加 --full-history 才会复查整个区间
.\.venv\Scripts\python.exe -m scripts.update_ifind_nav --codes 000001 --start-date 2023-01-01 --full-history

# 只读核对两库, 输出同日净值和共同基期复权收益差异
.\.venv\Scripts\python.exe -m research.compare_nav_sources --codes 000001 000216 110003 000311 110020 000171
```

默认采集公共名单中的全部基金; `--codes` 与 `--file` 互斥, CSV 必须有非空 `ts_code` 列, 空批次会报错, 不回退为全库. 名单沿用 `sync_fund_list` 的结果, 新增基金在下一次全库更新时自动纳入. `--start-date` 默认取本地成立日期, `--end-date` 默认今天; 名单外代码需要显式起始日. `--database` 指定独立净值库, `--reference-database` 指定只读名单库.

`--refresh-days` 默认 `7`, 按自身最后数据日及之前六个自然日增量请求, 加 `--full-history` 复查整个区间. `--nav-state` 默认 `all`, 可与指定代码或批次组合使用. 单基金各段全部成功后提交, 失败保留该基金原数据并继续其他基金. 三种净值全空的占位行不入库, 不删除历史; 重取时刷新获取时间, 也计入更新数量.

`--workers` 默认 `1`, 可选 `1` 至 `16`, 日常示例使用 `4`. 工作线程只取数和清洗, 主线程串行写库, 在途基金数限制为并发数的两倍. `--request-interval` 是所有线程共用的请求间隔, 默认 `0.15` 秒, 最小 `0.1` 秒. 鉴权和缓存加锁, 不会让所有网络请求串行等待. 接口频率说明见 [数据源](data-sources.md#页面配置与请求).

每个工作线程复用自己的 HTTP 连接, 整轮任务复用主线程写库连接, 避免逐次建连和重复检查表结构; 每只基金仍单独提交或回滚. 过程日志的 `fetch` 为取数及解析耗时, `write` 为写入耗时, 可据此判断耗时来源. 若主要等待接口响应, 可提高 `--workers` 至 `8`; 更短的请求间隔不会消除接口响应时间.

正在运行的旧任务继续按启动时的代码执行, 新增并发参数需在下次启动命令时使用. 重跑会按 iFinD 库实际末日增量处理, 已提交数据保留.

核对命令只读取本地数据, 不请求接口. 默认输出到 `data/nav_comparison/时间戳/`, 包含 `nav_comparison.csv` 和 `summary.json`; 支持 `--start-date`, `--end-date`, `--tolerance`(默认绝对误差 `1e-6`), `--ifind-database`, `--tushare-database` 和 `--output`. Tushare 按净值日取最新公告, 日期外连接保留单侧记录; 复权净值除原值外, 额外按首个共同有效日期归一化核对累计收益.

### 宽基指数行情

基金分析图表中的宽基对比需要单独采集指数行情. 停止 Streamlit 后运行:

```powershell
# 首次回填全部五个指数; 后续同一命令按各指数最新日期增量更新
.\.venv\Scripts\python.exe -m scripts.update_index_prices

# 只更新沪深300和中证500
.\.venv\Scripts\python.exe -m scripts.update_index_prices --codes 000300.SH 000905.SH
```

默认从 1990-01-01 查询, 实际历史以接口返回为准. 支持 `--start-date YYYY-MM-DD`, `--end-date YYYY-MM-DD`, `--full-history` 和 `--database`. 增量覆盖各指数最后数据日及之前六个自然日, 更早修订需加 `--full-history`.

每五年一段请求, 单个指数全部获取成功后才提交. 某指数失败保留原数据并继续其他指数, 摘要标记 `partial`. 此命令不下载基金数据, `update_database` 也不会自动更新指数. 字段和账号权限见 [数据源](data-sources.md#index_daily).

## 指定范围与参数

### 更新哪些基金

`update_database` 默认处理全部已入库基金. `--codes` 和 `--file` 二选一, 可搭配 `--datasets` 等参数:

```powershell
# 指定基金, 六位代码会自动补 .OF
.\.venv\Scripts\python.exe -m scripts.update_database --codes 000001 040001.OF --datasets nav --workers 8

# 指定批次, CSV 只需包含 ts_code 列
.\.venv\Scripts\python.exe -m scripts.update_database --file data/fund_batches/batch_001.csv --datasets nav --workers 8
```

指定代码必须已入库. 净值筛选还可使用 `--nav-state existing`(已有净值) 或 `--nav-state missing`(尚无净值), 默认 `all`. 终止基金仍在更新范围内.

### 更新哪些数据

| `--datasets` 值 | 内容 | 获取范围 |
| --- | --- | --- |
| `basic` | 基础资料 | 每次获取最新资料 |
| `nav` | 单位净值, 累计净值, 复权净值, 累计分红 | 按最后净值日增量更新 |
| `dividend` | 分红披露 | 每次获取全部记录 |
| `manager` | 经理任职历史 | 每次获取全部记录 |
| `asset` | 资产净值与合计资产净值 | 按资产表自己的末日增量更新 |
| `calendar` | SSE 交易日历 | 按日历末日增量更新, 每轮只处理一次 |

**省略 `--datasets` 会更新全部六项.** 对全库日常更新, 使用前面的净值与日历命令即可.

### 并发和日期

以下参数用于 `update_database`:

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--workers` | `1` | 取数并发数, 可选 1 至 16; 示例使用 8 |
| `--request-interval` | `0.15` | 所有线程共享的请求间隔(秒), 最小 `0.12` |
| `--refresh-days` | `7` | 增量窗口包含末日及之前 6 个日历日, 并更新至本轮结束日期 |
| `--end-date` | 今天 | 限制净值, 资产和日历的结束日期, 格式 `YYYY-MM-DD` |
| `--full-history` | 关闭 | 复查所选净值, 资产和日历的全部历史 |
| `--database` | `data/fund_database.duckdb` | 指定数据库; 其他入库命令也支持此参数 |

默认请求节奏合计约 400 次/分钟, 项目按账号 500 次/分钟设置上限. 并发用于重叠网络等待, 数据库由主线程统一写入; 发生限频时所有线程共同冷却 30 秒.

增量窗口外的历史缺口或修订, 使用 `--full-history` 复查. 例如:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database --codes 000001 --datasets nav --full-history
```

## 首次建库与手动添加

**首次使用**先初始化, 然后按前面的日常流程同步名单和数据:

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database
```

初始化只创建缺失的表, 重跑保留已有数据; 旧表字段变更需要单独迁移.

**只维护少量基金**时, 可以手动添加, 再更新该基金的数据:

```powershell
.\.venv\Scripts\python.exe -m scripts.add_fund 000001
.\.venv\Scripts\python.exe -m scripts.update_database --codes 000001
```

**批量添加**时, CSV 格式如下:

```csv
ts_code
000001.OF
040001.OF
```

```powershell
.\.venv\Scripts\python.exe -m scripts.add_fund --file data/fund_batches/batch_001.csv
.\.venv\Scripts\python.exe -m scripts.update_database --file data/fund_batches/batch_001.csv
```

批量添加会跳过已有代码, 每只新基金单独提交; 失败后可重跑同一文件. 刷新已有资料使用名单同步或 `update_database --datasets basic`.

也可从本地名单生成候选批次:

```powershell
.\.venv\Scripts\python.exe -m scripts.add_fund --from-list --fund-types 股票型 混合型 债券型 --limit 30
```

此命令仅生成 CSV, 路径在终端输出. 默认选取存续基金, 排除已入库代码, 按成立日期和代码排序; 不指定数量时最多 20 只. `--status` 可选 L/D/I, `--output` 可指定输出文件, 已有文件不覆盖. 生成后用同一 CSV 添加和更新数据.

## 中断与异常处理

| 情况 | 如何处理 |
| --- | --- |
| 断网, 超时或 `FetchError` | 查看重试日志; 默认最多尝试 3 次, 权限或凭证错误直接失败 |
| 某只基金最终失败 | 任务继续处理其他基金; 结束摘要标记 `partial`, 命令返回非零退出码 |
| 关机或手动中断 | 已提交数据保留; 等旧进程退出后可重跑原命令, 按主键新增或更新 |
| 需要跳过已处理基金续跑 | 根据日志整理剩余代码 CSV, 再用 `--file` 更新; 当前没有自动断点续跑参数 |
| 净值或资产缺少公告日期 | 异常行写入数据库同目录的 `rejected_rows/运行时间/`, 合格行继续入库, 本轮标记 `partial` |
| 修复更早的异常记录 | 来源补全后, 对对应基金加 `--full-history` 复查 |

日常更新中, 同一基金的基础资料与净值共同提交, 分红, 经理和资产各自提交. 名单同步和分类更新分别在整批获取及校验成功后提交, 写入失败整批回滚.

净值筛选 `existing` 只表示至少已有一条净值, 更早的缺口仍需全历史复查. 接口空返回时保留已有记录; 尚无净值的基金继续属于 `missing`.

## 日志与输出

更新结束后查看 JSON 摘要中的状态, 写入数量和失败原因. **强制中断可能没有结束摘要**, 此时以过程日志和数据库记录核对进度.

日志按任务分类存放. 历史文件归入相应目录; 整理时已运行的任务继续写原路径, 等任务结束后再迁移本轮日志.

| 任务 | 过程日志 | 摘要或快照 |
| --- | --- | --- |
| 名单同步 | `logs/fund_list/fund_list_sync.log` | `logs/fund_list/fund_list_sync_时间戳.json` |
| 数据更新 | `logs/database/database_update.log` | `logs/database/database_update_时间戳.json` |
| 指数行情 | `logs/database/index_prices.log` | `logs/database/index_prices_时间戳.json` |
| 建库与快照导入 | `logs/database/database_import.log` | 终端输出 |
| 手动添加 | `logs/fund_add/add_fund.log` | 批量添加: `logs/fund_add/add_funds_时间戳.json` |
| 生成批次 | `logs/fund_add/add_fund.log` | `data/fund_batches/batch_时间戳.csv`, `logs/fund_add/fund_batch_时间戳.json` |
| 名单快照 | 仅下载时为 `logs/fund_list/fund_list.log` | `data/fund_list/时间戳/` |
| iFinD 分类 | 终端输出 | `data/fund_categories/时间戳/` 中的 `fund_categories.csv` 和 `summary.json` |
| iFinD 净值 | `logs/database/ifind_nav.log` | `logs/database/ifind_nav_时间戳.json` |

研究脚本日志放在 `logs/research/`, 公网演示记录放在 `logs/demo/`, 页面预览记录放在 `logs/previews/`. 中断检查报告放在 `logs/database/`.

名单快照包含 `fund_basic_all.csv`, L/D/I 三份 CSV 和 `list_summary.json`. 三种状态全部获取并校验完成后才生成摘要; 自动选择名单时取目录名最新的完整快照.

### 空间控制

命令行过程日志每个文件最多约 10 MB, 自动轮转为 `.log.1` 至 `.log.5`, 保留五个备份. 已有超大日志会在下次写入时整体转为备份, 后续随轮转替换.

定期运行以下命令, 例如每周一次:

```powershell
# 先查看待清理清单
.\.venv\Scripts\python.exe -m scripts.clean_logs

# 执行清理
.\.venv\Scripts\python.exe -m scripts.clean_logs --apply
```

清理按最后修改时间计算: 成功摘要(`status=completed`)保留 30 天, 异常或其他任务摘要保留 90 天, 页面预览目录保留 7 天. 预览目录按全部子项的最新时间判断; 符号链接, 目录联接, 无法解析的摘要和检查后发生变化的文件跳过并列出原因.

命令只清理 `logs/` 中已知命名的任务摘要和过期预览目录, 保留过程日志, 演示记录和其他文件. 数据库及 `data/` 快照不在清理范围内. 当前没有定时清理任务, `--apply` 才执行删除.

## 其他操作

**只下载名单, 暂不入库:**

```powershell
.\.venv\Scripts\python.exe -m scripts.update_fund_list
```

`--run-dir` 可指定输出目录. 后续运行 `sync_fund_list --from-list <该目录>` 即可同步该快照.

**从已有单基金快照导入:**

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database --snapshot data/single_fund_audit/20261012_093903
```

目录须包含 `fund_basic.csv`, `fund_nav.csv`, `fund_div.csv`, `trade_cal.csv`. 导入时统一类型, 去除完全重复行, 主键缺失或冲突则拒绝导入; 四张表在同一事务中提交.
