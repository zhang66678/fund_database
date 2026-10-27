"""组装 demo 服务, 路径和存储实现集中在这里."""

import os
from pathlib import Path

from database.csv_repository import CsvFundRepository
from database.connection import DATABASE_PATH, ROOT
from database.duckdb_repository import DuckDBFundRepository
from services.fund_service import FundService


def create_service(source: str = "tushare") -> FundService:
    """根据页面选择组装服务, iFinD 客户端在浏览器会话内复用."""
    if source == "ifind":
        import streamlit as st
        from api.ifind_client import IfindClient
        from database.ifind_repository import IfindFundRepository

        path = _database_path()
        cached = st.session_state.get("_ifind_service")
        if cached is None or cached[0] != str(path):
            service = FundService(IfindFundRepository(IfindClient(), DuckDBFundRepository(path)))
            st.session_state["_ifind_service"] = (str(path), service)
        return st.session_state["_ifind_service"][1]
    if source != "tushare":
        raise ValueError("未知数据源")
    nav_setting = os.environ.get("NAV_CSV")
    if not nav_setting:
        return FundService(DuckDBFundRepository(_database_path()))
    path = Path(nav_setting)
    if not path.is_absolute():
        path = ROOT / path
    # 单基金快照使用同目录资料, 旧 demo 仍支持 top_10.csv.
    names_setting = os.environ.get("FUND_NAMES_CSV")
    default_names = path.parent / "fund_basic.csv"
    if not default_names.is_file():
        default_names = path.parent.parent.parent / "top_10.csv"
    names_path = Path(names_setting) if names_setting else default_names
    if not names_path.is_absolute():
        names_path = ROOT / names_path
    calendar_setting = os.environ.get("CALENDAR_CSV")
    calendar_path = Path(calendar_setting) if calendar_setting else path.parent / "trade_cal.csv"
    if not calendar_path.is_absolute():
        calendar_path = ROOT / calendar_path
    return FundService(CsvFundRepository(
        path, names_path if names_setting or names_path.is_file() else None,
        calendar_path if calendar_setting or calendar_path.is_file() else None,
    ))


def _database_path() -> Path:
    path = Path(os.environ.get("DATABASE_PATH") or DATABASE_PATH)
    return path if path.is_absolute() else ROOT / path
