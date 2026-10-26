# 数据库与数据访问层

## DuckDB 单基金 MVP

已建立 `data/fund_database.duckdb`, 导入华夏成长核查快照 `data/single_fund_audit/20261012_093903/`. 页面默认已通过 `DuckDBFundRepository` 查询数据库.

| 表 | 首次单基金快照导入行数 | 主键 |
| --- | --- | --- |
| fund_basic | 1 | ts_code |
| fund_nav | 6027 | ts_code, nav_date, ann_date |
| fund_dividend | 25 | ts_code, ann_date, ex_date, div_proc |
| trade_calendar | 9065 | exchange, cal_date |

保留快照中已请求的字段, 代码和文本使用 VARCHAR, 日期使用 DATE, 数值使用 DECIMAL(24,10), 日历开闭市标记使用 INTEGER. 空值保留为 NULL. Decimal 从源字符串转换, 避免先转浮点造成额外精度损失.

写入时先将 Decimal 以精确字符串传给 DuckDB, 再按 schema 转换为 DECIMAL(24,10), 避免 DataFrame 抽样推断出的中间数值类型过窄. 不扩大数据库字段, 不截断超出字段范围的数值.

- 基础资料为当前状态, 更新不删除历史净值或分红.
- 净值保留不同公告日期的版本; 同代码, 净值日和公告日的修订更新原记录, 暂不另建变更审计表.
- 分红主键是当前样本的披露记录标识, 不是供应商提供的永久事件 ID. 公告日期, 除息日或方案进度变化会形成新记录. 后续多基金与增量更新时需进一步确认事件关联, 不能直接把各披露版本的金额汇总为总分红.
- 主键字段缺失或同主键存在冲突源记录时拒绝导入, 不补造日期. 错误信息区分缺失与冲突, 分别列出缺失字段及行数, 或冲突记录数. 当前样本的 29 行分红中有 4 行完全重复, 入库为 25 条, 原始 CSV 不变.
- 四张表在同一事务中写入, 任一写入失败则全部回滚. 不删除已有记录. 日历标记限制为 0 或 1.
- 重复运行仅更新变化记录, 日志分别统计新增, 更新, 未变化及源数据重复行数. 当前单基金导入器校验净值和分红代码归属, 暂未添加跨表外键.
- 建表仅使用 CREATE TABLE IF NOT EXISTS. 后续字段变更需要明确迁移, 不能靠重复运行初始化修改旧表结构.

实现文件:

- `connection.py`: 默认路径与连接生命周期.
- `schema.py`: 核心四张表及经理, 资产两张补充表的字段和约束.
- `writer.py`: 数据库 UPSERT 和写入数量统计.
- `etl/import_snapshot.py`: CSV 类型转换, 去重, 质量检查与事务导入.
- `scripts/init_database.py`: 命令行入口及日志配置.

