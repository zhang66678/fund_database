"""单基金 MVP 的四张表, 仅使用常规关系型字段和主键."""


TABLES = {
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
}


def initialize(connection) -> None:
    """创建缺失的表, 不删除或自动重建已有数据. 标识符仅来自本模块常量."""
    for table, spec in TABLES.items():
        columns = ", ".join(f'"{name}" {kind}' for name, kind in spec["columns"].items())
        key = ", ".join(f'"{name}"' for name in spec["key"])
        check = ', CHECK (is_open IN (0, 1))' if table == "trade_calendar" else ""
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({columns}, PRIMARY KEY ({key}){check})')
