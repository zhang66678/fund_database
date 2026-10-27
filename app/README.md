# 页面说明

页面使用 Streamlit + Plotly，默认读取 DuckDB。指标公式见 [指标与对比](../docs/metrics.md)，数据准备见 [数据流程](../docs/etl.md)。

## 启动

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

打开终端显示的地址，通常为 `http://127.0.0.1:8501`。请使用模块方式启动，不要直接运行 Python 文件。

## 临时公网演示

```powershell
.\data\tools\cloudflared.exe tunnel --url http://127.0.0.1:8501 --protocol http2 --no-autoupdate
```

把终端中生成的 `https://...trycloudflare.com` 地址发给对方。该方式没有登录验证，仅用于临时演示；结束隧道后地址失效。

## 页面结构

| 页面 | 内容 |
| --- | --- |
| 基金分析 | 单基金净值、累计收益、回撤、绩效、分红、经理、资产 |
| 基金对比 | 多基金共同区间累计收益与绩效对照 |
| 数据说明 | 页面内展示的数据口径与限制 |

两个分析页面各自保存“编辑条件”和“已提交结果”，切换导航不会互相覆盖。

## 基金分析

### 搜索与区间

- 左侧按代码或名称选择本地基金，右侧保留手动输入。
- 首次进入不预选基金；选择后默认使用近 1 年。
- 快捷区间：1 个月、3 个月、6 个月、1 年、3 年、全部、自定义。
- 快捷区间以该基金本地最新净值日为末日，向前回溯；全部范围取实际起止日。
- 自定义日期限制在该基金本地净值范围内，且不超过今天。

### 曲线

默认展示单位净值、累计收益率和回撤，可自由勾选：

- 单位净值、累计净值、复权单位净值：左轴，原始量级。
- 累计收益率：右轴，区间首个开市日为 0%。
- 回撤：下方独立区域，从 0% 向下绘制。

累计收益和回撤基于复权净值，与勾选哪种原始净值无关。缺失开市日保留断点，不插值。

### 概览与资料

- 概览：成立日期、投资类型、业绩比较基准、区间末日单位/累计净值。
- 绩效：七项指标，按 3 + 2 + 2 排列；不足一年时提示年化影响。
- 分红：本地全部披露，按除息日和公告日倒序；不同方案版本分别展示。
- 经理：按任职日期倒序，每个任期取最新公告；离任日期缺失显示“未提供”。
- 资产：本地最新披露日期、公告日期和两种原始金额。

资料读取失败时只影响对应卡片，不影响其他板块。

### 提交行为

- 修改基金、日期或曲线后，页面提示“条件已修改”，但仍展示上次结果。
- 点击“查询”后统一更新概览、图表、绩效和资料。
- 查询失败时清除旧结果并显示原因。

## 基金对比

- 多选两只及以上本地基金。
- 快捷区间以所选基金的共同净值末日为基准；“全部”取共同覆盖范围。
- 提交后服务计算首末共同开市日，并在图表上方标注实际比较区间。
- 累计收益从同一基期的 0% 开始；中间缺失保留断点。
- 绩效表以指标为行、基金为列。
- 每行最优值红色、最差值绿色；收益和比率越高越优，波动、下行风险和最大回撤越低越优。
- 某只基金指标不可计算时显示原因，不影响其他基金。

## 主题与图表

- 浅色金融分析主题：灰蓝背景、白色卡片、蓝色主按钮。
- 净值颜色固定：单位净值蓝、累计净值青绿、复权净值紫。
- 累计收益橙色虚线，回撤淡红色面积。
- 带回撤区的单基金图高 560px，其他图 440px。
- 主题配置在 `.streamlit/config.toml`，需从项目根目录启动。
- 修改主题后停止并重启 Streamlit，再刷新浏览器。

## 数据源模式

默认使用 `data/fund_database.duckdb`，可用 `DATABASE_PATH` 指定其他文件。

显式设置 `NAV_CSV` 时切换到 CSV 对照模式：

```powershell
$env:NAV_CSV = "data/fund_list/20260921_095101/longest_lived/history/20260921_102854/nav.csv"
```

CSV 模式可选配置：

| 变量 | 内容 |
| --- | --- |
| `FUND_NAMES_CSV` | 基金名称，默认读取净值同目录的 `fund_basic.csv` |
| `CALENDAR_CSV` | 交易日历，默认读取净值同目录的 `trade_cal.csv` |

CSV 模式不提供分红、经理和资产资料；没有日历时不能计算指标。恢复默认：

```powershell
Remove-Item Env:NAV_CSV, Env:FUND_NAMES_CSV, Env:CALENDAR_CSV -ErrorAction SilentlyContinue
```

## 代码组织

| 文件 | 职责 |
| --- | --- |
| `bootstrap.py` | 根据环境变量组装 Repository 和 Service |
| `streamlit_app.py` | 单基金页面编排、查询调度、会话状态 |
| `comparison_page.py` | 对比页面编排和独立会话状态 |
| `components.py` | 导航、搜索、筛选、概览、图表、绩效和资料卡片 |
| `styles.css` | 卡片、导航和窄屏布局补充 |
| `../.streamlit/config.toml` | Streamlit 全局主题 |

页面不访问 Tushare，也不直接执行 SQL。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_streamlit_app.py tests/test_fund_service.py tests/test_fund_metrics.py -q
```

页面测试使用临时 DuckDB，覆盖查询、筛选、未提交提示、会话状态、数据缺口、无共同区间和读取失败。
