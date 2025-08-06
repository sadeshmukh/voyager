import os
import sys
import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import nextcord
from nextcord.ext import commands

from instance import Instance, GameState
from config import SERVER_DEFAULTS, ERROR_RESPONSE

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
    game_roles: Dict[int, int] = None  # channel_id -> role_id mapping
    pending_waitlist_interactions: Dict[int, "nextcord.Interaction"] = (
        None  # user_id -> interaction mapping
    )
    initialized: bool = SERVER_DEFAULTS[
        "initialized"
    ]  # whether the server has been automatically initialized
    max_channels: int = SERVER_DEFAULTS["max_channels"]
    config: Dict[str, any] = None  # server-specific

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
        if self.game_roles is None:
            self.game_roles = {}
        if self.pending_waitlist_interactions is None:
            self.pending_waitlist_interactions = {}
        if self.config is None:
            self.config = {
                "hoist_roles": SERVER_DEFAULTS["hoist_roles"],
                "rounds_per_game": SERVER_DEFAULTS["rounds_per_game"],
                "role_color": SERVER_DEFAULTS["role_color"],
                "max_channels": SERVER_DEFAULTS["max_channels"],
                "default_time_limit": SERVER_DEFAULTS["default_time_limit"],
                "min_players_to_start": SERVER_DEFAULTS["min_players_to_start"],
                "max_players_per_game": SERVER_DEFAULTS["max_players_per_game"],
                "waitlist_timeout": SERVER_DEFAULTS["waitlist_timeout"],
                "enable_speed_bonus": SERVER_DEFAULTS["enable_speed_bonus"],
                "enable_first_answer_bonus": SERVER_DEFAULTS[
                    "enable_first_answer_bonus"
                ],
                "game_types_enabled": SERVER_DEFAULTS["game_types_enabled"],
            }


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
    server_state = get_server_state(guild.id)
    max_channels = server_state.config.get("max_channels", 10)
    game_channels = game_channels[:max_channels]

    server_state.all_game_channels = [c.id for c in game_channels]

    logger.debug(f"Limited to {len(game_channels)} game channels in {guild.name}")

    return game_channels


async def purge_game_channel(channel: nextcord.TextChannel) -> bool:
    """Purge all messages from a game channel to reset it"""
    try:
        # check if channel has any messages first
        message_count = 0
        try:
            # get just one message to check if channel has content
            async for _ in channel.history(limit=1):
                message_count = 1
                break
        # this caused bot hang initially :(
        except Exception as e:
            logger.debug(f"Failed to check messages in {channel.name}: {e}")

        if message_count == 0:
            logger.debug(f"Channel {channel.name} is already empty, skipping purge")
        else:
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

                role = await create_game_role(guild, channel_id, game_name)
                if role:
                    try:
                        await channel.set_permissions(
                            role, read_messages=True, send_messages=True
                        )
                        await channel.set_permissions(
                            guild.default_role, read_messages=False, send_messages=False
                        )
                        logger.info(f"Set up channel permissions for role {role.name}")
                    except Exception as e:
                        logger.error(
                            f"Failed to set channel permissions for role {role.name}: {e}"
                        )

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

    await cleanup_game_role(guild, channel_id)

    # release to pool
    if await purge_game_channel(channel):
        server_state.available_game_channels.append(channel_id)
        logger.debug(f"Released game channel #{channel.name} back to pool")
        return True
    else:
        logger.debug(f"Failed to purge channel #{channel.name}, not returning to pool")
        return False


async def create_game_role(
    guild: nextcord.Guild, channel_id: int, game_name: str
) -> Optional[nextcord.Role]:
    server_state = get_server_state(guild.id)

    try:
        import random
        from config import ROLE_NAME_FRUITS

        random_fruit = random.choice(ROLE_NAME_FRUITS)
        role_name = f"Voyaging {random_fruit}"

        color_name = server_state.config.get("role_color", "blue")
        color_map = {
            "blue": nextcord.Color.blue(),
            "green": nextcord.Color.green(),
            "red": nextcord.Color.red(),
            "yellow": nextcord.Color.yellow(),
            "purple": nextcord.Color.purple(),
            "orange": nextcord.Color.orange(),
            "pink": nextcord.Color.from_rgb(255, 105, 180),
            "teal": nextcord.Color.teal(),
            "default": nextcord.Color.default(),
        }
        role_color = color_map.get(color_name, nextcord.Color.blue())

        hoist_roles = server_state.config.get("hoist_roles", True)

        role = await guild.create_role(
            name=role_name,
            color=role_color,
            reason=f"Auto-created role for game: {game_name}",
            mentionable=True,
            hoist=hoist_roles,
        )

        server_state.game_roles[channel_id] = role.id
        logger.info(f"Created game role {role.name} for channel {channel_id}")

        return role

    except Exception as e:
        logger.error(f"Failed to create game role for channel {channel_id}: {e}")
        return None


async def get_or_create_game_role(
    guild: nextcord.Guild, channel_id: int, game_name: str
) -> Optional[nextcord.Role]:
    server_state = get_server_state(guild.id)

    if channel_id in server_state.game_roles:
        role_id = server_state.game_roles[channel_id]
        role = guild.get_role(role_id)
        if role:
            return role

    return await create_game_role(guild, channel_id, game_name)


