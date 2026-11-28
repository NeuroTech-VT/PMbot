"""Discord front-end for the calendar: card image, buttons, commands, reminders.

All scheduling logic lives in `calendar_core`; this cog only carries it to Discord.
Typed commands only: add, remove, one day, the month. No free text, no model.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import discord
from discord.ext import commands, tasks

import calendar_card
import calendar_core as core

log = logging.getLogger("calendar")

CARD_FILE_NAME = "calendar.png"


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def scope_for(ctx_or_message) -> str:
    guild = getattr(ctx_or_message, "guild", None)
    author = getattr(ctx_or_message, "author", None)
    return core.scope_for(guild.id if guild else None, author.id)


def tasks_for_scope(scope: str) -> list[dict]:
    """Open tasks with due dates: a read-only overlay from PMbot's store."""
    if not scope.startswith("guild_"):
        return []
    try:
        guild_id = int(scope.split("_", 1)[1])
    except (IndexError, ValueError):
        return []
    import pmbot  # local, so the calendar still works without PMbot

    data = pmbot.server(guild_id)
    found = []
    for task in data.get("tasks", []):
        if task.get("state") == "done" or not task.get("due"):
            continue
        found.append({
            "date": task["due"],
            "title": task.get("text") or "task",
            "owner": task.get("owner") or "",
            "state": task.get("state") or "open",
        })
    found.sort(key=lambda task: (task["date"], task["title"].lower()))
    return found


# ------------------------------------------------------------------- ui views
class UndoView(discord.ui.View):
    def __init__(self, cog: "Calendar", scope: str, event: dict):
        super().__init__(timeout=300)
        self.cog = cog
        self.scope = scope
        self.event = event

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary)
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            restored = core.restore(self.scope, self.event)
        except Exception as exc:  # noqa: BLE001
            log.exception("undo failed")
            await interaction.response.send_message(f"Could not restore it: {exc}", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(view=None)
        await interaction.followup.send(f"Restored **{restored['title']}**", ephemeral=True)
        await self.cog.refresh_card(interaction.channel, self.scope)


class ReminderView(discord.ui.View):
    def __init__(self, cog: "Calendar", scope: str, event_id: int):
        super().__init__(timeout=3600)
        self.cog = cog
        self.scope = scope
        self.event_id = event_id

    @discord.ui.button(label="Snooze 10 min", style=discord.ButtonStyle.secondary)
    async def snooze(self, interaction: discord.Interaction, button: discord.ui.Button):
        core.snooze(self.scope, self.event_id, 10)
        self.stop()
        await interaction.response.edit_message(view=None)
        await interaction.followup.send("Snoozed 10 minutes.", ephemeral=True)

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.secondary)
    async def dismiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)


class CardNavView(discord.ui.View):
    """One persistent view shared by every card message."""

    def __init__(self, cog: "Calendar"):
        super().__init__(timeout=None)
        self.cog = cog

    async def _move(self, interaction: discord.Interaction, delta: int) -> None:
        found = self.cog.entry_for_message(interaction.message.id)
        if not found:
            await interaction.response.send_message("This card is out of date, run `!cal` again.", ephemeral=True)
            return
        channel_id, entry = found
        if delta == 0:
            now = core.now_local(entry["scope"])
            year, month = now.year, now.month
        else:
            year, month = shift_month(int(entry["year"]), int(entry["month"]), delta)

        await interaction.response.defer()
        try:
            file = await self.cog.render_file(entry["scope"], year, month)
            await interaction.message.edit(attachments=[file], view=self)
            self.cog.save_entry(channel_id, year=year, month=month)
        except Exception as exc:  # noqa: BLE001
            log.exception("card navigation failed")
            await interaction.followup.send(f"Could not render the calendar: {exc}", ephemeral=True)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="cal:prev")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, -1)

    @discord.ui.button(label="Today", style=discord.ButtonStyle.secondary, custom_id="cal:today")
    async def current(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, 0)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="cal:next")
    async def following(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, 1)