在项目根目录仅创建数据库和六张空表, 不需要快照或 Tushare Token:

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database
```

重复执行只创建缺失的表, 保留已有数据. 可通过 `--database` 指定数据库路径. 建库后可以运行 `scripts.add_fund` 添加基金资料, 再运行 `scripts.update_database` 获取历史数据.

如需导入已有单基金快照, 显式指定目录:

```powershell
.\.venv\Scripts\python.exe -m scripts.init_database --snapshot data/single_fund_audit/20261012_093903
```

快照导入会在同一事务中建表并写入数据, 不需要事先单独建库. 当前仅接受单基金快照, 初始化入口不会请求 Tushare. 日志位于 `logs/database_import.log`, 数据库和日志不提交 Git.

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

### 固定 CSV 批量添加

可以先从本地总名单生成小批次, 或在 `data/fund_batches/` 下自行准备 CSV. 只需要 `ts_code` 列, 其他列可以保留用于人工查看, 不作为入库资料. 使用 UTF-8 或 UTF-8 BOM 编码, 推荐保留 `.OF` 后缀以避免 Excel 丢失代码前导零:

```csv
ts_code
000001.OF
040001.OF
000011.OF
```

添加前先停止 Streamlit, 然后在项目根目录运行:

```powershell
.\.venv\Scripts\python.exe -m scripts.add_fund --file data/fund_batches/batch_001.csv
```

单只代码, `--file` 入库和 `--from-list` 生成清单三选一, 均支持 `--database`. 批量模式先统一六位代码及 `.OF` 大小写, 清单内部按代码去重, 并跳过完整 `fund_basic` 中已有的代码, 包括已终止或尚无净值的基金. 已有基金不请求 API 或更新资料; 资料更新继续交给 `update_database`. 不按基金名称合并, 不同代码的份额仍是不同记录.

每只新基金通过 Tushare 获取最新资料, 复用原有字段清洗, 名称与成立日期检查及主键约束, 单只独立提交事务. 一只失败会记录错误并继续, 同一代码在同一批次只尝试一次. 重跑同一份文件时, 已成功添加的基金跳过, 失败的代码重新尝试. 如文件中有非法代码, 请先修正对应记录. 全是已有基金或清单内部重复代码时不初始化 API 客户端, 不需要 Token.

终端和 `logs/add_fund.log` 展示逐只状态, 每次批量任务另存 `logs/add_funds_时间戳.json`. 摘要包含开始与结束时间, `total`, `added`, `skipped_existing`, `skipped_duplicate`, `failed` 及逐条原因. 正常完成返回 0, 存在失败时状态为 `partial` 并返回 1. CSV 缺少 `ts_code` 等文件级错误在请求前终止.

`--file` 会处理文件内全部记录, 不在入库阶段重新筛选或限制数量. 不要直接传入全市场 `fund_basic_all.csv`, 否则会尝试添加其中全部尚未入库的代码. 建议先用下面的 `--from-list` 生成小批次. 批量添加只写基础资料, 不下载净值或改变数据库结构; 添加完成后继续使用原有的统一更新命令.

实现: `scripts/add_fund.py` 只处理参数及日志, `etl/add_funds.py` 负责筛选, 单只和批量添加, `database/writer.py` 负责完整代码清单查询及事务写入. 两个集成场景使用临时数据库验证批量排重与失败重跑, 以及最新名单筛选和只读生成, 不请求真实 API:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_add_funds.py -q
```

### 从总名单自动生成批次

继续使用同一个入口, `--from-list` 只生成候选清单, 不请求 Tushare, 不读取 Token 或写数据库. 不传路径时, 自动从 `data/fund_list/` 下按 `YYYYMMDD_HHMMSS` 快照目录名取最新 `fund_basic_all.csv`, 不按文件修改时间判断:

```powershell
# 默认选存续场外基金, 最多 20 只, 自动生成时间戳文件名.
.\.venv\Scripts\python.exe -m scripts.add_fund --from-list

# 指定类型和数量, 可一次选择多种类型.
.\.venv\Scripts\python.exe -m scripts.add_fund --from-list --fund-types 股票型 混合型 债券型 --limit 30

# 指定来源快照, 包含已终止基金, 并指定输出文件.
.\.venv\Scripts\python.exe -m scripts.add_fund --from-list data/fund_list/20261019_093730/fund_basic_all.csv --status L D --limit 30 --output data/fund_batches/batch_001.csv
```

- `--status` 默认 `L`, 可同时选 `L`, `D`, `I`; 发行中基金若缺少成立日期则不进入本次清单.
- `--fund-types` 不指定时不限类型; 名称取自快照的 `fund_type`, 如 股票型, 混合型, 债券型. 不存在的类型报错, 不默默生成空清单. 多种类型合并为一个候选池, 不按类型分配配额; 需要每类若干只时可分别生成批次.
- `--limit` 必须大于 0, 默认 20. 先按代码去重并排除完整 `fund_basic` 中的已有基金, 再限数量; 候选不足时导出实际数量.
- 固定按成立日期从早到晚, 同日按代码排序, 方便重现. 不缺省随机抽样.
- 排除非法代码, 空名称或无效成立日期, 只选场外市场 `O`. 清单保存代码, 名称, 类型, 成立日期及状态, 日期以 `YYYY-MM-DD` 展示.
- `--output` 不传时保存在 `data/fund_batches/batch_时间戳.csv`. 已有文件不覆盖, 避免破坏用于重试的固定批次.

终端和 `logs/add_fund.log` 展示选中数量, 可用候选数量及跳过统计, 另存 `logs/fund_batch_时间戳.json`, 保留来源, 条件及输出路径. 生成清单可以与本机只读查询共存; 正式 `--file` 入库前仍应停止 Streamlit. `--limit`, `--status`, `--fund-types` 和 `--output` 仅用于生成清单, 不能附加到单只添加或 `--file` 模式.

