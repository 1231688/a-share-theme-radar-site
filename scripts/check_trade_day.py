from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mainline_scan import TushareClient, load_token


def main() -> None:
    requested = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    asof = requested or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    datetime.strptime(asof, "%Y%m%d")

    client = TushareClient(load_token(), cache_dir=None, pause=0)
    calendar = client.call(
        "trade_cal",
        {"exchange": "SSE", "start_date": asof, "end_date": asof},
        "cal_date,is_open",
    )
    if calendar.empty:
        for failure in client.failures:
            print(f"交易日历接口返回：{failure}", file=sys.stderr)
        raise SystemExit(f"无法核对 {asof} 的交易日历，本次不发布。")

    matched = calendar[calendar["cal_date"].astype(str) == asof]
    is_open = not matched.empty and str(matched.iloc[0]["is_open"]) == "1"
    print(f"{asof} 是交易日，继续更新。" if is_open else f"{asof} 非交易日，跳过本次更新。")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"should_run={'true' if is_open else 'false'}\n")
            stream.write(f"asof={asof}\n")


if __name__ == "__main__":
    main()
