# iFinD 公募基金调用示例

示例脚本: [fund_api_example.py](fund_api_example.py). 分别使用 Python SDK 和 HTTP 查询公募基金资料, 日期序列净值, 历史净值及公告, 结果直接打印到终端. 以下步骤按 Windows PowerShell 环境说明.

## 前期准备

### 1. 准备示例目录和 Python 环境

将收到的 `ifind_test` 文件夹放到自己的工作目录中. 目录结构如下:

```text
工作目录/
├── .env                         # 后续创建, 保存账号配置
└── ifind_test/
    ├── README.md
    └── fund_api_example.py
```

在这个工作目录中打开 PowerShell 终端. **以下所有命令都在该目录执行.**

先安装 64 位 Python, 并确认终端可以识别 Python:

```powershell
python --version
```

创建本示例使用的虚拟环境 `.venv`; 已有可用的 `.venv` 时可以跳过创建:

```powershell
python -m venv .venv
```

以下命令都直接指定 `.venv` 中的解释器, 无需先激活虚拟环境. 如使用 VS Code 运行脚本, Python 解释器也选择该工作目录下的 `.venv\Scripts\python.exe`.

### 2. 安装 SDK 和其他依赖

安装 iFinD 官方 Python SDK:

```powershell
.\.venv\Scripts\python.exe -m pip install iFinDAPI
```

安装本示例使用的其他依赖:

```powershell
.\.venv\Scripts\python.exe -m pip install requests python-dotenv pandas
```

其中 `requests` 用于发送 HTTP 请求, `python-dotenv` 用于读取 `.env`, `pandas` 用于展示返回数据. 示例脚本同时导入 SDK 和 HTTP 依赖, 先安装上述全部包后再运行.

