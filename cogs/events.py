import os
import sys
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import nextcord
from nextcord.ext import commands

from instance import Instance, GameState
from config import MAX_CHANNELS, SERVER_DEFAULTS, ERROR_RESPONSE

DEBUG = (
    "--debug" in sys.argv
    or "--dev" in sys.argv
    or os.environ.get("DEBUG", "false").lower() == "true"
)
logger = logging.getLogger("voyager_discord")


@dataclass
class ServerState:
    # basically just wrapped up state behind guild_id
    guild_id: int
    lobby_channel_id: Optional[int] = None
    category_id: Optional[int] = None
    waiting_users: List[int] = None
    instances: Dict[int, Instance] = None
    round_timers: Dict[int, asyncio.Task] = None
    available_game_channels: List[int] = None  # pool of v-inst- channels for reuse
    used_game_channels: Dict[int, str] = None  # channel_id -> game_name mapping
    all_game_channels: List[int] = None  # total 10 channels
    initialized: bool = SERVER_DEFAULTS[
        "initialized"
    ]  # whether the server has been automatically initialized
    max_channels: int = SERVER_DEFAULTS["max_channels"]

    def __post_init__(self):
        if self.waiting_users is None:
            self.waiting_users = []
        if self.instances is None:
            self.instances = {}
        if self.round_timers is None:
            self.round_timers = {}
        if self.available_game_channels is None:
            self.available_game_channels = []
        if self.used_game_channels is None:
            self.used_game_channels = {}
        if self.all_game_channels is None:
            self.all_game_channels = []


SERVERS: Dict[int, ServerState] = {}  # guild_id: ServerState


def get_server_state(guild_id: int) -> ServerState:
    """Get or create server state"""
    if guild_id not in SERVERS:
        SERVERS[guild_id] = ServerState(
            guild_id=guild_id, max_channels=SERVER_DEFAULTS["max_channels"]
        )

    return SERVERS[guild_id]


async def ensure_voyager_category(guild: nextcord.Guild) -> nextcord.CategoryChannel:
    """Ensure Voyager Games category exists"""
    server_state = get_server_state(guild.id)

    if server_state.category_id:
        category = guild.get_channel(server_state.category_id)
        if category:
            return category

    category = nextcord.utils.get(guild.categories, name="Voyager")
    if not category:
        category = await guild.create_category(
            "Voyager", reason="Auto-created for Voyager game instances"
        )
        logger.debug(f"Created Voyager category in {guild.name}")

    server_state.category_id = category.id
    return category


async def find_or_create_lobby(guild: nextcord.Guild) -> nextcord.TextChannel:
    """Find or create lobby channel for the server"""
    server_state = get_server_state(guild.id)

    if server_state.lobby_channel_id:
        lobby = guild.get_channel(server_state.lobby_channel_id)
        if lobby:
            return lobby

    lobby = nextcord.utils.get(guild.text_channels, name="voyager-lobby")
    if not lobby:
        category = await ensure_voyager_category(guild)
        lobby = await guild.create_text_channel(
            "voyager-lobby",
            category=category,
            topic="Join games with /waitlist! Check status with /state",
            reason="Auto-created Voyager lobby channel",
        )
        logger.debug(f"Created voyager-lobby channel in {guild.name}")

    server_state.lobby_channel_id = lobby.id
    return lobby


async def discover_existing_game_channels(
    guild: nextcord.Guild,
) -> List[nextcord.TextChannel]:
    """Discover existing v-inst- channels in the server (limited to 10)"""
    game_channels = []
    server_state = get_server_state(guild.id)

    for channel in guild.text_channels:
        if channel.name.startswith("v-inst-"):
            game_channels.append(channel)
            logger.debug(
                f"Found existing game channel: #{channel.name} in {guild.name}"
            )

    game_channels.sort(key=lambda c: c.created_at)
    game_channels = game_channels[:MAX_CHANNELS]

    server_state.all_game_channels = [c.id for c in game_channels]

    logger.debug(f"Limited to {len(game_channels)} game channels in {guild.name}")

    return game_channels


