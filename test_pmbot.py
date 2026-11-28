"""Tests for PMbot's pure parts: dates, rules, rendering, storage.

Run with the repo venv: `.venv/bin/python -m unittest test_pmbot test_calendar -v`
"""

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pmbot as p

TODAY = date(2026, 12, 2)  # a Wednesday


def task(tid=1, text="Print the jig", owner="sam", due="2026-12-05",
         state="open", done_at=None):
    return {"id": tid, "project": 1, "text": text, "owner": owner,
            "owner_id": "42", "due": due, "state": state, "done_at": done_at,
            "reminded_due": None, "reminded_late": None, "created_by": "1"}


class DateParsing(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(p.parse_when("ship 2026-12-05 please", TODAY)[0], date(2026, 12, 5))

    def test_relative(self):
        self.assertEqual(p.parse_when("+3d", TODAY)[0], date(2026, 12, 5))
        self.assertEqual(p.parse_when("in 2w", TODAY)[0], date(2026, 12, 16))

    def test_weekday(self):
        self.assertEqual(p.parse_when("fri", TODAY)[0], date(2026, 12, 4))
        self.assertEqual(p.parse_when("monday", TODAY)[0], date(2026, 12, 7))

    def test_words(self):
        self.assertEqual(p.parse_when("today", TODAY)[0], TODAY)
        self.assertEqual(p.parse_when("tomorrow", TODAY)[0], date(2026, 12, 3))

    def test_day_month(self):
        self.assertEqual(p.parse_when("5 dec", TODAY)[0], date(2026, 12, 5))
        self.assertEqual(p.parse_when("5 December 2026", TODAY)[0], date(2026, 12, 5))

    def test_measurements_are_not_dates(self):
        # The whole point of dropping bare d/m and bare d/w: a design team writes
        # dimensions and printer names, and those must stay in the task's words.
        for text in ("cut 1/2 plywood", "sand 8.5 mm edge", "3D print the case",
                     "order 4W LED strip", "print at 210x297"):
            self.assertIsNone(p.parse_when(text, TODAY), text)

    def test_relative_needs_its_prefix(self):
        self.assertIsNone(p.parse_when("3d", TODAY))
        self.assertEqual(p.parse_when("in 3 days", TODAY)[0], date(2026, 12, 5))
        self.assertEqual(p.parse_when("in 2 weeks", TODAY)[0], date(2026, 12, 16))

    def test_nothing(self):
        self.assertIsNone(p.parse_when("print the jig", TODAY))

    def test_monitor_is_not_monday(self):
        self.assertIsNone(p.parse_when("fix the monitor", TODAY))

    def test_times(self):
        self.assertEqual(p.parse_hhmm("09:30").hour, 9)
        self.assertIsNone(p.parse_hhmm("off"))
        self.assertIsNone(p.parse_hhmm("25:00"))
        self.assertEqual(p.parse_note("sun 18:00")[0], 6)
        self.assertIsNone(p.parse_note("someday 18:00"))


class ProjectLifecycle(unittest.TestCase):
    def test_remove_project_takes_its_tasks(self):
        data = {"projects": [{"id": 1, "name": "Halo"}, {"id": 2, "name": "Jig"}],
                "tasks": [task(1), task(2, text="other"), task(3, text="third")],
                "runs": {"checkin": {}, "note": None, "backup": None},
                "meta": {"tz": "UTC"}}
        data["tasks"][1]["project"] = 2
        data["tasks"][2]["project"] = 2

        project, removed = p.remove_project(data, 1)

        self.assertEqual(project["name"], "Halo")
        self.assertEqual(removed, 1)
        self.assertEqual([t["id"] for t in data["tasks"]], [2, 3])
        self.assertEqual([x["id"] for x in data["projects"]], [2])

    def test_remove_unknown_project_changes_nothing(self):
        data = {"projects": [{"id": 1, "name": "Halo"}], "tasks": [task(1)],
                "runs": {"checkin": {}, "note": None, "backup": None}, "meta": {"tz": "UTC"}}
        self.assertEqual(p.remove_project(data, 9), (None, 0))
        self.assertEqual(len(data["projects"]), 1)
        self.assertEqual(len(data["tasks"]), 1)

    def test_ids_of_remaining_projects_are_untouched(self):
        data = {"projects": [{"id": 1, "name": "A"}, {"id": 5, "name": "B"}],
                "tasks": [], "runs": {"checkin": {}, "note": None, "backup": None},
                "meta": {"tz": "UTC"}}
        p.remove_project(data, 1)
        self.assertEqual(p.next_id(data["projects"]), 6)


class Member:
    def __init__(self, mid, admin=False):
        self.id = mid
        self.guild_permissions = type("P", (), {"administrator": admin, "manage_guild": False})()


class Permissions(unittest.TestCase):
    def test_owner_can_tap(self):
        self.assertTrue(p.PMBot.can_tap(Member(42), task()))

    def test_someone_else_cannot(self):
        self.assertFalse(p.PMBot.can_tap(Member(7), task()))

    def test_admin_can_tap_anything(self):
        self.assertTrue(p.PMBot.can_tap(Member(7, admin=True), task()))

    def test_name_only_owner_is_open(self):
        loose = dict(task(), owner_id=None, owner="sam")
        self.assertTrue(p.PMBot.can_tap(Member(7), loose))


class Projects(unittest.TestCase):
    data = {"projects": [{"id": 1, "name": "Halo", "lists": {}},
                         {"id": 2, "name": "Jig", "lists": {}}]}

    def test_named_project_beats_the_only_one(self):
        one = {"projects": [{"id": 1, "name": "Halo", "lists": {}}]}
        self.assertEqual(p.PMBot.resolve_project(one, None)["name"], "Halo")

    def test_unknown_name_does_not_guess(self):
        two = dict(self.data)
        self.assertIsNone(p.PMBot.resolve_project(two, "Website"))
        self.assertIsNone(p.PMBot.resolve_project(two, None))
        self.assertEqual(p.PMBot.resolve_project(two, "jig")["name"], "Jig")

    def test_add_prefix_takes_its_project(self):
        project, _, rest = p.take_project_ref(self.data, "Halo: print the jig @sam fri")
        self.assertEqual(project["name"], "Halo")
        self.assertEqual(rest, "print the jig @sam fri")

    def test_add_prefix_is_not_forced(self):
        for line in ("fix the halo mount @sam fri", "Halo is broken @sam fri", "Halo:"):
            project, _, rest = p.take_project_ref(self.data, line)
            self.assertIsNone(project, line)
            self.assertEqual(rest, line)

    def test_legacy_channel_becomes_a_list_and_a_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = (p.DATA_DIR, dict(p._servers))
            p.DATA_DIR = Path(tmp)
            p._servers.clear()
            try:
                (Path(tmp) / "5.json").write_text(json.dumps({
                    "meta": {"tz": "UTC"},
                    "projects": [{"id": 1, "name": "Halo",
                                  "channel_id": "77", "todo_message_id": "900"}],
                    "tasks": [],
                }))
                data = p.server(5)
                self.assertEqual(data["meta"]["channel"], "77")
                self.assertEqual(data["projects"][0]["lists"], {"77": "900"})
                self.assertNotIn("channel_id", data["projects"][0])
            finally:
                p.DATA_DIR = saved[0]
                p._servers.clear()
                p._servers.update(saved[1])


class Rules(unittest.TestCase):
    def test_late_and_days_late(self):
        late = task(due="2026-11-30")
        self.assertTrue(p.is_late(late, TODAY))
        self.assertEqual(p.days_late(late, TODAY), 2)
        self.assertFalse(p.is_late(task(due="2026-12-05"), TODAY))

    def test_done_is_never_late(self):
        self.assertFalse(p.is_late(task(due="2026-11-01", state="done"), TODAY))

    def test_at_risk(self):
        self.assertTrue(p.at_risk(task(due="2026-12-04"), TODAY))          # not started, soon
        self.assertFalse(p.at_risk(task(due="2026-12-20"), TODAY))         # not started, far
        self.assertTrue(p.at_risk(task(due="2026-12-20", state="blocked"), TODAY))
        self.assertFalse(p.at_risk(task(due="2026-11-30"), TODAY))         # slipped is its own list

    def test_seven_day_window(self):
        fresh = task(1, "fresh", state="done", done_at="2026-11-28T10:00:00")
        old = task(2, "old", state="done", done_at="2026-11-01T10:00:00", due="2026-11-01")
        ids = [t["id"] for t in p.finished_since([fresh, old], TODAY)]
        self.assertIn(fresh["id"], ids)
        self.assertNotIn(old["id"], ids)

    def test_counts(self):
        tasks = [task(due="2026-11-30"), task(due="2026-12-05"), task(state="done", done_at="x")]
        self.assertEqual(p.counts(tasks, TODAY), (2, 1, 1))


class Rendering(unittest.TestCase):
    project = {"id": 1, "name": "Project Halo"}

    def test_late_comes_first_and_done_collapses(self):
        tasks = [
            task(1, "Bracket CAD", due="2026-12-10"),
            task(2, "Machine the brackets", due="2026-11-30"),
            task(3, "Old thing", state="done", done_at="2026-12-01T09:00:00"),
        ]
        text = p.render_list(self.project, tasks, TODAY)
        self.assertLess(text.index("Machine the brackets"), text.index("Bracket CAD"))
        self.assertIn("1 late", text)
        self.assertIn("1 finished", text)
        self.assertNotIn("Old thing", text)

    def test_empty_list_points_at_add(self):
        text = p.render_list(self.project, [], TODAY)
        self.assertIn("!add", text)

    def test_more_than_25_rows_is_capped(self):
        tasks = [task(i, f"task {i}", due="2026-12-09") for i in range(1, 41)]
        text = p.render_list(self.project, tasks, TODAY)
        self.assertIn("more, the daily check-in", text)
        self.assertLess(len(text), 2000)
        self.assertLessEqual(text.count("\n**sam**"), 50)  # owner line per rendered task
        self.assertNotIn("task 40", text)

    def test_owner_and_date_get_their_own_lines(self):
        text = p.task_line(task(1, "Print the jig", owner="sam", due="2026-12-05"), TODAY)
        self.assertEqual(
            text.splitlines(),
            ["⬜ Print the jig", "**sam**", "Sat 5 Dec"],
        )

    def test_status_is_an_emoji(self):
        for state, icon in (("open", "⬜"), ("on", "🔄"), ("blocked", "🚫"), ("done", "✅")):
            self.assertTrue(p.task_line(task(state=state, done_at="x"), TODAY).startswith(icon))

    def test_late_task_shows_days_late_on_the_date_line(self):
        text = p.task_line(task(due="2026-11-30"), TODAY)
        self.assertEqual(text.splitlines()[-1], "Mon 30 Nov, 2 days late")

    def test_note_sections(self):
        tasks = [
            task(1, "Frame machined", state="done", done_at="2026-12-01T10:00:00"),
            task(2, "Brackets", due="2026-11-30"),
            task(3, "Drop test", due="2026-12-04", state="blocked"),
            task(4, "Review", due="2026-12-05"),
        ]
        text = p.render_note([(self.project, tasks)], TODAY)
        for section in ("Finished", "Slipped", "At risk", "Next week"):
            self.assertIn(section, text)
        self.assertIn("Frame machined", text)
        self.assertIn("2 days late", text)

    def test_checkin_lists_only_that_person(self):
        text = p.render_checkin("sam", [("Halo", task(due="2026-11-30"))], TODAY)
        self.assertIn("Daily check-in · sam", text)

    def test_checkin_groups_by_project_only_when_needed(self):
        one = p.render_checkin("sam", [("Halo", task())], TODAY)
        self.assertNotIn("**Halo**", one)
        two = p.render_checkin("sam", [("Halo", task()), ("Jig", task(2))], TODAY)
        self.assertIn("**Halo**", two)
        self.assertIn("**Jig**", two)

    def test_note_covers_every_project(self):
        text = p.render_note([(self.project, [task()]),
                              ({"id": 2, "name": "Jig"}, [task(2, "Glue up")])], TODAY)
        self.assertIn("Project Halo", text)
        self.assertIn("Jig", text)
        self.assertIn("Glue up", text)

    def test_note_only_has_one_header(self):
        text = p.render_note([(self.project, [task()]),
                              ({"id": 2, "name": "Jig"}, [task(2, "Glue up")])], TODAY)
        self.assertEqual(text.count("End of week note"), 1)


class Storage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = (p.DATA_DIR, p.BACKUP_DIR, dict(p._servers))
        p.DATA_DIR = Path(self.tmp.name) / "servers"
        p.BACKUP_DIR = Path(self.tmp.name) / "backups"
        p._servers.clear()

    def tearDown(self):
        p.DATA_DIR, p.BACKUP_DIR = self.saved[0], self.saved[1]
        p._servers.clear()
        p._servers.update(self.saved[2])

    def test_defaults_and_roundtrip(self):
        data = p.server(99)
        self.assertEqual(data["projects"], [])
        self.assertEqual(data["meta"]["checkin"], "09:30")
        data["projects"].append({"id": 1, "name": "Halo", "channel_id": "5"})
        p.save(99)
        written = json.loads((p.DATA_DIR / "99.json").read_text())
        self.assertEqual(written["projects"][0]["name"], "Halo")
        self.assertFalse((p.DATA_DIR / "99.json.tmp").exists())

    def test_backup_and_prune(self):
        for i in range(p.KEEP_BACKUPS + 3):
            (p.BACKUP_DIR / (TODAY - timedelta(days=i)).isoformat()).mkdir(parents=True)
        data = p.server(99)
        data["projects"].append({"id": 1, "name": "Halo"})
        p.save(99)
        p.backup_all(TODAY)
        self.assertTrue((p.BACKUP_DIR / TODAY.isoformat() / "99.json").exists())
        self.assertEqual(len(list(p.BACKUP_DIR.iterdir())), p.KEEP_BACKUPS)

    def test_hand_edited_json_is_normalised(self):
        p.DATA_DIR.mkdir(parents=True)
        (p.DATA_DIR / "7.json").write_text('{"meta": {"tz": "UTC"}, "projects": []}')
        data = p.server(7)
        self.assertEqual(data["tasks"], [])
        self.assertIn("checkin", data["runs"])


class ButtonIds(unittest.TestCase):
    def test_ids_carry_guild_task_state(self):
        view = p.task_view(123, [task(7)])
        ids = [item.custom_id for item in view.children]
        self.assertEqual(len(ids), 4)
        self.assertIn("pm:t:123:7:done", ids)
        self.assertIn("pm:t:123:7:blocked", ids)

    def test_five_rows_max(self):
        view = p.task_view(1, [task(i) for i in range(1, 9)])
        self.assertEqual(len(view.children), 20)
        self.assertEqual({item.row for item in view.children}, {0, 1, 2, 3, 4})


if __name__ == "__main__":
    unittest.main()


class FakeMessage:
    id = 1
    edited = None

    async def edit(self, content=None, view=None):
        self.content = content
        self.edited = content


class FakePerms:
    send_messages = True


class FakeChannel:
    def __init__(self, cid):
        self.id = cid
        self.sent = []
        self.msgs = {}

    def permissions_for(self, _me):
        return FakePerms()

    async def send(self, text=None, **kwargs):
        message = FakeMessage()
        message.id = len(self.sent) + 1
        message.edited = None
        self.msgs[message.id] = message
        self.sent.append(text)
        return message

    async def fetch_message(self, mid):
        return self.msgs[mid]


class FakeGuild:
    def __init__(self, gid, channels):
        self.id = gid
        self.text_channels = channels
        self.me = type("Me", (), {"id": 1})()

    def get_channel(self, cid):
        return next((c for c in self.text_channels if c.id == cid), None)


class Ticker(unittest.IsolatedAsyncioTestCase):
    """The scheduling rules, which used to be untestable inside the cog."""

    GID = 4242

    def setUp(self):
        self.channel = FakeChannel(11)
        self.guild = FakeGuild(self.GID, [self.channel])
        self.cog = p.PMBot(type("Bot", (), {"guilds": [self.guild]})())
        p._servers[self.GID] = {
            "meta": {"tz": "UTC", "checkin": "09:30", "note": "sun 18:00",
                     "staff_can_create": False, "channel": "11"},
            "projects": [{"id": 1, "name": "Halo", "lists": {}}],
            "tasks": [task(1, due="2026-12-03"), task(2, "Other", due="2026-12-09")],
            "runs": {"checkin": {}, "note": None, "backup": "2026-12-02"},
        }
        self.data = p._servers[self.GID]

    def tearDown(self):
        p._servers.pop(self.GID, None)

    def at(self, day, hour, minute=0):
        from datetime import datetime
        return datetime(2026, 12, day, hour, minute)

    async def test_checkin_is_one_message_per_person_per_day(self):
        await self.cog.run_checkins(self.guild, self.data, self.at(3, 10))
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("Daily check-in", self.channel.sent[0])
        await self.cog.run_checkins(self.guild, self.data, self.at(3, 11))
        self.assertEqual(len(self.channel.sent), 1, "same day, no second check-in")

    async def test_checkin_waits_for_its_time(self):
        await self.cog.run_checkins(self.guild, self.data, self.at(3, 8))
        self.assertEqual(self.channel.sent, [])

    async def test_checkin_spans_projects(self):
        self.data["projects"].append({"id": 2, "name": "Jig", "lists": {}})
        self.data["tasks"].append(task(3, "Glue up", due="2026-12-04"))
        self.data["tasks"][2]["project"] = 2
        await self.cog.run_checkins(self.guild, self.data, self.at(3, 10))
        self.assertEqual(len(self.channel.sent), 1, "one person, one message")
        self.assertIn("Glue up", self.channel.sent[0])

    async def test_reminder_is_a_threshold_not_a_window(self):
        # The bot was off on the 2nd, so this is the first tick that sees the task.
        await self.cog.run_reminders(self.guild, self.data, self.at(3, 10))
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("is due today", self.channel.sent[0])
        await self.cog.run_reminders(self.guild, self.data, self.at(3, 10, 30))
        self.assertEqual(len(self.channel.sent), 1, "two reminders ever")

    async def test_reminder_day_before(self):
        await self.cog.run_reminders(self.guild, self.data, self.at(2, 10))
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("is due tomorrow", self.channel.sent[0])

    async def test_late_reminder_fires_once(self):
        late = self.data["tasks"][0]
        late["reminded_due"] = "2026-12-02"
        await self.cog.run_reminders(self.guild, self.data, self.at(4, 10))
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("day late", self.channel.sent[0])

    async def test_sunday_note_covers_every_project_once(self):
        self.data["projects"].append({"id": 2, "name": "Jig", "lists": {}})
        self.data["tasks"].append(task(3, "Glue up", due="2026-12-04"))
        self.data["tasks"][2]["project"] = 2
        await self.cog.run_note(self.guild, self.data, self.at(6, 18, 30))
        self.assertEqual(len(self.channel.sent), 1)
        self.assertIn("Halo", self.channel.sent[0])
        self.assertIn("Jig", self.channel.sent[0])
        await self.cog.run_note(self.guild, self.data, self.at(6, 19))
        self.assertEqual(len(self.channel.sent), 1, "one note a week")

    async def test_posts_go_to_the_home_channel(self):
        other = FakeChannel(12)
        self.guild.text_channels = [other, self.channel]
        self.data["meta"]["channel"] = "12"
        await self.cog.run_checkins(self.guild, self.data, self.at(3, 10))
        self.assertEqual(len(other.sent), 1)
        self.assertEqual(self.channel.sent, [])

    async def test_todo_edits_its_list_instead_of_posting_again(self):
        channel = FakeChannel(11)
        await self.cog.render_project(self.GID, self.data["projects"][0], channel)
        first = dict(self.data["projects"][0]["lists"])
        await self.cog.render_project(self.GID, self.data["projects"][0], channel)
        self.assertEqual(self.data["projects"][0]["lists"], first, "same message id")
        self.assertEqual(len(channel.sent), 1, "one message, edited in place")

    async def test_backup_runs_once_a_day_for_all_servers(self):
        saved = p.BACKUP_DIR
        with tempfile.TemporaryDirectory() as tmp:
            p.BACKUP_DIR = Path(tmp) / "backups"
            p.DATA_DIR = Path(tmp) / "servers"
            p.DATA_DIR.mkdir()
            (p.DATA_DIR / f"{self.GID}.json").write_text("{}")
            try:
                await self.cog.run_backup(self.GID, self.data, self.at(3, 4))
                made = list(p.BACKUP_DIR.iterdir())
                self.assertEqual(len(made), 1)
                await self.cog.run_backup(self.GID, self.data, self.at(3, 5))
                self.assertEqual(len(list(p.BACKUP_DIR.iterdir())), 1, "one copy a day")
            finally:
                p.BACKUP_DIR = saved

    async def test_no_home_channel_means_first_usable(self):
        self.data["meta"]["channel"] = None
        await self.cog.run_checkins(self.guild, self.data, self.at(3, 10))
        self.assertEqual(len(self.channel.sent), 1)