async def assign_player_to_game_role(
    guild: nextcord.Guild, user_id: int, channel_id: int, game_name: str
) -> bool:
    try:
        if isinstance(user_id, str):
            user_id = int(user_id)

        user = guild.get_member(user_id)
        if not user:
            logger.debug(
                f"User {user_id} not found in guild cache, trying Discord API..."
            )
            try:
                user = await guild.fetch_member(user_id)
                logger.info(f"Successfully fetched user {user_id} from Discord API")
            except Exception as fetch_error:
                logger.error(
                    f"Failed to fetch user {user_id} from Discord API: {fetch_error}"
                )
                return False

        role = await get_or_create_game_role(guild, channel_id, game_name)
        if not role:
            logger.error(f"Failed to get/create game role for channel {channel_id}")
            return False

        if role not in user.roles:
            await user.add_roles(role, reason=f"Player joined game: {game_name}")
            logger.info(
                f"Assigned role {role.name} to user {user_id} for game {game_name}"
            )
        else:
            logger.debug(f"User {user_id} already has role {role.name}")

        return True

    except Exception as e:
        logger.error(f"Failed to assign role to user {user_id}: {e}")
        return False


async def remove_player_from_game_role(
    guild: nextcord.Guild, user_id: int, channel_id: int
) -> bool:
    try:
        server_state = get_server_state(guild.id)

        if channel_id not in server_state.game_roles:
            logger.debug(f"No game role found for channel {channel_id}")
            return True

        role_id = server_state.game_roles[channel_id]
        role = guild.get_role(role_id)
        if not role:
            logger.debug(f"Game role {role_id} not found, removing from state")
            del server_state.game_roles[channel_id]
            return True

        user = guild.get_member(user_id)
        if not user:
            logger.debug(f"User {user_id} not found in guild {guild.name}")
            return True

        if role in user.roles:
            await user.remove_roles(role, reason="Player left game")
            logger.info(f"Removed role {role.name} from user {user_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to remove role from user {user_id}: {e}")
        return False