async def purge_game_channel(channel: nextcord.TextChannel) -> bool:
    """Purge all messages from a game channel to reset it"""
    try:
        # BULK DELETE
        try:
            await channel.purge(bulk=True)
            logger.debug(f"Bulk deleted messages in {channel.name}")
        except Exception as e:
            logger.warning(
                f"Bulk delete failed in {channel.name}, falling back to individual: {e}"
            )
            # fallback (please not necessary)
            async for message in channel.history(limit=None):
                try:
                    await message.delete()
                except Exception as e:
                    logger.debug(f"Failed to delete message in {channel.name}: {e}")

        # ATOMIC PERMISSION RESET TO PREVENT FLASH
        # probably should explain for future me ^^ so if you update otherwise, it causes a "flash" of the channel
        # where permissions briefly entirely disappear
        try:
            everyone_role = channel.guild.default_role
            default_overwrites = {
                everyone_role: nextcord.PermissionOverwrite(
                    view_channel=False, send_messages=False
                ),
                channel.guild.me: nextcord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                ),
            }
            await channel.edit(
                topic="Available game channel - waiting for assignment",
                overwrites=default_overwrites,
            )
            logger.debug(f"Reset permissions for {channel.name}")
        except Exception as e:
            logger.debug(f"Failed to reset permissions for {channel.name}: {e}")
            try:
                await channel.edit(
                    topic="Available game channel - waiting for assignment"
                )
            except Exception as e2:
                logger.debug(f"Failed to update topic for {channel.name}: {e2}")

        logger.debug(f"Purged game channel #{channel.name}")
        return True

    except Exception as e:
        logger.error(f"Failed to purge channel #{channel.name}: {e}")
        return False


async def allocate_game_channel(
    guild: nextcord.Guild, game_name: str
) -> Optional[nextcord.TextChannel]:
    """Allocate a game channel from the available pool - NO AUTO-CREATION"""
    server_state = get_server_state(guild.id)

    if not server_state.initialized:
        logger.warning(f"Server {guild.name} not initialized | ID: {guild.id}")
        return None

    if server_state.available_game_channels:
        channel_id = server_state.available_game_channels.pop(0)
        channel = guild.get_channel(channel_id)

        if channel:
            if await purge_game_channel(channel):
                try:
                    await channel.edit(
                        topic=f"Game: {game_name} - Active game in progress",
                    )
                except Exception as e:
                    logger.debug(f"Could not update channel topic: {e}")

                server_state.used_game_channels[channel_id] = game_name
                logger.debug(
                    f"Allocated existing channel #{channel.name} for game {game_name}"
                )
                return channel
            else:
                logger.debug(
                    f"Failed to purge channel {channel_id}, removing from pool"
                )

    logger.warning(
        ERROR_RESPONSE["no_available_channels_guild"].format(guild_name=guild.name)
    )
    return None


async def release_game_channel(guild: nextcord.Guild, channel_id: int) -> bool:
    """Release a game channel back to the available pool instead of deleting it"""
    server_state = get_server_state(guild.id)

    if channel_id in server_state.used_game_channels:
        del server_state.used_game_channels[channel_id]

    channel = guild.get_channel(channel_id)
    if not channel:
        return False

    # release to pool
    if await purge_game_channel(channel):
        server_state.available_game_channels.append(channel_id)
        logger.debug(f"Released game channel #{channel.name} back to pool")
        return True
    else:
        logger.debug(f"Failed to purge channel #{channel.name}, not returning to pool")
        return False


