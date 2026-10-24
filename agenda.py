#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日程提醒：每天 8:00 和 20:00，把 events.txt 里快到期的事推到微信。

  python agenda.py --preview        只打印，不发
  python agenda.py --test           立刻发一条（测试）
  python agenda.py --slot morning   云端用：到点才发
"""

import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

CONFIG = {
    "tz": "Asia/Shanghai",
    "events": "events.txt",
    "lookahead_days": 30,
    "overdue_days": 14,
    "window_minutes": 20,
    "morning": (8, 0),
    "evening": (20, 0),
    "send_when_empty": False,
}

if os.environ.get("BH_EVENTS"):
    CONFIG["events"] = os.environ["BH_EVENTS"]

WEEK = "一二三四五六日"

# 2026年11月5日 / 2026-11-05 / 11-05 / 11月5日
RE_CN = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})?\s*(.*)$")
RE_FULL = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*(\d{1,2}:\d{2})?\s*(.*)$")
RE_SHORT = re.compile(r"^(\d{1,2})[-/.](\d{1,2})\s*(\d{1,2}:\d{2})?\s*(.*)$")
RE_SHORT_CN = re.compile(r"^(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})?\s*(.*)$")


def parse_line(line, today):
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    def make(y, m, d, clock, title):
        try:
            dt = date(int(y), int(m), int(d))
        except ValueError:
            return None
        return dt, clock, title.strip() or "（未写内容）"

    m = RE_CN.match(raw)
    if m:
        return make(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))

    m = RE_FULL.match(raw)
    if m:
        return make(m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))

    m = RE_SHORT_CN.match(raw)
    if m:
        y = today.year
        dt = make(y, m.group(1), m.group(2), m.group(3), m.group(4))
        if dt and (dt[0] - today).days < -180:
            dt = make(y + 1, m.group(1), m.group(2), m.group(3), m.group(4))
        return dt

    m = RE_SHORT.match(raw)
    if m:
        y = today.year
        dt = make(y, m.group(1), m.group(2), m.group(3), m.group(4))
        if dt and (dt[0] - today).days < -180:
            dt = make(y + 1, m.group(1), m.group(2), m.group(3), m.group(4))
        return dt

    return None


def load_events(today):
    path = CONFIG["events"]
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        items = [parse_line(ln, today) for ln in fh]
    return [x for x in items if x]


def build(slot, today, events):
    horizon = today + timedelta(days=CONFIG["lookahead_days"])
    horizon_start = today - timedelta(days=CONFIG["overdue_days"])

    buckets = {"overdue": [], "today": [], "tomorrow": [], "later": []}
    for dt, clock, title in sorted(events, key=lambda x: (x[0], x[1] or "")):
        delta = (dt - today).days
        label = f"{clock} {title}" if clock else title
        if dt < horizon_start or dt > horizon:
            continue
        if delta < 0:
            buckets["overdue"].append((delta, label))
        elif delta == 0:
            buckets["today"].append(label)
        elif delta == 1:
            buckets["tomorrow"].append(label)
        else:
            buckets["later"].append((delta, label))

    lines = []
    if buckets["today"]:
        lines.append("**今天**")
        lines += [f"· {x}" for x in buckets["today"]]
    if buckets["tomorrow"]:
        if lines:
            lines.append("")
        lines.append("**明天**")
        lines += [f"· {x}" for x in buckets["tomorrow"]]
    if buckets["later"]:
        if lines:
            lines.append("")
        lines.append("**之后**")
        lines += [f"· {x} — {d} 天后" for d, x in buckets["later"]]
    if buckets["overdue"]:
        if lines:
            lines.append("")
        lines.append("**已经过了，记得删掉**")
        lines += [f"· {x} — 已过 {-d} 天" for d, x in buckets["overdue"]]

    if not lines:
        if not CONFIG["send_when_empty"]:
            return None
        return f"**{slot} · {today:%m月%d日}**\n\n未来 {CONFIG['lookahead_days']} 天没有要提醒的事。"

    head = f"**{'早间' if slot == 'morning' else '晚间'}日程 · {today:%m月%d日} 周{WEEK[today.weekday()]}**"
    return head + "\n\n" + "\n".join(lines)


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


def current_slot(now):
    for name in ("morning", "evening"):
        h, m = CONFIG[name]
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= now < start + timedelta(minutes=CONFIG["window_minutes"]):
            return name
    return None


def main():
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(CONFIG["tz"])
    now = datetime.now(tz)
    today = now.date()

    slot = None
    for arg in sys.argv:
        if arg.startswith("--slot="):
            slot = arg.split("=", 1)[1]

    if "--test" in sys.argv:
        slot = slot or "morning"
        evs = load_events(today)
        keep = CONFIG["lookahead_days"]
        CONFIG["lookahead_days"] = 365          # 测试时把所有事都列出来
        body = build(slot, today, evs)
        if not body:
            demo = [(today, "09:30", "示例·晨会"), (today + timedelta(days=2), None, "示例·报名截止")]
            body = "（示例——events.txt 里还没有内容）\n\n" + build(slot, today, demo)
        CONFIG["lookahead_days"] = keep
        return 0 if send("日程提醒（测试）", body) else 1

    if "--preview" in sys.argv:
        slot = slot or current_slot(now) or "morning"
        body = build(slot, today, load_events(today))
        print(f"时段 {slot}　今天 {today}　事件 {len(load_events(today))} 条")
        print("-" * 30)
        print(body if body else "(这个时段没有要发的内容)")
        return 0

    slot = slot or current_slot(now)
    if not slot:
        print("不在提醒时段，跳过。")
        return 0

    body = build(slot, today, load_events(today))
    if not body:
        print("未来几天没有事件，不发。")
        return 0

    label = "早间" if slot == "morning" else "晚间"
    if send(f"{label}日程 · {today:%m月%d日}", body):
        os.makedirs(".sent", exist_ok=True)
        open(f".sent/agenda-{today}-{slot}", "w").close()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
