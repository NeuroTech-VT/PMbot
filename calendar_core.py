"""Calendar store, receipts, typed parsing and card bookkeeping.

Deliberately free of Discord imports: the cog is a front-end over this module,
and a Telegram adapter later can reuse the same store and parsing.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "calendar"
CACHE_DIR = DATA_DIR / "cache"
CARDS_FILE = DATA_DIR / "cards.json"

WDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WD_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MONTH_INDEX = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
               "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

REMINDER_MINUTES = 15
ALL_DAY_REMINDER = dtime(9, 0)


class CalendarError(ValueError):
    """Something the user can fix (unknown id, bad date, missing title)."""


# --------------------------------------------------------------------- storage
def scope_for(guild_id: int | None, user_id: int) -> str:
    """One shared calendar per server, one private calendar per DM user."""
    return f"guild_{guild_id}" if guild_id else f"dm_{user_id}"


def _path(scope: str) -> Path:
    safe = re.sub(r"[^a-z0-9_]+", "", scope.lower()) or "calendar"
    return DATA_DIR / f"{safe}.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load(scope: str) -> dict[str, Any]:
    path = _path(scope)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            data.setdefault("next_id", 1)
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"version": 1, "next_id": 1, "events": []}


def _save(scope: str, data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(scope)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def scopes() -> list[str]:
    if not DATA_DIR.exists():
        return []
    found = []
    for path in DATA_DIR.glob("*.json"):
        if path.name == "cards.json":
            continue
        found.append(path.stem)
    return found


# ------------------------------------------------------------------- date/time
TASK_STATES = {
    "open": ("⬜", "not started"),
    "on": ("🔄", "on it"),
    "blocked": ("🚫", "blocked"),
    "done": ("✅", "done"),
}


def task_emoji(state: str | None) -> str:
    return TASK_STATES.get(state or "open", TASK_STATES["open"])[0]


def task_state_label(state: str | None) -> str:
    return TASK_STATES.get(state or "open", TASK_STATES["open"])[1]


def today_local() -> date:
    return datetime.now().date()


def iso_date(day: date) -> str:
    return day.isoformat()


def add_days(day: date, amount: int) -> date:
    return day + timedelta(days=amount)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DMY = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?\b")
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
    re.I,
)
_MONTH_DAY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I,
)
_REL = re.compile(r"\b(?:\+|in\s+)?(\d{1,2})\s*([dw])\b", re.I)
_WEEKDAY = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?\b", re.I)
_WORD = re.compile(r"\b(today|tomorrow)\b", re.I)


def find_date(text: str, today: date | None = None) -> tuple[date, tuple[int, int]] | None:
    """Find one date in `text`, returning it and the matched span."""
    today = today or today_local()

    match = _ISO.search(text)
    if match:
        found = _safe_date(int(match[1]), int(match[2]), int(match[3]))
        if found:
            return found, match.span()

    for pattern, flip in ((_DAY_MONTH, False), (_MONTH_DAY, True)):
        match = pattern.search(text)
        if match:
            day, mon = (match[2], match[1]) if flip else (match[1], match[2])
            month = MONTH_INDEX[mon.lower()[:3]]
            found = _safe_date(today.year, month, int(day))
            if found and found < today - timedelta(days=180):
                found = _safe_date(today.year + 1, month, int(day))
            if found:
                return found, match.span()

    match = _DMY.search(text)
    if match:
        month, day = int(match[1]), int(match[2])
        year = int(match[3]) if match[3] else today.year
        if year < 100:
            year += 2000
        found = _safe_date(year, month, day)
        if found and not match[3] and found < today - timedelta(days=180):
            found = _safe_date(year + 1, month, day)
        if found:
            return found, match.span()

    match = _REL.search(text)
    if match:
        amount = int(match[1])
        unit = match[2].lower()
        return add_days(today, amount * (7 if unit == "w" else 1)), match.span()

    match = _WEEKDAY.search(text)
    if match:
        target = WD_INDEX[match[1].lower()[:3]]
        return add_days(today, (target - today.weekday()) % 7), match.span()

    match = _WORD.search(text)
    if match:
        return add_days(today, 0 if match[1].lower() == "today" else 1), match.span()

    return None


_TIME_RANGE = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?:\s*[-–]\s*(\d{1,2}):(\d{2}))?(?![\d:])")
_TIME_AMPM = re.compile(r"(?<![\d:])(\d{1,2})\s*(am|pm)\b", re.I)


def find_time(text: str) -> tuple[str, int | None, tuple[int, int]] | None:
    """Find a start time (and duration) in text: `13:15`, `9:00-9:30`, `2pm`."""
    match = _TIME_RANGE.search(text)
    if match:
        start_h, start_m = int(match[1]), int(match[2])
        end_h, end_m = (int(match[3]), int(match[4])) if match[3] else (None, None)
        if start_h > 23 or start_m > 59 or (end_h is not None and (end_h > 23 or end_m > 59)):
            return None
        duration = None
        if end_h is not None:
            duration = (end_h * 60 + end_m) - (start_h * 60 + start_m)
            if duration <= 0:
                duration = None
        return f"{start_h:02d}:{start_m:02d}", duration, match.span()

    match = _TIME_AMPM.search(text)
    if match:
        hour = int(match[1])
        if hour > 12:
            return None
        if match[2].lower() == "pm" and hour < 12:
            hour += 12
        if match[2].lower() == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:00", None, match.span()

    return None


def _parse_clock(value: str) -> str:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value))
    if not match:
        raise CalendarError(f"bad time {value!r}, use HH:MM")
    hour, minute = int(match[1]), int(match[2])
    if hour > 23 or minute > 59:
        raise CalendarError(f"bad time {value!r}, use HH:MM")
    return f"{hour:02d}:{minute:02d}"


def _parse_iso_date(value: str | None) -> str:
    if not value:
        raise CalendarError("this event needs a date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise CalendarError(f"bad date {value!r}, use YYYY-MM-DD") from exc


def _add_minutes(clock: str, minutes: int) -> str:
    hour, minute = (int(part) for part in clock.split(":"))
    total = (hour * 60 + minute + minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


# ---------------------------------------------------------------------- events
def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def add(
    scope: str,
    title: str,
    when: str,
    time: str | None = None,
    duration: int | None = None,
    place: str | None = None,
    notes: str | None = None,
    created_by: int | None = None,
    channel_id: int | None = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise CalendarError("the event needs a title")

    data = _load(scope)
    event = {
        "id": int(data.get("next_id", 1)),
        "title": title,
        "date": _parse_iso_date(when),
        "time": _parse_clock(time) if time else None,
        "duration": int(duration) if duration else None,
        "place": _clean(place),
        "notes": _clean(notes),
        "done": False,
        "created_by": created_by,
        "channel_id": channel_id,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "reminded": False,
        "snooze_until": None,
    }
    data["next_id"] = event["id"] + 1
    data["events"].append(event)
    _save(scope, data)
    return event


def _find(data: dict[str, Any], event_id: Any) -> dict[str, Any]:
    try:
        wanted = int(event_id)
    except (TypeError, ValueError) as exc:
        raise CalendarError(f"bad event id {event_id!r}") from exc
    for event in data["events"]:
        if event["id"] == wanted:
            return event
    raise CalendarError(f"no event with id {wanted}")


def update(scope: str, event_id: Any, **fields: Any) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    data = _load(scope)
    event = _find(data, event_id)
    before = dict(event)
    changed: list[str] = []

    if "title" in fields:
        title = _clean(fields["title"])
        if not title:
            raise CalendarError("the title cannot be empty")
        if title != event["title"]:
            event["title"] = title
            changed.append("title")
    if "date" in fields:
        when = _parse_iso_date(str(fields["date"]))
        if when != event["date"]:
            event["date"] = when
            changed.append("date")
    if "time" in fields:
        raw = fields["time"]
        clock = _parse_clock(raw) if raw else None
        if clock != event["time"]:
            event["time"] = clock
            changed.append("time")
    if "duration" in fields:
        raw = fields["duration"]
        duration = int(raw) if raw else None
        if duration and not 0 < duration <= 24 * 60:
            raise CalendarError("duration must be between 1 and 1440 minutes")
        if duration != event["duration"]:
            event["duration"] = duration
            changed.append("duration")
    for key in ("place", "notes"):
        if key in fields:
            value = _clean(fields[key])
            if value != event[key]:
                event[key] = value
                changed.append(key)
    if "done" in fields:
        done = bool(fields["done"])
        if done != event["done"]:
            event["done"] = done
            changed.append("done")

    if changed:
        event["updated_at"] = _now_iso()
        if {"date", "time", "title", "place"} & set(changed):
            event["reminded"] = False
            event["snooze_until"] = None
        _save(scope, data)
    return event, changed, before


def remove(scope: str, event_id: Any) -> dict[str, Any]:
    data = _load(scope)
    event = _find(data, event_id)
    data["events"] = [item for item in data["events"] if item["id"] != event["id"]]
    _save(scope, data)
    return event


def restore(scope: str, event: dict[str, Any]) -> dict[str, Any]:
    data = _load(scope)
    if any(item["id"] == event["id"] for item in data["events"]):
        event = dict(event, id=int(data.get("next_id", 1)))
        data["next_id"] = int(data["next_id"]) + 1
    event = dict(event, reminded=False, snooze_until=None, updated_at=_now_iso())
    data["events"].append(event)
    _save(scope, data)
    return event


def snooze(scope: str, event_id: Any, minutes: int = 10) -> None:
    data = _load(scope)
    event = _find(data, event_id)
    event["snooze_until"] = (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    event["reminded"] = False
    _save(scope, data)


def events(scope: str) -> list[dict[str, Any]]:
    return list(_load(scope)["events"])


def month_events(scope: str, year: int, month: int) -> list[dict[str, Any]]:
    prefix = f"{year:04d}-{month:02d}"
    found = [event for event in events(scope) if str(event["date"]).startswith(prefix)]
    return sorted(found, key=lambda ev: (ev["date"], ev["time"] or "", ev["title"]))


def day_events(scope: str, day: date, include_done: bool = True) -> list[dict[str, Any]]:
    found = [
        event for event in events(scope)
        if event["date"] == day.isoformat() and (include_done or not event["done"])
    ]
    return sorted(found, key=lambda ev: (ev["time"] or "", ev["title"]))


def upcoming(scope: str, days: int = 120, limit: int = 120) -> list[dict[str, Any]]:
    today = today_local()
    end = today + timedelta(days=days)
    found = [
        event for event in events(scope)
        if not event["done"] and today <= date.fromisoformat(event["date"]) <= end
    ]
    found.sort(key=lambda ev: (ev["date"], ev["time"] or "", ev["title"]))
    return found[:limit]


def overdue(scope: str) -> list[dict[str, Any]]:
    today = today_local()
    found = [
        event for event in events(scope)
        if not event["done"] and date.fromisoformat(event["date"]) < today
    ]
    found.sort(key=lambda ev: (ev["date"], ev["time"] or ""))
    return found


# ------------------------------------------------------------------ reminders
def reminder_at(event: dict[str, Any]) -> datetime | None:
    if event.get("done"):
        return None
    if event.get("snooze_until"):
        try:
            return datetime.fromisoformat(event["snooze_until"])
        except ValueError:
            return None
    day = date.fromisoformat(event["date"])
    if event.get("time"):
        hour, minute = (int(part) for part in event["time"].split(":"))
        return datetime.combine(day, dtime(hour, minute)) - timedelta(minutes=REMINDER_MINUTES)
    return datetime.combine(day, ALL_DAY_REMINDER)


def event_start(event: dict[str, Any]) -> datetime:
    day = date.fromisoformat(event["date"])
    if event.get("time"):
        hour, minute = (int(part) for part in event["time"].split(":"))
        return datetime.combine(day, dtime(hour, minute))
    return datetime.combine(day, dtime(0, 0))


def due_reminders(now: datetime | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Events whose reminder time has arrived, skipping ones the bot slept through."""
    now = now or datetime.now()
    due: list[tuple[str, dict[str, Any]]] = []
    for scope in scopes():
        data = _load(scope)
        dirty = False
        for event in data["events"]:
            if event.get("reminded") or event.get("done"):
                continue
            at = reminder_at(event)
            if at is None or at > now:
                continue
            start = event_start(event)
            stale_after = start + timedelta(hours=2) if event.get("time") else start + timedelta(days=1)
            if now > stale_after:
                event["reminded"] = True
                dirty = True
                continue
            due.append((scope, event))
        if dirty:
            _save(scope, data)
    return due


