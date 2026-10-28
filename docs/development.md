# 开发约定

## 环境

- Windows + PowerShell。
- Python 3.14，项目内使用 `.venv`。
- 依赖见 `requirements.txt`。
- 命令统一使用 `.\.venv\Scripts\python.exe`，不依赖终端是否已激活虚拟环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

## 测试

```powershell
# 全部测试
.\.venv\Scripts\python.exe -m pytest -q

# 按模块
.\.venv\Scripts\python.exe -m pytest tests/test_database_import.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_update_funds.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_fund_metrics.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_streamlit_app.py -q
```

测试覆盖重点：

| 文件 | 行为 |
| --- | --- |
| `test_database_import.py` | 幂等导入、公告版本、Decimal 精度、事务回滚 |
| `test_update_funds.py` | 数据库驱动更新、失败隔离、异常行、批次范围 |
| `test_add_funds.py` | 批量排重、失败重跑、从名单生成批次 |
| `test_fund_list.py` | 名单分页与快照输出 |
| `test_ifind.py` | iFinD HTTP 缓存、字段适配和 Repository 契约 |
| `test_fund_service.py` | 代码规范化、区间查询、空结果、对比与回撤 |
| `test_fund_metrics.py` | 七项指标手算、零风险、缺口处理 |
| `test_streamlit_app.py` | 查询、筛选、会话状态、对比页、错误反馈 |
| `test_csv_repository.py` | CSV 查询契约与版本选择 |

原则：

- 一个测试只验证一个行为；重复场景用参数化合并。
- 测试行为边界和回归点，不为实现细节写测试。
- 页面测试使用临时 DuckDB，不依赖真实数据或网络。
- 删除功能时同步删除对应测试，不保留“以后可能用”的测试。

## 代码约定

- 函数和变量使用清晰的英文命名。
- 中文文档字符串和关键逻辑注释，解释职责、参数、原因和边界。
- 注释和文档字符串使用英文半角标点。
- 类型提示用在接口和容易出错的地方，不为显而易见的局部变量补类型。
- 一个函数只做一件事；接近 60 行时先检查职责，不要直接拆。
- 一个文件接近 300 行时，优先按职责拆分，而不是按行数机械拆分。
- 错误信息应可读、可定位，不把凭证或原始请求写进日志。

## 文档约定

- 根 `README.md` 只保留项目定位、快速开始、架构图、目录和索引。
- 跨模块主题放在 `docs/`：架构、数据流程、指标、数据源、开发约定。
- 各代码目录的 README 只讲本目录，不重复总览和全局口径。
- 指标公式只在 [指标与对比](metrics.md) 维护；命令只在 [数据流程](etl.md) 维护；接口字段只在 [数据源](data-sources.md) 维护。
- 历史报告保留原日期和结论，不随代码更新改写；需要时在开头注明当前口径所在文档。

## 维护原则

1. **依赖单向**：`app -> services -> database`，`etl -> api/database`，不出现反向依赖。
2. **单一事实来源**：同一规则只写一处，其他文档链接过去。
3. **够用即可**：不为一个用例引入框架、基类或抽象层。
4. **重复第三次再抽象**：涉及口径的重复（代码格式、日历、净值版本）第一次就统一。
5. **优先删除和合并**：能删的遗留模式、能合并的测试，不保留维护负担。
6. **先确认再扩展**：新增数据源、页面或指标前，先确定归属层和最小实现。

## 待收敛项

以下问题不影响当前功能，但会持续增加维护成本，适合在后续迭代中逐步处理：

- `ROOT` 和日志初始化在多处重复；可收敛为一个 `config.py`。
- 基金代码规范化有 4 份实现；建议统一为一个函数。
- `etl/import_snapshot.py` 中的 `normalize` 被添加和更新流程共用，建议移到 `etl/normalize.py`。
- `services/fund_service.py` 与 `services/fund_metrics.py` 各有一份日历校验逻辑。
- `etl/update_funds.py` 的 `update_funds` 约 126 行，可拆出日历更新和基础资料/净值更新。
- `app/components.py` 约 390 行，可按筛选、图表、资料拆成少数几个模块。
- CSV 对照模式是否长期保留需要明确决定；删除后可减少一个 Repository、一组环境变量和一批测试。

## Git

- 不提交 `.env`、`.venv/`、`data/`、`logs/`、数据库文件。
- 提交前检查：

```powershell
git status --short --untracked-files=all
git check-ignore .env .venv/ data/ logs/
```

- 代码、文档和测试可以一起提交；数据更新和页面重启不进入版本管理。
