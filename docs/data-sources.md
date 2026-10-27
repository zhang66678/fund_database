# 数据源

## Tushare Pro

### 凭证

- 优先读取环境变量 `TUSHARE_TOKEN`，否则读取项目根目录 `.env`。
- 客户端直接传入 Token 初始化，不调用 `ts.set_token`，避免凭证另存到本地文件。
- Token 不写入代码、日志或输出文件。

### 限频与重试

- 默认最小请求间隔 0.15 秒，单客户端理论上限约 400 次/分钟。
- 每个请求最多尝试 3 次。
- 限频错误等待 30 秒后重试；其他临时错误按 2、4 秒递增等待。
- 权限或凭证错误直接失败，不做无意义重试。
- 实际额度仍由 Tushare 账号权限决定，客户端设置不会提高额度。

## 当前使用的接口

| 接口 | 用途 | 程序请求字段 |
| --- | --- | --- |
| `fund_basic` | 场外基金档案 | 25 个基础字段 |
| `fund_nav` | 历史净值与资产披露 | `ts_code, ann_date, nav_date, unit_nav, accum_nav, adj_nav, accum_div`；资产另请求 `net_asset, total_netasset` |
| `fund_div` | 分红披露 | `ts_code, ann_date, div_proc, record_date, ex_date, pay_date, div_cash, net_ex_date, account_date` |
| `fund_manager` | 基金经理任职历史 | `ts_code, ann_date, name, begin_date, end_date, edu, resume` |
| `trade_cal` | SSE 交易日历 | `exchange, cal_date, is_open` |

### `fund_basic`

- `market='O'` 表示场外；接口默认是场内，必须显式指定。
- 分别请求 `status='L'`（存续）、`'D'`（终止）、`'I'`（发行）。
- 分页参数 `limit`、`offset` 按实测行为使用，每页 1000 行，直到空页。
- 同一基金的不同份额类别可能有不同代码，不自动合并。

关键字段：

| 字段 | 说明 |
| --- | --- |
| `ts_code` | 基金代码 |
| `name` | 基金简称 |
| `fund_type` | 投资类型，如股票型、混合型、债券型 |
| `status` | `L` 存续、`D` 终止、`I` 发行 |
| `found_date` | 成立日期 |
| `due_date` | 到期日期；未核实前不能直接当作实际终止日 |
| `delist_date` | 退市日期，场外样本可能为空 |
| `benchmark` | 业绩比较基准 |
| `issue_amount` | 发行份额，单位亿份 |
| `min_amount` | 起点金额，单位万元 |

### `fund_nav`

- 按基金代码逐只请求。
- 历史净值按 6 年窗口分段，避免单次返回被截断。
- 单段达到 2000 行时视为疑似截断并报错；这是程序保护阈值。
- `ann_date` 与 `nav_date` 可能不同；同日多公告版本保留，查询时取最新。

字段说明：

| 字段 | 说明 |
| --- | --- |
| `unit_nav` | 单位净值 |
| `accum_nav` | 累计净值 |
| `adj_nav` | 复权单位净值，指标计算使用此列 |
| `accum_div` | 累计分红；缺失不等于没有分红 |
| `net_asset` | 资产净值 |
| `total_netasset` | 合计资产净值 |

资产接口与净值来自同一接口，但字段和更新窗口不同，分别写入 `fund_asset`。

### `fund_div`

- 按基金代码获取全部返回事件，不受 `--end-date` 限制。
- `div_proc` 表示方案进度；不同公告或进度是不同披露记录。
- 不把不同版本的 `div_cash` 相加，也不把记录数当实际分红次数。
- `div_cash` 单位为每份派现金额。

### `fund_manager`

- 按基金代码分页获取全部任职记录。
- 姓名不是永久人员 ID；同名人员暂不合并。
- 离任日期缺失只表示接口未提供，不能据此认定仍在任。
- 官方要求至少 500 积分。

### `trade_cal`

- 固定使用 SSE 日历。
- 按年分段请求，校验日期完整、无重复、`is_open` 只能为 0 或 1。
- 日历覆盖范围可能晚于基金成立日，查询前需确认。

## 复权因子

