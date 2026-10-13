# 净值曲线 demo

在项目根目录的 PowerShell 中启动:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/streamlit_app.py --server.address 127.0.0.1 --browser.gatherUsageStats false
```

打开终端显示的本地地址, 通常为 `http://127.0.0.1:8501`. 请使用上述模块启动方式, 不要直接运行 Python 文件. 终端按 `Ctrl+C` 停止服务.

## 临时公网演示

使用 Cloudflare Quick Tunnel 将本机网站映射到临时 HTTPS 地址, 其他电脑无需连接同一 Wi-Fi. 本机网站仍监听 `127.0.0.1`, 不需要开放路由器端口或新增入站防火墙规则.

先按上文启动 Streamlit, 然后在另一个项目终端运行:

```powershell
.\data\tools\cloudflared.exe tunnel --url http://127.0.0.1:8501 --protocol http2 --no-autoupdate
```

将终端中生成的 `https://...trycloudflare.com` 地址发给演示对象. 保持电脑开机联网, 网站和隧道进程持续运行. 隧道终端按 `Ctrl+C` 结束公网访问, 再次启动通常会得到新地址. 此方式没有登录验证, 得到地址的人可以访问 demo, 仅用于临时演示.

隧道工具保存在 Git 忽略的 `data/tools/` 中. 如果由助手后台启动, 进程编号分别记录在 `logs/demo_server.pid` 和 `logs/demo_tunnel.pid`, 日志也在 `logs/`. 停止后台隧道前可核对进程:

```powershell
$demoTunnelId = [int](Get-Content logs/demo_tunnel.pid)
Get-Process -Id $demoTunnelId
# 确认为本次 cloudflared 进程后执行.
Stop-Process -Id $demoTunnelId
```

后台网站可用 `logs/demo_server.pid` 同样核对后停止. 不要在重启电脑后直接复用旧 PID, 它可能已被其他进程使用.

## 页面操作

1. 可以直接输入基金代码, 如 `000001` 或 `000001.OF`, 也可以在新增的下拉框中按代码和名称选择. 选择后自动填入代码框, 仍可手动修改, 最终以代码框为准.
2. 分别通过年, 月, 日下拉框选择开始日期和结束日期. 年份完整列出 1900 年至今年, 可选日期截止今天. 年月变化时自动更新有效天数, 如将闰年 2 月 29 日切换到平年会调整为 2 月 28 日.
3. 勾选单位净值, 累计净值, 复权单位净值中的一种或多种.
4. 点击查询, 在日期选择栏上方的固定图表中查看净值曲线, 下方展示七项指标, 基金经理和资产信息. 修改选项后再次点击查询, 页面不展示数据表.

页面首次打开即显示空坐标图. 查询后保留最近一次曲线和指标, 调整日期或净值选项时不会消失, 点击查询后更新. 图表下方标明实际查询的基金与日期区间. 查询失败或范围内没有数据时清除旧曲线, 保留空图并显示提示.

基金经理和资产资料使用独立板块展示, 同样保留最近一次查询结果:

- 基金经理: 按任职日期倒序展示任期, 每个任期使用最新公告记录, 可展开查看任职日期, 离任日期, 学历, 公告日期和简历. 离任日期缺失显示"未提供", 不直接认定在任.
- 资产信息: 展示本地最新披露的资产日期, 公告日期, 资产净值和合计资产净值. 日期不受所选净值区间限制, 金额按原值展示, 单位及合计范围待核对, 不相加或换算为亿元. 缺失金额显示"未提供".

资料读取失败或尚未入库时显示对应提示, 不影响其他板块. 净值区间没有数据时仍可查看该基金的经理和资产资料. 旧 CSV 对照模式暂不提供这两类资料, 不自动混用数据库.

默认演示华夏成长 `000001.OF`, 日期为 2020-01-01 至 2025-12-31. 指标包括夏普, 卡玛, 索提诺, 年化波动, 年化收益, 年化下行风险和最大回撤. 全部基于交易日复权净值, 不随曲线勾选变化. 页面标注实际指标区间, 样本数及年化参数, 短于一年时显示提示. 缺少日历或有效净值时保留曲线并说明指标不可计算的原因.

曲线使用原始净值, 不做归一化或收益率计算. 曲线不跨越已有行中的缺失值连接. 未提供记录的日期不会自动补行. 本地数据不存在时显示提示, 不请求 Tushare.

## 数据与分层

- `bootstrap.py`: 选择文件并组装 Repository 和 Service.
- `streamlit_app.py`: 用户输入, 调用 Service 和 Plotly 绘图.
- `FundService`: 规范代码, 校验输入, 整理经理和最新资产资料, 选择净值列, 调用 `fund_metrics.py` 计算指标.
- `DuckDBFundRepository`: 查询净值, 日历, 经理任职记录及资产历史, 处理公告版本.
- `CsvFundRepository`: 读取 CSV, 日期筛选和公告版本处理.

默认查询 `data/fund_database.duckdb`, 基金名称, 净值和日历均来自数据库. 首次使用先执行 `python -m scripts.init_database` 建空库, 再添加基金并更新数据; 也可使用 `--snapshot` 导入已有单基金快照. 只有空库时页面没有可展示的基金数据. 具体命令见 [数据库说明](../database/README.md). 可通过 `DATABASE_PATH` 环境变量指定其他数据库文件, 相对路径以项目根目录为基准.

显式设置 `NAV_CSV` 会优先切换至 CSV 模式, 例如切回原有 10 基金曲线:

```powershell
$env:NAV_CSV = "data/exploration/20260921_095101/longest_lived/history/20260921_102854/nav.csv"
```

以下名称和日历文件配置仅用于 CSV 模式: 相对路径以项目根目录为基准, 也可以使用绝对路径. 名称优先读取净值同目录的 `fund_basic.csv`, 旧目录兼容 `longest_lived/top_10.csv`, 或通过 `FUND_NAMES_CSV` 指定. 下拉框只列出当前数据源有净值的基金, 选项随数据库更新变化.

日历默认读取净值同目录的 `trade_cal.csv`, 或通过 `CALENDAR_CSV` 指定含 `exchange,cal_date,is_open` 的完整 SSE 日历. 未配置日历时仍可看曲线. 目前交易日口径仅在华夏成长上核查, 不应直接用于 QDII 等特殊基金. 已设置的环境变量优先于默认配置, 恢复默认可在终端执行 `Remove-Item Env:NAV_CSV, Env:FUND_NAMES_CSV, Env:CALENDAR_CSV -ErrorAction SilentlyContinue`.

每轮页面运行创建一个服务实例, 供选择列表与查询复用, 当前样本规模暂不使用缓存. 指标计算才读取日历, 点击查询时读取经理和资产. 查询结果保存在当前浏览器会话, 修改控件不会重复获取这些资料, 更新数据库后需重新点击查询. 读取失败会显示提示并在运行终端记录错误.

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_streamlit_app.py -q -k "query_draws_selected_series_without_table or metrics_do_not_depend_on_visible_nav_types"
```

以上现有用例验证勾选, 日期筛选和指标口径. 本次另用临时 CSV 的 AppTest 交互验证固定图表位置, 初始空图, 结果保留和查询失败时保留空图. 按当前开发安排暂未调整测试文件, 旧测试中"空选择时没有图表"的断言需要后续同步为"保留空图".
