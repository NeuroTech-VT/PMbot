# PMbot

A project manager that lives in a Discord server, built for a college design team that already does
its work in Discord. It is not a chatbot and not an app: it keeps one todo list per project, asks a
daily check-in, sends two reminders per task, and posts an end of week note on Sunday evening.

## Status

Implemented in this repo as one bot process. `bot.py` loads two cogs:

- `pmbot.py`: one list per project, `!todo`, `!add`, `!newproject`, `!settings`, the four buttons, the
  daily check-in, two reminders per task and the Sunday note. JSON store in `data/servers/`, nightly
  backup in `data/backups/`.
- `calendar_bot.py` + `calendar_core.py` + `calendar_card.py/html`: the month card, typed `!cal`
  commands, one day as text and event reminders in a DM. No hour-by-hour view and no free text:
  calendar changes are typed.
- `test_pmbot.py`, `test_calendar.py`: the pure parts.

No Pi, no LLM, no database. This repo is separate from the Discord bridge, which keeps its own
personal PMbot and a fuller calendar.

## Running

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # fill in DISCORD_TOKEN
.venv/bin/python bot.py
```

The month card is rendered by a headless browser named in `CALENDAR_BROWSER` (`librewolf` by
default). `data/` is created on the first write and is gitignored.

A systemd user unit for a server:

```
[Unit]
Description=PMbot
After=network-online.target

[Service]
WorkingDirectory=/path/to/pmbot
ExecStart=/path/to/pmbot/.venv/bin/python /path/to/pmbot/bot.py
Restart=always

[Install]
WantedBy=default.target
```

Tests:

```
.venv/bin/python -m unittest test_pmbot test_calendar -v
```

## Docs

- `plan.md` / `plan.pdf`: what it is and why, then the feature set.
- `implementation.md` / `implementation.pdf`: how it gets built (stack, JSON shape, scheduling,
  storage safety, the calendar, order of work).
- `prototypes/`: HTML mocks of the two things people actually see, the todo list and the end of week
  note. Open them in a browser, each is one Discord screen. `prototypes/sanitized/` holds the same two
  screens with a neutral demo project (building a phone) instead of the real one, for showing to anyone.

## Decisions worth remembering

- **The todo list is the product.** One message per project, summoned with `!todo`, edited in place
  forever. Nothing is pinned.
- **Four taps, never a typed status.** Done, On it, Blocked, Not started.
- **Two reminders, ever**: one before the deadline, one when it first slips. After that a task waits
  for the note.
- **No AI.** Tasks and calendar changes are typed; there is no model call anywhere.
- **College team, not a company.** Weeks run Sunday to Saturday, weekend work counts, and nothing
  measures hours.
- **JSON on disk**, one file per server, hand editable while the bot is stopped.

## Regenerating the docs

Both documents are generated, so edit the Python and re-render rather than editing the PDF:

```
.venv-pdf/bin/python build_plan.py
.venv-pdf/bin/python build_impl.py
```

`build_plan.py` writes `plan.md` and `plan.pdf`; `build_impl.py` writes `implementation.md` and
`implementation.pdf`.
