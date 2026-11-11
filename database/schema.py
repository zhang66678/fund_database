"""基金数据库表结构, 仅使用常规关系型字段和主键."""


TABLES = {
    "index_daily": {
        "columns": {"ts_code": "VARCHAR", "trade_date": "DATE", "close": "DECIMAL(24,10)"},
        "key": ("ts_code", "trade_date"),
    },
    "fund_category": {
        "columns": {"ts_code": "VARCHAR", "source": "VARCHAR", "snapshot_date": "DATE",
                    "category": "VARCHAR", "class_level1": "VARCHAR",
                    "class_level2": "VARCHAR", "class_level3": "VARCHAR"},
        "key": ("ts_code", "source", "snapshot_date"),
    },
    "fund_basic": {
        "columns": {
            **dict.fromkeys("ts_code name management custodian fund_type".split(), "VARCHAR"),
            **dict.fromkeys("found_date due_date list_date issue_date delist_date".split(), "DATE"),
            **dict.fromkeys("issue_amount m_fee c_fee duration_year p_value min_amount exp_return".split(), "DECIMAL(24,10)"),
            **dict.fromkeys("benchmark status invest_type type trustee".split(), "VARCHAR"),
            "purc_startdate": "DATE", "redm_startdate": "DATE", "market": "VARCHAR",
        },
        "key": ("ts_code",),
    },
    "fund_nav": {
        "columns": {"ts_code": "VARCHAR", "ann_date": "DATE", "nav_date": "DATE",
                    **dict.fromkeys("unit_nav accum_nav adj_nav accum_div".split(), "DECIMAL(24,10)")},
        "key": ("ts_code", "nav_date", "ann_date"),
    },
    "fund_dividend": {
        "columns": {"ts_code": "VARCHAR", "ann_date": "DATE", "div_proc": "VARCHAR",
                    "record_date": "DATE", "ex_date": "DATE", "pay_date": "DATE",
                    "div_cash": "DECIMAL(24,10)", "net_ex_date": "DATE", "account_date": "DATE"},
        "key": ("ts_code", "ann_date", "ex_date", "div_proc"),
    },
    "trade_calendar": {
        "columns": {"exchange": "VARCHAR", "cal_date": "DATE", "is_open": "INTEGER"},
        "key": ("exchange", "cal_date"),
    },
    "fund_manager": {
        "columns": {"ts_code": "VARCHAR", "ann_date": "DATE", "name": "VARCHAR",
                    "begin_date": "DATE", "end_date": "DATE", "edu": "VARCHAR", "resume": "VARCHAR"},
        # 姓名不是永久人员 ID, 保留不同公告及重复任职的记录.
        "key": ("ts_code", "name", "begin_date", "ann_date"),
    },
    "fund_asset": {
        "columns": {"ts_code": "VARCHAR", "ann_date": "DATE", "nav_date": "DATE",
                    "net_asset": "DECIMAL(24,10)", "total_netasset": "DECIMAL(24,10)"},
        "key": ("ts_code", "nav_date", "ann_date"),
    },
}


IFIND_TABLES = {
    "fund_nav": {
        "columns": {"ts_code": "VARCHAR", "nav_date": "DATE",
                    **dict.fromkeys("unit_nav accum_nav adj_nav".split(), "DECIMAL(24,10)"),
                    "fetched_at": "TIMESTAMP"},
        "key": ("ts_code", "nav_date"),
    },
}


def _create_tables(connection, tables) -> None:
    for table, spec in tables.items():
        columns = ", ".join(f'"{name}" {kind}' for name, kind in spec["columns"].items())
        key = ", ".join(f'"{name}"' for name in spec["key"])
        check = ', CHECK (is_open IN (0, 1))' if table == "trade_calendar" else ""
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({columns}, PRIMARY KEY ({key}){check})')


def initialize(connection) -> None:
    """创建缺失的表, 不删除或自动重建已有数据. 标识符仅来自本模块常量."""
    _create_tables(connection, TABLES)


def initialize_ifind(connection) -> None:
    """iFinD 独立库只包含净值表, 遇到其他结构时拒绝混写."""
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    if tables - IFIND_TABLES.keys():
        raise ValueError("目标不是 iFinD 净值专用库, 已停止写入")
    if "fund_nav" in tables:
        description = connection.execute("DESCRIBE fund_nav").fetchall()
        columns = {row[0]: row[1] for row in description}
        expected = IFIND_TABLES["fund_nav"]["columns"]
        keys = {row[0] for row in description if row[3] == "PRI"}
        if columns != expected or keys != set(IFIND_TABLES["fund_nav"]["key"]):
            raise ValueError("目标净值表结构不匹配, 已停止写入")
    _create_tables(connection, IFIND_TABLES)
