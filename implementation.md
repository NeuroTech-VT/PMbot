# PMbot: technical implementation

One Python process, one Discord bot, one JSON file per server. The todo list, the four taps, the daily check-in, two reminders, the end of week note and the calendar are renders of the same task and event data plus a ticker that decides what to post. No database, no queue, no web service, no Pi. The one optional network call is a small chat model behind the calendar's free text.

## Stack

- **Python 3.11 and discord.py 2.3**, one long running process, deployed as a systemd user service next to the Pi bridge but independent of it: no Pi process and no session. Token in `.env`.
- **One process, no services.** Scheduling is a `discord.ext.tasks` loop that runs every 30 seconds; storage is plain JSON written with the standard library; the model call uses `aiohttp`, already required by discord.py.
- **One external program:** the headless browser that renders the month card, named in `CALENDAR_BROWSER` (`librewolf` or `chromium`). Everything else is Python.
- **Nothing lives in memory that matters.** State is loaded at boot, mutated under a lock and written back; a restart is always safe.

## Data: one JSON file per server

- **File:** `data/servers/<guild_id>.json`. A busy club is a few dozen tasks, so this stays in the low kilobytes and can be read in full on boot.
- **Shape:** five top level keys, shown below. Tasks and events are flat lists: a task carries a `project` id, so it never nests and moving one is a field change; an event is independent of any task.
- **Dates:** `YYYY-MM-DD` in the server timezone, so a deadline never shifts because of a daylight saving change. Timestamps are stored in UTC.
- **Ids:** short integers per server. Button ids read `task:7:done`, which survives restarts and needs no lookup table.

```json
{
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
}
```

## Commands and taps

- **`!todo`** renders the list for the channel's project. It edits the message it remembers; if that message was deleted, it posts a new one and remembers that.
- **`!add`** takes one line with one owner and one date. A line without both is rejected with a one line hint, because that rule is what keeps the list clean.
- **`!new`** asks for a project name and the first few tasks, creates the project, optionally a channel, and posts the list. Admin only.
- **`!cal`** shows the month card, one day, or adds and removes events. Text it does not recognise as a command goes to the small model. Details below.
- **`!settings`** holds check-in time, note day and time, timezone, who may create projects, and the calendar model: endpoint, model and API key. Admin only, ephemeral.
- **Four buttons** (Done, On it, Blocked, Not started) sit on the daily check-in and on each list row. A tap writes the state, re-renders the list and answers in the channel with one confirmation line.

## Calendar: events and the month card

- **Events sit next to tasks** in the same server JSON: one day, all day allowed, `YYYY-MM-DD` in the server timezone, ids from `meta.next_event_id`. No repeats, no multi day spans, no attendees.
- **Two views, one command.** `!cal` posts or edits the month card; `!cal fri` or `!cal 3 Dec` answers with that day as text. The card is the bot's one image, because a month reads at a glance where a list does not.
- **The card** is a 550 px wide PNG at 2x from a small HTML template, rendered by the headless browser and cached by a hash of its HTML. It shows a seven column month, today outlined, one star mark per event and one status emoji per due task (at most four marks), and a footer that lists today's events and tasks one per line. Previous, Today and Next are persistent buttons on the same message.
- **Tasks are a read-only overlay.** Open tasks with a due date appear with their status emoji: not started, on it, blocked. The calendar never creates or edits a task, and a task never becomes an event.
- **Reminders** come from the same ticker: 15 minutes before a timed event, 09:00 for an all day one, a DM to the person who added it with Snooze 10 min and Dismiss. A `reminded` stamp makes a missed slot fire once, not twice.
- **Typed commands need no model.** `!cal add Lunch fri 12:30 @ Café` and `!cal rm fri lunch`; dates parse ISO, `3 Dec`, `fri`, `+2d`; times parse `12:30` or `15:00-16:00`. An ambiguous remove lists the matches instead of guessing.

## Calendar free text: one small model call

