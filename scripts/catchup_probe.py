from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import mainline_scan as scanner


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
OUT = WORK / "catchup_probe.json"
METRICS_OUT = WORK / "catchup_metrics.csv"
CACHE = WORK / "catchup_cache"


def compound(period: int, pct: pd.Series) -> float:
    return (math.prod(1 + pct.tail(period) / 100) - 1) * 100


def main() -> None:
    parser = argparse.ArgumentParser(description="为当前主线与潜在主线生成动态补涨候选指标")
    parser.add_argument("--asof", default=None, help="交易日 YYYYMMDD")
    args = parser.parse_args()
    asof = args.asof or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    datetime.strptime(asof, "%Y%m%d")
    start = (datetime.strptime(asof, "%Y%m%d") - timedelta(days=120)).strftime("%Y%m%d")

    summaries = sorted((WORK / "mainline_out").glob(f"{asof}_*_summary.json"))
    if not summaries:
        raise SystemExit(f"找不到 {asof} 的主线扫描结果。")
    summary = json.loads(summaries[-1].read_text(encoding="utf-8"))
    stocks: dict[str, str] = {}
    for groups in summary.get("leaders", {}).values():
        for rows in groups.values():
            for stock in rows:
                stocks[stock["ts_code"]] = stock["name"]
    if not stocks:
        raise SystemExit("主线扫描没有可供评估的核心股。")

    client = scanner.TushareClient(scanner.load_token(), CACHE, pause=0.12)
    daily = {}
    for code, name in stocks.items():
        frame = client.call(
            "daily",
            {"ts_code": code, "start_date": start, "end_date": asof},
            scanner.STOCK_FIELDS,
        )
        if not frame.empty:
            frame = scanner.to_num(frame, ["close", "pre_close", "pct_chg", "amount"])
            frame = frame.sort_values("trade_date").reset_index(drop=True)
            if str(frame.iloc[-1]["trade_date"]) != asof:
                frame = frame.iloc[0:0]
        daily[code] = {"name": name, "rows": frame.to_dict("records")}

    basic = client.call(
        "daily_basic",
        {"trade_date": asof},
        "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv",
    )
    if not basic.empty:
        basic = scanner.to_num(
            basic,
            ["close", "turnover_rate", "volume_ratio", "pe", "pb", "total_mv", "circ_mv"],
        )
        basic = basic[basic["ts_code"].isin(stocks)]
    money = client.call(
        "moneyflow",
        {"trade_date": asof},
        (
            "ts_code,trade_date,buy_lg_amount,sell_lg_amount,buy_elg_amount,"
            "sell_elg_amount,net_mf_amount"
        ),
    )
    if not money.empty:
        money = scanner.to_num(
            money,
            ["buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount", "net_mf_amount"],
        )
        money = money[money["ts_code"].isin(stocks)]

    WORK.mkdir(parents=True, exist_ok=True)
    payload = {
        "asof": asof,
        "daily": daily,
        "daily_basic": basic.to_dict("records") if not basic.empty else [],
        "moneyflow": money.to_dict("records") if not money.empty else [],
        "failures": client.failures,
        "calls": client.calls,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    basic_map = basic.set_index("ts_code").to_dict("index") if not basic.empty else {}
    money_map = money.set_index("ts_code").to_dict("index") if not money.empty else {}
    metric_rows = []
    for code, info in daily.items():
        rows = info["rows"]
        if len(rows) < 21:
            continue
        frame = pd.DataFrame(rows)
        close = frame["close"].astype(float)
        pct = frame["pct_chg"].astype(float)
        amount = frame["amount"].astype(float)
        last = frame.iloc[-1]
        metric_rows.append(
            {
                "code": code,
                "name": info["name"],
                "today_pct": float(last["pct_chg"]),
                "ret5": compound(5, pct),
                "ret10": compound(10, pct),
                "ret15": compound(15, pct),
                "amount_ratio3": float(amount.tail(3).mean() / amount.tail(20).mean()),
                "above_ma10": float(last["close"]) >= float(close.tail(10).mean()),
                "distance_ma10": (float(last["close"]) / float(close.tail(10).mean()) - 1) * 100,
                "distance_ma20": (float(last["close"]) / float(close.tail(20).mean()) - 1) * 100,
                "distance_high20": (float(last["close"]) / float(close.tail(20).max()) - 1) * 100,
                "limit_up_days15": int((pct.tail(15) >= 9.7).sum()),
                "turnover_rate": basic_map.get(code, {}).get("turnover_rate"),
                "volume_ratio": basic_map.get(code, {}).get("volume_ratio"),
                "total_mv_yi": basic_map.get(code, {}).get("total_mv"),
                "net_mf_amount": money_map.get(code, {}).get("net_mf_amount"),
            }
        )
    if not metric_rows:
        raise SystemExit(f"{asof} 补涨候选行情尚未就绪，本次不发布。")

    metrics = pd.DataFrame(metric_rows)
    metrics["total_mv_yi"] = metrics["total_mv_yi"] / 10000
    metrics["net_mf_amount_yi"] = metrics["net_mf_amount"] / 10000
    metrics = metrics.sort_values(["today_pct", "ret5"], ascending=[False, False])
    metrics.to_csv(METRICS_OUT, index=False, encoding="utf-8-sig")
    print(json.dumps({
        "asof": asof,
        "stocks": len(stocks),
        "metrics": len(metrics),
        "basic_rows": len(basic),
        "money_rows": len(money),
        "failures": client.failures,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
