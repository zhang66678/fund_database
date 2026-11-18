"""优先复查 Tushare, 必要时用 AKShare 生成候选修正, 审核后显式入库."""

import argparse
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import importlib.util
from io import StringIO
from pathlib import Path
import subprocess
import sys

import duckdb
import pandas as pd

from api.fund_fields import NAV_FIELDS
from api.tushare_client import FetchError, Source
from database.connection import DATABASE_PATH, ROOT, connect
from database.nav_corrections import current_nav, save_corrections, validate_series
from etl.add_funds import _normalize_code
from etl.batch_nav_repair import repair_review
from etl.extract import fetch_series
from etl.import_snapshot import normalize
from etl.nav_reconcile import rebuild_nav


def _price(value):
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0 or number >= 10**14:
        raise ValueError(f"无效净值: {value}")
    rounded = number.quantize(Decimal("0.0000000001"))
    if not 0 < rounded < 10**14:
        raise ValueError(f"净值超出入库精度: {value}")
    return rounded


def _series(frame, code):
    unit = "unit_nav" if "unit_nav" in frame else "unit_nav_akshare"
    if missing := {"nav_date", "reference_adj_nav", unit} - set(frame):
        raise ValueError(f"修正文件缺少 {', '.join(sorted(missing))}, 请提供完整净值序列")
    if "ts_code" in frame and not frame.ts_code.map(_normalize_code).eq(code).all():
        raise ValueError("修正序列和指定基金代码不一致")
    rows = {}
    for nav_date, reference_unit, adjusted in frame[["nav_date", unit, "reference_adj_nav"]].itertuples(index=False, name=None):
        if reference_unit == adjusted == "":
            continue
        day = date.fromisoformat(str(nav_date))
        if day in rows:
            raise ValueError(f"{day} 有重复修正数据")
        rows[day] = (_price(reference_unit), _price(adjusted))
    if not rows:
        raise ValueError("修正序列为空")
    sources = frame.source.unique() if "source" in frame else ["reviewed_csv"]
    if len(sources) != 1 or not sources[0].strip():
        raise ValueError("修正序列必须采用同一来源和计算口径")
    return rows, sources[0]


def _tushare_candidate(raw, base, code):
    frame, _ = normalize("fund_nav", raw)
    frame = frame.sort_values(["nav_date", "ann_date"]).drop_duplicates("nav_date", keep="last")
    frame = frame[["nav_date", "unit_nav", "accum_nav", "adj_nav"]].astype(object).where(lambda f: f.notna(), None)
    latest = list(frame.itertuples(index=False, name=None))
    for _, unit, _, adj in latest:
        if unit is None or unit <= 0 or adj is None or adj <= 0:
            raise ValueError("Tushare 最新序列仍有无效净值")
    reviewed = {day: (unit, adj) for day, unit, _, adj in latest}
    validate_series(base, reviewed)
    if not any(day in reviewed and reviewed[day][1] != adj for day, _, _, adj, _ in base):
        raise ValueError("Tushare 未返回复权历史修订")
    for (_, previous_unit, previous_accum, previous_adj), (day, unit, accum, adj) in zip(latest, latest[1:]):
        adj_ratio, unit_ratio = adj / previous_adj, unit / previous_unit
        if abs(adj_ratio - unit_ratio) < Decimal("0.02"):
            continue
        if accum is not None and previous_accum is not None:
            cash = (accum - unit) - (previous_accum - previous_unit)
            if 0 < cash < previous_unit and min(abs(adj_ratio - unit / (previous_unit - cash)),
                                               abs(adj_ratio - (unit + cash) / previous_unit)) <= Decimal("0.0005"):
                continue
        raise ValueError(f"{day} 仍有无法解释的复权涨幅")
    return frame.assign(ts_code=code, reference_adj_nav=frame.adj_nav, source="tushare_revised")[[
        "ts_code", "nav_date", "unit_nav", "reference_adj_nav", "source"]]


