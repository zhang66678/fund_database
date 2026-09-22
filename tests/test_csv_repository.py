"""使用临时 CSV 验证查询契约, 不依赖真实数据和网络."""

from datetime import date

import pandas as pd
import pytest

from database.csv_repository import CsvFundRepository
from database.repository import NAV_COLUMNS


@pytest.fixture
def nav_path(tmp_path):
    path = tmp_path / "nav.csv"
    # 故意乱序并包含同日不同公告, 不需要基金名单或分红字段.
    pd.DataFrame([
        ["000001.OF", "20200104", "20200102", 1.2, 1.4, None],
        ["000002.OF", None, "20190101", 1.0, 1.0, 1.0],
        ["000001.OF", "20200102", "20200101", 1.0, 1.2, 1.3],
        ["000001.OF", "20200103", "20200102", 1.1, 1.3, 1.4],
        ["000001.OF", None, "20200102", 0.9, 1.1, 1.2],
    ], columns=["ts_code", "ann_date", *NAV_COLUMNS]).to_csv(path, index=False)
    return path


def test_range_latest_version_and_nulls(nav_path):
    repo = CsvFundRepository(nav_path)
    result = repo.get_nav("000001.OF", date(2020, 1, 2), date(2020, 1, 2))
    assert result.unit_nav.tolist() == [1.2]
    assert result.adj_nav.isna().all()
    assert result.columns.tolist() == NAV_COLUMNS
    assert result.unit_nav.dtype == "float64"
    assert pd.api.types.is_datetime64_any_dtype(result.nav_date)
    assert len(repo.get_nav("000001.OF", end_date=date(2020, 1, 1))) == 1
    assert repo.get_nav("000002.OF").unit_nav.tolist() == [1.0]
    assert repo.get_nav("999999.OF").empty
    empty = repo.get_nav("000001.OF", start_date=date(2030, 1, 1))
    assert empty.empty and empty.dtypes.equals(result.dtypes)


def test_query_cannot_modify_loaded_data(nav_path):
    repo = CsvFundRepository(nav_path)
    result = repo.get_nav("000001.OF")
    result.loc[0, "unit_nav"] = 999
    assert repo.get_nav("000001.OF").unit_nav.iloc[0] == 1.0
    with pytest.raises(ValueError, match="start_date"):
        repo.get_nav("000001.OF", date(2020, 2, 1), date(2020, 1, 1))


@pytest.mark.parametrize("column,value", [
    ("nav_date", "20200230"), ("nav_date", None),
    ("ann_date", "2020011"), ("unit_nav", "bad"),
    ("ts_code", None),
])
def test_invalid_nav_is_rejected(nav_path, column, value):
    frame = pd.read_csv(nav_path, dtype="string")
    frame.loc[0, column] = value
    frame.to_csv(nav_path, index=False)
    with pytest.raises(ValueError):
        CsvFundRepository(nav_path)


@pytest.mark.parametrize("announcement", ["20200104", None])
def test_conflicting_latest_values_are_rejected(nav_path, announcement):
    frame = pd.read_csv(nav_path, dtype="string").iloc[:1].copy()
    frame["ann_date"] = announcement
    duplicate = frame.copy()
    duplicate["unit_nav"] = "9.0"
    pd.concat([frame, duplicate]).to_csv(nav_path, index=False)
    with pytest.raises(ValueError, match="Conflicting NAV"):
        CsvFundRepository(nav_path).get_nav("000001.OF")


def test_identical_rows_are_collapsed(nav_path):
    frame = pd.read_csv(nav_path, dtype="string")
    pd.concat([frame, frame.iloc[:1]]).to_csv(nav_path, index=False)
    assert len(CsvFundRepository(nav_path).get_nav("000001.OF")) == 2


def test_missing_columns_are_rejected(nav_path):
    frame = pd.read_csv(nav_path).drop(columns="adj_nav")
    frame.to_csv(nav_path, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        CsvFundRepository(nav_path)


def test_header_only_nav_is_supported(nav_path):
    pd.DataFrame(columns=["ts_code", "ann_date", *NAV_COLUMNS]).to_csv(nav_path, index=False)
    repo = CsvFundRepository(nav_path)
    assert repo.get_nav("000001.OF").empty
