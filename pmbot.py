"""PMbot: a project manager that lives in the Discord server.

The todo cog, loaded by `bot.py`.  One JSON file per server in `data/servers/`,
one todo message per project edited in place, four state buttons, one daily
check-in, two reminders per task ever, and an end of week note on Sunday evening.

Commands: `!todo`, `!add`, `!newproject`, `!settings`.
Deliberately not here: no model, no boards, no dashboards, no per-task threads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import discord
from discord.ext import commands, tasks

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "servers"
BACKUP_DIR = BASE_DIR / "data" / "backups"

MAX_ROWS = 25          # tasks rendered per list message
MAX_BUTTON_ROWS = 5    # four buttons per row => five tasks per message
KEEP_BACKUPS = 14
MAX_LISTS = 25         # channels a project keeps a live list in
TICK_SECONDS = 30
AUTODELETE = 90        # seconds before hints and confirmations tidy themselves

log = logging.getLogger("pmbot")

STATE_ICON = {"open": "⬜", "on": "🔄", "blocked": "🚫", "done": "✅"}
STATE_LABEL = {"open": "not started", "on": "on it", "blocked": "blocked", "done": "done"}
BUTTONS = (
    ("done", "✅ Done", discord.ButtonStyle.success),
    ("on", "🔄 On it", discord.ButtonStyle.primary),
    ("blocked", "🚫 Blocked", discord.ButtonStyle.danger),
    ("open", "⬜ Not started", discord.ButtonStyle.secondary),
)
WDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WD_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MONTH_INDEX = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
               "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


# --------------------------------------------------------------------- storage
_servers: dict[int, dict] = {}
_locks: dict[int, asyncio.Lock] = {}


def lock_for(guild_id: int) -> asyncio.Lock:
    return _locks.setdefault(guild_id, asyncio.Lock())


def default_tz() -> str:
    if os.getenv("PMBOT_TZ"):
        return os.environ["PMBOT_TZ"]
    try:
        name = Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    return name or "UTC"


def zone(name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001  (bad hand edit, unknown tz)
        log.warning("unknown timezone %r, falling back to UTC", name)
        return ZoneInfo("UTC")


def new_server(tz: str) -> dict:
    return {
        "meta": {
            "tz": tz,
            "checkin": "09:30",
            "note": "sun 18:00",
            "staff_can_create": False,
            "channel": None,
        },
        "version": 1,
        "projects": [],
        "tasks": [],
        "runs": {"checkin": {}, "note": None, "backup": None},
    }


def server(guild_id: int) -> dict:
    """Load (once) and normalise the JSON file for a server."""
    if guild_id in _servers:
        return _servers[guild_id]

    path = DATA_DIR / f"{guild_id}.json"
    data = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        log.exception("corrupt server file %s, starting from the .bak if there is one", path)
        try:
            data = json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None

    if not isinstance(data, dict) or "meta" not in data:
        data = new_server(default_tz())

    data.setdefault("version", 1)
    meta = data.setdefault("meta", {})
    meta.setdefault("tz", default_tz())
    meta.setdefault("checkin", "09:30")
    meta.setdefault("note", "sun 18:00")
    meta.setdefault("staff_can_create", False)
    meta.setdefault("channel", None)
    data.setdefault("projects", [])
    data.setdefault("tasks", [])
    runs = data.setdefault("runs", {})
    runs.setdefault("checkin", {})
    runs.setdefault("note", None)
    runs.setdefault("backup", None)

    # Projects used to be tied to a channel. A project is a server-wide thing now:
    # `lists` maps a channel to the list message posted there, `meta.channel` is
    # where check-ins, reminders and the note go.
    for project in data["projects"]:
        lists = project.setdefault("lists", {})
        old_channel = project.pop("channel_id", None)
        old_message = project.pop("todo_message_id", None)
        if old_channel and old_message:
            lists.setdefault(str(old_channel), str(old_message))
        if old_channel and not meta.get("channel"):
            meta["channel"] = str(old_channel)

    _servers[guild_id] = data
    return data


def save(guild_id: int) -> None:
    """Atomic write: temp file, fsync, keep the old copy as .bak, then replace."""
    data = _servers.get(guild_id)
    if data is None:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{guild_id}.json"
    tmp = path.with_name(f"{guild_id}.json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    if path.exists():
        shutil.copyfile(path, path.with_name(f"{guild_id}.json.bak"))
    os.replace(tmp, path)


async def asave(guild_id: int) -> None:
    """Write off the event loop: fsync is a few milliseconds of dead Discord time."""
    await asyncio.to_thread(save, guild_id)


def backup_all(today: date) -> Path | None:
    """Copy every server file into data/backups/<date>/, keep the newest 14."""
    if not DATA_DIR.exists():
        return None
    target = BACKUP_DIR / today.isoformat()
    target.mkdir(parents=True, exist_ok=True)
    for path in DATA_DIR.glob("*.json"):
        shutil.copyfile(path, target / path.name)
    old = sorted(p for p in BACKUP_DIR.iterdir() if p.is_dir())
    for stale in old[:-KEEP_BACKUPS]:
        shutil.rmtree(stale, ignore_errors=True)
    return target


# ----------------------------------------------------------------------- model
def home_channel(guild: discord.Guild, data: dict) -> discord.abc.GuildChannel | None:
    """Where PMbot speaks: `!settings channel`, else any channel it can write in."""
    wanted = data["meta"].get("channel")
    if wanted:
        channel = guild.get_channel(int(wanted)) if str(wanted).isdigit() else None
        if channel is not None:
            return channel
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            return channel
    return None


def project_by_ref(data: dict, ref: str | None) -> dict | None:
    if not ref:
        return None
    ref = ref.strip().strip("#")
    if ref.isdigit():
        for project in data["projects"]:
            if project["id"] == int(ref):
                return project
    low = ref.lower()
    for project in data["projects"]:
        if project["name"].lower() == low:
            return project
    for project in data["projects"]:
        if project["name"].lower().startswith(low):
            return project
    return None


def take_project_ref(data: dict, line: str) -> tuple[dict | None, str | None, str]:
    """`Halo: print the jig @sam fri` -> (Halo, "Halo", "print the jig @sam fri").

    The colon is what makes it safe: task text that merely starts with a project's
    name is left alone.
    """
    head, colon, tail = line.partition(":")
    if colon:
        project = project_by_ref(data, head)
        if project is not None and tail.strip():
            return project, head.strip(), tail.strip()
    return None, None, line


def tasks_of(data: dict, project_id: int) -> list[dict]:
    return [t for t in data["tasks"] if t.get("project") == project_id]


def next_id(items: list[dict]) -> int:
    return max((i["id"] for i in items), default=0) + 1


def remove_project(data: dict, project_id: int) -> tuple[dict | None, int]:
    """Drop a project and the tasks that belong to it. Returns (project, tasks removed).

    Tasks carry the project id, so nothing is left dangling; calendar events are
    not tied to projects and are untouched.
    """
    project = next((p for p in data["projects"] if p["id"] == project_id), None)
    if project is None:
        return None, 0
    doomed = [t for t in data["tasks"] if t.get("project") == project_id]
    data["tasks"] = [t for t in data["tasks"] if t.get("project") != project_id]
    data["projects"] = [p for p in data["projects"] if p["id"] != project_id]
    return project, len(doomed)


def counts(tasks: list[dict], today: date) -> tuple[int, int, int]:
    open_tasks = [t for t in tasks if t["state"] != "done"]
    done = len(tasks) - len(open_tasks)
    late = len([t for t in open_tasks if is_late(t, today)])
    return len(open_tasks), late, done


def is_late(task: dict, today: date) -> bool:
    if task["state"] == "done" or not task.get("due"):
        return False
    return date.fromisoformat(task["due"]) < today


def days_late(task: dict, today: date) -> int:
    return (today - date.fromisoformat(task["due"])).days if task.get("due") else 0


def at_risk(task: dict, today: date) -> bool:
    """Due within three days and not started, or blocked. Late is its own list."""
    if task["state"] == "done" or not task.get("due"):
        return False
    due = date.fromisoformat(task["due"])
    if due < today:
        return False
    if task["state"] == "blocked":
        return True
    return task["state"] == "open" and due <= today + timedelta(days=3)


def finished_since(tasks: list[dict], today: date, days: int = 7) -> list[dict]:
    floor = today - timedelta(days=days)
    out = []
    for task in tasks:
        if task["state"] != "done" or not task.get("done_at"):
            continue
        try:
            when = datetime.fromisoformat(task["done_at"]).date()
        except ValueError:
            continue
        if floor <= when <= today:
            out.append(task)
    return out


def owner_key(task: dict) -> str:
    return f"id:{task['owner_id']}" if task.get("owner_id") else f"name:{(task.get('owner') or '').lower()}"


def owner_tag(task: dict) -> str:
    return f"<@{task['owner_id']}>" if task.get("owner_id") else (task.get("owner") or "someone")


# ----------------------------------------------------------------------- dates
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_MONTH_DAY = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
# The "+"/"in" is not optional: a bare "3d" or "3D" is a measurement or a printer,
# not a date. Same for slash dates — "1/2 plywood" is a thickness.
_REL = re.compile(r"(?:\+|\bin\s+)(\d{1,2})\s*(d(?:ays?)?|w(?:eeks?|ks)?)\b", re.I)
_WEEKDAY = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)(?:day|sday|nesday|rsday|urday)?\b", re.I)
_WORD = re.compile(r"\b(today|tomorrow)\b", re.I)


def parse_when(text: str, today: date) -> tuple[date, str] | None:
    """Find one date in `text`. Returns (date, matched substring) or None.

    Understands 2026-12-05, 5 Dec, Dec 5, +3d, in 2 weeks, mon..sun, today and
    tomorrow. Deliberately does not understand bare `d/m`: "cut 1/2 plywood" and
    "8.5 mm" would otherwise become deadlines and lose the number from the title.
    """
    match = _ISO.search(text)
    if match:
        try:
            return date(int(match[1]), int(match[2]), int(match[3])), match.group(0)
        except ValueError:
            pass

    for pattern, flip in ((_DAY_MONTH, False), (_MONTH_DAY, True)):
        match = pattern.search(text)
        if match:
            day, mon = (match[2], match[1]) if flip else (match[1], match[2])
            month = MONTH_INDEX[mon.lower()[:3]]
            year = today.year
            try:
                candidate = date(year, month, int(day))
            except ValueError:
                continue
            if candidate < today - timedelta(days=180):  # "5 dec" said in january
                candidate = date(year + 1, month, int(day))
            return candidate, match.group(0)

    match = _REL.search(text)
    if match:
        amount = int(match[1])
        days = amount if match[2].lower().startswith("d") else amount * 7
        return today + timedelta(days=days), match.group(0)

    match = _WEEKDAY.search(text)
    if match:
        target = WD_INDEX[match[1].lower()[:3]]
        return today + timedelta(days=(target - today.weekday()) % 7), match.group(0)

    match = _WORD.search(text)
    if match:
        word = match[1].lower()
        return today + timedelta(days=0 if word == "today" else 1), match.group(0)

    return None


def parse_hhmm(spec: str | None) -> dtime | None:
    if not spec or str(spec).strip().lower() in {"off", "none", "no"}:
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(spec))
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2])
    if hour > 23 or minute > 59:
        return None
    return dtime(hour, minute)


def parse_note(spec: str | None) -> tuple[int, dtime] | None:
    """`sun 18:00` -> (weekday index, time). `off` disables the note."""
    if not spec or str(spec).strip().lower() in {"off", "none", "no"}:
        return None
    match = re.fullmatch(r"\s*([a-z]{3,9})\s+(\d{1,2}:\d{2})\s*", str(spec), re.I)
    if not match:
        return None
    day = WD_INDEX.get(match[1].lower()[:3])
    when = parse_hhmm(match[2])
    if day is None or when is None:
        return None
    return day, when


def fmt_day(day: date, today: date) -> str:
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return f"{WDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"


def fmt_week_span(today: date) -> str:
    start = today - timedelta(days=6)
    if start.month == today.month:
        return f"{start.day}–{today.day} {MONTHS[today.month - 1]}"
    return f"{start.day} {MONTHS[start.month - 1]} – {today.day} {MONTHS[today.month - 1]}"


def iso_to_date(task: dict) -> date | None:
    if not task.get("due"):
        return None
    try:
        return date.fromisoformat(task["due"])
    except ValueError:
        return None


def clip(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# -------------------------------------------------------------------- rendering
def task_line(task: dict, today: date) -> str:
    """One task as a small block: status, then owner and date on their own lines."""
    icon = STATE_ICON.get(task["state"], "⬜")
    block = [f"{icon} {task['text']}"]
    if task.get("owner"):
        block.append(f"**{task['owner']}**")
    due = iso_to_date(task)
    if due:
        when = fmt_day(due, today)
        if task["state"] != "done" and due < today:
            late = (today - due).days
            when += f", {late} day{'s' if late != 1 else ''} late"
        block.append(when)
    return "\n".join(block)


def fits(lines: list[str], block: str, budget: int = 1900) -> bool:
    """Does one more block still leave room for the footer inside a message?"""
    return len("\n".join(lines)) + len(block) + 120 <= budget


def ordered_open(tasks: list[dict], today: date) -> list[dict]:
    """Late first, then by date, undated last."""
    open_tasks = [t for t in tasks if t["state"] != "done"]
    return sorted(
        open_tasks,
        key=lambda t: (
            iso_to_date(t) is None,
            iso_to_date(t) or date.max,
            t["id"],
        ),
    )


def render_list(project: dict, tasks: list[dict], today: date) -> str:
    open_tasks, late, done = counts(tasks, today)
    head = [f"**{project['name']}** · {open_tasks} open"]
    if late:
        head.append(f"**{late} late**")
    if done:
        head.append(f"{done} done")

    lines = [" · ".join(head), ""]
    if not tasks:
        lines.append("_No tasks yet._")
        lines.append("")
        lines.append("`!add <task> @who <date>` to put the first one in.")
        return "\n".join(lines)

    shown = 0
    truncated = False
    late_tasks = [t for t in ordered_open(tasks, today) if is_late(t, today)]
    rest = [t for t in ordered_open(tasks, today) if not is_late(t, today)]
    dated = [t for t in rest if iso_to_date(t)]
    undated = [t for t in rest if not iso_to_date(t)]

    for title, group in (("Late", late_tasks), ("Open", dated), ("No date yet", undated)):
        if not group or truncated:
            continue
        headed = False
        for task in group:
            block = task_line(task, today)
            if shown >= MAX_ROWS or not fits(lines, block, 1900):
                truncated = True
                break
            if not headed:
                lines.append(f"**{title}**")
                headed = True
            lines.append(block)
            shown += 1
        if headed:
            lines.append("")

    hidden = open_tasks - shown
    if hidden > 0:
        lines.append(f"+{hidden} more, the daily check-in shows each person their own.")
        lines.append("")

    if done:
        lines.append(f"✅ {done} finished, kept out of the way.")
        lines.append("")

    if shown > MAX_BUTTON_ROWS:
        lines.append(f"Only the first {MAX_BUTTON_ROWS} rows carry buttons; the check-in taps each person's own.")
        lines.append("")
    lines.append("`!add <task> @who <date>` · `!tasks` ids · `!todo`")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def render_checkin(display_name: str, entries: list[tuple[str, dict]], today: date) -> str:
    """One person's open work, grouped by project. entries = [(project, task)]."""
    names = {name for name, _ in entries}
    lines = [f"**Daily check-in · {display_name}**", ""]
    shown = 0
    last_project = None
    for project, task in entries[:MAX_ROWS]:
        block = task_line(task, today)
        if not fits(lines, block, 1900):
            break
        if len(names) > 1 and project != last_project:
            lines.append(f"**{project}**")
            last_project = project
        lines.append(block)
        shown += 1
    if len(entries) > shown:
        lines.append(f"+{len(entries) - shown} more in `!todo`")
    if len(entries) > MAX_BUTTON_ROWS:
        lines.append(f"Buttons cover the first {MAX_BUTTON_ROWS}; `!tasks` lists the rest with ids.")
    lines.append("")
    lines.append("Tap a state for each row, or ignore this if nothing moved.")
    return "\n".join(lines)