def _akshare_candidate(python, code, start, end):
    if python is None and importlib.util.find_spec("akshare") is not None:
        import akshare

        akshare.set_timeout((10, 30))
        return rebuild_nav(akshare, code, start, end)
    python = (python or ROOT.parent / "akshare/.venv/Scripts/python.exe").resolve()
    if not python.is_file():
        raise ValueError("请安装 AKShare, 或用 --akshare-python 指定已安装的 Python")
    process = subprocess.run([str(python), "-X", "utf8", "-m", "etl.nav_reconcile", code, str(start), str(end)],
                             cwd=ROOT, capture_output=True, encoding="utf-8", timeout=180)
    if process.returncode:
        details = process.stderr.strip().splitlines()
        raise ValueError(f"AKShare 重建失败: {details[-1] if details else process.returncode}")
    return pd.read_csv(StringIO(process.stdout), dtype=str, keep_default_na=False)


def _prepare(database, code, end, python):
    with connect(database, read_only=True) as db:
        fund = db.execute("SELECT found_date, fund_type FROM fund_basic WHERE ts_code = ?", [code]).fetchone()
        base = current_nav(db, code)
    if not fund or fund[0] is None or end < base[-1][0]:
        raise ValueError("需要成立日期, 且截止日须覆盖本地最新净值")
    start, fund_type = fund
    if not fund_type or "货币" in fund_type:
        raise ValueError("货币基金或分类缺失需要单独核对, 不自动重建")
    try:
        raw = fetch_series(Source(), "fund_nav", NAV_FIELDS, code, start, end)
        return _tushare_candidate(raw, base, code)
    except (FetchError, ValueError, InvalidOperation) as exc:
        print(f"Tushare 复查未通过, 转向 AKShare: {exc}", flush=True)
    frame = _akshare_candidate(python, code, start, end)
    validate_series(base, _series(frame, code)[0])
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--code", help="单只基金代码")
    scope.add_argument("--review", type=Path, help="直接使用已有的 AKShare 复核结果修正跳变")
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--file", type=Path, help="已人工核对的完整修正 CSV")
    parser.add_argument("--apply", action="store_true", help="备份原值并写入 fund_nav")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--akshare-python", type=Path)
    parser.add_argument("--output", type=Path, help="取数阶段输出候选 CSV")
    args = parser.parse_args()
    if args.file and not args.code:
        parser.error("--file 需要搭配 --code")
    if args.apply and not (args.file or args.review):
        parser.error("--apply 需要 --review 或 --file")
    try:
        if not args.database.is_file():
            raise ValueError("指定数据库不存在")
        if args.review:
            count, funds, backup, skipped = repair_review(args.database, args.review, apply=args.apply)
            action = "已写入原表" if args.apply else "待写入原表"
            print(f"{action} {funds} 只基金, {count} 条复权净值")
            if skipped:
                print(f"暂缓 {len(skipped)} 只基金, 前 5 项: {list(skipped.items())[:5]}")
            if backup:
                print(f"原值备份 {backup}")
            return 0
        code = _normalize_code(args.code)
        if args.file:
            frame = pd.read_csv(args.file, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            rows, source = _series(frame, code)
            result = save_corrections(args.database, code, rows, source=source, source_file=str(args.file.resolve()),
                                      apply=args.apply)
            action = "已写入原表" if args.apply else "预览"
            print(f"{code} {action}, 匹配原表 {result['rows']} 条, 修正 {result['changes']} 条, "
                  f"仅参考源有净值 {result['reference_only_dates']} 日, "
                  f"缺参考值的休市日 {result['closed_dates_without_correction']} 日")
            if result["backup"]:
                print(f"原值备份 {result['backup']}")
        else:
            path = args.output or ROOT / "data/nav_audit/repairs" / f"repair_{code}_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
            if path.exists():
                raise ValueError(f"候选文件已存在: {path}")
            frame = _prepare(args.database, code, args.end_date, args.akshare_python)
            rows, source = _series(frame, code)
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False, encoding="utf-8-sig", mode="x", float_format="%.10f")
            print(f"{code} 候选来源 {source}, {len(rows)} 条待审核净值, {path}")
        return 0
    except (ValueError, InvalidOperation, OSError, duckdb.Error, subprocess.TimeoutExpired) as exc:
        print(f"修正失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
