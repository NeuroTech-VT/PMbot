# PMbot

Simple project management tools in Discord

## Main features

- Todo list per project --> TO IMPLEMENT
- Daily check-in & reminders (before deadline and after deadline if late)
- End of week note every Sunday evening

## Status: AISLOP, REWRITE PENDING

Implemented in this repo as one bot process. `bot.py` loads two cogs:

- `pmbot.py`: one list per project, `!todo`, `!add`, `!tasks`, `!edit`, `!del`, `!newproject`, `!delproject`, `!settings`, the four buttons, the daily check-in, two reminders per task and the Sunday note. JSON store in `data/servers/`, nightly backup in `data/backups/`.

- `calendar_bot.py` + `calendar_core.py` + `calendar_card.py/html`: a month card per project, typed `!cal` commands, one day as text and event reminders in a DM. No hour-by-hour view and no free text: calendar changes are typed.

- `test_pmbot.py`, `test_calendar.py`, `test_help.py`: the pure parts, the ticker (with stub guilds), and the user docs.

`!help` in Discord is the manual: an overview plus `!help tasks`, `!help projects`, `!help taps`, `!help calendar`, `!help settings`. The text lives in `help_text.py`, and `test_help.py` fails if a command has no page mentioning it or a page outgrows one message — so the help cannot drift.

No Pi, no LLM, no database. This repo is separate from the Discord bridge, which keeps its own personal PMbot and a fuller calendar.

## Running: AISLOP, REWRITE PENDING

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # fill in DISCORD_TOKEN
.venv/bin/python bot.py
```

The month card is rendered by a headless browser named in `CALENDAR_BROWSER` (`librewolf` by default). `data/` is created on the first write and is gitignored.

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
.venv/bin/python -m unittest test_pmbot test_calendar test_help -v
```

## How it is put together: AISLOP, REWRITE PENDING

One process, no services. `data/servers/<guild_id>.json` per server, four keys: `meta` (timezone, home channel, check-in and note times, who may start projects), `projects`, `tasks`, `runs` (last check-in per person, last note, last backup). Tasks are a flat list — a task carries a `project` id, so it never nests and moving one is a field change. Dates are `YYYY-MM-DD` in the server's timezone; button ids read `task:7:done`, so they survive a restart with no lookup table.

A project is a server-wide thing, not a channel. `!newproject Halo` only takes a name; the list is summoned with `!todo Halo` in whatever channel you are in, and each project keeps one live list per channel in `lists: {channel_id: message_id}`. Check-ins, reminders and the note all go to `meta.channel` — the channel the first project was created in until you move it with `!settings channel #pm`. Each project has its own calendar, one file each in `data/calendar/`.

Scheduling is one `discord.ext.tasks` loop every 30 seconds asking four questions — is a check-in due, is a task reminder due, is a calendar reminder due, is the note due — all answered from the same task and event data. Every send stamps what it did, so a missed slot fires once after boot instead of twice. Reminders are thresholds, not windows: "due tomorrow" means "due within a day and not reminded yet", so a bot that was off for a day still sends it.

Writes go through a temporary file, fsync and `os.replace`, with the previous version kept as `.json.bak`, under one lock per server; a nightly copy of `data/servers/` lands in `data/backups/` and the newest 14 are kept. A corrupt file falls back to its `.bak`, and with the bot stopped the JSON can be edited by hand.

## Decisions worth remembering: AISLOP, REWRITE PENDING

- **The todo list is the product.** One message per project per channel, summoned with `!todo` and edited in place forever — asking twice refines the same message. Nothing is pinned. Rows past the fifth have no buttons (Discord allows five action rows), so the message says so and `!tasks` carries the ids the check-in cannot reach.
- **Four taps, never a typed status.** Done, On it, Blocked, Not started. Owners move their own rows; an admin can move anyone's, and a row whose owner is a bare name is open to the channel.
- **Corrections are typed, one task at a time.** `!tasks` prints every open task with its id, `!edit 7 <words> @who <date>` changes only what you give it, `!del 7` removes it.
- **Help is part of the product.** `!help` is written for the person who has never used a bot command, in the same words the bot uses: no "invoke the command", no config table. It is tested.
- **Two reminders, ever**: one before the deadline, one when it first slips. After that a task waits for the note.
- **No AI.** Tasks and calendar changes are typed; there is no model call anywhere.
- **College team, not a company.** Weeks run Sunday to Saturday, weekend work counts, and nothing measures hours.
- **JSON on disk**, one file per server, hand editable while the bot is stopped.
- **A project has no channel.** It is a name and a set of tasks, server-wide. The list shows up wherever you ask for it and is kept up to date in each channel separately.
- **Deleting is a button, not a typed word.** `!delproject <name>` prints the project, its counts and how many list messages it will remove, then waits for a press; only the admin who ran it can confirm, and its tasks go with it.

## Further Applications

- Could use the same tool for personal things too
- Project management is useful for AI agent orchestration