async def initialize_app(bot):
    logger.debug("Initializing Discord bot...")

    async def process_guild(guild):
        logger.debug(f"Processing server: {guild.name} ({guild.id})")

        try:
            bot_member = guild.get_member(bot.user.id)
            if not bot_member:
                logger.error(f"Could not get bot member in {guild.name}")
                return

            required_permissions = [
                "send_messages",
                "read_messages",
                "manage_channels",
                "manage_messages",
                "embed_links",
            ]

            missing_permissions = []
            for perm in required_permissions:
                if not getattr(bot_member.guild_permissions, perm, False):
                    missing_permissions.append(perm)

            if missing_permissions:
                logger.debug(
                    f"Bot missing permissions in {guild.name}: {missing_permissions}"
                )
                for channel in guild.text_channels:
                    if channel.permissions_for(bot_member).send_messages:
                        try:
                            embed = nextcord.Embed(
                                title="Voyaging",
                                description="⚠️ I need some permissions to work properly",
                                color=nextcord.Color.red(),
                            )
                            embed.add_field(
                                name="Missing Permissions",
                                value=", ".join(missing_permissions),
                                inline=False,
                            )
                            embed.add_field(
                                name="Required Permissions",
                                value="• Send Messages\n• Read Messages\n• Manage Channels\n• Manage Messages\n• Embed Links",
                                inline=False,
                            )
                            embed.add_field(
                                name="Next Steps",
                                value="Grant the missing permissions and restart the bot",
                                inline=False,
                            )
                            await channel.send(embed=embed)
                            logger.debug(f"Sent permission warning to {guild.name}")
                            break
                        except Exception as e:
                            logger.debug(
                                f"Failed to send permission warning in {guild.name}: {e}"
                            )
                            continue
                return

            server_state = get_server_state(guild.id)

            lobby_exists = (
                nextcord.utils.get(guild.text_channels, name="voyager-lobby")
                is not None
            )

            game_channels_exist = any(
                channel.name.startswith("v-inst-") for channel in guild.text_channels
            )

            if lobby_exists and game_channels_exist:
                logger.debug(f"Starting server {guild.name}")

                lobby_channel = await find_or_create_lobby(guild)

                existing_channels = await discover_existing_game_channels(guild)

                purge_tasks = []
                for channel in existing_channels:
                    purge_tasks.append(purge_game_channel(channel))

                purge_results = await asyncio.gather(
                    *purge_tasks, return_exceptions=True
                )

                for i, result in enumerate(purge_results):
                    if isinstance(result, Exception):
                        logger.error(
                            f"Failed to purge channel {existing_channels[i].name}: {result}"
                        )
                    elif result:
                        server_state.available_game_channels.append(
                            existing_channels[i].id
                        )
                        logger.debug(
                            f"Added existing channel #{existing_channels[i].name} to available pool"
                        )

                server_state.initialized = True

                embed = nextcord.Embed(
                    title="Voyaging",
                    description="Voyager is ready to host games.",
                    color=nextcord.Color.green(),
                )
                embed.add_field(
                    name="Status",
                    value="Ready!",
                    inline=True,
                )
                embed.add_field(
                    name="Game Channels",
                    value=f"{len(existing_channels)}/{MAX_CHANNELS}",
                    inline=True,
                )
                embed.add_field(
                    name="Available",
                    value=f"{len(server_state.available_game_channels)}",
                    inline=True,
                )
                embed.add_field(
                    name="Commands",
                    value="• Use `/waitlist` to join games\n• Use `/state` to check status\n• Use `/admin create` to add more channels",
                    inline=False,
                )
            else:
                lobby_channel = await find_or_create_lobby(guild)

                embed = nextcord.Embed(
                    title="Voyager Online",
                    description="The party game bot is ready! Setup required.",
                    color=nextcord.Color.orange(),
                )
                embed.add_field(
                    name="Status", value="⚠️ Manual setup required", inline=True
                )
                embed.add_field(
                    name="Missing",
                    value="Lobby channel" if not lobby_exists else "Game channels",
                    inline=True,
                )
                embed.add_field(
                    name="Setup",
                    value="• Use `/admin create` to create game channels\n• Server will auto-initialize when ready",
                    inline=False,
                )

            await lobby_channel.send(embed=embed)
            logger.debug(f"Processed server {guild.name}")

        except Exception as e:
            logger.warning(f"Failed to process server {guild.name}: {e}")

    guild_tasks = [process_guild(guild) for guild in bot.guilds]
    await asyncio.gather(*guild_tasks, return_exceptions=True)  # gather, my beloved

    logger.info("Discord bot initialization complete")