查看生成的 CSV 后, 用 `--file` 指向同一文件添加. 重试时复用该文件; 不要重新生成一批来替代重试, 因为数据库已有基金变化后, 新清单会排除上一批成功添加的基金. 筛选基于本地快照, 不表示实时状态或历史净值完整, 入库资料仍由 Tushare 重新获取. 新增更早成立的基金后, 历史查询前还需检查交易日历覆盖范围.

### 获取历史与增量数据

然后运行下面的更新命令, 同时获取净值, 分红, 经理和资产资料. 新基金没有净值或资产时会从成立日回填; 有净值后才出现在页面下拉列表里. 添加和更新均可用 `--database` 指定数据库, 两次需指向同一文件. 添加基金前也应停止网站以避免写入锁冲突.

在项目根目录运行, 更新前先停止 Streamlit, 避免独立进程的 DuckDB 文件锁冲突:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database
```

默认更新数据库中的全部基金. 若只想下载本次新增批次的数据, 使用添加时的同一个 CSV:

```powershell
# 添加本批基金基础资料
.\.venv\Scripts\python.exe -m scripts.add_fund --file data/fund_batches/batch_001.csv

# 只更新本批基金的历史数据
.\.venv\Scripts\python.exe -m scripts.update_database --file data/fund_batches/batch_001.csv

# 少量基金也可以直接指定代码
.\.venv\Scripts\python.exe -m scripts.update_database --codes 000001 040001.OF

# 只更新本批基金的净值和资产
.\.venv\Scripts\python.exe -m scripts.update_database --file data/fund_batches/batch_001.csv --datasets nav asset
```

`--file` 与 `--codes` 二选一, 不传则更新全库. CSV 只需 `ts_code` 列, 名称等其他列不参与更新; 代码自动统一为 `.OF` 后缀并去重. 所有指定基金必须先添加到数据库, 未入库代码或空名单会报错, 不自动添加或退回全库更新. 更新目标仍从数据库读取名称, 成立日期和最新净值日, 仅筛选本轮任务.

指定 200 只基金时进度为 `1/200` 至 `200/200`, 不逐只请求其他基金. 范围参数可与 `--datasets`, `--full-history`, `--refresh-days` 组合使用. 不传 `--datasets` 仍更新所选基金的全部数据; 交易日历是共享数据, 选中 `calendar` 时仍统一更新一次. 同一批次可重复执行, 已有净值按窗口补更, 尚无净值的基金从成立日回填, 便于中断或失败后重试.

不传 `--datasets` 时更新全部数据. 指定后只请求和写入对应条目, 可一次选择多个:

| 参数值 | 更新内容 |
| --- | --- |
| basic | 基金基础资料 |
| nav | 单位净值, 累计净值, 复权单位净值及累计分红字段 |
| dividend | 分红披露记录 |
| manager | 基金经理任职历史 |
| asset | 资产净值及合计资产净值 |
| calendar | SSE 交易日历 |

```powershell
# 只更新资产信息
.\.venv\Scripts\python.exe -m scripts.update_database --datasets asset

# 只更新净值
.\.venv\Scripts\python.exe -m scripts.update_database --datasets nav

