"""Tests for the calendar's pure parts: parsing, receipts, reminders, the month card.

Run with the repo venv: `.venv/bin/python -m unittest test_calendar -v`
"""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import calendar_card
import calendar_core as core

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

    def test_slash_is_month_first(self):
        self.assertEqual(core.find_date("12/5", TODAY)[0], date(2026, 12, 5))

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