注意 SDK 的安装包名是 **`iFinDAPI`**, Python 导入名是 **`iFinDPy`**. [官方安装说明](https://pypi.org/project/iFinDAPI/)

### 3. 验证安装

执行以下命令检查 SDK 是否能正常加载:

```powershell
.\.venv\Scripts\python.exe -c "import iFinDPy; print('SDK import OK')"
```

出现 `SDK import OK` 表示导入成功. 如提示找不到 `iFinDPy`, 先确认安装和运行使用的是同一个 `.venv` 解释器.

### 4. 准备账号和 HTTP Token

SDK 使用 iFinD 数据接口账号和密码登录. HTTP 使用 `refresh_token` 换取 `access_token`, 再用 access_token 请求数据.

获取 refresh_token 可选择以下任一种方式:

- 登录 [网页版超级命令的账号详情](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/super-command-web/index.html#/AccountDetails), 查看并复制 refresh_token.（网页中显示为Refresh Token request）
- 下载官方 Windows 接口包并打开 `SuperCommand.exe`, 登录后进入 `工具 → refresh_token 查询` 获取.

示例会自动获取 access_token, 无需手工填写它. 已有有效 refresh_token 时直接使用即可; 更新 refresh_token 会使旧 Token 失效. [官方鉴权与部署说明](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/deploy.html)

### 5. 填写本地配置

在工作目录创建 `.env` 文件, 写入以下三项, 将占位文字替换为自己的真实值. 已有 `.env` 时追加这三项即可:

```dotenv
IFIND_USERNAME=接口账号
IFIND_PASSWORD=接口密码
IFIND_REFRESH_TOKEN=获取到的refresh_token
```

`.env` 位于 `ifind_test` 文件夹的上一层, 与 `.venv` 同级. 脚本会自动读取, 不需要把账号密码写入 Python 代码.

运行机器需要能通过 HTTPS 访问 `quantapi.51ifind.com:443`. 如果网络有限制, 可按官方部署说明配置域名白名单或代理.

## 运行示例

完成上述准备后, 在工作目录执行:

```powershell
.\.venv\Scripts\python.exe ifind_test/fund_api_example.py
```

示例默认运行 `test_sdk()`. 要单独运行 HTTP, 将脚本末尾改为:

```python
if __name__ == "__main__":
    # test_sdk()
    test_http()
```

两行调用都取消注释时, 会先运行 SDK, 再运行 HTTP.

## 样本与字段

修改脚本顶部的变量即可更换基金和区间:

```python
CODE = "003349.OF"           # 长信稳益纯债A
START_DATE = "2026-09-21"
END_DATE = "2026-09-24"
REPORT_START_DATE = "2026-09-01"  # 公告查询的开始日期
```

净值使用 `START_DATE` 至 `END_DATE`, 公告使用 `REPORT_START_DATE` 至 `END_DATE`. 公告区间稍长, 便于看到样本记录.

| 内容 | 指标名 |
| --- | --- |
| 基金简称 | `ths_fund_short_name_fund` |
| 成立日期 | `ths_fund_establishment_date_fund` |
| 运作形式 | `ths_fund_type_fund` |
| 日期序列单位净值 | `ths_unit_nv_fund` |
| 单位净值 | `netAssetValue` |
| 累计净值 | `accumulatedNAV` |
| 复权净值 | `adjustedNAV` |
| 公告日期, 基金代码, 标题 | `reportDate`, `thscode`, `reportTitle` |

`ths_fund_type_fund` 在样本中返回“契约型开放式”, 不表示股票型/混合型/债券型分类. 其他指标可在 [网页版超级命令](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/super-command-web/index.html) 中选择基金品种, 搜索中文指标名并生成调用命令.

## Python SDK

在 `test_sdk()` 中, 先用 `THS_iFinDLogin(账号, 密码)` 登录, 再调用以下四个接口, 最后用 `THS_iFinDLogout()` 登出.

```python
# 基金资料: 指标之间用分号分隔; 第三个参数对应三个指标的空参数.
basic = THS_BD(
    CODE, "ths_fund_short_name_fund;ths_fund_establishment_date_fund;ths_fund_type_fund", ";;"
)

# 日期序列: 演示用基础数据指标提取每日单位净值.
series = THS_DS(
    CODE, "ths_unit_nv_fund", "", "Interval:D,Days:Tradedays,Fill:Blank",
    START_DATE, END_DATE,
)

# 历史净值: 指标之间用逗号分隔, Interval:D 表示日频, Fill:Blank 表示不沿用前值.
nav = THS_HQ(
    CODE, "netAssetValue,accumulatedNAV,adjustedNAV",
    "Interval:D,Fill:Blank", START_DATE, END_DATE,
)

# 公告查询: 参数以分号分隔, 输出列使用 列名:Y 选择.
reports = THS_ReportQuery(
    CODE, f"beginrDate:{REPORT_START_DATE};endrDate:{END_DATE}",
    "reportDate:Y,thscode:Y,reportTitle:Y",
)
```

以上结果对象的 `.errorcode` 为 0 表示调用成功; `.data` 是 Pandas DataFrame. 脚本直接打印结果对象, 会同时显示错误码, 错误信息和数据.

## HTTP

`test_http()` 使用 `requests.post()` 调用接口. 主地址为 `https://quantapi.51ifind.com/api/v1/`.

| 顺序 | 路径 | 用途 |
| --- | --- | --- |
| 1 | `get_access_token` | 请求头传入 `refresh_token`, 获取 `data.access_token` |
| 2 | `basic_data_service` | 查询基金简称, 成立日期和运作形式 |
| 3 | `date_sequence` | 查询日期序列单位净值 |
| 4 | `cmd_history_quotation` | 查询历史净值 |
| 5 | `report_query` | 查询基金公告日期, 代码和标题 |

后续取数请求共用以下请求头, `access_token` 使用第一个请求的返回值:

```python
headers = {"Content-Type": "application/json", "access_token": access_token}
```

资料请求中, `codes` 是基金代码, `indipara` 列出指标及参数. 本例三个资料指标均使用 `indiparams: [""]`. 日期序列同样使用 `indipara`, 指标换为 `ths_unit_nv_fund`, 并通过 `startdate` 和 `enddate` 指定区间.

净值请求的 JSON 参数如下, 通过 `requests.post(..., json=参数)` 发送:

```python
{
    "codes": CODE,
    "indicators": "netAssetValue,accumulatedNAV,adjustedNAV",
    "startdate": START_DATE,
    "enddate": END_DATE,
    "functionpara": {"Interval": "D", "Fill": "Blank"},
}
```

响应的 `errorcode` 为 0 表示调用成功. 本例只有一只基金, 从 `tables[0]` 取结果: `table` 是各字段的数值列表, 净值响应的 `time` 是对应日期列表. 脚本将它们组合为 DataFrame 后打印.

公告请求使用 `beginrDate` 和 `endrDate` 指定区间, 日期格式为 `YYYY-MM-DD`; `outputpara` 选择要返回的列, 本例为 `reportDate:Y,thscode:Y,reportTitle:Y`. 查询结果可以为空, 示例不下载公告 PDF.

实际可获取的数据以使用者账号的接口权限为准. 接口参数可参考 [官方手册](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html).