def note_sections(project: dict, tasks: list[dict], today: date) -> list[str]:
    """One project's part of the end of week note."""
    finished = finished_since(tasks, today)
    slipped = [t for t in tasks if is_late(t, today)]
    risky = [t for t in tasks if at_risk(t, today)]
    week_end = today + timedelta(days=7)
    upcoming = sorted(
        (t for t in tasks
         if t["state"] != "done" and iso_to_date(t)
         and today < iso_to_date(t) <= week_end),
        key=lambda t: t["due"],
    )

    lines = [f"**{project['name']}**", ""]

    lines.append("**Finished**")
    if finished:
        for task in finished[:8]:
            lines.append(f"· {task['text']} · {task.get('owner') or 'someone'}")
        if len(finished) > 8:
            lines.append(f"+{len(finished) - 8} more")
    else:
        lines.append("· nothing closed this week")
    lines.append("")

    lines.append("**Slipped**")
    if slipped:
        for task in slipped[:5]:
            lines.append(
                f"· {task['text']} · {task.get('owner') or 'someone'}, "
                f"{days_late(task, today)} days late"
            )
    else:
        lines.append("· nothing slipped")
    lines.append("")

    lines.append("**At risk**")
    if risky:
        for task in risky[:5]:
            reason = "blocked" if task["state"] == "blocked" else "not started"
            lines.append(
                f"· {task['text']} · {reason}, due {fmt_day(iso_to_date(task), today)}"
            )
    else:
        lines.append("· nothing looks risky")
    lines.append("")

    lines.append("**Next week**")
    if upcoming:
        for task in upcoming[:8]:
            lines.append(
                f"· {WDAYS[iso_to_date(task).weekday()]} {task['text']} · "
                f"{task.get('owner') or 'someone'}"
            )
    else:
        lines.append("· nothing due, put next week in `!add`")

    return lines