def mark_reminded(scope: str, event_id: int) -> None:
    data = _load(scope)
    for event in data["events"]:
        if event["id"] == event_id:
            event["reminded"] = True
            break
    _save(scope, data)


# ------------------------------------------------------------------- receipts
def when_text(event: dict[str, Any], today: date | None = None) -> str:
    today = today or today_local()
    day = date.fromisoformat(event["date"])
    label = f"{WDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"
    if day.year != today.year:
        label += f" {day.year}"
    if not event.get("time"):
        return f"{label}, all day"
    clock = event["time"]
    if event.get("duration"):
        clock = f"{clock}–{_add_minutes(clock, int(event['duration']))}"
    return f"{label}, {clock}"


def _place_suffix(event: dict[str, Any]) -> str:
    return f" · {event['place']}" if event.get("place") else ""


def _reminder_line(event: dict[str, Any]) -> str:
    if event.get("done"):
        return ""
    if event.get("time"):
        return "DM 15 min before"
    return "DM at 09:00"


def add_receipt(event: dict[str, Any]) -> str:
    text = f"Added **{event['title']}** · {when_text(event)}{_place_suffix(event)}"
    reminder = _reminder_line(event)
    return f"{text}\n{reminder}" if reminder else text


def update_receipt(event: dict[str, Any], changed: list[str], before: dict[str, Any]) -> str:
    title = f"**{event['title']}**"
    if changed == ["title"]:
        return f"Renamed **{before['title']}** to {title}"
    if set(changed) <= {"date", "time"}:
        return f"Moved {title} to {when_text(event)}"
    parts: list[str] = []
    if "title" in changed:
        parts.append(f"renamed from **{before['title']}**")
    if "date" in changed or "time" in changed:
        parts.append(when_text(event))
    if "duration" in changed:
        parts.append(f"{event['duration']} min" if event.get("duration") else "duration removed")
    if "place" in changed:
        parts.append(event["place"] if event.get("place") else "place removed")
    if "notes" in changed:
        parts.append("notes updated")
    if "done" in changed:
        parts.append("done" if event["done"] else "reopened")
    return f"Updated {title} · " + " · ".join(parts)