- **No Pi.** PMbot calls an OpenAI compatible `POST <endpoint>/chat/completions` with `aiohttp`. No session, no tools, no agent loop, one request per free text message.
- **Set in `!settings`:** endpoint, model and API key, admin only, ephemeral. The key is stored with the server file, shown masked and never logged; a Test button checks the endpoint.
- **A small instruct model, no thinking variant.** One plain chat completion, no streaming and no retry loop. Nothing is cut to fit: a long task or event title goes in as written, and the model's context is the only ceiling.
- **One compact prompt.** Today and the timezone, upcoming events, open tasks with due dates (read only), the action schema, and JSON only. The answer is one object: `{"reply": "", "actions": [{"action": "add", "title": "Lunch", "date": "2026-12-04", "time": "13:15"}]}`.
- **The bot owns the state.** Every action is validated by the same code as `!cal`: real dates, valid times, known ids. Malformed or unknown actions change nothing. Receipts are bot written with exact dates; the model's `reply` is used only for a clarifying question.
- **It is optional.** Without a key, or when the call fails, free text answers with the `!cal add ...` hint. Views, typed commands and reminders never touch the network.

## Scheduling: one ticker, no cron

- **Every 30 seconds** the loop asks four questions: is a check-in due, is a task reminder due, is a calendar reminder due, is the note due. All four are computed from the task and event data, so there is no schedule to keep in sync with the lists.
- **Idempotent by stamp.** Reminders write `reminded_due` or `reminded_late`; the check-in and note write the date into `runs`. A missed slot fires once after boot instead of twice.
- **Reminders, two per task, ever:** the day before the due date, and the morning after it first goes late. Nothing else is ever sent about a task.
- **The check-in** posts once per person per day, listing only their open tasks with the four buttons. Someone with nothing open gets nothing.
- **Calendar reminders use the same stamps:** one `reminded` flag per event, set after the DM; Snooze moves `snooze_until` 10 minutes out and resets the flag.
- **The note** posts Sunday evening into the team channel: finished in the last 7 days, slipped, at risk (due within 3 days and not started, or blocked), and what is due next week.

## Rendering rules

- **One message per project,** edited in place. The top of it is whatever hurts: late tasks first, then open tasks by date, undated last.
- **Finished work collapses** to a count, so the message does not grow without bound.
- **Limits:** 25 rows and 25 buttons per message. Beyond that the list shows a count and points at the check-in, where each person only sees their own tasks.
- **Plain text for lists,** no embeds beyond one border, so it reads the same on a phone in a corridor as on a desktop. The month calendar is the one image: 550 px wide, 2x, dark theme, posted and edited like the todo message.

## Storage safety

- **Atomic writes:** write to a temporary file, fsync, then `os.replace`, keeping the previous version as `.bak`. A crash mid-write can never truncate the list.
- **One writer:** mutations happen under a single `asyncio.Lock` with a two second debounce, so ten taps in a row cost one write.
- **Nightly backup** to `data/backups/`, 14 kept. Restoring is copying a file back, no tooling.
- **Escape hatch:** with the bot stopped, the JSON can be edited by hand, which is the whole point of choosing it over a database.

## What it will not do

- No model for tasks, no drafting, no chat. The only call is the calendar's free text, with a small instruct model and no thinking mode. No boards, Gantt charts, custom fields, time tracking or web dashboard. No notifications for every change: the lists change silently and the reminders and the Sunday note are the only things that speak.
- No per-server theming, no config panel: the settings above, now including the calendar model, are the whole configuration surface.

## Order of work, and how to test it

- **1.** Store, model and render, plus `!todo` and the buttons: at that point the club can already use it.
- **2.** `!add` and `!new`, then the ticker with the check-in and the two reminders.
- **3.** The end of week note, then settings, permissions and backups.
- **4.** Calendar: store and card, then `!cal` and the reminders, then the free text call and its settings.
- **Tests worth writing:** the render function, the at risk rule, the 7 day window, the stamp logic, the calendar date parser, action validation and the card renderer. A stub HTTP endpoint stands in for the model. A `--clock` flag that fakes the time lets the whole week run in a few seconds.