`fund_adj` 曾在研究脚本中尝试获取独立复权因子。当前场外样本均返回空表，因此正式流程直接使用供应商提供的 `adj_nav`：

- 不把缺失因子填为 1。
- 不用 `adj_nav / unit_nav` 冒充独立因子。
- 代码和日期对齐逻辑保留在 `research/fetch_fund_history.py`，不在正式流程中。

## 未接入的接口

以下接口仅保留调研条目，当前程序不调用：

| 接口 | 用途 |
| --- | --- |
| `fund_company` | 基金管理人 |
| `mkt_idx_bmk` | 业绩比较基准 |
| `fund_share` | 基金份额；文档市场参数为 SH/SZ，暂不用于场外规模 |
| `fund_portfolio` | 基金持仓；需区分报告期和公告版本 |
| `fund_factor_pro` | 场内基金技术因子 |

## iFinD

页面已接入 iFinD HTTP 查询，侧栏可切换来源。`ifind_test/` 仍是独立 SDK / HTTP 示例，正式页面不依赖测试脚本。已验证：

- Python SDK 登录和 HTTP `refresh_token` 鉴权。
- 基金资料、日期序列净值、历史净值、公告查询。
- 错误码 0，SDK 与 HTTP 结果一致。

运行方式和指标名见 [iFinD 示例](../ifind_test/README.md)，测试记录见 [TEST_RESULTS](../ifind_test/TEST_RESULTS.md)。

### 页面配置与请求

项目根目录 `.env` 增加：

```dotenv
IFIND_REFRESH_TOKEN=你的refresh_token
```

环境变量优先于 `.env`。页面 HTTP 模式不需要 `IFIND_USERNAME`、`IFIND_PASSWORD` 或 SDK；Refresh Token 获取方式见 [独立示例](../ifind_test/README.md)。修改凭证后重启页面服务。

`api/ifind_client.py` 使用 `get_access_token` 获取访问令牌，再请求 `basic_data_service` 和 `cmd_history_quotation`。访问令牌仅保存在当前会话内存；不会调用刷新 Refresh Token 的接口。

成功请求按接口和参数缓存 10 分钟，每会话最多缓存 128 个结果，失败不缓存。超时为 30 秒，错误显示接口及错误码，不显示凭证或原始响应，不自动重试或切换来源。缓存结果保留原始净值获取时间，长历史取各段最早的获取时间。

### 字段映射与范围

| 接口 / 字段 | 用途 / 项目字段 |
| --- | --- |
| `ths_fund_short_name_fund` | 基金简称 |
| `ths_fund_establishment_date_fund` | `found_date`，成立日期 |
| `netAssetValue` | `unit_nav`，单位净值 |
| `accumulatedNAV` | `accum_nav`，累计净值 |
| `adjustedNAV` | `adj_nav`，复权净值 |

净值按最多 3 个自然年分段请求，日期为 `YYYY-MM-DD`，使用 `Interval:D,Fill:Blank`，保留缺失值。不写入现有数据库，也不构造公告日期；HTTP 历史净值返回的当前序列不提供原有 Tushare 公告版本档案。

基金代码沿用六位代码加 `.OF` 的场外基金约定，不合并份额类别。基金候选名单取自本地数据库，名单不代表 iFinD 可查询范围；单基金和对比页均可补充输入其他代码。

投资类型、业绩比较基准、分红、经理和资产暂未接入。`ths_fund_type_fund` 是运作形式，不能直接映射为投资类型。

绩效计算显式复用本地 SSE 日历及现有公式。日期未覆盖或样本不足时提示计算失败，仍可展示原始净值。两源复权方法尚需以分红样本进一步核对，不能要求原始复权数值或计算结果完全相同。页面交互见 [页面说明](../app/README.md#数据源模式)。

## 官方文档

- [基金列表](https://tushare.pro/document/2?doc_id=19)
- [公募基金净值](https://tushare.pro/document/2?doc_id=119)
- [基金分红](https://tushare.pro/document/2?doc_id=120)
- [交易日历](https://tushare.pro/document/2?doc_id=26)
- [基金经理](https://tushare.pro/document/2?doc_id=208)
- [基金复权因子](https://tushare.pro/document/2?doc_id=199)
