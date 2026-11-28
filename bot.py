"""PMbot: the project manager Discord bot.

Run: `.venv/bin/python bot.py` (Discord token in `.env`).
"""

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

import help_text
from calendar_bot import Calendar
from pmbot import PMBot

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    handlers=[
        logging.FileHandler(filename=str(BASE_DIR / "pmbot.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pmbot")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class Client(commands.Bot):
    async def setup_hook(self) -> None:
        await self.add_cog(PMBot(self))
        await self.add_cog(Calendar(self))

    async def on_ready(self) -> None:
        log.info("logged in as %s (id=%s)", self.user, self.user.id)


bot = Client(command_prefix="!", intents=intents, help_command=None)


@bot.command(name="help", aliases=["h", "commands"])
async def help_command(ctx: commands.Context, *, topic: str | None = None):
    """`!help` for the overview, `!help tasks` (and friends) for the detail."""
    page = help_text.page_for(topic)
    if page is None:
        topics = " · ".join(f"`!help {name}`" for name in help_text.PAGES)
        await ctx.reply(f"No page for `{topic}`.\n{topics}", mention_author=False)
        return
    await ctx.reply(page, mention_author=False)


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise SystemExit("DISCORD_TOKEN missing (copy .env.example to .env)")

bot.run(token, log_handler=None, log_level=logging.INFO)