# 同时更新净值和资产
.\.venv\Scripts\python.exe -m scripts.update_database --datasets nav asset
```

指定条目后不会自动更新其他条目. 净值和资产需要的成立日期直接读取数据库, 不额外请求基金基础资料; 日期缺失会记录失败. 日历只在选中 `calendar` 或默认全部更新时请求. `asset` 和 `nav` 虽来自同一接口, 分别请求各自字段并写入各自表. 未选中的业务记录保留不变.

任务从数据库 `fund_basic` 读取代码, 名称, 市场和成立日期, 并关联每只基金的最新净值日. 名称用于日志, 代码用于 API 请求, 不使用固定清单或 `top_10.csv`, 不按存续状态排除基金. 今后将基金资料加入此表后, 同一程序即可处理, 无须修改基金代码列表.

- 净值从最后净值日前回看 7 个日历日 (含该日), 获取至任务截止日. 停更后会覆盖中间未更新的时间, 长区间继续分段请求. 无净值的基金从成立日获取.
- 基础资料重新获取并更新名称和状态; 分红记录量小, 每只基金重新分页获取后去重并 UPSERT. 不删除历史记录.
- 经理重新分页获取全部任职记录; 资产根据资产表自己的最新日期滚动更新, 两项资产全空的行不保存. 同一次命令完成, 无需另行操作.
- SSE 日历每轮更新一次, 从库内日历末日回看, 不为每只基金重复请求.
- 每只基金所选的基础资料和净值先共同提交, 本批失败则回滚. 所选的分红, 经理和资产分别获取, 清洗并独立提交, 单项失败不撤销其他已成功的数据, 继续其他任务. 存在失败时整体状态为 partial, 下次运行重新尝试.
- 净值返回空表时保留原数据, 日志标记 `empty_nav`, 不能据此证明该基金没有遗漏或已经完整. 当前不做自动停运判定.
- 净值和资产缺少公告日期时, 仅将这些原始行隔离到数据库所在目录的 `rejected_rows/运行时间/` CSV 中, 其余记录继续严格校验并 UPSERT. 不补造公告日期, 不填充缺失净值, 不更改主键; 其他主键缺失或冲突仍报错. 有隔离行时该基金和本轮任务标记 `partial`, 即使全部行被隔离也不会标记成功.
- 隔离文件保留接口原始字段和值, 与日志一起用于追查. 净值摘要的 `quality.fund_nav` 和资产摘要的 `details.fund_asset.quality` 记录隔离数量, 原因和文件路径. 常规增量不会自动回查窗口外的隔离日期; 数据源补全后需用 `--full-history` 复查, 历史隔离文件保留作为记录. 本轮无异常不代表全部历史已完整.
- 历史窗口外的净值或资产修订可以扩大回看区间, 或使用 `--full-history` 重新获取全部历史. 回看窗口不是完整历史质量保证.
- 当前分红表要求公告日期和除息日等主键非空且无冲突. 若接口返回异常主键, 本轮分红更新失败并保留原有分红记录, 该基金标记 partial; 不影响净值等其他数据入库, 不补造主键或静默跳过异常记录.

可调整窗口, 截止日和数据库:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database --refresh-days 30 --end-date 2026-10-12 --database data/fund_database.duckdb
```

日志为 `logs/database_update.log`, 每次运行另存一个 `database_update_时间戳.json` 结果摘要, `datasets` 列出本次选择的条目, 含各基金状态及所选表的新增, 更新, 未变化数量. `details` 记录所选分红, 经理和资产的状态, 失败原因, 重复行及忽略的全空行; 分红失败可查看 `details.fund_dividend`. `partial` 返回非零退出码, 表示部分数据失败, 其他已成功的数据仍然保存. 暂不自动发现数据库之外的新基金, 不做并发请求或持久化任务队列.

指定更新范围时, JSON 摘要另含规范化后的 `requested_codes`; 使用 CSV 时还记录 `source_file`, 便于确认本轮实际处理的批次.

实现: `etl/update_funds.py` 负责更新流程, `scripts/update_database.py` 提供命令入口. `api/tushare_client.py` 统一处理限频, 重试和凭证脱敏, `api/fund_fields.py` 维护请求字段, `etl/extract.py` 负责分页和日期分段获取. 复用快照导入的类型转换和数据库 UPSERT, 正式运行不依赖 research 目录.

API 客户端默认请求最小间隔为 0.2 秒, 单个客户端理论上限约 300 次/分钟. 实际速度受接口响应及处理耗时影响; 检测到限频错误仍等待 30 秒后重试. 此设置不会改变账号本身的接口额度.

## 基金经理和资产披露资料

经理和资产资料已合并到日常更新流程, 从数据库 `fund_basic` 读取基金代码与成立日期, 包含已终止基金. 当前选用以下信息:

| 表 | 来源接口 | 保存字段 | 主键 |
| --- | --- | --- | --- |
| fund_manager | fund_manager | ts_code, ann_date, name, begin_date, end_date, edu, resume | ts_code, name, begin_date, ann_date |
| fund_asset | fund_nav | ts_code, ann_date, nav_date, net_asset, total_netasset | ts_code, nav_date, ann_date |

基金类型, 投资类型, 基金公司, 托管人, 管理费, 托管费及业绩基准已经保存在 `fund_basic`, 无须重复建表. 基金持仓作为后续步骤, 需区分报告期, 公告版本和披露范围. `fund_share` 文档的市场参数为 SH/SZ, 输出为基金份额, 本轮不作为场外基金资产规模来源.

