"""PMbot: the project manager Discord bot.

Run: `.venv/bin/python bot.py` (Discord token in `.env`).
"""

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

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

HELP = """**PMbot** · one list per project, one check-in a day, two reminders a task.
Owners move their own rows; an admin can move any.

**Projects**
• `!todo [project]` the list, late first, four taps per row
• `!add [project:] <task> @who <date>` one task, one owner, one date · `!add Print the jig @sam fri`
• `!tasks [project]` the ids · `!edit 7 new words @sam fri` fix one · `!del 7` remove it
• `!newproject <name>` start a project (admin) · `!delproject <name>` delete one (admin)
• `!settings [channel|checkin|note|tz|staff]` where it talks, times, timezone (admin)

**Calendar**
• `!cal` the month card, with ◀ Today ▶
• `!cal fri` one day · `!cal tomorrow` · `!cal 3 dec`
• `!cal add <what> <when>` an event with optional time and place
• `!cal rm <when> <what>` cancel an event"""

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


@bot.command(name="help")
async def help_command(ctx: commands.Context):
    await ctx.reply(HELP, mention_author=False)


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise SystemExit("DISCORD_TOKEN missing (copy .env.example to .env)")

bot.run(token, log_handler=None, log_level=logging.INFO)
