import os
import sys
import signal
import asyncio
import logging
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

DEBUG = (
    "--debug" in sys.argv
    or "--dev" in sys.argv
    or os.environ.get("DEBUG", "false").lower() == "true"
)
logger = logging.getLogger("voyager_discord")
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

load_dotenv()

DISCORD_ADMIN_ID = int(os.environ.get("DISCORD_ADMIN_ID", "0"))
if not DISCORD_ADMIN_ID:
    raise ValueError("DISCORD_ADMIN_ID environment variable must be set")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable must be set")

intents = nextcord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

shutdown_event = asyncio.Event()


def signal_handler(signum, _frame):
    logger.info(
        f"\n\n\nReceived signal {signum}, shutting down - please wait a few seconds"
    )
    shutdown_event.set()


async def shutdown_bot():
    await cleanup()
    await bot.close()
    logger.info("farewell")


async def cleanup():
    logger.info("Cleaning up cogs")

    for cog_name in bot.cogs:
        cog = bot.cogs[cog_name]
        if hasattr(cog, "cleanup"):
            try:
                await cog.cleanup()
            except Exception as e:
                logger.error(f"Error during cleanup of {cog_name}: {e}")

    logger.info("Cleaned up cogs")


def load_cogs():
    cogs = [
        "cogs.events",
        "cogs.game",
        "cogs.admin",
        "cogs.server",
        "cogs.debug",
        "cogs.tasks",
    ]

    for cog in cogs:
        try:
            bot.load_extension(cog)
            logger.info(f"Loaded cog: {cog}")

        except Exception as e:
            logger.error(f"Failed to load cog {cog}: {e}")


async def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    load_cogs()

    # llm black magic to receive ctrl c

    bot_task = asyncio.create_task(bot.start(DISCORD_BOT_TOKEN))
    shutdown_task = asyncio.create_task(shutdown_event.wait())

    try:
        await asyncio.wait(
            [bot_task, shutdown_task], return_when=asyncio.FIRST_COMPLETED
        )

        if shutdown_event.is_set():
            logger.debug("Shutdown signal received, closing bot")
            await cleanup()
            await bot.close()
            logger.info("farewell, my friend")
        else:
            logger.error("Bot stopped unexpectedly")

    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        if not bot_task.done():
            bot_task.cancel()
        if not shutdown_task.done():
            shutdown_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown complete")
    except Exception as e:
        logger.error(f"Unexpected error during shutdown: {e}")
        sys.exit(1)