停止网站后运行:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database
```

统一更新入口自动为旧数据库创建缺失表, 不改动原有表结构和数据. 实现集中在 `etl/update_funds.py`, 使用现有 API 客户端, 分页, 日期分段, 类型转换和 UPSERT. 没有数据库时先初始化, 空基金清单不会发起请求.

- 经理记录量小, 每次重新分页获取全部任职记录. 日期中的空格转为 NULL. 离任日期缺失只表示接口未提供, 不能据此认定仍在任职. 姓名不是永久人员 ID, 暂不合并跨基金同名人员.
- 资产首次从成立日起按两年分段回填, 后续从资产表自己的最后披露日前回看 7 个日历日, 不能用日频净值末日代替资产末日. 窗口通过 `--refresh-days` 调整.
- 只保存资产净值或合计资产净值至少一项非空的行, 不保存全空的日频记录, 不前向填充. 保留两项原始金额, 不相加或擅自把合计值视为该份额的资产净值. 官方字段表未明确金额单位和合计份额范围, 页面换算前还需核对基金原始报告.
- 经理和资产分别提交事务; 一个接口失败不妨碍另一类资料或其他基金. 重复运行不产生重复业务记录. 不同公告日期的版本保留, 同主键变更更新, 主键缺失或冲突不补造数据.
- `--end-date` 限制净值, 资产的净值日期及日历日期, 经理和分红获取全部返回记录; 本入口不是按历史公告时点还原的查询. 增量窗口外的修订和补录需要定期全历史复查.

同一入口全历史复查净值, 资产和交易日历; 配合 `--datasets` 时仅复查所选条目:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_database --full-history

# 只复查资产的全部历史
.\.venv\Scripts\python.exe -m scripts.update_database --datasets asset --full-history
```

全部更新结果统一记录在 `logs/database_update.log` 和 `database_update_时间戳.json`, 不再需要独立的补充更新日志. 经理或资产接口返回空表会标记 `empty`, 不能解释为该基金确实没有对应数据. 有失败时任务标记 `partial` 并返回非零退出码.

参考官方文档: [基金经理](https://tushare.pro/document/2?doc_id=208), [基金净值与资产净值](https://tushare.pro/document/2?doc_id=119), [基金份额](https://tushare.pro/document/2?doc_id=207), [基金持仓](https://tushare.pro/document/2?doc_id=121).

2026-10-13 实际补充结果:

| 基金代码 | 名称 | 经理披露记录 | 有效资产记录 | 最新资产日期 |
| --- | --- | --- | --- | --- |
| 000001.OF | 华夏成长 | 19 | 101 | 2026-06-30 |
| 005827.OF | 易方达蓝筹精选混合 | 4 | 38 | 2026-06-30 |
| 040001.OF | 华安创新 | 12 | 102 | 2026-06-30 |

任务完成, 共写入 35 条经理披露记录和 241 条有效资产记录, 忽略 13835 条两项资产全空的记录. 经理记录数不等于不同经理人数, 资产末日不等于日频净值末日或更新任务运行日期. 页面已接入经理任职资料和最新资产披露卡片.

## DuckDB 查询实现

`duckdb_repository.py` 实现既有的基金列表, 区间净值和交易日历接口. 日期和代码全部参数化; 数据库金额字段保留 DECIMAL, 查询净值时转换为浮点数, 供现有服务层计算. 按净值日选择公告日期最新的版本, 主键保证同版本唯一. 每次查询打开并关闭只读连接, 不共享全局连接.

`get_basic(ts_code)` 只读接口从已有 `fund_basic` 表返回成立日期, 投资类型和业绩比较基准, 不修改表结构. 返回当前本地档案, 不按净值区间回溯历史资料. CSV 模式从同一份名单读取, 缺少的字段保留为空.

`get_managers(ts_code)`, `get_assets(ts_code)` 和 `get_dividends(ts_code)` 也是只读接口. 经理按姓名和任职日期区分任期, 每个任期选最新公告, 按任职日期倒序返回. 资产每个净值日期选最新公告, 按日期升序返回, 金额转浮点供展示. 分红读取全部本地披露, 按除息日和公告日倒序返回, 保留不同公告和方案版本, 不累加金额或推断实际分红次数. 不修改原有历史版本或补填缺失值. `FundService.get_details` 同时整理基础资料, 任职记录, 最新资产和分红, 各类读取错误分别返回给页面, 互不影响. CSV 对照实现明确提示未支持分红, 经理及资产, 不混用其他数据源.

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
    "data/fund_list/20260921_095101/longest_lived/history/20260921_102854/nav.csv"
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
