# 可视化 demo 的数据访问层

当前实现查询约定和 CSV 读取. 后续调用方向为:

```text
Streamlit 页面 -> FundService -> FundRepository -> CSV (以后替换为 DuckDB)
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

初始化只接收 `nav.csv` 路径, 不依赖基金名单. 文件需有 `ts_code`, `ann_date` 和上述四列. 代码用于筛选, 公告日期仅用于内部版本选择, 其他列忽略. 无需分红或复权因子字段.

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
    "data/exploration/20260921_095101/longest_lived/history/20260921_102854/nav.csv"
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