def render_note(entries: list[tuple[dict, list[dict]]], today: date) -> str:
    """One note for the whole server, a section per project. entries = [(project, tasks)]."""
    finished = sum(len(finished_since(group, today)) for _, group in entries)
    slipped = sum(1 for _, group in entries for t in group if is_late(t, today))
    risky = sum(1 for _, group in entries for t in group if at_risk(t, today))
    head = [
        f"**End of week note · {fmt_week_span(today)}**",
        " · ".join([f"**{finished} finished**", f"**{slipped} slipped**", f"**{risky} at risk**"]),
        "",
    ]
    body: list[str] = []
    for project, group in entries:
        body.extend(note_sections(project, group, today))
        body.append("")
    body.append("`!todo <project>` for a full list.")
    return "\n".join(head + body)


def task_view(guild_id: int, tasks: list[dict]) -> discord.ui.View | None:
    """Up to five rows, four state buttons each, ids that survive a restart."""
    if not tasks:
        return None
    view = discord.ui.View(timeout=None)
    for row, task in enumerate(tasks[:MAX_BUTTON_ROWS]):
        for state, label, style in BUTTONS:
            view.add_item(
                discord.ui.Button(
                    style=style,
                    label=label,
                    custom_id=f"pm:t:{guild_id}:{task['id']}:{state}",
                    row=row,
                )
            )
    return view


