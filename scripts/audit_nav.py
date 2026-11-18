"""筛查 Tushare 历史净值, 或用 AKShare 复核及修正异常."""

import argparse
from datetime import date, datetime
from importlib.util import find_spec
from pathlib import Path
import re
import subprocess
import sys

import pandas as pd

from etl.update_scope import read_update_codes


def _codes(args):
    raw_codes = read_update_codes(args.codes, args.file)
    if raw_codes is None:
        return None
    codes = []
    for code in raw_codes:
        code = code.strip().upper().removesuffix(".OF")
        if not re.fullmatch(r"[0-9]{6}", code):
            raise ValueError(f"无效的场外基金代码: {code}")
        codes.append(code + ".OF")
    return list(dict.fromkeys(codes))


def _review(path, codes, start, end, *, workers=1, request_interval=0.11):
    from etl.nav_reconcile import review_nav

    candidates = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    required = {"ts_code", "fund_type", "nav_date", "prev_date", "unit_nav", "prev_unit_nav", "adj_move_pct"}
    if missing := required - set(candidates):
        raise ValueError(f"候选清单缺少列: {', '.join(sorted(missing))}")
    if codes is not None:
        candidates = candidates.loc[candidates.ts_code.isin(codes)]
    if start:
        candidates = candidates.loc[candidates.nav_date.ge(start.isoformat())]
    if end:
        candidates = candidates.loc[candidates.nav_date.ne("") & candidates.nav_date.le(end.isoformat())]
    if candidates.empty:
        raise ValueError("指定范围内没有候选记录")
    if not candidates.ts_code.str.fullmatch(r"[0-9]{6}\.OF").all():
        raise ValueError("候选清单包含无效的场外基金代码")
    try:
        import akshare
    except ModuleNotFoundError as exc:
        if exc.name != "akshare":
            raise
        raise ValueError("复核需要使用安装了 AKShare 的 Python 环境") from exc
    akshare.set_timeout((10, 30))

    def show_progress(completed, total):
        if completed % 100 == 0 or completed == total:
            print(f"已处理 {completed}/{total} 只基金", flush=True)

    print(f"开始复核 {candidates.ts_code.nunique()} 只基金, {workers} 并发, 指标请求间隔 {request_interval:g} 秒", flush=True)
    return review_nav(candidates, akshare, workers=workers, request_interval=request_interval, progress=show_progress)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="审核使用的 DuckDB, 默认 data/fund_database.duckdb")
    parser.add_argument("--review", type=Path, metavar="CSV", help="读取候选 CSV, 默认复核清单内全部基金")
    parser.add_argument("--apply", action="store_true", help="AKShare 复核后直接修正已确认的复权跳变")
    parser.add_argument("--split-events", type=Path, metavar="CSV", help="本地筛查使用的已核实份额折算清单")
    parser.add_argument("--workers", type=int, default=1, help="AKShare 复核并发数, 默认 1")
    parser.add_argument("--request-interval", type=float, default=0.11, help="AKShare 指标请求的全局启动间隔, 单位为秒")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--codes", nargs="+", help="指定场外基金代码")
    scope.add_argument("--file", type=Path, help="指定名单 CSV, 包含 ts_code 列")
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--gap-pp", type=float, default=2.0, help="两种涨幅差值阈值, 单位为百分点")
    parser.add_argument("--move-pct", type=float, default=20.0, help="单项涨幅绝对值阈值, 单位为百分比")
    parser.add_argument("--output", type=Path, help="输出 CSV, 已存在时不覆盖")
    args = parser.parse_args()
    if args.apply and not args.review:
        parser.error("--apply 需要 --review")
    if args.review and args.split_events:
        parser.error("--split-events 用于本地筛查")
    if args.start_date and args.end_date and args.start_date > args.end_date:
        parser.error("开始日期不能晚于结束日期")

    root = Path(__file__).resolve().parents[1]
    prefix = "akshare_review" if args.review else "tushare_candidates"
    folder = "reviews" if args.review else "scans"
    output = args.output or root / "data/nav_audit" / folder / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S_%f}.csv"
    try:
        if output.exists():
            raise ValueError(f"输出已存在: {output}")
        database = args.database or root / "data/fund_database.duckdb"
        if args.apply:
            venv_python = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
            writer_python = Path(sys.executable) if find_spec("duckdb") else root / ".venv" / venv_python
            if not writer_python.is_file() or not database.is_file():
                raise ValueError("请检查项目 .venv 和要写入的 DuckDB 文件")
        codes = _codes(args)
        if args.review:
            frame = _review(args.review, codes, args.start_date, args.end_date,
                            workers=args.workers, request_interval=args.request_interval)
            print("复核结果", frame.review_status.value_counts().to_dict())
            failed = frame.review_status.eq("akshare_failed").any()
        else:
            from etl.nav_quality import audit_nav

            frame, funds = audit_nav(database, codes=codes,
                                     start=args.start_date, end=args.end_date, gap_pp=args.gap_pp,
                                     move_pct=args.move_pct, split_events=args.split_events)
            print(f"有历史净值 {funds} 只, 筛查命中 {len(frame)} 条, 涉及 {frame.ts_code.nunique()} 只")
            print("命中原因", frame.reason.value_counts().to_dict())
            failed = False
        output.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(output, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d", mode="x",
                     float_format="%.10g")
        print(output, flush=True)
        if args.apply:
            updated = subprocess.run([str(writer_python), "-X", "utf8", "-m", "scripts.repair_nav",
                                      "--review", str(output.resolve()), "--database", str(database.resolve()), "--apply"],
                                     cwd=root)
            if updated.returncode:
                return updated.returncode
        return int(failed)
    except Exception as exc:
        print(f"净值审核失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