def remove_receipt(event: dict[str, Any]) -> str:
    return f"Removed **{event['title']}** · {when_text(event)}{_place_suffix(event)}"


# ------------------------------------------------------- commands (typed path)
_PLACE = re.compile(r"(?:^|\s)@\s*(.+?)\s*$")


def parse_add(text: str, today: date | None = None) -> dict[str, Any] | None:
    """`Lunch fri 12:30 @ Café Nord` -> event fields, or None when there is no date."""
    today = today or today_local()
    spans: list[tuple[int, int]] = []

    date_hit = find_date(text, today)
    if not date_hit:
        return None
    when, span = date_hit
    spans.append(span)

    time_hit = find_time(text)
    clock, duration = (time_hit[0], time_hit[1]) if time_hit else (None, None)
    if time_hit:
        spans.append(time_hit[2])

    place = None
    match = _PLACE.search(text)
    if match:
        place = match.group(1).strip()
        spans.append(match.span(0))

    title = text
    for start, end in sorted(spans, reverse=True):
        title = title[:start] + " " + title[end:]
    title = re.sub(r"\s+", " ", title).strip(" ,-")
    if not title:
        return None
    return {"title": title, "date": iso_date(when), "time": clock, "duration": duration, "place": place}


def find_remove(scope: str, text: str, today: date | None = None) -> list[dict[str, Any]]:
    """Match events by day and/or words: `fri lunch`, `dentist`, `3 dec`."""
    today = today or today_local()
    when = None
    query = text.strip()
    date_hit = find_date(text, today)
    if date_hit:
        when = date_hit[0]
        query = (text[: date_hit[1][0]] + " " + text[date_hit[1][1]:]).strip()
    query = re.sub(r"\s+", " ", query).strip().lower()

    found = []
    for event in events(scope):
        if when and event["date"] != iso_date(when):
            continue
        if query and query not in event["title"].lower() and query not in (event.get("place") or "").lower():
            continue
        found.append(event)
    found.sort(key=lambda ev: (ev["date"], ev["time"] or "", ev["title"]))
    return found


