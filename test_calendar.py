"""Tests for the calendar's pure parts: parsing, receipts, reminders, the month card.

Run with the repo venv: `.venv/bin/python -m unittest test_calendar -v`
"""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import json

import calendar_bot
import calendar_card
import calendar_core as core
import pmbot
from calendar_core import iso_date

TODAY = date(2026, 12, 2)  # a Wednesday


class Isolated(unittest.TestCase):
    """Every test gets its own data/calendar, no real events touched."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._data_dir = core.DATA_DIR
        self._cards_file = core.CARDS_FILE
        core.DATA_DIR = Path(self.tmp.name)
        core.CARDS_FILE = core.DATA_DIR / "cards.json"

    def tearDown(self):
        core.DATA_DIR = self._data_dir
        core.CARDS_FILE = self._cards_file
        self.tmp.cleanup()


class DateParsing(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(core.find_date("2026-12-05", TODAY)[0], date(2026, 12, 5))

    def test_day_month(self):
        self.assertEqual(core.find_date("3 dec", TODAY)[0], date(2026, 12, 3))
        self.assertEqual(core.find_date("dec 3", TODAY)[0], date(2026, 12, 3))

    def test_measurements_are_not_dates(self):
        # Same rule as the task parser: a bare d/m and a bare d/w are measurements.
        for text in ("cut 1/2 plywood", "sand 8.5 mm edge", "3D print the case"):
            self.assertIsNone(core.find_date(text, TODAY), text)

    def test_relative(self):
        self.assertEqual(core.find_date("+2d", TODAY)[0], date(2026, 12, 4))
        self.assertEqual(core.find_date("in 2w", TODAY)[0], date(2026, 12, 16))

    def test_weekday_and_words(self):
        self.assertEqual(core.find_date("fri", TODAY)[0], date(2026, 12, 4))
        self.assertEqual(core.find_date("monday", TODAY)[0], date(2026, 12, 7))
        self.assertEqual(core.find_date("tomorrow", TODAY)[0], date(2026, 12, 3))

    def test_nothing(self):
        self.assertIsNone(core.find_date("fix the monitor", TODAY))

    def test_span_covers_the_match(self):
        text = "Lunch fri 12:30"
        day, (start, end) = core.find_date(text, TODAY)
        self.assertEqual(text[start:end], "fri")
        self.assertEqual(day, date(2026, 12, 4))


class PerProjectCalendars(unittest.TestCase):
    """Each project keeps its own events; the old shared calendar is adopted once."""

    GID = 4242

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.saved = (core.DATA_DIR, core.CACHE_DIR, core.CARDS_FILE,
                      pmbot.DATA_DIR, pmbot.BACKUP_DIR, dict(pmbot._servers))
        core.DATA_DIR = root / "calendar"
        core.CACHE_DIR = core.DATA_DIR / "cache"
        core.CARDS_FILE = core.DATA_DIR / "cards.json"
        pmbot.DATA_DIR = root / "servers"
        pmbot.BACKUP_DIR = root / "backups"
        pmbot._servers.clear()
        self.write_projects([{"id": 1, "name": "Halo", "lists": {}},
                             {"id": 2, "name": "Jig", "lists": {}}])

    def tearDown(self):
        (core.DATA_DIR, core.CACHE_DIR, core.CARDS_FILE,
         pmbot.DATA_DIR, pmbot.BACKUP_DIR, _) = self.saved
        pmbot._servers.clear()
        pmbot._servers.update(self.saved[5])

    def write_projects(self, projects):
        pmbot.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (pmbot.DATA_DIR / f"{self.GID}.json").write_text(json.dumps({
            "meta": {"tz": "UTC"}, "projects": projects, "tasks": [], "runs": {},
        }))
        pmbot._servers.pop(self.GID, None)

    def guild(self):
        return type("G", (), {"id": self.GID})()

    def test_scopes_are_per_project(self):
        self.assertEqual(core.scope_for(7, 3, 9), "guild_7_p3")
        self.assertEqual(core.scope_project("guild_7_p3"), 3)
        self.assertEqual(core.scope_guild("guild_7_p3"), 7)
        self.assertIsNone(core.scope_project("guild_7"))

    def test_events_do_not_leak_between_projects(self):
        halo = core.scope_for(self.GID, 1, 0)
        jig = core.scope_for(self.GID, 2, 0)
        core.add(halo, title="Halo review", when=iso_date(TODAY))
        self.assertEqual([e["title"] for e in core.events(halo)], ["Halo review"])
        self.assertEqual(core.events(jig), [])

    def test_resolve_defaults_to_the_only_project(self):
        self.write_projects([{"id": 1, "name": "Halo", "lists": {}}])
        scope, project, rest = calendar_bot.resolve("fri", self.guild(), 9)
        self.assertEqual(project["name"], "Halo")
        self.assertEqual(scope, core.scope_for(self.GID, 1, 9))
        self.assertEqual(rest, "fri")

    def test_resolve_needs_a_name_with_two_projects(self):
        scope, project, rest = calendar_bot.resolve("fri", self.guild(), 9)
        self.assertIsNone(scope)
        self.assertIsNone(project)

    def test_resolve_takes_the_longest_project_name(self):
        self.write_projects([{"id": 1, "name": "Project", "lists": {}},
                             {"id": 2, "name": "Project A", "lists": {}}])
        scope, project, rest = calendar_bot.resolve("Project A add Lunch fri", self.guild(), 9)
        self.assertEqual(project["name"], "Project A")
        self.assertEqual(rest, "add Lunch fri")

    def test_resolve_leaves_a_non_project_word_alone(self):
        scope, project, rest = calendar_bot.resolve("add Lunch fri", self.guild(), 9)
        self.assertIsNone(scope)

    def test_adopting_the_legacy_calendar(self):
        legacy = core.legacy_scope(self.GID)
        core.add(legacy, title="Old shared thing", when=iso_date(TODAY))
        self.write_projects([{"id": 1, "name": "Halo", "lists": {}}])

        scope, project, _ = calendar_bot.resolve("", self.guild(), 9)

        self.assertEqual([e["title"] for e in core.events(scope)], ["Old shared thing"])
        self.assertFalse(core._path(legacy).exists())
        self.assertEqual(core.events(legacy), [])

    def test_legacy_calendar_is_not_stolen_by_one_of_many_projects(self):
        legacy = core.legacy_scope(self.GID)
        core.add(legacy, title="Old shared thing", when=iso_date(TODAY))
        calendar_bot.resolve("Halo", self.guild(), 9)
        self.assertTrue(core._path(legacy).exists())

    def test_adopting_by_hand_with_many_projects(self):
        legacy = core.legacy_scope(self.GID)
        core.add(legacy, title="Old shared thing", when=iso_date(TODAY))
        self.assertEqual(core.adopt_legacy(self.GID, 2), 1)
        jig = core.scope_for(self.GID, 2, 9)
        self.assertEqual([e["title"] for e in core.events(jig)], ["Old shared thing"])
        self.assertFalse(core._path(legacy).exists())
        self.assertEqual(core.adopt_legacy(self.GID, 2), 0)

    def test_adopted_events_get_fresh_ids(self):
        halo = core.scope_for(self.GID, 1, 9)
        core.add(halo, title="Existing", when=iso_date(TODAY))
        legacy = core.legacy_scope(self.GID)
        core.add(legacy, title="Old", when=iso_date(TODAY))
        core.adopt_legacy(self.GID, 1)
        ids = [e["id"] for e in core.events(halo)]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(ids), [1, 2])

    def test_cards_are_keyed_per_channel_and_project(self):
        halo = card_key = calendar_bot.card_key(55, core.scope_for(self.GID, 1, 9))
        jig = calendar_bot.card_key(55, core.scope_for(self.GID, 2, 9))
        self.assertNotEqual(halo, jig)
        self.assertTrue(card_key.endswith("guild_%d_p1" % self.GID))

    def test_old_card_entries_are_migrated(self):
        core.save_cards({"55": {"scope": core.scope_for(self.GID, 1, 9),
                                "message_id": 7, "year": 2026, "month": 12}})
        calendar_bot.migrate_cards()
        cards = core.load_cards()
        self.assertNotIn("55", cards)
        self.assertEqual(cards[calendar_bot.card_key(55, core.scope_for(self.GID, 1, 9))]["message_id"], 7)

    def test_tasks_overlay_is_per_project(self):
        self.write_projects([{"id": 1, "name": "Halo", "lists": {}},
                             {"id": 2, "name": "Jig", "lists": {}}])
        data = pmbot.server(self.GID)
        data["tasks"] += [
            {"id": 1, "project": 1, "text": "Halo task", "due": iso_date(TODAY), "state": "open"},
            {"id": 2, "project": 2, "text": "Jig task", "due": iso_date(TODAY), "state": "open"},
        ]
        halo = calendar_bot.tasks_for_scope(core.scope_for(self.GID, 1, 9))
        self.assertEqual([t["title"] for t in halo], ["Halo task"])


class Timezones(unittest.TestCase):
    def test_guild_scope_reads_the_pmbot_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = core.BASE_DIR
            core.BASE_DIR = Path(tmp)
            try:
                (Path(tmp) / "data" / "servers").mkdir(parents=True)
                (Path(tmp) / "data" / "servers" / "5.json").write_text(
                    '{"meta": {"tz": "Pacific/Auckland"}}')
                self.assertEqual(core.zone_for("guild_5").key, "Pacific/Auckland")
            finally:
                core.BASE_DIR = saved

    def test_missing_store_falls_back_to_utc(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = core.BASE_DIR
            core.BASE_DIR = Path(tmp)
            try:
                self.assertEqual(core.zone_for("guild_999999").key, "UTC")
            finally:
                core.BASE_DIR = saved

    def test_today_comes_from_the_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = core.BASE_DIR
            core.BASE_DIR = Path(tmp)
            try:
                (Path(tmp) / "data" / "servers").mkdir(parents=True)
                (Path(tmp) / "data" / "servers" / "5.json").write_text(
                    '{"meta": {"tz": "Pacific/Kiritimati"}}')
                self.assertEqual(core.today_local("guild_5"), core.now_local("guild_5").date())
                self.assertNotEqual(core.zone_for("guild_5").utcoffset(None),
                                    core.zone_for(None).utcoffset(None))
            finally:
                core.BASE_DIR = saved


class TimeParsing(unittest.TestCase):
    def test_clock(self):
        self.assertEqual(core.find_time("Lunch 12:30")[0], "12:30")
        self.assertIsNone(core.find_time("Lunch 12:30")[1])

    def test_range_sets_duration(self):
        clock, duration, _ = core.find_time("Standup 9:00-9:30")
        self.assertEqual(clock, "09:00")
        self.assertEqual(duration, 30)

    def test_am_pm(self):
        self.assertEqual(core.find_time("Call 2pm")[0], "14:00")
        self.assertEqual(core.find_time("Call 9am")[0], "09:00")

    def test_bad_time(self):
        self.assertIsNone(core.find_time("Call 25:00"))
        self.assertIsNone(core.find_time("Call 13pm"))


class AddParsing(unittest.TestCase):
    def test_full_line(self):
        parsed = core.parse_add("Lunch fri 12:30 @ Café Nord", TODAY)
        self.assertEqual(parsed["title"], "Lunch")
        self.assertEqual(parsed["date"], "2026-12-04")
        self.assertEqual(parsed["time"], "12:30")
        self.assertEqual(parsed["place"], "Café Nord")

    def test_no_time(self):
        parsed = core.parse_add("Dentist fri", TODAY)
        self.assertEqual(parsed["title"], "Dentist")
        self.assertIsNone(parsed["time"])

    def test_duration(self):
        parsed = core.parse_add("Standup fri 09:00-09:20", TODAY)
        self.assertEqual(parsed["duration"], 20)

    def test_needs_a_date(self):
        self.assertIsNone(core.parse_add("Lunch with Ana", TODAY))


class Events(Isolated):
    def add(self, title="Lunch", when="2026-12-04", **fields):
        return core.add("guild_1", title=title, when=when, **fields)

    def test_add_assigns_ids(self):
        first = self.add()
        second = self.add("Dentist")
        self.assertEqual((first["id"], second["id"]), (1, 2))
        self.assertEqual(len(core.events("guild_1")), 2)

    def test_add_validates(self):
        with self.assertRaises(core.CalendarError):
            self.add(title="  ")
        with self.assertRaises(core.CalendarError):
            self.add(when="4 dec")
        with self.assertRaises(core.CalendarError):
            self.add(time="25:00")

    def test_update_resets_the_reminder(self):
        event = self.add(time="12:30")
        core.mark_reminded("guild_1", event["id"])
        moved, changed, _ = core.update("guild_1", event["id"], time="13:15")
        self.assertEqual(changed, ["time"])
        self.assertFalse(moved["reminded"])
        self.assertEqual(core.events("guild_1")[0]["time"], "13:15")

    def test_update_duration_bounds(self):
        event = self.add(time="12:30")
        with self.assertRaises(core.CalendarError):
            core.update("guild_1", event["id"], duration=2000)

    def test_remove_and_restore(self):
        event = self.add()
        removed = core.remove("guild_1", event["id"])
        self.assertEqual(removed["title"], "Lunch")
        self.assertEqual(core.events("guild_1"), [])
        back = core.restore("guild_1", removed)
        self.assertFalse(back["reminded"])
        self.assertEqual(core.events("guild_1")[0]["title"], "Lunch")

    def test_find_remove_is_text_and_day(self):
        self.add("Lunch with Ana", "2026-12-04")
        self.add("Lunch with Sam", "2026-12-04")
        self.add("Dentist", "2026-12-05")
        hits = core.find_remove("guild_1", "fri lunch", TODAY)
        self.assertEqual({hit["title"] for hit in hits}, {"Lunch with Ana", "Lunch with Sam"})

    def test_month_and_day_views(self):
        self.add("Lunch", "2026-12-04", time="12:30")
        self.add("Dentist", "2026-12-05")
        self.assertEqual(len(core.month_events("guild_1", 2026, 12)), 2)
        self.assertEqual(core.day_events("guild_1", date(2026, 12, 4))[0]["title"], "Lunch")


class Receipts(Isolated):
    def test_add_receipt_names_day_time_place(self):
        event = core.add("guild_1", title="Lunch", when="2026-12-04", time="12:30", place="Café")
        text = core.add_receipt(event)
        self.assertIn("Lunch", text)
        self.assertIn("Fri 4 Dec", text)
        self.assertIn("12:30", text)
        self.assertIn("Café", text)

    def test_update_receipt(self):
        event = core.add("guild_1", title="Lunch", when="2026-12-04", time="12:30")
        moved, changed, before = core.update("guild_1", event["id"], time="13:15")
        self.assertIn("Moved", core.update_receipt(moved, changed, before))

    def test_remove_receipt(self):
        event = core.add("guild_1", title="Lunch", when="2026-12-04")
        self.assertIn("Removed", core.remove_receipt(event))


class Reminders(Isolated):
    def test_timed_event_fifteen_minutes_before(self):
        event = core.add("guild_1", title="Lunch", when="2026-12-04", time="12:30")
        self.assertEqual(core.reminder_at(event), datetime(2026, 12, 4, 12, 15))

    def test_all_day_is_0900(self):
        event = core.add("guild_1", title="Trip", when="2026-12-04")
        self.assertEqual(core.reminder_at(event), datetime(2026, 12, 4, 9, 0))

    def test_done_has_no_reminder(self):
        event = core.add("guild_1", title="Trip", when="2026-12-04")
        core.update("guild_1", event["id"], done=True)
        self.assertIsNone(core.reminder_at(core.events("guild_1")[0]))

    def test_due_reminders_skips_a_missed_slot(self):
        core.add("guild_1", title="Old", when="2026-12-01", time="10:00")
        soon = core.add("guild_1", title="Soon", when="2026-12-02", time="11:50")
        due = core.due_reminders(now=datetime(2026, 12, 2, 11, 45))
        self.assertEqual([event["id"] for _, event in due], [soon["id"]])
        stored = {event["title"]: event for event in core.events("guild_1")}
        self.assertTrue(stored["Old"]["reminded"])  # stamped, fires once
        self.assertFalse(stored["Soon"]["reminded"])


class MonthCard(Isolated):
    def test_card_shows_the_month_marks_and_footer(self):
        core.add("guild_1", title="Lunch", when="2026-12-04", time="12:30")
        doc, height = calendar_card.build("guild_1", 2026, 12)
        self.assertIn("December 2026", doc)
        self.assertIn("December", doc)
        self.assertIn("✱", doc)
        self.assertIn("Lunch", doc)
        self.assertGreater(height, 300)


if __name__ == "__main__":
    unittest.main()
