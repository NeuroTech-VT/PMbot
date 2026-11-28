"""Tests for `!help`: the pages must fit in a message and name real commands.

Docs drift silently, so this checks help_text against the cogs themselves.
"""

import re
import unittest

import discord.ext.commands

import calendar_bot
import help_text
import pmbot

LIMIT = 1900  # Discord's 2000 minus room for the rest of the message


def commands(*cog_classes) -> list:
    found = []
    for cog_class in cog_classes:
        found.extend(discord.ext.commands.Cog.get_commands(object.__new__(cog_class)))
    return found


def command_names(*cog_classes) -> set[str]:
    """Every name a user can type: canonical names plus aliases."""
    known: set[str] = set()
    for command in commands(*cog_classes):
        known.add(command.name)
        known.update(command.aliases)
    return known


class Pages(unittest.TestCase):
    def pages(self):
        yield "overview", help_text.OVERVIEW
        yield from help_text.PAGES.items()

    def test_every_page_fits_in_one_message(self):
        for name, text in self.pages():
            self.assertLess(len(text), LIMIT, f"{name} is {len(text)} characters")

    def test_every_documented_command_exists(self):
        known = command_names(pmbot.PMBot, calendar_bot.Calendar) | {"help", "h", "commands"}
        for name, text in self.pages():
            for hit in re.findall(r"`!([a-z][a-z-]*)", text):
                self.assertIn(hit, known, f"{name} mentions !{hit}")

    def test_every_command_appears_somewhere(self):
        documented = " ".join(text for _, text in self.pages())
        for command in commands(pmbot.PMBot, calendar_bot.Calendar):
            self.assertIn(f"!{command.name}", documented, f"!{command.name} is undocumented")

    def test_topics_resolve_and_aliases_work(self):
        for topic in help_text.PAGES:
            self.assertIsNotNone(help_text.page_for(topic))
        self.assertIs(help_text.page_for("task"), help_text.PAGES["tasks"])
        self.assertIs(help_text.page_for(" CAL "), help_text.PAGES["calendar"])
        self.assertIs(help_text.page_for(None), help_text.OVERVIEW)
        self.assertIs(help_text.page_for(""), help_text.OVERVIEW)

    def test_unknown_topic_is_not_a_page(self):
        self.assertIsNone(help_text.page_for("nonsense"))

    def test_overview_points_at_the_topics(self):
        for topic in help_text.PAGES:
            self.assertIn(f"!help {topic}", help_text.OVERVIEW)


if __name__ == "__main__":
    unittest.main()