class EventsCog(commands.Cog):
    """Event handlers for the bot"""

    def __init__(self, bot):
        self.bot = bot

    async def cleanup(self):
        """Clean up running timers and tasks"""
        for guild_id, server_state in SERVERS.items():
            for timer in server_state.round_timers.values():
                if timer and not timer.done():
                    timer.cancel()
                    logger.debug(f"Cancelled round timer for guild {guild_id}")
            server_state.round_timers.clear()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Discord bot logged in as {self.bot.user}")
        logger.debug(f"Bot is in {len(self.bot.guilds)} servers")

        from cogs.tasks import set_bot, start_process_waitlist_task

        set_bot(self.bot)
        start_process_waitlist_task()

        await initialize_app(self.bot)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Handle bot joining a new server"""
        logger.debug(f"Joined new server: {guild.name} ({guild.id})")

        try:
            bot_member = guild.get_member(self.bot.user.id)
            if not bot_member:
                logger.error(f"Could not get bot member in {guild.name}")
                return

            required_permissions = [
                "send_messages",
                "read_messages",
                "manage_channels",
                "manage_messages",
                "embed_links",
            ]

            missing_permissions = []
            for perm in required_permissions:
                if not getattr(bot_member.guild_permissions, perm, False):
                    missing_permissions.append(perm)

            if missing_permissions:
                logger.debug(
                    f"Bot missing permissions in {guild.name}: {missing_permissions}"
                )
                for channel in guild.text_channels:
                    if channel.permissions_for(bot_member).send_messages:
                        try:
                            embed = nextcord.Embed(
                                title="Not Voyaging Yet",
                                description="⚠️ Bot is missing required permissions",
                                color=nextcord.Color.red(),
                            )
                            embed.add_field(
                                name="Missing Permissions",
                                value=", ".join(missing_permissions),
                                inline=False,
                            )
                            embed.add_field(
                                name="Required Permissions",
                                value="• Send Messages\n• Read Messages\n• Manage Channels\n• Manage Messages\n• Embed Links",
                                inline=False,
                            )
                            embed.add_field(
                                name="Next Steps",
                                value="Grant the missing permissions and restart the bot",
                                inline=False,
                            )
                            await channel.send(embed=embed)
                            logger.debug(f"Sent permission warning to {guild.name}")
                            return
                        except Exception as e:
                            logger.error(
                                f"Failed to send permission warning in {guild.name}: {e}"
                            )
                            continue

                logger.error(
                    f"Could not send permission warning in {guild.name} - no accessible channels"
                )
                return

            server_state = get_server_state(guild.id)

            lobby_exists = (
                nextcord.utils.get(guild.text_channels, name="voyager-lobby")
                is not None
            )

            game_channels_exist = any(
                channel.name.startswith("v-inst-") for channel in guild.text_channels
            )

            if lobby_exists and game_channels_exist:
                logger.debug(f"Starting server {guild.name}")

                lobby_channel = await find_or_create_lobby(guild)

                existing_channels = await discover_existing_game_channels(guild)

                purge_tasks = []
                for channel in existing_channels:
                    purge_tasks.append(purge_game_channel(channel))

                purge_results = await asyncio.gather(
                    *purge_tasks, return_exceptions=True
                )

                for i, result in enumerate(purge_results):
                    if isinstance(result, Exception):
                        logger.error(
                            f"Failed to purge channel {existing_channels[i].name}: {result}"
                        )
                    elif result:
                        server_state.available_game_channels.append(
                            existing_channels[i].id
                        )
                        logger.debug(
                            f"Added existing channel #{existing_channels[i].name} to available pool"
                        )

                server_state.initialized = True

                embed = nextcord.Embed(
                    title="Voyaging",
                    description="Voyager is ready to host games.",
                    color=nextcord.Color.green(),
                )
                embed.add_field(
                    name="Status",
                    value="Ready!",
                    inline=True,
                )

                embed.add_field(
                    name="Game Channels",
                    value=f"{len(existing_channels)}/{MAX_CHANNELS}",
                    inline=True,
                )
                embed.add_field(
                    name="Available",
                    value=f"{len(server_state.available_game_channels)}",
                    inline=True,
                )
                embed.add_field(
                    name="Commands",
                    value="• Use `/waitlist` to join games\n• Use `/state` to check status\n• Use `/admin create` to add more channels",
                    inline=False,
                )
            else:
                lobby_channel = await find_or_create_lobby(guild)

                embed = nextcord.Embed(
                    title="Voyager Bot Joined!",
                    description="Thanks for adding Voyager! Setup required.",
                    color=nextcord.Color.orange(),
                )
                embed.add_field(
                    name="Status", value="⚠️ Manual setup required", inline=True
                )
                embed.add_field(
                    name="Missing",
                    value="Lobby channel" if not lobby_exists else "Game channels",
                    inline=True,
                )
                embed.add_field(
                    name="Setup",
                    value="• Use `/admin create` to create game channels\n• Server will auto-initialize when ready",
                    inline=False,
                )

            await lobby_channel.send(embed=embed)
            logger.debug(f"Bot joined server {guild.name}")

        except Exception as e:
            logger.warning(f"Failed to process new server {guild.name}: {e}")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Handle bot leaving a server"""
        logger.debug(f"Left server: {guild.name} ({guild.id})")

        if guild.id in SERVERS:
            server_state = SERVERS[guild.id]

            for timer in server_state.round_timers.values():
                if timer:
                    timer.cancel()

            del SERVERS[guild.id]
            logger.debug(f"Cleaned up state for server {guild.name}")

    @commands.Cog.listener()
    async def on_message(self, message: nextcord.Message):
        if not message or message.author.bot or not message.guild:
            return

        server_state = get_server_state(message.guild.id)
        channel_id = message.channel.id
        user_id = message.author.id

        if channel_id in server_state.instances:
            instance = server_state.instances[channel_id]
            if instance.current_challenge and instance.state == GameState.IN_PROGRESS:
                from cogs.game import manage_answer_reactions
                import time

                previous_ts = instance.submit_answer(
                    str(user_id), message.content, message.id
                )

                player = instance.players.get(str(user_id))
                if player and instance.round_start_time:
                    response_time = time.time() - instance.round_start_time
                    await manage_answer_reactions(message, previous_ts, response_time)


def setup(bot):
    bot.add_cog(EventsCog(bot))