async def cleanup_game_role(guild: nextcord.Guild, channel_id: int) -> bool:
    try:
        server_state = get_server_state(guild.id)

        if channel_id not in server_state.game_roles:
            return True

        role_id = server_state.game_roles[channel_id]
        role = guild.get_role(role_id)

        if role:
            await role.delete(reason="Game ended, cleaning up role")
            logger.info(f"Deleted game role {role.name} for channel {channel_id}")

        del server_state.game_roles[channel_id]
        return True

    except Exception as e:
        logger.error(f"Failed to cleanup game role for channel {channel_id}: {e}")
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
                "manage_roles",
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
                            await channel.send(
                                f"⚠️ I need some permissions to work properly\n"
                                f"Missing Permissions: {', '.join(missing_permissions)}\n"
                                f"Required Permissions:\n"
                                f"• Send Messages\n"
                                f"• Read Messages\n"
                                f"• Manage Channels\n"
                                f"• Manage Messages\n"
                                f"• Embed Links\n"
                                f"• Manage Roles\n"
                                f"Next Steps: Grant the missing permissions and restart the bot"
                            )
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

                try:
                    purge_results = await asyncio.wait_for(
                        asyncio.gather(*purge_tasks, return_exceptions=True),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"Purge operations timed out for {guild.name}, skipping channel purging"
                    )
                    purge_results = [Exception("Timeout") for _ in purge_tasks]

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
                    description="Voyager is ready!",
                    color=nextcord.Color.green(),
                )
                embed.add_field(
                    name="Status",
                    value="Ready!",
                    inline=True,
                )
                max_channels = server_state.config.get("max_channels", 10)
                embed.add_field(
                    name="Game Channels",
                    value=f"{len(existing_channels)}/{max_channels}",
                    inline=True,
                )
                embed.add_field(
                    name="Available",
                    value=f"{len(server_state.available_game_channels)}",
                    inline=True,
                )
                await lobby_channel.send(embed=embed)
            else:
                lobby_channel = await find_or_create_lobby(guild)

                await lobby_channel.send(
                    f"Voyager is ready! Setup required.\n"
                    f"Status: ⚠️ Manual setup required\n"
                    f"Missing: {'Lobby channel' if not lobby_exists else 'Game channels'}\n"
                    f"Setup:\n"
                    f"• Use `/admin create` to create game channels\n"
                    f"• Server will auto-initialize when ready"
                )

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
            guild = self.bot.get_guild(guild_id)

            for timer in server_state.round_timers.values():
                if timer and not timer.done():
                    timer.cancel()
                    logger.debug(f"Cancelled round timer for guild {guild_id}")
            server_state.round_timers.clear()

            numroles = len(server_state.game_roles)
            if guild:
                for role_id in server_state.game_roles.copy().values():
                    try:
                        role = guild.get_role(role_id)
                        if role:
                            await role.delete(reason="Bot shutdown cleanup")
                            logger.debug(
                                f"Deleted game role {role.name} during shutdown cleanup"
                            )
                    except Exception as e:
                        logger.error(
                            f"Failed to delete role {role_id} during shutdown: {e}"
                        )
                server_state.game_roles.clear()
            logger.info(f"Cleaned up {numroles} game roles for guild {guild_id}")

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
                "manage_roles",
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
                            await channel.send(
                                f"⚠️ Bot is missing required permissions\n"
                                f"Missing Permissions: {', '.join(missing_permissions)}\n"
                                f"Required Permissions:\n"
                                f"• Send Messages\n"
                                f"• Read Messages\n"
                                f"• Manage Channels\n"
                                f"• Manage Messages\n"
                                f"• Embed Links\n"
                                f"• Manage Roles\n"
                                f"Next Steps: Grant the missing permissions and restart the bot"
                            )
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

                try:
                    purge_results = await asyncio.wait_for(
                        asyncio.gather(*purge_tasks, return_exceptions=True),
                        timeout=30.0,
                    )
                except asyncio.TimeoutError:
                    logger.error(
                        f"Purge operations timed out for {guild.name}, skipping channel purging"
                    )
                    purge_results = [Exception("Timeout") for _ in purge_tasks]

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
                    description="Voyager is ready!",
                    color=nextcord.Color.green(),
                )
                embed.add_field(
                    name="Status",
                    value="Ready!",
                    inline=True,
                )
                max_channels = server_state.config.get("max_channels", 10)
                embed.add_field(
                    name="Game Channels",
                    value=f"{len(existing_channels)}/{max_channels}",
                    inline=True,
                )
                embed.add_field(
                    name="Available",
                    value=f"{len(server_state.available_game_channels)}",
                    inline=True,
                )
                await lobby_channel.send(embed=embed)
            else:
                lobby_channel = await find_or_create_lobby(guild)

                await lobby_channel.send(
                    f"Voyager added! Need to set up some channels first.\n"
                    f"Status: ⚠️ Manual setup required\n"
                    f"Missing: {'Lobby channel' if not lobby_exists else 'Game channels'}\n"
                    f"Setup:\n"
                    f"• Use `/admin create` to create game channels\n"
                    f"• Server will auto-initialize when ready"
                )

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

        if channel_id == server_state.lobby_channel_id and message.mentions:
            try:
                await message.delete()
                logger.debug(f"Deleted @mention invite in lobby from user {user_id}")

                user_game_channel = None
                user_game_instance = None

                for game_channel_id, instance in server_state.instances.items():
                    if str(user_id) in instance.players:
                        user_game_channel = game_channel_id
                        user_game_instance = instance
                        break

                if user_game_channel and user_game_instance:
                    # invite all mentioned users to the sender's current game
                    game_channel = message.guild.get_channel(user_game_channel)
                    if game_channel:
                        for mentioned_user in message.mentions:
                            if mentioned_user.bot:
                                continue

                            # check if mentioned user is already in any game
                            already_in_game = False
                            for other_instance in server_state.instances.values():
                                if str(mentioned_user.id) in other_instance.players:
                                    already_in_game = True
                                    break

                            if (
                                not already_in_game
                                and str(mentioned_user.id)
                                not in user_game_instance.players
                            ):
                                user_game_instance.add_player(str(mentioned_user.id))

                                success = await assign_player_to_game_role(
                                    message.guild,
                                    mentioned_user.id,
                                    user_game_channel,
                                    user_game_instance.name,
                                )
                                if success:
                                    logger.debug(
                                        f"Successfully assigned role to mentioned user {mentioned_user.id}"
                                    )

                                    await game_channel.send(
                                        f"{mentioned_user.mention} has been invited by {message.author.mention}!"
                                    )
                                else:
                                    logger.error(
                                        f"Failed to assign role to mentioned user {mentioned_user.id}"
                                    )

            except Exception as e:
                logger.error(f"Error handling @mention in lobby: {e}")

        if channel_id in server_state.instances:
            instance = server_state.instances[channel_id]
            if instance.state == GameState.WAITING:
                logger.debug(message.content, message.mentions, instance.players)
                # check for mentions
                for mention in message.mentions:
                    if mention.id not in instance.players:
                        await instance.add_player(mention.id)
                        await message.channel.send(f"{mention.mention} has been added!")

            if instance.current_challenge and instance.state == GameState.IN_PROGRESS:
                from cogs.game import manage_answer_reactions
                import time

                previous_ts = instance.submit_answer(
                    str(user_id), message.content, message.id
                )

                player = instance.players.get(str(user_id))
                if player and instance.round_start_time:
                    response_time = time.time() - instance.round_start_time
                    if response_time <= instance.current_challenge.time_limit:
                        await manage_answer_reactions(
                            message, previous_ts, response_time
                        )

                    if instance.all_players_answered():
                        if channel_id in server_state.round_timers:
                            server_state.round_timers[channel_id].cancel()
                            del server_state.round_timers[channel_id]

                        from cogs.game import auto_evaluate_round

                        await auto_evaluate_round(
                            message.guild.id, channel_id, message.guild.me._state.client
                        )


def setup(bot):
    bot.add_cog(EventsCog(bot))
