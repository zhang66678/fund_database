# 公募基金本地数据库与可视化系统

基于 Tushare Pro 的基金数据研究项目。第一阶段面向 Windows 本地单机使用，逐步实现历史数据存储、增量更新、查询分析与可视化。

## 当前状态

已完成工作区检查与基础准备，尚未实现配置加载、API 客户端、数据库、ETL 或页面。

按当前开发安排，暂缓正式 Phase 1，先进行[场外基金数据探索](research/README.md)。探索目录已有独立的凭证加载与请求代码，目前只完成基金清单获取；不属于正式分层模块。

- 开发环境：Windows、VS Code、Python 虚拟环境 `.venv`。
- 当前环境 Python 版本：3.14.3。后续新增依赖时逐项验证兼容性。
- `requirements.txt` 记录当前阶段直接依赖及已安装版本，不是完整的传递依赖锁文件。
- 已安装第一阶段依赖：Tushare、Pandas、DuckDB、python-dotenv 和 pytest，并验证基本导入、配置解析与内存数据库查询。
- Streamlit、Plotly 等依赖在后续对应阶段引入。

## 本地环境

在项目根目录的 PowerShell 中，可直接使用虚拟环境解释器，无需依赖终端是否已激活：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip check
```

在新电脑上准备环境时，先创建虚拟环境并安装当前阶段依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

项目根目录的 `.env` 用于保存 `TUSHARE_TOKEN`。已有文件应保留；不要将真实 Token 写入代码、日志、截图或 Git。当前尚未实现 `.env` 自动加载，后续统一在配置模块中处理。

## 分层约定

数据流：Tushare Pro → API → ETL → DuckDB → Service → Streamlit + Plotly。

| 模块（按需创建） | 职责 |
| --- | --- |
| `config/` | 集中配置、环境变量加载和日志配置 |
| `api/` | 数据请求、限频、重试与请求异常处理 |
| `etl/` | 提取、清洗、质量检查和调用数据库层加载数据 |
| `database/` | 连接、schema、参数化查询、事务与 UPSERT |
| `services/` | 查询用例、收益和风险指标、图表数据整理 |
| `app/` | 用户交互、页面布局与图表展示 |
| `scripts/` | 历史初始化与日常更新入口 |
| `tests/` | 关键行为和金融指标验证 |

页面通过服务层使用数据，不直接调用 Tushare 或操作数据库。服务层避免依赖 DuckDB 专有实现；未来迁移 PostgreSQL 时，尽量将变化限制在数据库层。当前不引入服务器框架或多人部署组件。

## 数据维护原则

- 保留当前与历史基金，基金终止只改变状态，不删除历史数据。
- 先调研接口，再确定业务表、主键和字段类型。
- 使用主键或唯一约束配合 UPSERT，保证重复运行不会产生重复记录。
- 历史回填需逐步支持失败重试、断点续跑和独立任务状态。
- 增量更新使用可配置的滚动窗口，同时考虑新基金与状态变化。窗口外历史修订需要后续补查机制，不能假设短窗口覆盖所有修订。
- 数据、日志与密钥留在本地，代码和文档进入版本管理。

## 开发顺序

1. Phase 1：配置、日志、Tushare 客户端和 DuckDB 连接；先验证最小基础能力。
2. Phase 2：调研基金接口的字段、历史覆盖、权限、频率和返回限制。
3. Phase 3：少量核心数据的获取、清洗与入库 MVP。
4. Phase 4–5：历史回填与增量更新。
5. Phase 6–7：业务指标与可视化。
6. Phase 8：持续完善测试、文档、错误处理和性能。

每个阶段先检查现状、说明修改范围，完成后验证并等待确认，再进入下一阶段。重要测试随功能加入，不集中拖到最后。

## Git 与本地文件

`.gitignore` 已覆盖虚拟环境、真实环境配置、Python 缓存、数据库、数据目录、日志和编辑器设置。忽略规则不会自动移除已经被 Git 跟踪的文件。

本地 Git 仓库已初始化，默认分支为 `main`，尚未创建首次提交或配置远程仓库。安装 Git 后，如果现有终端仍无法识别 `git`，请重新启动 VS Code，使终端获取新的 PATH。可在项目根目录检查状态：

```powershell
git --version
git check-ignore .env .venv/ .vscode/ data/ logs/
git status --short --untracked-files=all
```

确认敏感文件与本地数据没有出现在待提交列表后，再明确添加代码和文档。不要提交 `.env`、数据库文件或日志。
