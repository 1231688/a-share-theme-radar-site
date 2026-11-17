from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone


def fetch(url: str, timeout: int = 30) -> bytes:
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{separator}cloud_verify={time.time_ns()}",
        headers={"Cache-Control": "no-cache", "User-Agent": "theme-radar-release-verifier/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} 返回 HTTP {response.status}")
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description="回读公网版本并核验数据日期与题材周期")
    parser.add_argument("--url", required=True)
    parser.add_argument("--asof", required=True)
    args = parser.parse_args()
    base = args.url.rstrip("/")
    expected_suffix = f"{args.asof[4:6]}/{args.asof[6:8]}"

    deadline = time.monotonic() + 150
    last_error = None
    payload = None
    while time.monotonic() < deadline:
        try:
            candidate = json.loads(fetch(f"{base}/data/latest.json").decode("utf-8"))
            if candidate.get("meta", {}).get("asof") != args.asof:
                raise RuntimeError("公网 latest.json 仍是旧日期")
            themes = candidate.get("themes", [])
            if not themes:
                raise RuntimeError("公网版本没有题材数据")
            for theme in themes:
                cycle = theme.get("cycle", {})
                dates = cycle.get("dates", [])
                if len(dates) != 15 or dates[-1] != expected_suffix:
                    raise RuntimeError(f"{theme.get('name')} 缺少完整的当日 15 日周期")
                if not cycle.get("cycles") or not cycle.get("stocks"):
                    raise RuntimeError(f"{theme.get('name')} 的周期详情不完整")
            payload = candidate
            break
        except Exception as exc:
            last_error = exc
            time.sleep(5)
    if payload is None:
        raise SystemExit(f"公网回读验证失败：{last_error}")

    index = fetch(f"{base}/index.html").decode("utf-8")
    app = fetch(f"{base}/app.js").decode("utf-8")
    styles = fetch(f"{base}/styles.css").decode("utf-8")
    if "A股题材轮动雷达" not in index or "./data/latest.json" not in app or len(styles) < 1000:
        raise SystemExit("公网静态资源验证失败。")

    print(json.dumps({
        "verified_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "url": base,
        "asof": args.asof,
        "generated_at": payload["meta"].get("generatedAt"),
        "themes": len(payload["themes"]),
        "cycles_per_theme": 15,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