def day_text(scope: str, day: date, tasks: list[dict] | None = None) -> str:
    found = day_events(scope, day)
    due = [task for task in (tasks or []) if task.get("date") == day.isoformat()]
    due.sort(key=lambda task: (task.get("title") or "").lower())
    label = f"**{WDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}**"
    if day.year != today_local().year:
        label += f" {day.year}"
    if not found and not due:
        return f"{label}\nNothing scheduled.\n`!cal add <what> {WDAYS[day.weekday()].lower()} 14:00`"
    bits = []
    if found:
        bits.append(f"{len(found)} {('event' if len(found) == 1 else 'events')}")
    if due:
        bits.append(f"{len(due)} {('task' if len(due) == 1 else 'tasks')} due")
    lines = [f"{label} · {' · '.join(bits)}"]
    for event in found:
        clock = event["time"] or "all day"
        if event.get("duration"):
            clock = f"{event['time']}–{_add_minutes(event['time'], int(event['duration']))}"
        line = f"**{clock}** {event['title']}"
        if event.get("place"):
            line += f" · {event['place']}"
        if event.get("done"):
            line += " ✓"
        lines.append(line)
    for task in due:
        line = f"{task_emoji(task.get('state'))} {task['title']}"
        if task.get("owner"):
            line += f" · {task['owner']}"
        lines.append(line)
    lines.append("`!cal` for the month")
    return "\n".join(lines)


# ---------------------------------------------------------------- channel cards
def load_cards() -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cards(cards: dict[str, dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CARDS_FILE.write_text(json.dumps(cards, indent=2) + "\n", encoding="utf-8")
