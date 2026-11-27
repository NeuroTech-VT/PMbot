#!/usr/bin/env python3
"""Render the technical implementation doc for PMbot.

Written for the person building it: stack, data shape, scheduling, storage
safety. Unlike the conceptual plan, this one names tools and schemas on purpose.

Rules followed (see AGENTS.md): body text Times New Roman 12 pt, no em dashes.

Single source of truth for the content: writes both
  pmbot/implementation.md  (editable notes)
  pmbot/implementation.pdf (three-page doc)

Run: .venv-pdf/bin/python pmbot/build_impl.py
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

BASE = Path(__file__).resolve().parent
PDF_PATH = BASE / "implementation.pdf"
MD_PATH = BASE / "implementation.md"

NAVY = colors.HexColor("#1f3a5f")

TITLE = "PMbot: technical implementation"

INTRO = (
    "One Python process, one Discord bot, one JSON file per server. The todo list, the four taps, "
    "the daily check-in, two reminders, the end of week note and the calendar are renders of the same "
    "task and event data plus a ticker that decides what to post. No database, no queue, no web "
    "service, no Pi. The one optional network call is a small chat model behind the calendar's free "
    "text."
)

SCHEMA = """{
  "meta":     {"tz": "Europe/Lisbon", "checkin": "09:30",
               "note": "Sun 18:00", "staff_can_create": false,
               "next_event_id": 12,
               "llm": {"endpoint": "https://…/v1", "model": "…",
                       "key": "…"}},
  "projects": [{"id": 1, "name": "SimpleSexbotMK1", "channel_id": "…",
                "todo_message_id": "…", "created_by": "…"}],
  "tasks":    [{"id": 7, "project": 1, "text": "Print the fitting jig",
                "owner": "…", "due": "2026-12-05",
                "state": "open",
                "done_at": null, "reminded_due": null, "reminded_late": null}],
  "events":   [{"id": 3, "date": "2026-12-05", "time": "13:15",
                "duration": 45, "title": "Lunch with Ana", "place": "Café",
                "created_by": "…", "channel_id": "…",
                "reminded": false, "snooze_until": null, "done": false}],
  "runs":     {"checkin": {"…-…": "2026-12-01"}, "note": "2026-11-30"}
}"""

SECTIONS = [
    (
        "Stack",
        [
            "<b>Python 3.11 and discord.py 2.3</b>, one long running process, deployed as a systemd "
            "user service next to the Pi bridge but independent of it: no Pi process and no session. "
            "Token in <font face='Courier'>.env</font>.",
            "<b>One process, no services.</b> Scheduling is a <font face='Courier'>discord.ext.tasks</font> "
            "loop that runs every 30 seconds; storage is plain JSON written with the standard library; "
            "the model call uses <font face='Courier'>aiohttp</font>, already required by discord.py.",
            "<b>One external program:</b> the headless browser that renders the month card, named in "
            "<font face='Courier'>CALENDAR_BROWSER</font> (<font face='Courier'>librewolf</font> or "
            "<font face='Courier'>chromium</font>). Everything else is Python.",
            "<b>Nothing lives in memory that matters.</b> State is loaded at boot, mutated under a lock "
            "and written back; a restart is always safe.",
        ],
    ),
    (
        "Data: one JSON file per server",
        [
            "<b>File:</b> <font face='Courier'>data/servers/&lt;guild_id&gt;.json</font>. A busy club is a "
            "few dozen tasks, so this stays in the low kilobytes and can be read in full on boot.",
            "<b>Shape:</b> five top level keys, shown below. Tasks and events are flat lists: a task "
            "carries a <font face='Courier'>project</font> id, so it never nests and moving one is a field "
            "change; an event is independent of any task.",
            "<b>Dates:</b> <font face='Courier'>YYYY-MM-DD</font> in the server timezone, so a deadline "
            "never shifts because of a daylight saving change. Timestamps are stored in UTC.",
            "<b>Ids:</b> short integers per server. Button ids read "
            "<font face='Courier'>task:7:done</font>, which survives restarts and needs no lookup table.",
        ],
    ),
    (
        "Commands and taps",
        [
            "<b><font face='Courier'>!todo</font></b> renders the list for the channel's project. It edits "
            "the message it remembers; if that message was deleted, it posts a new one and remembers that.",
            "<b><font face='Courier'>!add</font></b> takes one line with one owner and one date. A line "
            "without both is rejected with a one line hint, because that rule is what keeps the list clean.",
            "<b><font face='Courier'>!new</font></b> asks for a project name and the first few tasks, "
            "creates the project, optionally a channel, and posts the list. Admin only.",
            "<b><font face='Courier'>!cal</font></b> shows the month card, one day, or adds and removes "
            "events. Text it does not recognise as a command goes to the small model. Details below.",
            "<b><font face='Courier'>!settings</font></b> holds check-in time, note day and time, timezone, "
            "who may create projects, and the calendar model: endpoint, model and API key. Admin only, "
            "ephemeral.",
            "<b>Four buttons</b> (Done, On it, Blocked, Not started) sit on the daily check-in and on each "
            "list row. A tap writes the state, re-renders the list and answers in the channel with one "
            "confirmation line.",
        ],
    ),
    (
        "Calendar: events and the month card",
        [
            "<b>Events sit next to tasks</b> in the same server JSON: one day, all day allowed, "
            "<font face='Courier'>YYYY-MM-DD</font> in the server timezone, ids from "
            "<font face='Courier'>meta.next_event_id</font>. No repeats, no multi day spans, no attendees.",
            "<b>Two views, one command.</b> <font face='Courier'>!cal</font> posts or edits the month card; "
            "<font face='Courier'>!cal fri</font> or <font face='Courier'>!cal 3 Dec</font> answers with "
            "that day as text. The card is the bot's one image, because a month reads at a glance where a "
            "list does not.",
            "<b>The card</b> is a 550 px wide PNG at 2x from a small HTML template, rendered by the "
            "headless browser and cached by a hash of its HTML. It shows a seven column month, today "
            "outlined, one star mark per event and one status emoji per due task (at most four marks), and a "
            "footer that lists today's events and tasks one per line. Previous, Today and Next are "
            "persistent buttons on the same message.",
            "<b>Tasks are a read-only overlay.</b> Open tasks with a due date appear with their status emoji: "
            "not started, on it, blocked. The calendar never creates or edits a task, and a task never "
            "becomes an event.",
            "<b>Reminders</b> come from the same ticker: 15 minutes before a timed event, 09:00 for an all "
            "day one, a DM to the person who added it with Snooze 10 min and Dismiss. A "
            "<font face='Courier'>reminded</font> stamp makes a missed slot fire once, not twice.",
            "<b>Typed commands need no model.</b> <font face='Courier'>!cal add Lunch fri 12:30 @ Café</font> "
            "and <font face='Courier'>!cal rm fri lunch</font>; dates parse ISO, "
            "<font face='Courier'>3 Dec</font>, <font face='Courier'>fri</font>, "
            "<font face='Courier'>+2d</font>; times parse <font face='Courier'>12:30</font> or "
            "<font face='Courier'>15:00-16:00</font>. An ambiguous remove lists the matches instead of "
            "guessing.",
        ],
    ),
    (
        "Calendar free text: one small model call",
        [
            "<b>No Pi.</b> PMbot calls an OpenAI compatible "
            "<font face='Courier'>POST &lt;endpoint&gt;/chat/completions</font> with "
            "<font face='Courier'>aiohttp</font>. No session, no tools, no agent loop, one request per free "
            "text message.",
            "<b>Set in <font face='Courier'>!settings</font>:</b> endpoint, model and API key, admin only, "
            "ephemeral. The key is stored with the server file, shown masked and never logged; a Test "
            "button checks the endpoint.",
            "<b>A small instruct model, no thinking variant.</b> One plain chat completion, no streaming "
            "and no retry loop. Nothing is cut to fit: a long task or event title goes in as written, and "
            "the model's context is the only ceiling.",
            "<b>One compact prompt.</b> Today and the timezone, upcoming events, open tasks with due dates "
            "(read only), the action schema, and JSON only. The answer is one object: "
            "<font face='Courier'>{\"reply\": \"\", \"actions\": [{\"action\": \"add\", "
            "\"title\": \"Lunch\", \"date\": \"2026-12-04\", \"time\": \"13:15\"}]}</font>.",
            "<b>The bot owns the state.</b> Every action is validated by the same code as "
            "<font face='Courier'>!cal</font>: real dates, valid times, known ids. Malformed or unknown "
            "actions change nothing. Receipts are bot written with exact dates; the model's "
            "<font face='Courier'>reply</font> is used only for a clarifying question.",
            "<b>It is optional.</b> Without a key, or when the call fails, free text answers with the "
            "<font face='Courier'>!cal add ...</font> hint. Views, typed commands and reminders never touch "
            "the network.",
        ],
    ),
    (
        "Scheduling: one ticker, no cron",
        [
            "<b>Every 30 seconds</b> the loop asks four questions: is a check-in due, is a task reminder "
            "due, is a calendar reminder due, is the note due. All four are computed from the task and "
            "event data, so there is no schedule to keep in sync with the lists.",
            "<b>Idempotent by stamp.</b> Reminders write <font face='Courier'>reminded_due</font> or "
            "<font face='Courier'>reminded_late</font>; the check-in and note write the date into "
            "<font face='Courier'>runs</font>. A missed slot fires once after boot instead of twice.",
            "<b>Reminders, two per task, ever:</b> the day before the due date, and the morning after it "
            "first goes late. Nothing else is ever sent about a task.",
            "<b>The check-in</b> posts once per person per day, listing only their open tasks with the four "
            "buttons. Someone with nothing open gets nothing.",
            "<b>Calendar reminders use the same stamps:</b> one "
            "<font face='Courier'>reminded</font> flag per event, set after the DM; Snooze moves "
            "<font face='Courier'>snooze_until</font> 10 minutes out and resets the flag.",
            "<b>The note</b> posts Sunday evening into the team channel: finished in the last 7 days, "
            "slipped, at risk (due within 3 days and not started, or blocked), and what is due next week.",
        ],
    ),
    (
        "Rendering rules",
        [
            "<b>One message per project,</b> edited in place. The top of it is whatever hurts: late tasks "
            "first, then open tasks by date, undated last.",
            "<b>Finished work collapses</b> to a count, so the message does not grow without bound.",
            "<b>Limits:</b> 25 rows and 25 buttons per message. Beyond that the list shows a count and "
            "points at the check-in, where each person only sees their own tasks.",
            "<b>Plain text for lists,</b> no embeds beyond one border, so it reads the same on a phone in "
            "a corridor as on a desktop. The month calendar is the one image: 550 px wide, 2x, dark theme, "
            "posted and edited like the todo message.",
        ],
    ),
    (
        "Storage safety",
        [
            "<b>Atomic writes:</b> write to a temporary file, fsync, then "
            "<font face='Courier'>os.replace</font>, keeping the previous version as "
            "<font face='Courier'>.bak</font>. A crash mid-write can never truncate the list.",
            "<b>One writer:</b> mutations happen under a single "
            "<font face='Courier'>asyncio.Lock</font> with a two second debounce, so ten taps in a row cost "
            "one write.",
            "<b>Nightly backup</b> to <font face='Courier'>data/backups/</font>, 14 kept. Restoring is "
            "copying a file back, no tooling.",
            "<b>Escape hatch:</b> with the bot stopped, the JSON can be edited by hand, which is the whole "
            "point of choosing it over a database.",
        ],
    ),
    (
        "What it will not do",
        [
            "No model for tasks, no drafting, no chat. The only call is the calendar's free text, with a "
            "small instruct model and no thinking mode. No boards, Gantt charts, custom fields, time "
            "tracking or web dashboard. No notifications for every change: the lists change silently and "
            "the reminders and the Sunday note are the only things that speak.",
            "No per-server theming, no config panel: the settings above, now including the calendar "
            "model, are the whole configuration surface.",
        ],
    ),
    (
        "Order of work, and how to test it",
        [
            "<b>1.</b> Store, model and render, plus <font face='Courier'>!todo</font> and the buttons: at "
            "that point the club can already use it.",
            "<b>2.</b> <font face='Courier'>!add</font> and <font face='Courier'>!new</font>, then the "
            "ticker with the check-in and the two reminders.",
            "<b>3.</b> The end of week note, then settings, permissions and backups.",
            "<b>4.</b> Calendar: store and card, then <font face='Courier'>!cal</font> and the reminders, "
            "then the free text call and its settings.",
            "<b>Tests worth writing:</b> the render function, the at risk rule, the 7 day window, the stamp "
            "logic, the calendar date parser, action validation and the card renderer. A stub HTTP endpoint "
            "stands in for the model. A <font face='Courier'>--clock</font> flag that fakes the time lets "
            "the whole week run in a few seconds.",
        ],
    ),
]


def styles():
    body = dict(fontName="Times-Roman", fontSize=12, leading=14.2,
                textColor=colors.HexColor("#1b1f23"))
    return {
        "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=17, leading=19, textColor=NAVY),
        "h": ParagraphStyle("h", fontName="Times-Bold", fontSize=12, leading=14, textColor=NAVY,
                            spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", **body),
        "bullet": ParagraphStyle("bullet", leftIndent=14, bulletIndent=2, bulletFontName="Times-Roman",
                                 bulletFontSize=12, **body),
        "code": ParagraphStyle("code", fontName="Courier", fontSize=8.4, leading=10.2,
                               leftIndent=10, spaceBefore=4, spaceAfter=2,
                               textColor=colors.HexColor("#1b1f23")),
    }


def story(s):
    out = [Paragraph(TITLE, s["title"]), Spacer(1, 4),
           HRFlowable(width="100%", thickness=1.0, color=NAVY, spaceBefore=1, spaceAfter=5),
           Paragraph(INTRO, s["body"])]
    for i, (name, items) in enumerate(SECTIONS):
        out.append(Paragraph(name.upper(), s["h"]))
        for it in items:
            out.append(Paragraph(it, s["bullet"], bulletText="–"))
        if i == 1:
            for line in SCHEMA.splitlines():
                out.append(Paragraph(line.replace(" ", "&nbsp;"), s["code"]))
    return out


def build_pdf():
    s = styles()
    doc = SimpleDocTemplate(
        str(PDF_PATH), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=14 * mm,
        title="PMbot: technical implementation", author="Owen Harlow",
        subject="Technical implementation: PMbot, a project-manager Discord bot",
    )
    flow = story(s)
    frame_w = A4[0] - 36 * mm
    heights, current = [], 0.0
    for f in flow:
        current += (f.wrap(frame_w, A4[1])[1] + getattr(f, "getSpaceBefore", lambda: 0)()
                    + getattr(f, "getSpaceAfter", lambda: 0)())
    heights.append(current)
    doc.build(flow)
    return heights


def write_md():
    def clean(t):
        return (t.replace("<b>", "**").replace("</b>", "**")
                 .replace("<font face='Courier'>", "`").replace("</font>", "`")
                 .replace("&lt;", "<").replace("&gt;", ">"))

    lines = [f"# {TITLE}", "", INTRO, ""]
    for i, (name, items) in enumerate(SECTIONS):
        lines += [f"## {name}", ""] + [f"- {clean(it)}" for it in items] + [""]
        if i == 1:
            lines += ["```json", SCHEMA, "```", ""]
    MD_PATH.write_text("\n".join(lines))


if __name__ == "__main__":
    heights = build_pdf()
    write_md()
    usable = A4[1] - 30 * mm
    total = sum(heights)
    print(f"{total / usable:.2f} pages of text ({total / mm:.0f} mm of {usable / mm:.0f} mm usable)")
    print(f"wrote {PDF_PATH} and {MD_PATH}")
