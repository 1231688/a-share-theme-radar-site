from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MAINLINE_DIR = ROOT / "work" / "mainline_out"
CATCHUP_METRICS = ROOT / "work" / "catchup_metrics.csv"
CATCHUP_JSON = ROOT / "work" / "catchup_probe.json"
NARRATIVE_JSON = ROOT / "work" / "narrative_overrides.json"
SITE_DATA_DIR = ROOT / "dist" / "data"
HISTORY_DIR = SITE_DATA_DIR / "history"
LATEST = SITE_DATA_DIR / "latest.json"


def latest_summary() -> tuple[Path, Path]:
    summaries = sorted(
        MAINLINE_DIR.glob("*_summary.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    if not summaries:
        raise SystemExit("找不到主线扫描结果，请先运行 mainline_scan.py")
    summary = summaries[0]
    metrics = summary.with_name(summary.name.replace("_summary.json", "_sector_metrics.csv"))
    if not metrics.exists():
        raise SystemExit(f"找到结论但缺少板块明细：{metrics}")
    return summary, metrics


def finite(value, digits=2):
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def classify_stage(theme: dict, potential: bool, stocks: list[dict] | None = None) -> tuple[str, str]:
    if potential:
        return "萌芽 / 试盘", "seed"
    if not theme.get("alive", False):
        return "退潮", "fade"
    stocks = stocks or []
    if stocks and not stocks[0].get("above_ma10") and any(
        stock.get("above_ma10") for stock in stocks[1:]
    ):
        return "分歧 / 换龙", "diverge"
    if (
        float(theme.get("vol_ratio") or 0) < 1.1
        and -0.2 <= float(theme.get("ret_recent") or 0) <= 1.0
    ):
        return "扩散 / 钝化", "active"
    if (
        float(theme.get("vol_ratio") or 0) >= 1.3
        and float(theme.get("recent_rank_pct") or 0) >= 95
        and float(theme.get("ret_recent") or 0) > 0
    ):
        return "加速 / 高潮", "hot"
    if float(theme.get("ret_recent") or 0) > 0:
        return "启动 / 扩散", "active"
    return "分歧 / 钝化", "diverge"


def assign_stock_roles(stocks: list[dict], potential: bool) -> list[dict]:
    if not stocks:
        return []
    result = []
    above = [stock for stock in stocks if stock.get("above_ma10")]
    primary = above[0] if above else stocks[0]
    remaining = [stock for stock in stocks if stock is not primary]
    amount_core = max(
        remaining,
        key=lambda stock: float(stock.get("amount_ratio") or 0),
        default=None,
    )
    recent_core = max(
        remaining,
        key=lambda stock: float(stock.get("ret_recent") or 0),
        default=None,
    )

    for stock in stocks:
        item = dict(stock)
        if potential:
            if stock is primary:
                role = "触发股"
            elif stock is amount_core:
                role = "量能股"
            else:
                role = "跟随股"
        elif stock is primary:
            role = "龙头" if stock.get("above_ma10") else "老龙头 / 风险"
        elif not stock.get("above_ma10"):
            role = "风险股"
        elif stock is amount_core:
            role = "中军 / 接力"
        elif stock is recent_core:
            role = "补涨"
        else:
            role = "跟随"
        item["role"] = role
        item["ret_lookback"] = finite(item.get("ret_lookback"))
        item["ret_recent"] = finite(item.get("ret_recent"))
        item["amount_ratio"] = finite(item.get("amount_ratio"))
        result.append(item)
    return result


def candidate_list(asof: str) -> list[dict]:
    if not CATCHUP_METRICS.exists() or not CATCHUP_JSON.exists():
        return []

    metrics = pd.read_csv(CATCHUP_METRICS)
    payload = json.loads(CATCHUP_JSON.read_text(encoding="utf-8"))
    if payload.get("asof") != asof:
        return []
    rows = []

    for _, row in metrics.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        if "ST" in name:
            continue
        if not bool(row["above_ma10"]) or float(row["ret5"]) <= 0:
            continue
        if float(row["today_pct"]) < -2:
            continue
        if float(row["amount_ratio3"]) < 1.2:
            continue
        if float(row["total_mv_yi"]) > 500:
            continue

        chart = payload["daily"].get(code, {}).get("rows", [])
        if not chart:
            continue
        closes = [float(item["close"]) for item in chart]
        close = closes[-1]
        ma10 = sum(closes[-10:]) / min(10, len(closes))
        high20 = max(closes[-20:])

        score = 0.0
        score += 20 if float(row["today_pct"]) > 0 else 0
        score += 20 if bool(row["above_ma10"]) else 0
        score += min(float(row["amount_ratio3"]), 3) * 10
        score += min(max(float(row["ret5"]), 0), 20) * 0.5
        score += 5 if float(row["net_mf_amount"]) > 0 else -10
        score += 5 if float(row["distance_high20"]) >= -3 else 0
        score -= max(float(row["ret15"]) - 30, 0) * 1.5
        score -= max(float(row["turnover_rate"]) - 25, 0) * 0.3

        rows.append(
            {
                "code": code,
                "name": name,
                "score": round(score, 1),
                "today": finite(row["today_pct"]),
                "ret5": finite(row["ret5"]),
                "ret15": finite(row["ret15"]),
                "amountRatio3": finite(row["amount_ratio3"]),
                "volumeRatio": finite(row["volume_ratio"]),
                "turnover": finite(row["turnover_rate"]),
                "marketCapYi": finite(row["total_mv_yi"], 1),
                "mainFlowYi": finite(float(row["net_mf_amount"]) / 10000),
                "close": finite(close),
                "ma10": finite(ma10),
                "high20": finite(high20),
                "distanceHigh20": finite(row["distance_high20"]),
            }
        )

    rows.sort(key=lambda item: item["score"], reverse=True)
    labels = ["优先潜伏", "高弹性备选", "容量中军", "确认型"]
    for index, item in enumerate(rows[:4]):
        item["rank"] = index + 1
        item["label"] = labels[index]
        if index == 0:
            item["trigger"] = f"放量突破20日高点 {item['high20']:.2f}，成交额不低于今日70%。"
        elif index == 1:
            item["trigger"] = f"守住10日线 {item['ma10']:.2f}，且量能不明显萎缩。"
        elif index == 2:
            item["trigger"] = f"重新站上20日高点 {item['high20']:.2f}，并出现板块同步转强。"
        else:
            item["trigger"] = f"突破 {item['high20']:.2f}并形成首板卡位。"
        item["invalidation"] = f"收盘跌破10日线 {item['ma10']:.2f}，或主力资金转为明显净流出。"
    return rows[:4]


STAGE_LABELS = {
    "seed": "萌芽 / 试盘",
    "launch": "启动 / 确认",
    "active": "扩散 / 走强",
    "hot": "加速 / 高潮",
    "diverge": "分歧 / 换龙",
    "fade": "退潮 / 观察",
}


def compound(values: list[float]) -> float:
    result = 1.0
    for value in values:
        result *= 1 + value / 100
    return (result - 1) * 100


def window_return(pct: list, index: int, days: int) -> float:
    values = [value for value in pct[max(0, index - days + 1): index + 1] if value is not None]
    return compound(values) if values else 0.0


def window_volume_ratio(vol: list, index: int) -> float:
    values = [value for value in vol[max(0, index - 9): index + 1] if value is not None]
    recent = [value for value in vol[max(0, index - 2): index + 1] if value is not None]
    if not values or not recent:
        return 1.0
    baseline = sum(values) / len(values)
    return sum(recent) / len(recent) / baseline if baseline else 1.0


def classify_cycle_day(pct: list, vol: list, rank: list, breadth: list, index: int, previous: str) -> str:
    if index < 2:
        return "seed"
    day = pct[index] or 0.0
    ret3 = window_return(pct, index, 3)
    ret5 = window_return(pct, index, 5)
    vol_ratio = window_volume_ratio(vol, index)
    rank_pct = rank[index] if rank[index] is not None else 50.0
    advance = breadth[index] if breadth[index] is not None else 50.0
    equity = compound([value or 0 for value in pct[: index + 1]])
    peak = max(
        [compound([value or 0 for value in pct[: i + 1]]) for i in range(index + 1)]
    )
    drawdown = equity - peak

    if index >= 4 and (drawdown <= -7 or (ret5 <= -3 and rank_pct < 50)):
        return "fade"
    if previous in {"hot", "diverge"} and (day < 0 or advance < 45 or drawdown <= -3):
        return "diverge"
    if ret5 >= 8 or (ret3 >= 4 and vol_ratio >= 1.15 and rank_pct >= 85):
        return "hot"
    if previous in {"launch", "active", "hot"} and day <= -1 and advance < 50:
        return "diverge"
    if ret3 >= 2 and ret5 > 0 and advance >= 50 and (rank_pct >= 65 or vol_ratio >= 1.05):
        return "active"
    if day >= 1 and advance >= 55 and (rank_pct >= 75 or vol_ratio >= 1.1):
        return "launch"
    if previous in {"launch", "active"} and ret3 > 0 and advance >= 45:
        return "active"
    if previous == "fade" and ret3 <= 0:
        return "fade"
    return "seed"


def cycle_rules(stage_key: str) -> tuple[str, str]:
    rules = {
        "seed": (
            "板块进入同类涨幅前20%，上涨家数≥60%，且近3日量能比≥1.2。",
            "异动后两日不能延续，或量能重新降至20日均量以下。",
        ),
        "launch": (
            "板块连续两日位于同类前30%，核心股同步走强而非单点拉升。",
            "启动阳线被完全吞没，或核心股收盘跌破10日线。",
        ),
        "active": (
            "板块回调不破5日趋势，上涨家数维持50%以上，龙头与中军轮动承接。",
            "板块近3日转为负收益，且上涨家数低于40%。",
        ),
        "hot": (
            "高潮后不追一致性；缩量企稳、龙头守住10日线，才观察二次转强。",
            "高位核心集体补跌，或板块从区间高点回撤超过7%。",
        ),
        "diverge": (
            "分歧后板块重新进入同类前30%，且新核心带动上涨家数恢复至55%以上。",
            "修复仅由单一个股维持，或老龙头与新核心同步走弱。",
        ),
        "fade": (
            "先等待止跌；近5日收益转正且量能重新放大，再按新启动评估。",
            "反弹缩量且板块排名仍低于同类中位数，不视为周期反转。",
        ),
    }
    return rules[stage_key]


def theme_cycle(theme: dict, stocks: list[dict], history: dict) -> dict:
    days = history.get("days", [])
    sector = history.get("sectors", {}).get(theme["code"], {})
    pct = sector.get("pct", [])
    vol = sector.get("vol", [])
    rank = sector.get("rankPct", [])
    breadth = sector.get("breadth", [])
    strong_count = sector.get("strongCount", [])
    if not days or len(pct) != len(days):
        return {
            "dates": [], "stocks": [], "cycles": [], "dailyStages": [],
            "stage": STAGE_LABELS["seed"], "stageKey": "seed",
            "summary": "当前缺少逐日数据，暂不能确认周期位置。",
            "trigger": cycle_rules("seed")[0], "invalidation": cycle_rules("seed")[1],
            "stats": {},
        }

    stages = []
    for index in range(len(days)):
        previous = stages[-1] if stages else "seed"
        stages.append(classify_cycle_day(pct, vol, rank, breadth, index, previous))
    # 普通阶段至少延续两日，避免把单日噪声误判为完整周期切换；高潮和退潮例外。
    for index in range(1, len(stages) - 1):
        if (
            stages[index] != stages[index - 1]
            and stages[index] != stages[index + 1]
            and stages[index] not in {"hot", "fade"}
        ):
            stages[index] = stages[index - 1]
    if theme.get("scope") == "mainline" and not theme.get("alive", False):
        stages[-1] = "fade"

    visible = min(15, len(days))
    start_at = len(days) - visible
    dates = [f"{day[4:6]}/{day[6:8]}" for day in days[start_at:]]
    visible_stages = stages[start_at:]
    cycles = []
    segment_start = 0
    for index in range(1, visible + 1):
        if index < visible and visible_stages[index] == visible_stages[segment_start]:
            continue
        key = visible_stages[segment_start]
        cycles.append(
            {
                "label": STAGE_LABELS[key],
                "range": f"{dates[segment_start]}—{dates[index - 1]}",
                "title": STAGE_LABELS[key],
                "start": segment_start + 1,
                "span": index - segment_start,
                "tone": key,
            }
        )
        segment_start = index

    timeline_stocks = [
        {
            "code": theme["code"], "name": theme["name"], "role": "板块指数",
            "pct": [finite(value) for value in pct[start_at:]], "events": {},
        }
    ]
    for stock in stocks:
        stock_history = history.get("stocks", {}).get(stock["ts_code"], {})
        stock_pct = stock_history.get("pct", [])
        if len(stock_pct) != len(days):
            stock_pct = [None] * len(days)
        events = {}
        for index, value in enumerate(stock_pct[start_at:]):
            if value is None:
                continue
            threshold = 4.7 if "ST" in stock["name"] else 19.7 if stock["ts_code"].startswith(("300", "688")) else 9.7
            if value >= threshold:
                events[str(index)] = "涨停"
            elif value <= -threshold:
                events[str(index)] = "跌停"
            elif value >= 5:
                events[str(index)] = "大涨"
            elif value <= -5:
                events[str(index)] = "大跌"
        timeline_stocks.append(
            {
                "code": stock["ts_code"], "name": stock["name"], "role": stock["role"],
                "pct": [finite(value) for value in stock_pct[start_at:]], "events": events,
            }
        )

    latest = len(days) - 1
    stage_key = stages[-1]
    ret5 = window_return(pct, latest, 5)
    vol_ratio = window_volume_ratio(vol, latest)
    rank_pct = rank[-1] if rank[-1] is not None else 50.0
    advance = breadth[-1] if breadth[-1] is not None else 0.0
    trigger, invalidation = cycle_rules(stage_key)
    return {
        "dates": dates,
        "stocks": timeline_stocks,
        "cycles": cycles,
        "dailyStages": [STAGE_LABELS[key] for key in visible_stages],
        "stage": STAGE_LABELS[stage_key],
        "stageKey": stage_key,
        "summary": (
            f"近5日 {ret5:+.2f}%，最新同类排名分位 {rank_pct:.0f}%，"
            f"上涨家数占比 {advance:.0f}%，近3日量能比 {vol_ratio:.2f}。"
        ),
        "trigger": trigger,
        "invalidation": invalidation,
        "stats": {
            "ret5": finite(ret5), "volRatio": finite(vol_ratio),
            "rankPct": finite(rank_pct, 0), "breadth": finite(advance, 0),
            "upDays": sum(1 for value in pct[start_at:] if value is not None and value > 0),
            "strongCount": int(strong_count[-1]) if strong_count else 0,
        },
    }


def build() -> None:
    summary_path, metrics_path = latest_summary()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    sectors = pd.read_csv(metrics_path)
    background = summary.get("background", [])
    latest = background[-1] if background else {}
    recent_bg = background[-5:]

    avg_up = sum(float(item.get("limit_up") or 0) for item in recent_bg) / max(len(recent_bg), 1)
    avg_down = sum(float(item.get("limit_down") or 0) for item in recent_bg) / max(
        len(recent_bg), 1
    )

    themes = []
    history = summary.get("cycle_history", {})
    for item in summary.get("mainlines", []):
        names = summary.get("leaders", {}).get("started", {}).get(item["name"], [])
        roles = assign_stock_roles(names, potential=False)
        cycle = theme_cycle(
            {"code": item["index"], "name": item["name"], "scope": "mainline", "alive": item.get("alive")},
            roles,
            history,
        )
        themes.append(
            {
                "code": item["index"],
                "name": item["name"],
                "kind": item["kind"],
                "scope": "mainline",
                "stage": cycle["stage"],
                "stageKey": cycle["stageKey"],
                "alive": bool(item.get("alive")),
                "note": cycle["summary"],
                "thesis": "按15日强度、近5日排名、量能与核心股梯队综合跟踪。",
                "metrics": {
                    "ret15": finite(item.get("ret_lookback")),
                    "ret5": finite(item.get("ret_recent")),
                    "rankDays": int(item.get("rank_days") or 0),
                    "volRatio": finite(item.get("vol_ratio")),
                    "shareDelta": finite(item.get("share_delta"), 3),
                    "recentRankPct": finite(item.get("recent_rank_pct"), 0),
                },
                "stocks": roles,
                "cycle": cycle,
            }
        )

    for item in summary.get("watch", []):
        names = summary.get("leaders", {}).get("potential", {}).get(item["name"], [])
        roles = assign_stock_roles(names, potential=True)
        cycle = theme_cycle(
            {"code": item["index"], "name": item["name"], "scope": "potential", "alive": True},
            roles,
            history,
        )
        themes.append(
            {
                "code": item["index"],
                "name": item["name"],
                "kind": item["kind"],
                "scope": "potential",
                "stage": cycle["stage"],
                "stageKey": cycle["stageKey"],
                "alive": True,
                "note": cycle["summary"],
                "thesis": "低位放量，结合上涨家数、持续排名和核心股承接确认板块效应。",
                "metrics": {
                    "ret15": finite(item.get("ret_lookback")),
                    "ret5": finite(item.get("ret_recent")),
                    "rankDays": int(item.get("rank_days") or 0),
                    "volRatio": finite(item.get("vol_ratio")),
                    "shareDelta": finite(item.get("share_delta"), 3),
                    "recentRankPct": finite(item.get("recent_rank_pct"), 0),
                },
                "stocks": roles,
                "cycle": cycle,
            }
        )

    sector_rows = []
    mainline_names = {item["name"] for item in summary.get("mainlines", [])}
    watch_names = {item["name"] for item in summary.get("watch", [])}
    for _, row in sectors.iterrows():
        sector_rows.append(
            {
                "code": row["ts_code"],
                "name": row["name"],
                "kind": row["kind"],
                "members": int(row["members"]) if pd.notna(row["members"]) else None,
                "ret15": finite(row["ret_lookback"]),
                "ret5": finite(row["ret_recent"]),
                "rankDays": int(row["rank_days"]) if pd.notna(row["rank_days"]) else 0,
                "volRatio": finite(row["vol_ratio"]),
                "shareDelta": finite(row["share_delta"], 3),
                "recentRankPct": finite(row["recent_rank_pct"], 0),
                "posPct": finite(row["pos_pct"], 0),
                "status": (
                    "主线" if row["name"] in mainline_names else "潜在" if row["name"] in watch_names else ""
                ),
            }
        )

    phase = "分歧偏退潮" if float(latest.get("limit_down") or 0) > avg_down * 2 else "中性轮动"
    alive_count = sum(1 for theme in themes if theme["scope"] == "mainline" and theme["alive"])
    candidate_data = candidate_list(summary["asof"])
    narrative_data = []
    if NARRATIVE_JSON.exists():
        narrative_payload = json.loads(NARRATIVE_JSON.read_text(encoding="utf-8"))
        if narrative_payload.get("asof") == summary["asof"]:
            narrative_data = narrative_payload.get("themes", [])
    if not narrative_data:
        narrative_data = [
            {
                "id": theme["code"],
                "name": theme["name"],
                "stage": theme["stage"],
                "tone": "active" if theme["stageKey"] == "launch" else theme["stageKey"],
                "evidence": theme["note"],
                "catalyst": f"行情复盘：{theme['thesis']}",
                "stocks": [
                    {
                        "code": stock["ts_code"], "name": stock["name"], "role": stock["role"],
                        "change": f"15日 {stock['ret_lookback']:+.2f}%", "pct": stock["ret_recent"],
                    }
                    for stock in theme["stocks"]
                ],
            }
            for theme in themes[:6]
        ]
    payload = {
        "meta": {
            "title": "A股题材轮动雷达",
            "asof": summary["asof"],
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "Tushare（同花顺指数与 A 股行情）",
            "window": summary["window"],
            "recentWindow": summary["recent_window"],
            "universe": summary["universe"],
            "sectorCount": len(sector_rows),
        },
        "market": {
            "phase": phase,
            "limitUp": int(latest.get("limit_up") or 0),
            "limitDown": int(latest.get("limit_down") or 0),
            "broken": int(latest.get("broken") or 0),
            "breakRate": finite(latest.get("break_rate")),
            "maxStreak": int(latest.get("max_streak") or 0),
            "streakCount": int(latest.get("streak_count") or 0),
            "avg5LimitUp": round(avg_up, 1),
            "avg5LimitDown": round(avg_down, 1),
            "aliveMainlines": alive_count,
        },
        "themes": themes,
        "narratives": narrative_data,
        "timeline": themes[0]["cycle"] if themes else {"dates": [], "stocks": [], "cycles": []},
        "candidates": candidate_data,
        "sectors": sector_rows,
        "failures": summary.get("failures", []),
    }

    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    LATEST.write_text(serialized, encoding="utf-8")
    (HISTORY_DIR / f"{summary['asof']}.json").write_text(serialized, encoding="utf-8")
    print(
        json.dumps(
            {
                "latest": str(LATEST),
                "themes": len(themes),
                "narratives": len(narrative_data),
                "sectors": len(sector_rows),
                "candidates": len(candidate_data),
                "asof": summary["asof"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    build()
