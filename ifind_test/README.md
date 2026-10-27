# iFinD 调用示例

本目录验证 iFinD（同花顺）公募基金取数能力，不属于正式数据流程。脚本 `fund_api_example.py` 分别使用 Python SDK 和 HTTP 查询基金资料、日期序列净值、历史净值和公告。

## 准备

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install iFinDAPI requests python-dotenv pandas
.\.venv\Scripts\python.exe -c "import iFinDPy; print('SDK import OK')"
```

在项目根目录 `.env` 中配置：

```dotenv
IFIND_USERNAME=接口账号
IFIND_PASSWORD=接口密码
IFIND_REFRESH_TOKEN=refresh_token
```

`refresh_token` 可从 iFinD 网页版账号详情或 SuperCommand 的“工具 → refresh_token 查询”获取。运行机器需要能访问 `quantapi.51ifind.com:443`。

## 运行

```powershell
.\.venv\Scripts\python.exe ifind_test/fund_api_example.py
```

默认运行 SDK。只运行 HTTP 时，把脚本末尾改为：

```python
if __name__ == "__main__":
    # test_sdk()
    test_http()
```

样本代码和日期在脚本顶部：

```python
CODE = "003349.OF"
START_DATE = "2026-09-21"
END_DATE = "2026-09-24"
REPORT_START_DATE = "2026-09-01"
```

## 接口

| 顺序 | HTTP 路径 | 用途 |
| --- | --- | --- |
| 1 | `get_access_token` | 用 `refresh_token` 换 `access_token` |
| 2 | `basic_data_service` | 基金简称、成立日期、运作形式 |
| 3 | `date_sequence` | 日期序列单位净值 |
| 4 | `cmd_history_quotation` | 历史净值 |
| 5 | `report_query` | 公告日期、代码、标题 |

SDK 使用 `THS_BD`、`THS_DS`、`THS_HQ`、`THS_ReportQuery`，登录和登出分别为 `THS_iFinDLogin`、`THS_iFinDLogout`。

## 字段

| 内容 | 指标名 |
| --- | --- |
| 基金简称 | `ths_fund_short_name_fund` |
| 成立日期 | `ths_fund_establishment_date_fund` |
| 运作形式 | `ths_fund_type_fund` |
| 日期序列单位净值 | `ths_unit_nv_fund` |
| 单位净值 | `netAssetValue` |
| 累计净值 | `accumulatedNAV` |
| 复权净值 | `adjustedNAV` |
| 公告日期 | `reportDate` |
| 公告代码 | `thscode` |
| 公告标题 | `reportTitle` |

`ths_fund_type_fund` 返回“契约型开放式”，不是股票型/混合型/债券型分类。

## 结果

2026-10-26 实测通过，SDK 和 HTTP 的四类接口均返回错误码 0，结果一致。详见 [TEST_RESULTS](TEST_RESULTS.md)。

正式接入前的口径和数据源选择见 [数据源](../docs/data-sources.md)。