class DeleteProjectView(discord.ui.View):
    """The delete confirmation. Only the admin who asked can press it."""

    def __init__(self, cog: "PMBot", guild_id: int, project: dict, tasks: int, author_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.guild_id = guild_id
        self.project = project
        self.tasks = tasks
        self.author_id = author_id

    async def _mine(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only whoever ran `!delproject` can confirm it.", ephemeral=True
            )
            return False
        if not PMBot.is_admin(interaction.user) and not server(self.guild_id)["meta"].get("staff_can_create"):
            await interaction.response.send_message(
                "Only an admin can delete a project.", ephemeral=True
            )
            return False
        return True

    async def _remove(self) -> str:
        project_id = self.project["id"]
        async with lock_for(self.guild_id):
            data = server(self.guild_id)
            project, removed = remove_project(data, project_id)
            if project is None:
                return "already gone"
            await asave(self.guild_id)

        for channel_id, message_id in (project.get("lists") or {}).items():
            try:
                channel = self.cog.bot.get_channel(int(channel_id))
                if channel is None:
                    continue
                message = await channel.fetch_message(int(message_id))
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                continue
        return f"{removed} task{'s' if removed != 1 else ''} deleted with it"

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._mine(interaction):
            return
        # Defer first: deleting the todo message and writing the file can take
        # longer than Discord's three second window for responding.
        await interaction.response.defer()
        name = self.project["name"]
        try:
            outcome = await self._remove()
        except Exception:  # noqa: BLE001  (tell the user instead of hanging)
            log.exception("deleting project %s failed", name)
            await interaction.edit_original_response(
                content=f"Could not delete **{name}**, nothing was changed.", view=None
            )
            return
        self.stop()
        await interaction.edit_original_response(
            content=f"**{name}** is gone · {outcome}", view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not await self._mine(interaction):
            return
        self.stop()
        await interaction.response.edit_message(content="Left it alone.", view=None)


# ------------------------------------------------------------------------- cog
class PMBot(commands.Cog):
    """Todo lists, check-ins, reminders and the Sunday note."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.ticker.start()

    async def cog_unload(self) -> None:
        self.ticker.cancel()

    # ------------------------------------------------------------- utilities
    async def say(self, ctx: commands.Context, text: str, keep: bool = False) -> None:
        message = await ctx.reply(text, mention_author=False)
        if not keep:
            asyncio.create_task(self._later_delete(message, AUTODELETE))

    @staticmethod
    async def _later_delete(message: discord.Message, delay: int) -> None:
        await asyncio.sleep(delay)
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    @staticmethod
    def is_admin(member: discord.Member) -> bool:
        perms = member.guild_permissions
        return bool(perms.administrator or perms.manage_guild)

    @classmethod
    def can_tap(cls, member: discord.Member, task: dict) -> bool:
        """Owner, or an admin. A task owned by a bare name (no id) is fair game."""
        owner_id = task.get("owner_id")
        if not owner_id:
            return True
        return str(member.id) == str(owner_id) or cls.is_admin(member)

    @staticmethod
    def resolve_project(data: dict, ref: str | None) -> dict | None:
        """What the user named, else the only project there is. Channels are not involved."""
        named = project_by_ref(data, ref)
        if named is not None:
            return named
        if (ref or "").strip():
            return None  # they named something that is not here; do not guess
        return data["projects"][0] if len(data["projects"]) == 1 else None

    async def refresh_lists(
        self, guild_id: int, project: dict, channel: discord.abc.Messageable | None
    ) -> None:
        """Refresh the project's list wherever it was already posted."""
        if channel is None:
            return
        message_id = project.get("lists", {}).get(str(getattr(channel, "id", "")))
        if message_id:
            await self.render_project(guild_id, project, channel)

    async def render_project(
        self, guild_id: int, project: dict, channel: discord.abc.Messageable,
        force_new: bool = False,
    ) -> discord.Message | None:
        """Post the list (force_new) or refresh the copy this project has in this channel."""
        data = server(guild_id)
        tasks = tasks_of(data, project["id"])
        today = datetime.now(zone(data["meta"]["tz"])).date()
        text = clip(render_list(project, tasks, today), 1990)
        view = task_view(guild_id, ordered_open(tasks, today))

        key = str(getattr(channel, "id", ""))
        lists = project.setdefault("lists", {})
        message_id = lists.get(key)

        if message_id and not force_new:
            try:
                message = await channel.fetch_message(int(message_id))
                await message.edit(content=text, view=view)
                return message
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                lists.pop(key, None)

        try:
            message = await channel.send(text, view=view)
        except discord.HTTPException as exc:
            log.warning("could not post todo list: %s", exc)
            return None

        lists[key] = str(message.id)
        while len(lists) > MAX_LISTS:  # oldest channel first
            lists.pop(next(iter(lists)))
        await asave(guild_id)
        return message

    # -------------------------------------------------------------- commands
    @commands.command(name="todo")
    async def todo(self, ctx: commands.Context, *, ref: str | None = None):
        """Show the project's todo list: !todo [project]"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)

        if not data["projects"]:
            return await self.say(
                ctx, "No project here yet. `!newproject <name>` starts one (admin)."
            )

        today = datetime.now(zone(data["meta"]["tz"])).date()

        if (ref or "").strip().lower() in {"list", "all", "projects"}:
            lines = ["**Projects**", ""]
            for project in data["projects"]:
                open_tasks, late, done = counts(tasks_of(data, project["id"]), today)
                lines.append(
                    f"· **{project['name']}** (#{project['id']}) · "
                    f"{open_tasks} open, {late} late, {done} done"
                )
            return await self.say(ctx, "\n".join(lines), keep=True)

        project = self.resolve_project(data, ref)
        if project is None:
            names = "\n".join(f"· **{p['name']}** (#{p['id']})" for p in data["projects"])
            return await self.say(ctx, f"Which project?\n{names}\n`!todo <name>`", keep=True)

        # One list per project per channel, edited in place: `!todo` is a summon,
        # not a new message. Only a deleted list gets posted again.
        await self.render_project(ctx.guild.id, project, ctx.channel)

    @commands.command(name="add", aliases=["task"])
    async def add(self, ctx: commands.Context, *, line: str | None = None):
        """Add one task: !add Print the brackets @sam fri"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)
        today = datetime.now(zone(data["meta"]["tz"])).date()

        if not line:
            return await self.say(
                ctx,
                "One line: `!add [project:] <task> @who <date>` · e.g. `!add Print the jig @sam fri`",
            )

        if not data["projects"]:
            return await self.say(ctx, "No project yet. `!newproject <name>` starts one (admin).")

        # `!add Halo: print the jig @sam fri` picks the project; the colon is what
        # makes it unambiguous against task text that happens to start with a name.
        project, ref, rest = take_project_ref(data, line)
        project = project or self.resolve_project(data, ref)
        if project is None:
            names = "\n".join(f"· **{p['name']}** (#{p['id']})" for p in data["projects"])
            return await self.say(
                ctx, f"Which project?\n{names}\n`!add <project>: <task> @who <date>`", keep=True
            )
        found = parse_when(rest, today)
        if not found:
            return await self.say(ctx, "Needs a date: `!add Print the jig @sam fri` (or `2026-12-05`, `+3d`).")
        due, matched = found
        rest = (rest[: rest.index(matched)] + " " + rest[rest.index(matched) + len(matched):]).strip()

        owner_id, owner_name, rest = self._take_owner(ctx, rest)
        if not owner_name:
            return await self.say(ctx, "Needs one owner: `!add Print the jig @sam fri`.")

        text = re.sub(r"\s{2,}", " ", rest).strip(" -–·,")
        if not text:
            return await self.say(ctx, "The task needs words: `!add Print the jig @sam fri`.")

        async with lock_for(ctx.guild.id):
            task = {
                "id": next_id(data["tasks"]),
                "project": project["id"],
                "text": text,
                "owner": owner_name,
                "owner_id": str(owner_id) if owner_id else None,
                "due": due.isoformat(),
                "state": "open",
                "done_at": None,
                "reminded_due": None,
                "reminded_late": None,
                "created_by": str(ctx.author.id),
            }
            data["tasks"].append(task)
            await asave(ctx.guild.id)

        await self.refresh_lists(ctx.guild.id, project, ctx.channel)
        open_tasks, late, _ = counts(tasks_of(data, project["id"]), today)
        await self.say(
            ctx,
            f"+ **{clip(text, 70)}** · **{owner_name}** · {fmt_day(due, today)} · "
            f"{open_tasks} open, {late} late.",
        )

    def _take_owner(self, ctx: commands.Context, text: str) -> tuple[int | None, str | None, str]:
        """Pull the owner out of an !add line: mentions first, then @name."""
        mention = re.search(r"<@!?(\d+)>", text)
        if mention:
            member = ctx.guild.get_member(int(mention[1]))
            name = member.display_name if member else mention[1]
            return (int(mention[1]), name, (text[: mention.start()] + text[mention.end():]).strip())

        at = re.search(r"(?<!\S)@([\w.\-]{2,32})", text)
        if not at:
            return None, None, text
        wanted = at[1].lower()
        for member in ctx.guild.members:
            if member.bot:
                continue
            if wanted in {member.name.lower(), member.display_name.lower(),
                          member.global_name.lower() if member.global_name else ""}:
                return member.id, member.display_name, (text[: at.start()] + text[at.end():]).strip()
        return None, at[1], (text[: at.start()] + text[at.end():]).strip()

    @commands.command(name="tasks")
    async def tasks_list(self, ctx: commands.Context, *, ref: str | None = None):
        """Every open task with its id: !tasks [project]"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)
        if not data["projects"]:
            return await self.say(ctx, "No project yet. `!newproject <name>` starts one (admin).")

        project = self.resolve_project(data, ref)
        if project is None:
            names = "\n".join(f"· **{p['name']}** (#{p['id']})" for p in data["projects"])
            return await self.say(ctx, f"Which project?\n{names}\n`!tasks <name>`", keep=True)

        today = datetime.now(zone(data["meta"]["tz"])).date()
        open_tasks = ordered_open(tasks_of(data, project["id"]), today)
        done = len(tasks_of(data, project["id"])) - len(open_tasks)
        if not open_tasks:
            tail = f" · {done} done" if done else ""
            return await self.say(ctx, f"**{project['name']}** has nothing open{tail}.", keep=True)

        lines = [f"**{project['name']}** · ids for `!edit` and `!del`", ""]
        for task in open_tasks[:MAX_ROWS]:
            bits = [f"`#{task['id']}`", STATE_ICON.get(task["state"], "⬜"), clip(task["text"], 60)]
            bits.append(f"· {task.get('owner') or 'someone'}")
            due = iso_to_date(task)
            if due:
                bits.append(f"· {fmt_day(due, today)}")
            lines.append(" ".join(bits))
        if len(open_tasks) > MAX_ROWS:
            lines.append(f"+{len(open_tasks) - MAX_ROWS} more")
        if done:
            lines.append(f"\n{done} done, not listed.")
        lines.append("\n`!edit 7 new words @sam fri` · `!del 7`")
        return await self.say(ctx, "\n".join(lines), keep=True)

    def _task_by_id(self, data: dict, raw: str | None) -> dict | None:
        digits = re.sub(r"[^0-9]", "", raw or "")
        if not digits:
            return None
        return next((t for t in data["tasks"] if t["id"] == int(digits)), None)

    @commands.command(name="edit")
    async def edit(self, ctx: commands.Context, *, spec: str | None = None):
        """Fix a task: !edit 7 new words @sam fri (give only what changed)"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)
        today = datetime.now(zone(data["meta"]["tz"])).date()

        head, _, rest = (spec or "").partition(" ")
        task = self._task_by_id(data, head)
        if task is None:
            ids = ", ".join(f"#{t['id']}" for t in ordered_open(data["tasks"], today)[:10])
            return await self.say(ctx, f"Which task? `!edit <id> …` · ids: {ids or 'none yet'}")
        if not rest.strip():
            return await self.say(
                ctx, f"`!edit {task['id']} <words>`, `@owner` or a date — give at least one."
            )

        changed: list[str] = []
        body = rest.strip()
        found = parse_when(body, today)
        if found:
            due, matched = found
            task["due"] = due.isoformat()
            body = (body[: body.index(matched)] + " " + body[body.index(matched) + len(matched):]).strip()
            changed.append(f"due {fmt_day(due, today)}")

        owner_id, owner_name, body = self._take_owner(ctx, body)
        if owner_name and owner_id is not None:
            task["owner"], task["owner_id"] = owner_name, str(owner_id)
            changed.append(f"owner {owner_name}")
        elif owner_name:
            task["owner"], task["owner_id"] = owner_name, None
            changed.append(f"owner {owner_name}")

        text = re.sub(r"\s{2,}", " ", body).strip(" -–·,")
        if text:
            task["text"] = text
            changed.append("words")
        if not changed:
            return await self.say(ctx, "Nothing to change there — words, `@owner` or a date.")

        async with lock_for(ctx.guild.id):
            await asave(ctx.guild.id)

        project = next((p for p in data["projects"] if p["id"] == task["project"]), None)
        if project is not None:
            await self.refresh_lists(ctx.guild.id, project, ctx.channel)
        await self.say(ctx, f"✅ **#{task['id']} {clip(task['text'], 50)}** · {' · '.join(changed)}.")

    @commands.command(name="del", aliases=["rmtask"])
    async def del_task(self, ctx: commands.Context, *, spec: str | None = None):
        """Delete one task: !del 7"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)
        today = datetime.now(zone(data["meta"]["tz"])).date()

        task = self._task_by_id(data, spec)
        if task is None:
            ids = ", ".join(f"#{t['id']}" for t in ordered_open(data["tasks"], today)[:10])
            return await self.say(ctx, f"Which task? `!del <id>` · ids: {ids or 'none yet'}")

        async with lock_for(ctx.guild.id):
            data["tasks"] = [t for t in data["tasks"] if t["id"] != task["id"]]
            await asave(ctx.guild.id)

        project = next((p for p in data["projects"] if p["id"] == task["project"]), None)
        if project is not None:
            await self.refresh_lists(ctx.guild.id, project, ctx.channel)
        await self.say(
            ctx,
            f"🗑️ **#{task['id']} {clip(task['text'], 60)}** is gone"
            f" ({task.get('owner') or 'unowned'}, {fmt_day(iso_to_date(task), today) if task.get('due') else 'no date'}).",
            keep=True,
        )

    @commands.command(name="newproject", aliases=["newp"])
    async def newproject(self, ctx: commands.Context, *, spec: str | None = None):
        """Start a project: !newproject Project Halo (admin)"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)
        if not self.is_admin(ctx.author) and not data["meta"].get("staff_can_create"):
            return await self.say(ctx, "Only an admin can start a project.")

        if not spec:
            return await self.say(
                ctx, "`!newproject Project Halo` starts one, then `!add` the first tasks."
            )

        name = re.sub(r"\s{2,}", " ", spec).strip(" -–·,:")
        if not name:
            return await self.say(ctx, "The project needs a name.")
        if project_by_ref(data, name) or any(p["name"].lower() == name.lower() for p in data["projects"]):
            return await self.say(ctx, f"There is already a project called **{name}**.")

        project = {
            "id": next_id(data["projects"]),
            "name": name[:60],
            "lists": {},
            "created_by": str(ctx.author.id),
        }
        data["projects"].append(project)
        if not data["meta"].get("channel"):
            data["meta"]["channel"] = str(ctx.channel.id)  # first one claims the home channel
        await asave(ctx.guild.id)

        await self.render_project(ctx.guild.id, project, ctx.channel)
        await self.say(
            ctx,
            f"Project **{project['name']}** (#{project['id']}). `!todo {project['name']}` any time, "
            "`!add <task> @who <date>` for the first few.",
        )

    @commands.command(name="delproject", aliases=["rmproject"])
    async def delproject(self, ctx: commands.Context, *, ref: str | None = None):
        """Delete a project and its tasks: !delproject Project Halo (admin)"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)
        if not self.is_admin(ctx.author) and not data["meta"].get("staff_can_create"):
            return await self.say(ctx, "Only an admin can delete a project.")

        if not data["projects"]:
            return await self.say(ctx, "There is no project here to delete.")

        # Deliberately no channel fallback: typing a name is the point.
        project = project_by_ref(data, ref)
        if project is None:
            names = "\n".join(f"· **{p['name']}** (#{p['id']})" for p in data["projects"])
            return await self.say(
                ctx, f"Which project?\n{names}\n`!delproject <name or #id>`", keep=True
            )

        tasks = tasks_of(data, project["id"])
        today = datetime.now(zone(data["meta"]["tz"])).date()
        open_tasks, late, done = counts(tasks, today)
        lists = len(project.get("lists") or {})
        summary = (
            f"**Delete {project['name']}** (#{project['id']})?\n"
            f"{open_tasks} open, {late} late, {done} done · "
            f"{lists} list message{'s' if lists != 1 else ''} to remove\n"
            "Its tasks go with it. Calendar events are not touched. This cannot be undone."
        )
        view = DeleteProjectView(self, ctx.guild.id, project, len(tasks), ctx.author.id)
        await ctx.reply(summary, view=view, mention_author=False)

    @commands.command(name="settings")
    async def settings(self, ctx: commands.Context, key: str | None = None, *, value: str | None = None):
        """Check-in, note, timezone, who may start projects: !settings (admin)"""
        if not ctx.guild:
            return await self.say(ctx, "PMbot works in a server channel.")
        data = server(ctx.guild.id)
        meta = data["meta"]

        if key is None:
            note = meta.get("note")
            home = f"<#{meta['channel']}>" if meta.get("channel") else "the first channel it can post in"
            return await self.say(
                ctx,
                f"**PMbot settings**\n"
                f"· channel `{home}`\n"
                f"· check-in `{meta.get('checkin') or 'off'}`\n"
                f"· note `{note or 'off'}`\n"
                f"· timezone `{meta.get('tz')}`\n"
                f"· staff can start projects: {'yes' if meta.get('staff_can_create') else 'no'}\n"
                f"Change one: `!settings channel #pm` · `!settings checkin 09:30` · "
                f"`!settings note sun 18:00` · `!settings tz Europe/Lisbon` · `!settings staff on`",
                keep=True,
            )

        if not self.is_admin(ctx.author):
            return await self.say(ctx, "Only an admin can change settings.")

        key = key.lower()
        value = (value or "").strip()

        if key == "channel":
            if not value or value.lower() in {"here", "this", "#here"}:
                meta["channel"] = str(ctx.channel.id)
            else:
                found = re.search(r"<#(\d+)>", value)
                channel = ctx.guild.get_channel(int(found[1])) if found else None
                if channel is None:
                    return await self.say(ctx, "Point at a channel: `!settings channel #pm`.")
                meta["channel"] = str(channel.id)
        elif key == "checkin":
            if value.lower() in {"off", "none", "no"}:
                meta["checkin"] = "off"
            elif (when := parse_hhmm(value)):
                meta["checkin"] = f"{when.hour:02d}:{when.minute:02d}"
            else:
                return await self.say(ctx, "Time like `09:30`, or `off`.")
        elif key == "note":
            spec = parse_note(value)
            if value.lower() in {"off", "none", "no"}:
                meta["note"] = "off"
            elif spec:
                day, when = spec
                meta["note"] = f"{WDAYS[day].lower()} {when.hour:02d}:{when.minute:02d}"
            else:
                return await self.say(ctx, "`sun 18:00`, or `off`.")
        elif key in {"tz", "timezone"}:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(value)
            except Exception:  # noqa: BLE001
                return await self.say(ctx, "That timezone is not a name like `Europe/Lisbon`.")
            meta["tz"] = value
        elif key == "staff":
            if value.lower() in {"on", "yes", "true", "1"}:
                meta["staff_can_create"] = True
            elif value.lower() in {"off", "no", "false", "0"}:
                meta["staff_can_create"] = False
            else:
                return await self.say(ctx, "`!settings staff on` or `!settings staff off`.")
        else:
            return await self.say(ctx, "Settings: `channel`, `checkin`, `note`, `tz`, `staff`.")

        await asave(ctx.guild.id)
        shown = {
            "channel": f"<#{meta['channel']}>" if meta.get("channel") else "none",
            "checkin": meta.get("checkin") or "off",
            "note": meta.get("note") or "off",
            "tz": meta.get("tz"),
            "timezone": meta.get("tz"),
            "staff": "on" if meta.get("staff_can_create") else "off",
        }[key]
        await self.say(ctx, f"`{key}` is now `{shown}`.")

    # ---------------------------------------------------------------- buttons
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id") or ""
        if not custom_id.startswith("pm:t:"):
            return

        parts = custom_id.split(":")
        if len(parts) != 5:
            return
        _, _, raw_guild, raw_task, state = parts
        if not (raw_guild.lstrip("-").isdigit() and raw_task.isdigit()) or state not in STATE_ICON:
            return

        await interaction.response.defer(thinking=False)
        guild_id, task_id = int(raw_guild), int(raw_task)

        if not interaction.guild or interaction.guild.id != guild_id:
            return

        data = server(guild_id)
        today = datetime.now(zone(data["meta"]["tz"])).date()

        async with lock_for(guild_id):
            task = next((t for t in data["tasks"] if t["id"] == task_id), None)
            if task is None:
                # A stale message from a deleted project or task. Say so instead of
                # leaving the tap looking broken.
                await interaction.followup.send(
                    "That task is not here any more — `!todo` has the current list.",
                    ephemeral=True,
                )
                return
            project = next((p for p in data["projects"] if p["id"] == task["project"]), None)
            if project is None:
                await interaction.followup.send(
                    "That project is gone — `!todo` has the current list.", ephemeral=True
                )
                return
            if not self.can_tap(interaction.user, task):
                await interaction.followup.send(
                    f"That one is {owner_tag(task)}'s. Only they or an admin can move it.",
                    ephemeral=True,
                )
                return

            task["state"] = state
            task["done_at"] = (
                datetime.now(zone(data["meta"]["tz"])).replace(microsecond=0).isoformat()
                if state == "done" else None
            )
            if state == "done":
                task["reminded_late"] = task["reminded_late"] or today.isoformat()
            await asave(guild_id)

        # Refresh the list in whatever channel was tapped in; a project can have a
        # list in several channels now.
        await self.refresh_lists(guild_id, project, interaction.channel)

        open_tasks, late, _ = counts(tasks_of(data, project["id"]), today)
        verb = STATE_LABEL[state]
        await interaction.followup.send(
            f"{STATE_ICON[state]} **{clip(task['text'], 60)}**: {verb}. "
            f"{open_tasks} open, {late} late. `!todo` to see it."
        )

    # ---------------------------------------------------------------- ticker
    @tasks.loop(seconds=TICK_SECONDS)
    async def ticker(self) -> None:
        try:
            await self.tick()
        except Exception:  # noqa: BLE001  (one bad server must not stop the rest)
            log.exception("pmbot tick failed")

    @ticker.before_loop
    async def before_ticker(self) -> None:
        await self.bot.wait_until_ready()

    async def tick(self) -> None:
        for guild in list(self.bot.guilds):
            if not (DATA_DIR / f"{guild.id}.json").exists() and guild.id not in _servers:
                continue
            data = server(guild.id)
            now = datetime.now(zone(data["meta"]["tz"]))
            await self.run_checkins(guild, data, now)
            await self.run_reminders(guild, data, now)
            await self.run_note(guild, data, now)
            await self.run_backup(guild.id, data, now)

    def _prune_runs(self, data: dict, today: date) -> None:
        cutoff = (today - timedelta(days=3)).isoformat()
        data["runs"]["checkin"] = {
            key: value for key, value in data["runs"]["checkin"].items() if key >= cutoff
        }

    async def run_checkins(self, guild: discord.Guild, data: dict, now: datetime) -> None:
        when = parse_hhmm(data["meta"].get("checkin"))
        if when is None or now.time() < when:
            return
        today = now.date()
        self._prune_runs(data, today)
        stamp = today.isoformat()
        done_today = data["runs"]["checkin"].setdefault(stamp, [])
        channel = home_channel(guild, data)
        if channel is None:
            return

        # One check-in per person, not per project: their tasks from every project,
        # in one message, in one channel.
        people: dict[str, list[tuple[str, dict]]] = {}
        for project in data["projects"]:
            for task in ordered_open(tasks_of(data, project["id"]), today):
                people.setdefault(owner_key(task), []).append((project["name"], task))

        for key, entries in people.items():
            if key in done_today:
                continue
            first = entries[0][1]
            display = first.get("owner") or "someone"
            head = owner_tag(first) if first.get("owner_id") else f"**{display}**"
            view = task_view(guild.id, [task for _, task in entries])
            try:
                await channel.send(
                    f"{head}\n{clip(render_checkin(display, entries, today), 1900)}",
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True),
                )
            except discord.HTTPException as exc:
                log.warning("check-in failed in %s: %s", channel, exc)
                continue
            done_today.append(key)
            await asave(guild.id)

    async def run_reminders(self, guild: discord.Guild, data: dict, now: datetime) -> None:
        check_time = parse_hhmm(data["meta"].get("checkin"))
        if check_time and now.time() < check_time:
            return
        today = now.date()
        channel = home_channel(guild, data)
        if channel is None:
            return
        projects = {p["id"]: p["name"] for p in data["projects"]}

        for task in data["tasks"]:
            if task["state"] == "done" or task["project"] not in projects:
                continue
            due = iso_to_date(task)
            if due is None:
                continue
            where = f" ({projects[task['project']]})" if len(projects) > 1 else ""

            if due < today and not task.get("reminded_late"):
                task["reminded_late"] = today.isoformat()
                await asave(guild.id)
                late = (today - due).days
                await self._remind(
                    channel,
                    f"⚠️ **{clip(task['text'], 90)}**{where} was due {fmt_day(due, today)} · "
                    f"{owner_tag(task)} · {late} day{'s' if late != 1 else ''} late",
                )
            elif due <= today + timedelta(days=1) and not task.get("reminded_due"):
                # Threshold, not "a tick happened to land on the day before": a bot
                # that was down for a day still sends this once before the deadline.
                task["reminded_due"] = today.isoformat()
                await asave(guild.id)
                when = "today" if due == today else "tomorrow"
                await self._remind(
                    channel,
                    f"⏰ **{clip(task['text'], 90)}**{where} is due {when} · {owner_tag(task)}",
                )

    @staticmethod
    async def _remind(channel: discord.abc.Messageable, text: str) -> None:
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException as exc:
            log.warning("reminder failed in %s: %s", channel, exc)

    async def run_note(self, guild: discord.Guild, data: dict, now: datetime) -> None:
        spec = parse_note(data["meta"].get("note"))
        if spec is None:
            return
        weekday, when = spec
        today = now.date()
        if now.weekday() != weekday or now.time() < when:
            return
        if data["runs"].get("note") == today.isoformat():
            return

        channel = home_channel(guild, data)
        if channel is None:
            return
        entries = [
            (project, tasks_of(data, project["id"]))
            for project in data["projects"]
        ]
        entries = [(name, tasks) for name, tasks in entries if tasks]
        if not entries:
            data["runs"]["note"] = today.isoformat()
            await asave(guild.id)
            return

        try:
            await channel.send(clip(render_note(entries, today), 1990))
        except discord.HTTPException as exc:
            log.warning("note failed in %s: %s", channel, exc)
            return

        data["runs"]["note"] = today.isoformat()
        await asave(guild.id)

    async def run_backup(self, guild_id: int, data: dict, now: datetime) -> None:
        """One copy of every server file a day, however many servers there are."""
        today = now.date()
        if now.hour < 3 or (BACKUP_DIR / today.isoformat()).exists():
            return
        try:
            await asyncio.to_thread(backup_all, today)
        except OSError:
            log.exception("backup failed")
            return
        data["runs"]["backup"] = today.isoformat()
        await asave(guild_id)
