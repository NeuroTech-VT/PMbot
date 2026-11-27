"""Tests for PMbot's pure parts: dates, rules, rendering, storage.

Run with the repo venv: `.venv/bin/python -m unittest test_pmbot test_calendar -v`
"""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
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

    def test_day_month_and_slash(self):
        self.assertEqual(p.parse_when("5 dec", TODAY)[0], date(2026, 12, 5))
        self.assertEqual(p.parse_when("5/12", TODAY)[0], date(2026, 12, 5))

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
        text = p.render_note(self.project, tasks, TODAY)
        for section in ("Finished", "Slipped", "At risk", "Next week"):
            self.assertIn(section, text)
        self.assertIn("Frame machined", text)
        self.assertIn("2 days late", text)

    def test_checkin_lists_only_that_person(self):
        text = p.render_checkin("sam", [task(due="2026-11-30")], TODAY)
        self.assertIn("Daily check-in · sam", text)
        self.assertIn("2 days late", text)


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
