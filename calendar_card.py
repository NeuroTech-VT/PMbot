"""Render one month of the calendar as the PNG card the bot posts."""

from __future__ import annotations

import calendar
import hashlib
import html
import os
import re
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path

import calendar_core as core

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_FILE = BASE_DIR / "calendar_card.html"
CARD_DIR = core.CACHE_DIR
BROWSER = os.getenv("CALENDAR_BROWSER", "librewolf")
SCALE = 2
CACHE_DAYS = 14

MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December")


def _grid_height(rows: int) -> int:
    return rows * 40 + (rows - 1) * 5


def card_height(rows: int, lines: int = 1) -> int:
    # padding 28 + header 40 + week 19 + grid + footer 37
    return 124 + _grid_height(rows) + (lines - 1) * 15


def _short(text: str, limit: int = 28) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _time_label(event: dict) -> str:
    if not event.get("time"):
        return "all day"
    if event.get("duration"):
        end = (int(event["time"][:2]) * 60 + int(event["time"][3:]) + int(event["duration"])) % (24 * 60)
        return f"{event['time']}–{end // 60:02d}:{end % 60:02d}"
    return event["time"]


def build(scope: str, year: int, month: int, tasks: list[dict] | None = None) -> tuple[str, int]:
    events = core.month_events(scope, year, month)
    prefix = f"{year:04d}-{month:02d}-"
    tasks = [task for task in (tasks or []) if str(task.get("date") or "").startswith(prefix)]
    today = core.today_local()
    by_day: dict[str, list[dict]] = {}
    for event in events:
        by_day.setdefault(event["date"], []).append(event)
    tasks_by_day: dict[str, list[dict]] = {}
    for task in tasks:
        tasks_by_day.setdefault(task["date"], []).append(task)

    first = date(year, month, 1)
    start_col = first.weekday()  # Monday = 0
    days_in_month = calendar.monthrange(year, month)[1]
    used = start_col + days_in_month
    rows = (used + 6) // 7

    cells: list[str] = []
    for _ in range(start_col):
        cells.append('<div class="cell blank"></div>')
    for day in range(1, days_in_month + 1):
        key = f"{year:04d}-{month:02d}-{day:02d}"
        day_events = by_day.get(key, [])
        day_tasks = tasks_by_day.get(key, [])
        is_today = (year, month, day) == (today.year, today.month, today.day)
        marks = ("✱" * min(len(day_events), 3) + "".join(
            core.task_emoji(task.get("state")) for task in day_tasks[:3]
        ))[:4]
        mark_html = f'<span class="marks">{marks}</span>' if marks else ""
        classes = "cell today" if is_today else "cell"
        cells.append(f'<div class="{classes}"><span class="num">{day}</span>{mark_html}</div>')
    for _ in range(rows * 7 - used):
        cells.append('<div class="cell blank"></div>')

    foot_lines: list[str] = []
    if (year, month) == (today.year, today.month):
        todays = [ev for ev in core.day_events(scope, today) if not ev["done"]]
        day_tasks = tasks_by_day.get(today.isoformat(), [])
        items = [
            f"{_time_label(ev).split('–')[0]} <b>{html.escape(ev['title'])}</b>" for ev in todays
        ]
        items += [
            f"{core.task_emoji(task.get('state'))} <b>{html.escape(' '.join(task['title'].split()))}</b>"
            for task in day_tasks
        ]
        foot_lines = ["<b>today</b>:"] + items if items else ["<b>today</b>: nothing scheduled"]
    elif events or tasks:
        by_date: dict[date, list[tuple[int, str, str]]] = {}
        for event in events:
            day = date.fromisoformat(event["date"])
            by_date.setdefault(day, []).append((0, event["time"] or "99:99", html.escape(event["title"])))
        for task in tasks:
            try:
                day = date.fromisoformat(task["date"])
            except ValueError:
                continue
            by_date.setdefault(day, []).append(
                (1, "", f"{core.task_emoji(task.get('state'))} " + html.escape(_short(task["title"], 24)))
            )
        total = sum(len(items) for items in by_date.values())
        parts = []
        for day in sorted(by_date)[:2]:
            label = sorted(by_date[day])[0][2]
            parts.append(f"<b>{day.day} {core.MONTHS[day.month - 1]}</b> {label}")
        if total > 2:
            parts.append(f"+{total - 2}")
        foot_lines = [", ".join(parts)]
    else:
        foot_lines = ["nothing scheduled"]

    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    doc = (
        template.replace("{{TITLE}}", f"{MONTH_NAMES[month - 1]} {year}")
        .replace("{{CELLS}}", "".join(cells))
        .replace("{{FOOT}}", "".join(f'<div class="fline">{line}</div>' for line in foot_lines))
    )
    return doc, card_height(rows, len(foot_lines))


def _prune() -> None:
    if not CARD_DIR.exists():
        return
    cutoff = time.time() - CACHE_DAYS * 86_400
    for path in CARD_DIR.glob("*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def render(scope: str, year: int, month: int, tasks: list[dict] | None = None) -> Path:
    doc, height = build(scope, year, month, tasks)
    digest = hashlib.sha1(doc.encode("utf-8")).hexdigest()[:12]
    safe_scope = re.sub(r"[^a-z0-9_]+", "", scope.lower()) or "calendar"
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    target = CARD_DIR / f"{safe_scope}-{year:04d}-{month:02d}-{digest}.png"
    if target.exists() and target.stat().st_size > 0:
        return target

    _prune()

    with tempfile.TemporaryDirectory(prefix="cal-card-") as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(doc, encoding="utf-8")
        profile = Path(tmp) / "profile"
        profile.mkdir()
        cmd = [
            BROWSER,
            "--headless",
            "--no-remote",
            "--profile",
            str(profile),
            f"--window-size={550 * SCALE},{height * SCALE}",
            "--screenshot",
            str(target),
            page.as_uri(),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"calendar image failed: {exc}") from exc
        if not target.exists() or target.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-300:]
            raise RuntimeError(f"calendar image failed: {detail or 'no screenshot'}")

    return target


if __name__ == "__main__":  # quick manual check: python calendar_card.py 2026 11
    import sys

    scope = sys.argv[3] if len(sys.argv) > 3 else "guild_0"
    path = render(scope, int(sys.argv[1]), int(sys.argv[2]))
    print(path)
