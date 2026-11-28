"""The text of `!help`, one page per topic.

Kept out of bot.py so it can be tested without a Discord connection: every page
must fit in one message, and every `!command` it names must exist.
"""

OVERVIEW = """**PMbot**: per-project todo list & calendar

**Most-used features:**
* `!todo` shows todo list for a current project
* `!todo list` shows todo list for all projects
* `!add <todo_text> @<person_assigned> <day_of_the_week>`
* `!cal` month-view of the calendar
* `!cal <day_of_the_week>` shows one day

**More**
`!help tasks` · `!help projects` · `!help taps` · `!help calendar` · `!help settings`"""

TASKS = """**Tasks**.

* `!add <task> @who <date>` e.g. `!add <todo_text> @<person_assigned> <day_of_the_week>`
* `!add <project>: <task> @who <date>` once there is more than one project:
  `!add Project A: <todo_text> @<person_assigned> <day_of_the_week>`, or `!add #2: glue up @<person_assigned> <day_of_the_week>`
* `!todo [project]` the list · `!todo list` every project with counts
* `!tasks [project]` every open task with its id
* `!edit <id> <words> @who <date>` change only what you give: `!edit 7 sand @<person_assigned> thu`
* `!del <id>` remove one task: `!del 7`

**Dates** `<day_of_the_week>` · `tomorrow` · `3 dec` · `2026-12-05` · `+3d` · `in 2 weeks`"""

PROJECTS = """**Projects** server-wide, not tied to a channel.

* `!newproject Project A` start one (admin)
* `!todo Project A` summon its list wherever you are; the message stays and is edited in place
* `!todo list` every project with open, late and done counts
* `!delproject Project A` shows the project, its counts and its list messages, then asks for a press (admin)
"""

TAPS = """
✅ Done · 🔄 On it · 🚫 Blocked · ⬜ Not started
"""

CALENDAR = """**Calendar**

* `!cal` the month card, with ◀ Today ▶
* `!cal <day_of_the_week>` · `!cal tomorrow` · `!cal 3 dec` one day as text
* `!cal add Lunch <day_of_the_week> 12:30 @ Café Nord` what, when, optional time and place
* `!cal rm <day_of_the_week> lunch` cancel one; if several match it lists them and asks for another word"""

SETTINGS = """**Settings** (admin)

`!settings` shows them; change one key at a time.

* `!settings channel #pm` where check-ins, reminders and the note go. Default: the channel the
  first project was created in.
* `!settings checkin 09:30` when the daily check-in posts, or `off`
* `!settings note sun 18:00` the end of week note, or `off`
* `!settings tz Europe/Lisbon` the timezone dates, times and the calendar follow
* `!settings staff on` let non-admins start projects (deleting stays admin-only)"""

PAGES = {
    "tasks": TASKS,
    "projects": PROJECTS,
    "taps": TAPS,
    "calendar": CALENDAR,
    "settings": SETTINGS,
}

ALIASES = {"task": "tasks", "todo": "tasks", "add": "tasks", "edit": "tasks", "del": "tasks",
           "project": "projects", "newproject": "projects", "delproject": "projects",
           "tap": "taps", "buttons": "taps", "checkin": "taps", "check-in": "taps",
           "cal": "calendar", "events": "calendar", "settings": "settings", "setup": "settings"}


def page_for(topic: str | None) -> str | None:
    """`!help tasks` -> the tasks page. Unknown topics return None."""
    key = (topic or "").strip().lower().lstrip("!")
    if not key:
        return OVERVIEW
    return PAGES.get(ALIASES.get(key, key))
