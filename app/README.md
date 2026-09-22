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
4. 点击查询, 查看净值曲线. 修改选项后再次点击查询, 页面不展示数据表.

曲线使用原始净值, 不做归一化或收益率计算. 曲线不跨越已有行中的缺失值连接. 未提供记录的日期不会自动补行. 本地数据不存在时显示提示, 不请求 Tushare.

## 数据与分层

- `bootstrap.py`: 选择文件并组装 Repository 和 Service.
- `streamlit_app.py`: 用户输入, 调用 Service 和 Plotly 绘图.
- `FundService`: 规范代码, 校验输入, 选择净值列.
- `CsvFundRepository`: 读取 CSV, 日期筛选和公告版本处理.

默认使用 `bootstrap.py` 中已验证的样本快照, 不自动切换到新下载的数据. 如需换文件, 在启动前设置环境变量:

```powershell
$env:NAV_CSV = "data/exploration/20260921_095101/longest_lived/history/20260921_102854/nav.csv"
```

相对路径以项目根目录为基准, 也可以使用绝对路径. 下拉框名称默认来自该历史目录对应的 `longest_lived/top_10.csv`, 只列出净值文件中实际存在的代码. 使用其他目录结构时, 可通过 `FUND_NAMES_CSV` 指定包含 `ts_code,name` 的名单文件. 未提供名单时显示代码和名称缺失提示, 手动查询仍可使用.

页面刷新时加载选择列表, 点击查询时重新读取净值, 当前样本规模暂不使用缓存. 读取失败会显示提示并在运行终端记录错误.

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_streamlit_app.py -q
```

测试使用临时 CSV, 验证勾选和日期筛选, 曲线生成且无表格, 完整日期范围, 空选择, 无数据及文件缺失提示.
