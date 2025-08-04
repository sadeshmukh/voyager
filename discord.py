import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

# import threading
import asyncio
import logging
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands, tasks
from nextcord import Interaction
# from apscheduler.schedulers.asyncio import AsyncIOScheduler
# from apscheduler.triggers.interval import IntervalTrigger

from instance import (
    Instance,
    GameState,
    PlayerState,
    GameType,
    GameConfig,
    Challenge,
)
from utils import get_trivia_question, get_riddle
from config import (
    two_player_config,
    multi_player_config,
    host_dialogue,
    dialogue_timing,
)
import random

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

ADMIN_ID = int(os.environ.get("DISCORD_ADMIN_ID", "0"))
if not ADMIN_ID:
    raise ValueError("DISCORD_ADMIN_ID environment variable must be set")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable must be set")


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
    initialized: bool = False  # whether the server has been automatically initialized
    max_channels: int = 10

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


intents = nextcord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

SERVERS: Dict[int, ServerState] = {}  # guild_id: ServerState


def get_server_state(guild_id: int) -> ServerState:
    """Get or create server state"""
    if guild_id not in SERVERS:
        SERVERS[guild_id] = ServerState(guild_id=guild_id, max_channels=10)

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
    game_channels = game_channels[: server_state.max_channels]

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

        # OPTIMIZED PERMISSION RESET!
        try:
            await channel.edit(
                topic="Available game channel - waiting for assignment",
                overwrites={},
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
        f"No available game channels in {guild.name}. Use /admin create to create more."
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


def create_progress_bar(current_round: int, total_rounds: int) -> str:
    """Create a visual progress bar for rounds"""
    progress_filled = "▓" * current_round
    progress_empty = "░" * (total_rounds - current_round)
    return f"[{progress_filled}{progress_empty}] Round {current_round}/{total_rounds}"


def create_round_embed(instance: Instance, challenge: Challenge) -> nextcord.Embed:
    """Create a standardized round embed with progress bar"""
    progress_bar = create_progress_bar(
        instance.current_round, instance.config.main_rounds
    )

    embed = nextcord.Embed(
        title=f"Round {instance.current_round}",
        description=f"`{progress_bar}`\n\n{challenge.question}",
        color=nextcord.Color.purple(),
    )
    embed.add_field(
        name="Time Limit",
        value=f"{challenge.time_limit} seconds",
        inline=True,
    )
    embed.add_field(
        name="Game Type",
        value=challenge.challenge_type.value.replace("_", " ").title(),
        inline=True,
    )
    return embed


async def manage_answer_reactions(
    message: nextcord.Message, previous_ts: Optional[str], response_time: float
):
    """Handle reaction management for answer submissions"""
    if previous_ts:
        try:
            previous_message = await message.channel.fetch_message(int(previous_ts))
            await previous_message.remove_reaction("👍", message.guild.me)
            await previous_message.remove_reaction("⚡", message.guild.me)
            await previous_message.remove_reaction("🐌", message.guild.me)
        except (nextcord.NotFound, nextcord.HTTPException, ValueError):
            pass  # message probably already unreacted?

    if response_time <= 3:
        await message.add_reaction("⚡")
    elif response_time <= 8:
        await message.add_reaction("👍")
    else:
        await message.add_reaction("🐌")


def generate_challenge(game_type: GameType) -> Challenge:
    if game_type == GameType.QUICK_MATH:
        a, b = random.randint(10, 99), random.randint(10, 99)
        return Challenge(
            challenge_type=game_type,
            question=f"What's {a} + {b}?",
            correct_answer=str(a + b),
            time_limit=10,
        )

    elif game_type == GameType.SPEED_CHALLENGE:
        return Challenge(
            challenge_type=game_type,
            question="First to respond wins! Type ANYTHING and hit enter!",
            correct_answer=None,
            time_limit=5,
            metadata={"speed_based": True},
        )

    elif game_type == GameType.TRIVIA:
        question, answers = get_trivia_question()
        return Challenge(
            challenge_type=game_type,
            question=question,
            correct_answer=answers,
            time_limit=20,
        )

    elif game_type == GameType.RIDDLE:
        riddle, answer = get_riddle()
        return Challenge(
            challenge_type=game_type,
            question=riddle,
            correct_answer=[answer],
            time_limit=30,
        )

    elif game_type == GameType.MEMORY_GAME:
        return Challenge(
            challenge_type=game_type,
            question="I'll show you a sequence. Remember it and type it back!",
            correct_answer=None,
            time_limit=15,
            metadata={"memory_based": True},
        )

    elif game_type == GameType.COLLABORATIVE:
        return Challenge(
            challenge_type=game_type,
            question="Work together! Everyone must respond with 'ready' to continue!",
            correct_answer=["ready"],
            time_limit=30,
            metadata={"collaborative": True},
        )

    else:
        return Challenge(
            challenge_type=GameType.TRIVIA,
            question="What is the capital of France?",
            correct_answer=["Paris"],
            time_limit=20,
        )


async def send_host_message(channel_id: int, dialogue_key: str):
    channel = bot.get_channel(channel_id)
    if not channel:
        return

    messages = host_dialogue.get(dialogue_key, [])
    timing = dialogue_timing.get(dialogue_key, dialogue_timing["default_wait"])

    for message in messages:
        await channel.send(message)
        await asyncio.sleep(timing)


def create_game_config(player_count: int) -> GameConfig:
    if player_count <= 2:
        config = GameConfig(player_count, **two_player_config)
    else:
        config = GameConfig(player_count, **multi_player_config)
    return config


async def auto_evaluate_round(guild_id: int, channel_id: int):
    server_state = get_server_state(guild_id)

    if channel_id not in server_state.instances:
        return

    instance = server_state.instances[channel_id]
    if not instance.current_challenge:
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    results = instance.evaluate_current_challenge()

    new_leader = instance.check_leader_change()
    if new_leader:
        leader_embed = nextcord.Embed(
            title="🚨 NEW LEADER! 🚨",
            description=f"<@{new_leader}> has taken the lead!",
            color=nextcord.Color.gold(),
        )
        await channel.send(embed=leader_embed)

    embed = nextcord.Embed(title="Round Results", color=nextcord.Color.blue())

    if instance.current_challenge and instance.current_challenge.correct_answer:
        correct_answer = instance.current_challenge.correct_answer
        if isinstance(correct_answer, list):
            answer_text = " / ".join(str(ans) for ans in correct_answer)
        else:
            answer_text = str(correct_answer)
        embed.add_field(name="Correct Answer", value=f"`{answer_text}`", inline=False)

    if results["correct_players"]:
        correct_names = [f"<@{uid}>" for uid in results["correct_players"]]
        embed.add_field(
            name="✅ Correct",
            value=", ".join(correct_names) if correct_names else "None",
            inline=False,
        )

    if results["failed_players"]:
        failed_names = [f"<@{uid}>" for uid in results["failed_players"]]
        embed.add_field(
            name="❌ Incorrect/No Answer",
            value=", ".join(failed_names) if failed_names else "None",
            inline=False,
        )

    leaderboard = []
    for user_id, player in sorted(
        instance.players.items(), key=lambda x: x[1].score, reverse=True
    ):
        if player.state == PlayerState.ACTIVE:
            leaderboard.append(f"<@{user_id}>: **{player.score}** pts")

    if leaderboard:
        embed.add_field(
            name="🏆 Live Leaderboard",
            value="\n".join(leaderboard[:5]),
            inline=False,
        )

    await channel.send(embed=embed)

    active_players = sum(
        1 for p in instance.players.values() if p.state == PlayerState.ACTIVE
    )

    if active_players <= 1:
        await send_host_message(channel_id, "final_results")
        final_results = instance.end_game()

        embed = nextcord.Embed(title="Game Complete!", color=nextcord.Color.green())

        if final_results["winners"]:
            winner_names = [f"<@{uid}>" for uid in final_results["winners"]]
            embed.add_field(name="Winners", value=", ".join(winner_names), inline=False)

        embed.add_field(
            name="Final Scores",
            value="\n".join(
                [f"<@{uid}>: {score}" for uid, score in final_results["scores"].items()]
            ),
            inline=False,
        )

        await channel.send(embed=embed)
        await send_host_message(channel_id, "outro")

        if channel_id in server_state.instances:
            del server_state.instances[channel_id]
            guild = channel.guild
            if guild:
                await release_game_channel(guild, channel_id)
    else:

        async def start_next():
            await asyncio.sleep(3)
            if channel_id in server_state.instances:
                await send_host_message(channel_id, "main_round")
                challenge = instance.start_main_round()
                embed = create_round_embed(instance, challenge)

                await channel.send(embed=embed)

                schedule_round_evaluation(guild_id, channel_id, challenge.time_limit)

        asyncio.create_task(start_next())


def schedule_round_evaluation(guild_id: int, channel_id: int, delay: int):
    server_state = get_server_state(guild_id)

    async def delayed_evaluation():
        await asyncio.sleep(delay)
        await auto_evaluate_round(guild_id, channel_id)

    server_state.round_timers[channel_id] = asyncio.create_task(delayed_evaluation())


def create_instance_with_dialogue(
    guild_id: int, channel_id: int, name: str
) -> Instance:
    # guild_id is kept for future extensibility
    _ = guild_id
    instance = Instance(channel_id=str(channel_id), name=name)
    instance.set_challenge_generator(generate_challenge)
    return instance


@tasks.loop(seconds=30.0)
async def process_waitlist():
    """Process waitlists for all servers"""
    for guild_id, server_state in SERVERS.items():
        if len(server_state.waiting_users) >= 2:
            guild = bot.get_guild(guild_id)
            if not guild:
                continue

            players = server_state.waiting_users[:4]
            server_state.waiting_users[:4] = []

            if not server_state.initialized:
                logger.debug(
                    f"Server {guild.name} not initialized, skipping waitlist processing"
                )
                continue

            game_name = f"game-{int(time.time())}"

            game_channel = await allocate_game_channel(guild, game_name)
            if not game_channel:
                logger.error(
                    f"Failed to allocate game channel for {game_name} in {guild.name}"
                )
                continue

            # embed = nextcord.Embed(
            #     title="Game Starting!",
            #     description=f"Players: {', '.join([f'<@{p}>' for p in players])}",
            #     color=nextcord.Color.green(),
            # )
            # embed.add_field(name="Game Name", value=game_name, inline=True)
            # embed.add_field(
            #     name="Game Channel", value=f"<#{game_channel.id}>", inline=True
            # )

            # await lobby_channel.send(embed=embed)
            # await game_channel.send(embed=embed)

            for player_id in players:
                try:
                    user = guild.get_member(player_id)
                    if user:
                        await game_channel.set_permissions(
                            user, read_messages=True, send_messages=True
                        )
                except Exception as e:
                    logger.debug(f"Failed to set permissions for user {player_id}: {e}")

            instance = create_instance_with_dialogue(
                guild_id, game_channel.id, game_name
            )
            for player_id in players:
                instance.add_player(str(player_id))

            server_state.instances[game_channel.id] = instance

            welcome_embed = nextcord.Embed(
                title=f"Welcome to {game_name}!",
                description="Use `/start` to begin the game when everyone is ready! The game will auto-progress through all rounds.",
                color=nextcord.Color.blue(),
            )
            welcome_embed.add_field(
                name="Players",
                value=", ".join([f"<@{p}>" for p in players]),
                inline=False,
            )
            await game_channel.send(embed=welcome_embed)


@process_waitlist.before_loop
async def before_process_waitlist():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    logger.info(f"Discord bot logged in as {bot.user}")
    logger.debug(f"Bot is in {len(bot.guilds)} servers")
    process_waitlist.start()
    await initialize_app()


@bot.event
async def on_guild_join(guild):
    """Handle bot joining a new server"""
    logger.debug(f"Joined new server: {guild.name} ({guild.id})")

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
            nextcord.utils.get(guild.text_channels, name="voyager-lobby") is not None
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

            purge_results = await asyncio.gather(*purge_tasks, return_exceptions=True)

            for i, result in enumerate(purge_results):
                if isinstance(result, Exception):
                    logger.error(
                        f"Failed to purge channel {existing_channels[i].name}: {result}"
                    )
                elif result:
                    server_state.available_game_channels.append(existing_channels[i].id)
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
                value="Ready for games!",
                inline=True,
            )
            embed.add_field(
                name="Lobby Channel",
                value=f"<#{lobby_channel.id}>",
                inline=True,
            )
            embed.add_field(
                name="Game Channels",
                value=f"{len(existing_channels)}/{server_state.max_channels}",
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
            embed.add_field(name="Status", value="⚠️ Manual setup required", inline=True)
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


@bot.event
async def on_guild_remove(guild):
    """Handle bot leaving a server"""
    logger.debug(f"Left server: {guild.name} ({guild.id})")

    if guild.id in SERVERS:
        server_state = SERVERS[guild.id]

        for timer in server_state.round_timers.values():
            if timer:
                timer.cancel()

        del SERVERS[guild.id]
        logger.debug(f"Cleaned up state for server {guild.name}")


@bot.slash_command(name="waitlist", description="Join the game queue")
async def join_game(interaction: Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return

    server_state = get_server_state(guild.id)

    if not server_state.initialized:
        await interaction.response.send_message(
            "Server not initialized! Server will auto-initialize when ready.",
            ephemeral=True,
        )
        return

    lobby_channel = await find_or_create_lobby(guild)
    if interaction.channel_id != lobby_channel.id:
        await interaction.response.send_message(
            f"This command can only be used in {lobby_channel.mention}!", ephemeral=True
        )
        return

    user_id = interaction.user.id
    if user_id in server_state.waiting_users:
        await interaction.response.send_message(
            "You're already in the waitlist!", ephemeral=True
        )
        return

    server_state.waiting_users.append(user_id)

    # embed = nextcord.Embed(
    #     title="Waitlist Updated",
    #     description=f"<@{user_id}> joined the waitlist!",
    #     color=nextcord.Color.blue(),
    # )
    # embed.add_field(
    #     name="Players Waiting", value=len(server_state.waiting_users), inline=True
    # )
    # embed.add_field(
    #     name="Players Needed",
    #     value=f"{2 - len(server_state.waiting_users)} more"
    #     if len(server_state.waiting_users) < 2
    #     else "Ready to start!",
    #     inline=True,
    # )

    await interaction.response.send_message("You're on the waitlist!", ephemeral=True)

    if len(server_state.waiting_users) >= 2:
        await process_waitlist()


@bot.slash_command(name="state", description="Check game/queue status")
async def status(interaction: Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return

    server_state = get_server_state(guild.id)
    channel_id = interaction.channel_id

    embed = nextcord.Embed(title="Game Status", color=nextcord.Color.blue())

    if not server_state.initialized:
        embed.add_field(
            name="Server Status",
            value="Not initialized - please wait (if this message persists, check settings)",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    lobby_channel = await find_or_create_lobby(guild)
    if channel_id == lobby_channel.id:
        embed.add_field(
            name="Lobby Status",
            value=f"Players waiting: {len(server_state.waiting_users)}",
            inline=False,
        )
        if server_state.waiting_users:
            waiting_names = [f"<@{uid}>" for uid in server_state.waiting_users]
            embed.add_field(
                name="Waiting Players", value=", ".join(waiting_names), inline=False
            )

        active_games = len(
            [
                i
                for i in server_state.instances.values()
                if i.state == GameState.IN_PROGRESS
            ]
        )
        waiting_games = len(
            [i for i in server_state.instances.values() if i.state == GameState.WAITING]
        )
        embed.add_field(name="Active Games", value=str(active_games), inline=True)
        embed.add_field(
            name="Games Waiting to Start", value=str(waiting_games), inline=True
        )
        embed.add_field(
            name="Available Game Channels",
            value=f"{len(server_state.available_game_channels)}/{server_state.max_channels}",
            inline=True,
        )
    else:
        if channel_id in server_state.instances:
            instance = server_state.instances[channel_id]
            state = instance.get_game_state()

            embed.add_field(
                name="Game State", value=state["state"].title(), inline=True
            )
            embed.add_field(
                name="Phase",
                value=state["phase"].replace("_", " ").title(),
                inline=True,
            )
            embed.add_field(name="Round", value=state["round"], inline=True)
            embed.add_field(name="Players", value=state["player_count"], inline=True)
            embed.add_field(
                name="Active Players", value=state["active_players"], inline=True
            )
            embed.add_field(
                name="Time Elapsed", value=state["time_elapsed"], inline=True
            )

            if instance.current_challenge:
                embed.add_field(
                    name="Current Challenge",
                    value=instance.current_challenge.question,
                    inline=False,
                )
        else:
            embed.add_field(
                name="No Active Game",
                value="No game is currently running in this channel.",
                inline=False,
            )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.slash_command(name="start", description="Start a game in this channel")
async def start_game(interaction: Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return
    await interaction.response.defer()
    server_state = get_server_state(guild.id)
    channel_id = interaction.channel_id
    user_id = interaction.user.id

    if channel_id in server_state.instances:
        instance = server_state.instances[channel_id]
        if instance.state != GameState.WAITING:
            await interaction.followup.send(
                "A game is already running in this channel!", ephemeral=True
            )
            return
    else:
        await interaction.followup.send(
            "No game instance found in this channel! Use `/waitlist` in the lobby to join a game.",
            ephemeral=True,
        )
        return

    lobby_channel = await find_or_create_lobby(guild)
    if channel_id == lobby_channel.id:
        await interaction.followup.send(
            "Games cannot be started in the lobby channel!", ephemeral=True
        )
        return

    instance = server_state.instances[channel_id]

    if str(user_id) not in instance.players:
        instance.add_player(str(user_id))

        try:
            channel = guild.get_channel(channel_id)
            if channel:
                player_embed = nextcord.Embed(
                    title="Player Joined!",
                    description=f"<@{user_id}> has joined the game!",
                    color=nextcord.Color.blue(),
                )
                player_embed.add_field(
                    name="Total Players", value=len(instance.players), inline=True
                )
                await channel.send(embed=player_embed)
        except Exception as e:
            logger.error(
                f"Failed to send player joined message for user {user_id}: {e}"
            )

    config = create_game_config(len(instance.players))
    instance.start_game(config)

    await send_host_message(channel_id, "intro")

    embed = nextcord.Embed(
        title="Game Started!",
        description=f"<@{user_id}> started the game!",
        color=nextcord.Color.green(),
    )
    embed.add_field(name="Game Name", value=instance.name, inline=True)
    embed.add_field(name="Players", value=str(len(instance.players)), inline=True)

    await interaction.followup.send(embed=embed)

    async def start_first_round():
        await asyncio.sleep(5)
        if channel_id in server_state.instances:
            await send_host_message(channel_id, "main_round")
            challenge = instance.start_main_round()
            embed = create_round_embed(instance, challenge)

            channel = guild.get_channel(channel_id)
            if channel:
                await channel.send(embed=embed)

            schedule_round_evaluation(guild.id, channel_id, challenge.time_limit)

    asyncio.create_task(start_first_round())


@bot.slash_command(name="next-round", description="Start the next round")
async def start_next_round(interaction: Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return

    server_state = get_server_state(guild.id)
    channel_id = interaction.channel_id

    if channel_id not in server_state.instances:
        await interaction.response.send_message(
            "No active game in this channel!", ephemeral=True
        )
        return

    instance = server_state.instances[channel_id]
    if instance.state != GameState.IN_PROGRESS:
        await interaction.response.send_message(
            "Game is not in progress!", ephemeral=True
        )
        return

    await send_host_message(channel_id, "main_round")
    challenge = instance.start_main_round()
    embed = create_round_embed(instance, challenge)

    await interaction.response.send_message(embed=embed)

    schedule_round_evaluation(guild.id, channel_id, challenge.time_limit)


@bot.slash_command(name="debug", description="Debug commands for managing games")
async def debug_group(interaction: Interaction):
    pass


@debug_group.subcommand(name="available", description="Show available game channels")
async def debug_available(interaction: Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "You need administrator permissions to use this command!", ephemeral=True
        )
        return

    server_state = get_server_state(guild.id)

    available_channels = [
        f"<#{channel_id}>" for channel_id in server_state.available_game_channels
    ]

    await interaction.response.send_message(
        f"Available game channels: {', '.join(available_channels)}"
    )


@bot.event
async def on_message(message: nextcord.Message):
    if not message or message.author.bot or not message.guild:
        return

    server_state = get_server_state(message.guild.id)
    channel_id = message.channel.id
    user_id = message.author.id

    if channel_id in server_state.instances:
        instance = server_state.instances[channel_id]
        if instance.current_challenge and instance.state == GameState.IN_PROGRESS:
            previous_ts = instance.submit_answer(
                str(user_id), message.content, message.id
            )

            player = instance.players.get(str(user_id))
            if player and instance.round_start_time:
                response_time = time.time() - instance.round_start_time
                await manage_answer_reactions(message, previous_ts, response_time)


@bot.slash_command(name="admin", description="Admin commands for managing games")
async def admin_group(interaction: Interaction):
    pass


@admin_group.subcommand(
    name="create", description="Create a new game channel (Admin only)"
)
async def admin_create_channel(interaction: Interaction, name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "You need administrator permissions to use this command!", ephemeral=True
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return

    server_state = get_server_state(guild.id)

    total_channels = len(server_state.all_game_channels)
    if total_channels >= server_state.max_channels:
        await interaction.response.send_message(
            f"Maximum number of game channels ({server_state.max_channels}) reached! Cannot create more channels.",
            ephemeral=True,
        )
        return

    # create new game channel
    try:
        category = await ensure_voyager_category(guild)
        channel_name = f"v-inst-{name.lower().replace(' ', '-')}"
        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            topic="Available game channel - waiting for assignment",
            reason=f"Admin-created game channel: {name}",
        )

        server_state.all_game_channels.append(channel.id)
        server_state.available_game_channels.append(channel.id)

        embed = nextcord.Embed(
            title="Game Channel Created",
            description=f"Admin created channel: {name}",
            color=nextcord.Color.gold(),
        )
        embed.add_field(name="Channel", value=f"<#{channel.id}>", inline=True)
        embed.add_field(name="Status", value="Available for games", inline=True)
        embed.add_field(
            name="Channels Total",
            value=f"{len(server_state.all_game_channels)}/{server_state.max_channels}",
            inline=True,
        )

        await interaction.response.send_message(embed=embed)

    except Exception as e:
        logger.error(f"Failed to create game channel in {guild.name}: {e}")
        await interaction.response.send_message(
            "Failed to create game channel! Bot may lack permissions.", ephemeral=True
        )


@admin_group.subcommand(
    name="instance", description="Create a new game instance (Admin only)"
)
async def admin_create_instance(interaction: Interaction, name: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "You need administrator permissions to use this command!", ephemeral=True
        )
        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return

    server_state = get_server_state(guild.id)

    if not server_state.initialized:
        await interaction.response.send_message(
            "Server not initialized! Server will auto-initialize when ready.",
            ephemeral=True,
        )
        return

    game_channel = await allocate_game_channel(guild, name)
    if not game_channel:
        await interaction.response.send_message(
            "No available game channels! Use `/admin create` to create more channels.",
            ephemeral=True,
        )
        return

    instance = create_instance_with_dialogue(guild.id, game_channel.id, name)
    server_state.instances[game_channel.id] = instance

    embed = nextcord.Embed(
        title="Game Instance Created",
        description=f"Admin created game: {name}",
        color=nextcord.Color.gold(),
    )
    embed.add_field(name="Channel", value=f"<#{game_channel.id}>", inline=True)
    embed.add_field(name="Status", value="Waiting for players", inline=True)

    await interaction.response.send_message(embed=embed)


@admin_group.subcommand(
    name="invite", description="Invite a user to the current game (Admin only)"
)
async def admin_invite_user(interaction: Interaction, user: nextcord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "You need administrator permissions to use this command!", ephemeral=True
        )

        return

    guild = interaction.guild
    if not guild:
        await interaction.response.send_message(
            "This command can only be used in a server!", ephemeral=True
        )
        return

    server_state = get_server_state(guild.id)
    channel_id = interaction.channel_id

    if channel_id not in server_state.instances:
        await interaction.response.send_message(
            "No active game in this channel!", ephemeral=True
        )
        return

    instance = server_state.instances[channel_id]

    if str(user.id) in instance.players:
        await interaction.response.send_message(
            f"{user.mention} is already in this game!", ephemeral=True
        )
        return

    instance.add_player(str(user.id))

    try:
        channel = guild.get_channel(channel_id)
        if channel:
            await channel.set_permissions(user, read_messages=True, send_messages=True)
    except Exception as e:
        logger.error(f"Failed to set permissions for user {user.id}: {e}")

    try:
        channel = guild.get_channel(channel_id)
        if channel:
            welcome_embed = nextcord.Embed(
                title="Player Joined!",
                description=f"{user.mention} has been invited to the game!",
                color=nextcord.Color.green(),
            )
            welcome_embed.add_field(
                name="Total Players", value=len(instance.players), inline=True
            )
            await channel.send(embed=welcome_embed)
    except Exception as e:
        logger.error(f"Failed to send welcome message for user {user.id}: {e}")

    embed = nextcord.Embed(
        title="Player Invited",
        description=f"{user.mention} has been invited to the game!",
        color=nextcord.Color.green(),
    )
    embed.add_field(name="Total Players", value=len(instance.players), inline=True)

    await interaction.response.send_message(embed=embed)


async def initialize_app():
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
                    value="✅ Ready for games!",
                    inline=True,
                )
                embed.add_field(
                    name="Lobby Channel",
                    value=f"<#{lobby_channel.id}>",
                    inline=True,
                )
                embed.add_field(
                    name="Game Channels",
                    value=f"{len(existing_channels)}/{server_state.max_channels}",
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

    logger.debug("Discord bot initialization complete")


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
