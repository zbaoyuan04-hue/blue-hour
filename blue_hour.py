#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蓝调时刻提醒：每天算一次"今天几点天会变蓝"，到点推到微信。

  python blue_hour.py --preview   只打印，不发
  python blue_hour.py --test      立刻发一条（测试用）
  python blue_hour.py             定时模式：命中提醒窗口才发

改城市只改下面 CONFIG 里的 lat / lon 两行。
"""

import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

CONFIG = {
    "city": "辽源",
    "lat": 42.888,
    "lon": 125.133,
    "tz": "Asia/Shanghai",
    "blue_start_deg": -4.0,   # 太阳落到 -4°，蓝调开始
    "blue_end_deg": -6.0,     # 太阳落到 -6°，蓝调结束
    "lead_minutes": 20,       # 提前多少分钟提醒
    "window_minutes": 15,     # 命中窗口（云端会延迟，留够余量）
}

for _k, _env in (("lat", "BH_LAT"), ("lon", "BH_LON"), ("tz", "BH_TZ")):
    if os.environ.get(_env):
        CONFIG[_k] = os.environ[_env] if _k == "tz" else float(os.environ[_env])


# ---------- 太阳高度角（NOAA 算法，误差 < 0.5°） ----------

def _julian_day(dt):
    y, m = dt.year, dt.month
    d = dt.day + (dt.hour + dt.minute / 60 + dt.second / 3600) / 24
    if m <= 2:
        y, m = y - 1, m + 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def elevation(dt_utc, lat, lon):
    jd = _julian_day(dt_utc)
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    mr = math.radians(m)
    c = (math.sin(mr) * (1.914602 - t * (0.004817 + 0.000014 * t))
         + math.sin(2 * mr) * (0.019993 - 0.000101 * t)
         + math.sin(3 * mr) * 0.000289)
    lam = l0 + c - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t))
    eps = (23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
           + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t)))
    decl = math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam)))
    y = math.tan(math.radians(eps / 2)) ** 2
    eqtime = 4 * math.degrees(
        y * math.sin(2 * math.radians(l0)) - 2 * e * math.sin(mr)
        + 4 * e * y * math.sin(mr) * math.cos(2 * math.radians(l0))
        - 0.5 * y * y * math.sin(4 * math.radians(l0)) - 1.25 * e * e * math.sin(2 * mr))
    ha = (dt_utc.hour * 60 + dt_utc.minute + dt_utc.second / 60 + eqtime + 4 * lon) / 4 - 180
    cos_zen = (math.sin(math.radians(lat)) * math.sin(decl)
               + math.cos(math.radians(lat)) * math.cos(decl) * math.cos(math.radians(ha)))
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))


def _cross(utc_start, lat, lon, target):
    """太阳高度角向下穿过 target 的时刻（UTC）。"""
    step = timedelta(seconds=30)
    t = utc_start + timedelta(hours=12)
    prev = elevation(t, lat, lon)
    end = utc_start + timedelta(hours=30)
    while t < end:
        nxt = t + step
        cur = elevation(nxt, lat, lon)
        if prev > target >= cur:
            lo, hi = t, nxt
            for _ in range(18):
                mid = lo + (hi - lo) / 2
                lo, hi = (mid, hi) if elevation(mid, lat, lon) > target else (lo, mid)
            return lo
        t, prev = nxt, cur
    return None


def events(day):
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(CONFIG["tz"])
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    utc0 = start.astimezone(ZoneInfo("UTC"))
    f = lambda d: _cross(utc0, CONFIG["lat"], CONFIG["lon"], d)
    out = [f(0.0), f(CONFIG["blue_start_deg"]), f(CONFIG["blue_end_deg"])]
    return [x.astimezone(tz) if x else None for x in out]


# ---------- 推送 ----------

def send(title, body):
    key = os.environ.get("SCT_KEY", "").strip()
    if not key:
        print("!! 缺少 SCT_KEY", file=sys.stderr)
        return False
    data = urllib.parse.urlencode({"title": title, "desp": body}).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://sctapi.ftqq.com/{key}.send", data=data)
            with urllib.request.urlopen(req, timeout=25) as r:
                print("Server酱:", r.read().decode("utf-8", "replace")[:200])
            return True
        except Exception as exc:                     # noqa: BLE001
            print(f"第 {attempt + 1} 次失败: {exc}", file=sys.stderr)
            time.sleep(5)
    return False


def build(sunset, b0, b1):
    h = lambda x: x.strftime("%H:%M")
    return "\n".join([
        f"**{CONFIG['city']} · 今日蓝调时刻**",
        "",
        f"日落　　{h(sunset)}",
        f"蓝调　　{h(b0)} — {h(b1)}",
        "",
        f"现在出发，{CONFIG['lead_minutes']} 分钟后正好赶上。",
    ])


def main():
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(CONFIG["tz"])
    now = datetime.now(tz)
    sunset, b0, b1 = events(now.date())
    if not (sunset and b0 and b1):
        print("今天算不出蓝调时刻")
        return 0

    alert = b0 - timedelta(minutes=CONFIG["lead_minutes"])
    body = build(sunset, b0, b1)
    print(f"{now.date()} 日落 {sunset:%H:%M} 蓝调 {b0:%H:%M}-{b1:%H:%M} 提醒 {alert:%H:%M} 现在 {now:%H:%M}")

    if "--preview" in sys.argv:
        print("\n" + body)
        return 0
    if "--test" in sys.argv:
        return 0 if send(f"{CONFIG['city']}蓝调时刻（测试）", body) else 1

    if alert <= now < alert + timedelta(minutes=CONFIG["window_minutes"]):
        if send(f"{CONFIG['city']} · 今天 {b0:%H:%M} 蓝调", body):
            os.makedirs(".sent", exist_ok=True)
            open(f".sent/{now:%Y-%m-%d}", "w").close()
            return 0
        return 1

    print("未到时间，跳过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