# --------------------------------------------------------------------- the cog
class Calendar(commands.Cog):
    """A month card, typed commands and event reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._render_lock = asyncio.Lock()
        self.nav_view = CardNavView(self)

    async def cog_load(self) -> None:
        self.bot.add_view(self.nav_view)
        self.reminder_loop.start()

    async def cog_unload(self) -> None:
        self.reminder_loop.cancel()

    # ------------------------------------------------------------- rendering
    async def render_file(self, scope: str, year: int, month: int) -> discord.File:
        tasks = tasks_for_scope(scope)
        async with self._render_lock:
            path = await asyncio.to_thread(calendar_card.render, scope, year, month, tasks)
        return discord.File(str(path), filename=CARD_FILE_NAME)
    # --------------------------------------------------------------- cards
    def entry_for_message(self, message_id: int):
        for channel_id, entry in core.load_cards().items():
            try:
                if int(entry.get("message_id") or 0) == message_id:
                    return channel_id, entry
            except (TypeError, ValueError):
                continue
        return None

    def save_entry(self, channel_id, **fields) -> None:
        cards = core.load_cards()
        entry = cards.get(str(channel_id))
        if entry is None:
            return
        entry.update(fields)
        core.save_cards(cards)

    async def show_card(
        self,
        channel: discord.abc.Messageable,
        scope: str,
        year: int | None = None,
        month: int | None = None,
    ) -> discord.Message:
        now = core.now_local(scope)
        year = year or now.year
        month = month or now.month
        file = await self.render_file(scope, year, month)

        cards = core.load_cards()
        entry = cards.get(str(channel.id))
        message: discord.Message | None = None
        if entry:
            try:
                message = await channel.fetch_message(int(entry["message_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                message = None

        if message:
            if getattr(channel, "last_message_id", None) == message.id:
                await message.edit(attachments=[file], view=self.nav_view)
            else:
                await message.delete()
                message = await channel.send(file=file, view=self.nav_view)
        else:
            message = await channel.send(file=file, view=self.nav_view)

        cards[str(channel.id)] = {
            "scope": scope,
            "message_id": message.id,
            "year": year,
            "month": month,
        }
        core.save_cards(cards)
        return message

    async def refresh_card(self, channel: discord.abc.Messageable, scope: str) -> None:
        cards = core.load_cards()
        entry = cards.get(str(channel.id))
        if not entry:
            if getattr(channel, "name", "") == "calendar":
                await self.show_card(channel, scope)
            return
        await self.show_card(channel, scope, int(entry["year"]), int(entry["month"]))
    # ------------------------------------------------------------ commands
    @commands.command(name="cal", usage="[day | add … | rm …]")
    async def cal(self, ctx: commands.Context, *, args: str = ""):
        """Calendar: month card, day list, or add/remove an event."""
        scope = scope_for(ctx)
        text = (args or "").strip()

        if not text:
            try:
                await self.show_card(ctx.channel, scope)
            except Exception as exc:  # noqa: BLE001
                log.exception("card failed")
                await ctx.reply(f"Could not render the calendar card: {exc}", mention_author=False)
            return

        lowered = text.lower()
        if lowered.startswith("add "):
            await self.add_command(ctx, scope, text[4:].strip())
            return
        if lowered.startswith(("rm ", "remove ", "cancel ")):
            await self.remove_command(ctx, scope, text.split(" ", 1)[1].strip())
            return

        hit = core.find_date(text, core.today_local(scope))
        if hit:
            leftover = (text[: hit[1][0]] + text[hit[1][1]:]).strip()
            if not leftover:
                await ctx.reply(
                    core.day_text(scope, hit[0], tasks=tasks_for_scope(scope)),
                    mention_author=False,
                )
                return

        await ctx.reply(
            "`!cal` month · `!cal fri` one day · "
            "`!cal add <what> <when> [hh:mm] [@ place]` · `!cal rm <when> <what>`",
            mention_author=False,
        )

    async def add_command(self, ctx: commands.Context, scope: str, text: str) -> None:
        parsed = core.parse_add(text, scope=scope)
        if not parsed:
            await ctx.reply(
                "Say what and when: `!cal add Lunch fri 12:30 @ Café Nord`",
                mention_author=False,
            )
            return
        try:
            event = core.add(
                scope,
                created_by=ctx.author.id,
                channel_id=ctx.channel.id,
                **parsed,
            )
        except core.CalendarError as exc:
            await ctx.reply(f"⚠️ {exc}", mention_author=False)
            return
        await ctx.reply(core.add_receipt(event), mention_author=False)
        try:
            await self.refresh_card(ctx.channel, scope)
        except Exception:  # noqa: BLE001
            log.exception("card refresh failed")

    async def remove_command(self, ctx: commands.Context, scope: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            await ctx.reply("Say which: `!cal rm fri lunch`", mention_author=False)
            return
        matches = core.find_remove(scope, text)
        if not matches:
            await ctx.reply(f"Nothing matches `{text}`.", mention_author=False)
            return
        if len(matches) > 1:
            listing = "\n".join(
                f"**{event['time'] or 'all day'}** {event['title']}" for event in matches[:8]
            )
            await ctx.reply(
                f"That matches {len(matches)} events:\n{listing}\nAdd a time or another word.",
                mention_author=False,
            )
            return

        event = core.remove(scope, matches[0]["id"])
        await ctx.reply(
            core.remove_receipt(event),
            mention_author=False,
            view=UndoView(self, scope, event),
        )
        try:
            await self.refresh_card(ctx.channel, scope)
        except Exception:  # noqa: BLE001
            log.exception("card refresh failed")

    # ------------------------------------------------------------ reminders
    @tasks.loop(seconds=30)
    async def reminder_loop(self) -> None:
        try:
            due = core.due_reminders()
        except Exception:  # noqa: BLE001
            log.exception("reminder scan failed")
            return

        for scope, event in due:
            core.mark_reminded(scope, event["id"])
            user_id = event.get("created_by")
            if not user_id:
                continue
            try:
                user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                await user.send(
                    self.reminder_text(event, scope),
                    view=ReminderView(self, scope, event["id"]),
                )
                log.info("reminder sent for %s (%s)", event["title"], event["date"])
            except Exception as exc:  # noqa: BLE001
                log.warning("reminder DM failed for %s: %s", user_id, exc)

    @reminder_loop.before_loop
    async def before_reminders(self) -> None:
        await self.bot.wait_until_ready()

    @staticmethod
    def reminder_text(event: dict, scope: str | None = None) -> str:
        place = f" · {event['place']}" if event.get("place") else ""
        if not event.get("time"):
            return f"**Today · {event['title']}**\nAll day{place}"
        start = datetime.fromisoformat(f"{event['date']}T{event['time']}")
        minutes = max(0, round((start - core.now_local(scope)).total_seconds() / 60))
        head = f"In {minutes} min" if minutes > 1 else "Now"
        span = event["time"]
        if event.get("duration"):
            total = (int(event["time"][:2]) * 60 + int(event["time"][3:]) + int(event["duration"])) % (24 * 60)
            span = f"{span}–{total // 60:02d}:{total % 60:02d}"
        return f"**{head} · {event['title']}**\n{span}{place}"
