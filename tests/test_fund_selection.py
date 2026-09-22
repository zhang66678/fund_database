"""使用合成清单验证存续时长计算和快照选择, 不使用网络或真实凭证."""

from datetime import date

import pandas as pd
import pytest

from research.select_longest_funds import latest_snapshot, rank_funds


def fund(code, status='L', found='20000101', due=None, delist=None, market='O'):
    """构造一条最小基金记录, 供边界测试使用."""
    return dict(ts_code=code, name=code, status=status, found_date=found,
                due_date=due, delist_date=delist, market=market)


def test_ranking_includes_terminated_and_uses_snapshot_date():
    frame = pd.DataFrame([
        fund('active', found='20150101', due='20160101'),
        fund('ended', status='D', found='20000101', due='20200101'),
        fund('delisted', status='D', found='20000101', due='20290101', delist='20100101'),
    ])
    ranked, excluded = rank_funds(frame, date(2021, 1, 1))
    assert ranked.ts_code.tolist() == ['ended', 'delisted', 'active']
    assert ranked.age_days.tolist() == [7305, 3653, 2192]
    assert ranked.end_date_source.tolist() == ['due_date_proxy', 'delist_date', 'snapshot_as_of']
    assert ranked.uses_due_date_proxy.tolist() == [True, False, False]
    assert excluded.empty


@pytest.mark.parametrize('record,reason', [
    (fund('x', 'D'), 'missing_or_invalid_end_date'),
    (fund('x', 'D', due='20100101', delist='bad'), 'missing_or_invalid_end_date'),
    (fund('x', 'D', due='19991231'), 'end_before_found_date'),
    (fund('x', 'D', due='20300101'), 'end_after_snapshot'),
    (fund('x', found='20000230'), 'invalid_found_date'),
    (fund('x', found='2000011'), 'invalid_found_date'),
    (fund('x', found='20300101'), 'found_after_snapshot'),
    (fund('x', status='I'), 'not_active_or_terminated'),
    (fund('x', market='E'), 'not_otc'),
])
def test_invalid_records_are_reported(record, reason):
    ranked, excluded = rank_funds(pd.DataFrame([record]), date(2021, 1, 1))
    assert ranked.empty
    assert excluded.exclusion_reason.tolist() == [reason]


def test_ties_have_stable_code_order_and_do_not_mutate_input():
    frame = pd.DataFrame([fund('B'), fund('A')])
    before = frame.copy(deep=True)
    ranked, _ = rank_funds(frame, date(2021, 1, 1))
    assert ranked.ts_code.tolist() == ['A', 'B']
    pd.testing.assert_frame_equal(frame, before)


def test_duplicate_codes_fail_instead_of_silent_deduplication():
    with pytest.raises(ValueError):
        rank_funds(pd.DataFrame([fund('A'), fund('A')]), date(2021, 1, 1))


def test_latest_complete_snapshot_skips_newer_incomplete_run(tmp_path):
    for name in ['20260916_154011', '20260921_094931', '20260921_095101']:
        folder = tmp_path / name
        folder.mkdir()
        (folder / 'fund_basic_all.csv').touch()
        if name != '20260921_095101':
            (folder / 'list_summary.json').touch()
    assert latest_snapshot(tmp_path).name == '20260921_094931'
    with pytest.raises(ValueError):
        latest_snapshot(tmp_path / '20260921_095101')
