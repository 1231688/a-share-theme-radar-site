#!/usr/bin/env python3
"""主线复盘扫描器：一条命令算出三个窗口的板块指标。

数据源：Tushare HTTP 接口。token 取环境变量 TUSHARE_TOKEN，
否则读 ~/.codex/config.toml 里 mcp_servers.tushare_official.env.TUSHARE_TOKEN。

用法：
  python mainline_scan.py
  python mainline_scan.py --asof 20261013 --lookback 15 --recent 5 --top 12
  python mainline_scan.py --universe concept --out ./out

输出：
  <out>/<asof>_sector_metrics.csv   全部板块的指标明细
  <out>/<asof>_summary.json         三个窗口的结论
  控制台打印三段摘要
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore

API_URL = "https://api.tushare.pro"
DEFAULT_CONFIG = Path.home() / ".codex" / "config.toml"

THS_PREFIX = {
    "concept": ("885", "886"),
    "industry": ("881",),
    "sub": ("884",),
}
UNIVERSE_LABEL = {
    "concept": "同花顺概念",
    "industry": "同花顺行业",
    "sub": "同花顺细分行业",
    "sw": "申万一级",
}

THS_FIELDS = "ts_code,trade_date,close,pre_close,pct_change,vol,turnover_rate"
SW_FIELDS = "ts_code,trade_date,name,close,pct_change,vol,amount,float_mv"
LIMIT_FIELDS = "trade_date,ts_code,name,industry,close,pct_chg,amount,open_times,limit_times,up_stat,limit"
STOCK_FIELDS = "ts_code,trade_date,close,pre_close,pct_chg,amount"


def load_token(config_path: Path = DEFAULT_CONFIG) -> str:
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    if tomllib is None:
        raise SystemExit("缺少 tomllib，无法读取配置文件；请设置环境变量 TUSHARE_TOKEN")
    if not config_path.exists():
        raise SystemExit(f"TUSHARE_TOKEN 未设置，也找不到 {config_path}")
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    token = (
        cfg.get("mcp_servers", {})
        .get("tushare_official", {})
        .get("env", {})
        .get("TUSHARE_TOKEN", "")
    ).strip()
    if not token:
        raise SystemExit(f"{config_path} 里没有 mcp_servers.tushare_official.env.TUSHARE_TOKEN")
    return token


class TushareClient:
    """带磁盘缓存的 Tushare 客户端。重跑不会重复拉数据。"""

    def __init__(self, token: str, cache_dir: Path | None, pause: float = 0.1):
        self.token = token
        self.cache_dir = cache_dir
        self.pause = pause
        self.calls = 0
        self.failures: list[str] = []
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, api: str, params: dict, fields: str):
        if self.cache_dir is None:
            return None
        key = json.dumps({"a": api, "p": params, "f": fields}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{api}_{digest}.csv"

    def call(self, api: str, params: dict | None = None, fields: str = "") -> pd.DataFrame:
        params = params or {}
        cache_path = self._cache_path(api, params, fields)
        if cache_path is not None and cache_path.exists():
            try:
                return pd.read_csv(cache_path, dtype=str)
            except pd.errors.EmptyDataError:
                pass  # 空结果缓存读不出来，视为未缓存，重新请求

        body = json.dumps(
            {"api_name": api, "token": self.token, "params": params, "fields": fields}
        ).encode("utf-8")
        payload = None
        last_error = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    API_URL, data=body, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except Exception as exc:  # 网络抖动重试
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        if payload is None:
            raise SystemExit(f"调用 {api} 失败：{type(last_error).__name__}: {last_error}")

        self.calls += 1
        if self.pause:
            time.sleep(self.pause)

        data = payload.get("data")
        if data is None:
            self.failures.append(f"{api} {params} -> {payload.get('msg')}")
            return pd.DataFrame()
        frame = pd.DataFrame(data["items"], columns=data["fields"])
        if cache_path is not None and not frame.empty:
            frame.to_csv(cache_path, index=False)
        return frame


def to_num(frame: pd.DataFrame, columns) -> pd.DataFrame:
    for column in columns:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def trading_days(client: TushareClient, asof: str | None, need: int) -> list[str]:
    end = asof or datetime.now().strftime("%Y%m%d")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=need * 3 + 40)).strftime("%Y%m%d")
    cal = client.call(
        "trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end}, "cal_date,is_open"
    )
    if cal.empty:
        raise SystemExit("取交易日历失败，检查 Tushare token 或网络")
    open_days = sorted(cal.loc[cal["is_open"].astype(str) == "1", "cal_date"].tolist())
    open_days = [d for d in open_days if d <= end]
    if len(open_days) < need:
        raise SystemExit(f"可用交易日不足：只有 {len(open_days)} 天，需要 {need} 天")
    return open_days[-need:]


def build_universe(client: TushareClient, kinds: list[str], min_members: int) -> pd.DataFrame:
    frames = []
    needs_ths = any(kind in THS_PREFIX for kind in kinds)
    if needs_ths:
        index = client.call("ths_index", {}, "ts_code,name,count,type")
        if index.empty:
            raise SystemExit("取不到同花顺指数列表（ths_index）")
        index["count"] = pd.to_numeric(index["count"], errors="coerce")
        for kind in kinds:
            for prefix in THS_PREFIX.get(kind, ()):
                sub = index[index["ts_code"].str.startswith(prefix)].copy()
                if sub.empty:
                    continue
                sub["kind"] = kind
                frames.append(sub)

    if "sw" in kinds:
        sw_index = client.call(
            "index_classify", {"level": "L1", "src": "SW2021"}, "index_code,industry_name"
        )
        if sw_index.empty:
            print("警告：取不到申万一级行业列表，已跳过 sw。", file=sys.stderr)
        else:
            sw_index = sw_index.rename(columns={"index_code": "ts_code", "industry_name": "name"})
            sw_index["kind"] = "sw"
            sw_index["count"] = float("nan")
            frames.append(sw_index[["ts_code", "name", "count", "kind"]])

    if not frames:
        raise SystemExit("板块池为空，检查 --universe 参数")

    universe = pd.concat(frames, ignore_index=True).rename(columns={"count": "members"})
    universe["members"] = pd.to_numeric(universe["members"], errors="coerce")
    keep = universe["members"].isna() | (universe["members"] >= min_members)
    universe = universe[keep].drop_duplicates("ts_code").reset_index(drop=True)
    universe["panel"] = universe["kind"].map(lambda k: "sw" if k == "sw" else "ths")
    return universe


def collect_panels(client: TushareClient, days: list[str]):
    ths_rows, sw_rows, limit_rows, ladder_rows, stock_rows = [], [], [], [], []
    for day in days:
        ths = client.call("ths_daily", {"trade_date": day}, THS_FIELDS)
        if not ths.empty:
            ths_rows.append(ths)
        sw = client.call("sw_daily", {"trade_date": day}, SW_FIELDS)
        if not sw.empty:
            sw_rows.append(sw)
        limit = client.call("limit_list_d", {"trade_date": day}, LIMIT_FIELDS)
        if not limit.empty:
            limit_rows.append(limit)
        ladder = client.call("limit_step", {"trade_date": day}, "trade_date,ts_code,name,nums")
        if not ladder.empty:
            ladder_rows.append(ladder)
        stocks = client.call("daily", {"trade_date": day}, STOCK_FIELDS)
        if not stocks.empty:
            stock_rows.append(stocks)

    def concat(rows, numeric):
        if not rows:
            return pd.DataFrame()
        frame = pd.concat(rows, ignore_index=True)
        return to_num(frame, numeric)

    panels = {
        "ths": concat(ths_rows, ["close", "pre_close", "pct_change", "vol", "turnover_rate"]),
        "sw": concat(sw_rows, ["close", "pct_change", "vol", "amount", "float_mv"]),
        "limit": concat(limit_rows, ["pct_chg", "amount", "open_times", "limit_times"]),
        "ladder": concat(ladder_rows, ["nums"]),
        "stock": concat(stock_rows, ["close", "pre_close", "pct_chg", "amount"]),
    }
    return panels

def compute_metrics(panels, universe, days, lookback, recent, top_rank):
    frame = pd.concat([panels["ths"], panels["sw"]], ignore_index=True, sort=False)
    if frame.empty:
        raise SystemExit("板块行情为空，无法计算")
    frame = frame[frame["ts_code"].isin(set(universe["ts_code"]))]
    if frame.empty:
        raise SystemExit("板块池与行情对不上，检查 --universe 参数")

    pct = frame.pivot_table(index="ts_code", columns="trade_date", values="pct_change")
    vol = frame.pivot_table(index="ts_code", columns="trade_date", values="vol")

    lookback_days = [d for d in days[-lookback:] if d in pct.columns]
    recent_days = [d for d in days[-recent:] if d in pct.columns]
    vol_days = [d for d in days[-20:] if d in vol.columns]

    def compound(source, cols):
        if not cols:
            return pd.Series(index=source.index, dtype=float)
        return (1 + source[cols].fillna(0) / 100.0).prod(axis=1).sub(1).mul(100)

    ret_total = compound(pct, lookback_days)
    ret_recent = compound(pct, recent_days)

    rank_series = {}
    for _, group in universe.groupby("kind"):
        codes = [c for c in group["ts_code"] if c in pct.index]
        if not codes or not lookback_days:
            continue
        ranked = pct.loc[codes, lookback_days].rank(axis=0, ascending=False)
        for code, row in ranked.iterrows():
            rank_series[code] = row
    rank_frame = pd.DataFrame(rank_series).T
    reach_rank = (rank_frame <= top_rank).sum(axis=1) if not rank_frame.empty else pd.Series(dtype=float)

    max_day = pct[lookback_days].max(axis=1) if lookback_days else pd.Series(dtype=float)
    concentration = (max_day / ret_total).where(ret_total > 0)

    if vol_days:
        vol_ratio = vol[vol_days[-3:]].mean(axis=1) / vol[vol_days].mean(axis=1)
        share = vol[vol_days].div(vol[vol_days].sum(axis=0), axis=1) * 100
        share_delta = share[vol_days[-3:]].mean(axis=1) - share[vol_days[:3]].mean(axis=1)
    else:
        vol_ratio = pd.Series(dtype=float)
        share_delta = pd.Series(dtype=float)

    metrics = pd.DataFrame(
        {
            "ret_lookback": ret_total,
            "ret_recent": ret_recent,
            "rank_days": reach_rank,
            "concentration": concentration,
            "vol_ratio": vol_ratio,
            "share_delta": share_delta,
        }
    )
    metrics = metrics.join(universe.set_index("ts_code")[["name", "kind", "members", "panel"]])
    metrics = metrics.dropna(subset=["ret_lookback"])
    metrics["pos_pct"] = metrics.groupby("kind")["ret_lookback"].rank(pct=True) * 100
    metrics["recent_rank_pct"] = metrics.groupby("kind")["ret_recent"].rank(pct=True) * 100
    return metrics


def find_leaders(client, panels, days, lookback, recent, codes, horizon="lookback", per_sector=3):
    """找出板块内的代表个股。horizon=lookback 用于已启动主线（谁涨得最多），
    horizon=recent 用于潜在主线（谁先异动、成交额先放量）。"""
    stock = panels["stock"]
    if stock.empty or not codes:
        return {}

    def num(value):
        if value is None or pd.isna(value):
            return None
        return round(float(value), 2)

    close = stock.pivot_table(index="ts_code", columns="trade_date", values="close")
    pct = stock.pivot_table(index="ts_code", columns="trade_date", values="pct_chg")
    amount = stock.pivot_table(index="ts_code", columns="trade_date", values="amount")

    def compound(cols):
        if not cols:
            return pd.Series(dtype=float)
        return (1 + pct[cols].fillna(0) / 100.0).prod(axis=1).sub(1).mul(100)

    ret_lookback = compound([d for d in days[-lookback:] if d in pct.columns])
    ret_recent = compound([d for d in days[-recent:] if d in pct.columns])
    amt_cols = [d for d in days[-20:] if d in amount.columns]
    amount_ratio = (
        amount[amt_cols[-3:]].mean(axis=1) / amount[amt_cols].mean(axis=1) if amt_cols else pd.Series(dtype=float)
    )
    ma_cols = [d for d in days[-10:] if d in close.columns]
    ma10 = close[ma_cols].mean(axis=1) if ma_cols else pd.Series(dtype=float)

    key = ret_lookback if horizon == "lookback" else ret_recent
    if key.empty:
        return {}

    leaders = {}
    for code in codes:
        members = client.call("ths_member", {"ts_code": code}, "ts_code,con_code,con_name")
        if members.empty:
            # 申万行业在同花顺成分接口里没有对应，改用申万成分
            fallback = client.call("index_member_all", {"l1_code": code}, "l1_code,ts_code,name,is_new")
            if not fallback.empty:
                fallback = fallback[fallback["is_new"].astype(str) == "Y"]
                members = fallback.rename(columns={"ts_code": "con_code", "name": "con_name"})[
                    ["con_code", "con_name"]
                ]
        if members.empty:
            continue
        inside = [c for c in members["con_code"] if c in key.index and not pd.isna(key.get(c))]
        if not inside:
            continue
        ranked = key.loc[inside].sort_values(ascending=False)
        rows = []
        for member_code in ranked.index[:per_sector]:
            matched = members.loc[members["con_code"] == member_code, "con_name"]
            last_close = close.loc[member_code].dropna() if member_code in close.index else pd.Series(dtype=float)
            # 最新交易日没有成交，说明停牌或长期无数据，不能拿停牌前的价格判断“未破均线”
            trading = bool(
                member_code in close.index and days and not pd.isna(close.loc[member_code].get(days[-1]))
            )
            rows.append(
                {
                    "ts_code": member_code,
                    "name": matched.iloc[0] if not matched.empty else member_code,
                    "ret_lookback": num(ret_lookback.get(member_code)),
                    "ret_recent": num(ret_recent.get(member_code)),
                    "amount_ratio": num(amount_ratio.get(member_code)),
                    "trading": trading,
                    "above_ma10": bool(
                        not last_close.empty
                        and member_code in ma10.index
                        and not pd.isna(ma10[member_code])
                        and last_close.iloc[-1] >= ma10[member_code]
                    ),
                }
            )
        leaders[code] = rows
    return leaders


def build_cycle_history(client, panels, days, sector_codes, started, potential) -> dict:
    """保留题材与核心股逐日轨迹，供前端识别完整周期，而非只给单日标签。"""
    sector_panel = panels["ths"]
    stock_panel = panels["stock"]
    leader_rows = {**started, **potential}
    stock_codes = {
        stock["ts_code"]
        for rows in leader_rows.values()
        for stock in rows
    }

    def num(value, digits=4):
        if value is None or pd.isna(value):
            return None
        return round(float(value), digits)

    stocks = {}
    for code in sorted(stock_codes):
        rows = stock_panel[stock_panel["ts_code"] == code].set_index("trade_date")
        name = next(
            stock["name"]
            for rows in leader_rows.values()
            for stock in rows
            if stock["ts_code"] == code
        )
        stocks[code] = {
            "name": name,
            "pct": [num(rows["pct_chg"].get(day)) for day in days],
            "close": [num(rows["close"].get(day)) for day in days],
        }

    sectors = {}
    for code in sector_codes:
        rows = sector_panel[sector_panel["ts_code"] == code].set_index("trade_date")
        members = client.call("ths_member", {"ts_code": code}, "ts_code,con_code,con_name")
        member_codes = set(members["con_code"]) if not members.empty else set()
        pct_values, vol_values, rank_values, breadth_values, strong_values = [], [], [], [], []
        for day in days:
            pct_values.append(num(rows["pct_change"].get(day)))
            vol_values.append(num(rows["vol"].get(day), 2))
            day_panel = sector_panel[sector_panel["trade_date"] == day]
            rank_pct = None
            if not day_panel.empty and code in set(day_panel["ts_code"]):
                ranks = day_panel["pct_change"].rank(pct=True)
                matched = ranks[day_panel["ts_code"] == code]
                if not matched.empty:
                    rank_pct = num(matched.iloc[0] * 100, 2)
            rank_values.append(rank_pct)
            day_stocks = stock_panel[
                (stock_panel["trade_date"] == day) & stock_panel["ts_code"].isin(member_codes)
            ]
            active = day_stocks["pct_chg"].dropna()
            breadth_values.append(num((active > 0).mean() * 100, 2) if len(active) else None)
            strong_values.append(int((active >= 5).sum()))
        sectors[code] = {
            "pct": pct_values,
            "vol": vol_values,
            "rankPct": rank_values,
            "breadth": breadth_values,
            "strongCount": strong_values,
        }
    return {"days": days, "sectors": sectors, "stocks": stocks}


def market_background(panels, days) -> list:
    limit = panels["limit"]
    ladder = panels["ladder"]
    rows = []
    for day in days:
        day_limit = limit[limit["trade_date"] == day] if not limit.empty else pd.DataFrame()
        if day_limit.empty:
            up = down = broken = 0
        else:
            up = int((day_limit["limit"] == "U").sum())
            down = int((day_limit["limit"] == "D").sum())
            broken = int((day_limit["limit"] == "Z").sum())
        day_ladder = ladder[ladder["trade_date"] == day] if not ladder.empty else pd.DataFrame()
        rows.append(
            {
                "trade_date": day,
                "limit_up": up,
                "limit_down": down,
                "broken": broken,
                "break_rate": round(broken / (broken + up) * 100, 1) if (broken + up) else None,
                "max_streak": int(day_ladder["nums"].max()) if not day_ladder.empty else 0,
                "streak_count": int(len(day_ladder)),
            }
        )
    return rows


def classify(metrics, lookback, still_leading_pct=20.0):
    threshold = max(3, math.ceil(lookback * 0.25))
    base = metrics[(metrics["rank_days"] >= threshold) & (metrics["ret_lookback"] > 0)]
    base = base[base["concentration"].isna() | (base["concentration"] <= 0.6)]
    base = base.assign(
        still_leading=(base["recent_rank_pct"] >= 100.0 - still_leading_pct) & (base["ret_recent"] > 0),
        capital_ok=(base["vol_ratio"].fillna(1) >= 0.7) & (base["share_delta"].fillna(0) >= -0.2),
    )
    base = base.assign(alive=base["still_leading"] & base["capital_ok"])
    mainlines = base.sort_values(["alive", "rank_days", "ret_lookback"], ascending=False)

    watch = metrics[
        (metrics["pos_pct"] <= 50)
        & (metrics["vol_ratio"].fillna(0) >= 1.15)
        & (metrics["ret_lookback"] <= 12)
        & (metrics["ret_recent"].fillna(0).between(-5, 8))
        & (metrics["concentration"].isna() | (metrics["concentration"] <= 0.8))
    ].sort_values("vol_ratio", ascending=False)
    return mainlines, watch


def fmt(value, digits=2, suffix=""):
    if value is None:
        return "-"
    try:
        if math.isnan(float(value)):
            return "-"
    except (TypeError, ValueError):
        return "-"
    return f"{float(value):.{digits}f}{suffix}"


def print_report(asof, window, universe, background, mainlines, watch, leaders, watch_leaders,
                recent_n, top, failures, calls, report_path=None):
    lines = []

    def emit(text=""):
        lines.append(text)
        print(text)

    def ma_label(leader):
        if leader.get("trading") is False:
            return "停牌中（不适用均线判断）"
        return "未破 10 日线" if leader.get("above_ma10") else "已破 10 日线"

    latest = background[-1]
    threshold = max(3, math.ceil(len(window) * 0.25))
    emit(f"\n=== 主线复盘扫描 {asof} ===")
    emit(f"板块池 {len(universe)} 个 | 回顾窗口 {window[0]}—{window[-1]}（{len(window)} 个交易日）"
         f" | 确认窗口最近 {recent_n} 日 | Tushare 调用 {calls} 次")
    emit(f"\n【环境背景】涨停 {latest['limit_up']} / 跌停 {latest['limit_down']} / 炸板 {latest['broken']}"
         f"（炸板率 {fmt(latest['break_rate'], 1, '%')}）| 最高 {latest['max_streak']} 板"
         f" | 连板股 {latest['streak_count']} 只")
    streak = ", ".join(f"{r['trade_date'][4:]}:{r['limit_up']}/{r['max_streak']}" for r in background[-5:])
    emit(f"           近 5 日「涨停数/最高板」 {streak}")

    emit(f"\n【窗口一 回顾】上榜天数 ≥ {threshold} 的持续领涨板块（共 {len(mainlines)} 个）")
    emit(f"{'板块':<16}{'类型':<12}{'区间涨幅':>9}{'上榜':>6}{'集中度':>8}{'近5日':>9}{'量比':>7}  龙头")
    for code, row in mainlines.head(top).iterrows():
        lead = leaders.get(code) or []
        lead_text = f"{lead[0]['name']} {fmt(lead[0]['ret_lookback'], 1, '%')}" if lead else "-"
        emit(f"{row['name']:<16}{UNIVERSE_LABEL.get(row['kind'], row['kind']):<12}"
             f"{fmt(row['ret_lookback'], 2, '%'):>9}{int(row['rank_days']):>6}"
             f"{fmt(row['concentration'], 2):>8}{fmt(row['ret_recent'], 2, '%'):>9}"
             f"{fmt(row['vol_ratio'], 2):>7}  {lead_text}")

    emit("\n【窗口二 确认】活着 / 已死")
    for code, row in mainlines.head(top).iterrows():
        reasons = []
        if not row["still_leading"]:
            reasons.append(f"近 {recent_n} 日收益分位 {fmt(row['recent_rank_pct'], 0)}%，掉出前 20%")
        if not row["capital_ok"]:
            reasons.append(f"量能萎缩（近 3 日/20 日均量 {fmt(row['vol_ratio'], 2)}）")
        lead = (leaders.get(code) or [{}])[0]
        lead_text = ""
        if lead:
            lead_text = f"｜龙头 {lead.get('name', '-')} {ma_label(lead)}"
        verdict = "三项检验全过" if not reasons else "；".join(reasons)
        emit(f"  [{'存活' if row['alive'] else '已死'}] {row['name']}"
             f"（{UNIVERSE_LABEL.get(row['kind'], row['kind'])}）：{verdict}{lead_text}")

    emit("\n【窗口三 预判】底部异动、放量但尚未加速")
    if watch.empty:
        emit("  本期无合格候选")
    else:
        for code, row in watch.head(top).iterrows():
            emit(f"  {row['name']:<16}{UNIVERSE_LABEL.get(row['kind'], row['kind']):<12}"
                 f"区间 {fmt(row['ret_lookback'], 1, '%'):>8}（分位 {fmt(row['pos_pct'], 0)}%）"
                 f"   近 3 日量比 {fmt(row['vol_ratio'], 2)}   近 5 日 {fmt(row['ret_recent'], 2, '%')}")
            for leader in (watch_leaders.get(code) or [])[:3]:
                emit(f"      异动个股  {leader['name']}({leader['ts_code']})  近5日 "
                     f"{fmt(leader['ret_recent'], 1, '%')}  成交额比 {fmt(leader['amount_ratio'], 2)}  "
                     f"{ma_label(leader)}")

    if failures:
        emit(f"\n【取数失败】{len(failures)} 项")
        for item in failures[:10]:
            emit(f"  - {item}")
    emit()

    if report_path is not None:
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="主线复盘扫描：算出回顾、确认、预判三个窗口的板块指标")
    parser.add_argument("--asof", default=None, help="截止交易日 YYYYMMDD，默认最近已收盘交易日")
    parser.add_argument("--lookback", type=int, default=15, help="回顾窗口交易日数，默认 15")
    parser.add_argument("--recent", type=int, default=5, help="确认窗口交易日数，默认 5")
    parser.add_argument("--universe", default="concept,industry",
                        help="板块池：concept,industry,sub,sw 逗号分隔，默认 concept,industry")
    parser.add_argument("--top", type=int, default=10, help="每段列出的板块数，默认 10")
    parser.add_argument("--top-rank", type=int, default=10, help="当日涨幅前 N 名算上榜，默认 10")
    parser.add_argument("--min-members", type=int, default=8, help="板块最少成分股数，默认 8")
    parser.add_argument("--lead", type=int, default=10, help="为前 N 个板块识别龙头，默认 10")
    parser.add_argument("--out", default="mainline_out", help="输出目录")
    parser.add_argument("--no-cache", action="store_true", help="不用缓存，全部重新拉取")
    parser.add_argument("--report", action="store_true", help="把控制台报告另存为 Markdown，便于归档")
    args = parser.parse_args()

    kinds = [k.strip() for k in args.universe.split(",") if k.strip()]
    unknown = [k for k in kinds if k not in UNIVERSE_LABEL]
    if unknown:
        raise SystemExit(f"未知板块池 {unknown}，可选：{list(UNIVERSE_LABEL)}")

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = None if args.no_cache else out_dir / ".cache"

    client = TushareClient(load_token(), cache_dir)
    need = max(args.lookback, 20, args.recent) + 2
    days = trading_days(client, args.asof, need)
    asof = days[-1]
    print(f"拉取数据中：{len(days)} 个交易日，板块池 {','.join(kinds)}（首次较慢，之后走缓存）")

    universe = build_universe(client, kinds, args.min_members)
    panels = collect_panels(client, days)
    incomplete = [
        name
        for name in ("ths", "stock")
        if panels[name].empty or asof not in set(panels[name]["trade_date"])
    ]
    if incomplete:
        raise SystemExit(
            f"{asof} 当日行情尚未就绪（缺少 {', '.join(incomplete)}），本次不生成、不发布。"
        )
    metrics = compute_metrics(panels, universe, days, args.lookback, args.recent, args.top_rank)
    mainlines, watch = classify(metrics, args.lookback)

    lead_codes = list(mainlines.head(args.lead).index)
    watch_codes = list(watch.head(args.lead).index)
    leaders = find_leaders(client, panels, days, args.lookback, args.recent, lead_codes, horizon="lookback")
    watch_leaders = find_leaders(client, panels, days, args.lookback, args.recent, watch_codes, horizon="recent")
    history_codes = list(dict.fromkeys(lead_codes + watch_codes))
    cycle_history = build_cycle_history(
        client, panels, days, history_codes, leaders, watch_leaders
    )

    background = market_background(panels, days)

    metrics_out = metrics.copy()
    metrics_out.index.name = "ts_code"
    columns = ["name", "kind", "members", "ret_lookback", "rank_days", "concentration",
               "ret_recent", "recent_rank_pct", "vol_ratio", "share_delta", "pos_pct"]
    tag = "-".join(kinds)
    metrics_path = out_dir / f"{asof}_{tag}_sector_metrics.csv"
    summary_path = out_dir / f"{asof}_{tag}_summary.json"
    metrics_out[columns].sort_values("ret_lookback", ascending=False).round(4).to_csv(
        metrics_path, encoding="utf-8-sig"
    )

    summary = {
        "asof": asof,
        "window": {"start": days[-args.lookback], "end": asof, "days": args.lookback},
        "recent_window": {"start": days[-args.recent], "end": asof, "days": args.recent},
        "universe": kinds,
        "sector_count": int(len(universe)),
        "background": background,
        "mainlines": json.loads(mainlines.head(args.top).reset_index().round(4)
                                .to_json(orient="records", force_ascii=False)),
        "watch": json.loads(watch.head(args.top).reset_index().round(4)
                            .to_json(orient="records", force_ascii=False)),
        "leaders": {
            "started": {metrics.loc[c, "name"]: v for c, v in leaders.items() if c in metrics.index},
            "potential": {metrics.loc[c, "name"]: v for c, v in watch_leaders.items() if c in metrics.index},
        },
        "cycle_history": cycle_history,
        "failures": client.failures,
        "api_calls": client.calls,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = out_dir / f"{asof}_{tag}_report.md" if args.report else None
    print_report(asof, days[-args.lookback:], universe, background, mainlines, watch, leaders,
                 watch_leaders, args.recent, args.top, client.failures, client.calls, report_path)
    print(f"明细：{metrics_path}")
    print(f"结论：{summary_path}")
    if report_path is not None:
        print(f"报告：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
