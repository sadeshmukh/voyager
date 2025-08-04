import os
import sys
import time

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

LOBBY_CHANNEL_ID = int(os.environ.get("DISCORD_LOBBY_CHANNEL_ID", "0"))
if not LOBBY_CHANNEL_ID:
    raise ValueError("DISCORD_LOBBY_CHANNEL_ID environment variable must be set")

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable must be set")

intents = nextcord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

CURRENTLY_WAITING = []  # [user_id, ...]
INSTANCES = {}  # channel_id: Instance
ROUND_TIMERS = {}  # channel_id: Timer

# scheduler = AsyncIOScheduler()


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
        await channel.send(f"**Host**: {message}")
        await asyncio.sleep(timing)


def create_game_config(player_count: int) -> GameConfig:
    if player_count <= 2:
        config = GameConfig(player_count, **two_player_config)
    else:
        config = GameConfig(player_count, **multi_player_config)
    return config


async def auto_evaluate_round(channel_id: int):
    if channel_id not in INSTANCES:
        return

    instance = INSTANCES[channel_id]
    if not instance.current_challenge:
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    results = instance.evaluate_current_challenge()

    embed = nextcord.Embed(title="Round Results", color=nextcord.Color.blue())

    if results["correct_players"]:
        correct_names = [f"<@{uid}>" for uid in results["correct_players"]]
        embed.add_field(
            name="Correct Answers",
            value=", ".join(correct_names) if correct_names else "None",
            inline=False,
        )

    if results["failed_players"]:
        failed_names = [f"<@{uid}>" for uid in results["failed_players"]]
        embed.add_field(
            name="Failed/No Answer",
            value=", ".join(failed_names) if failed_names else "None",
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

        if channel_id in INSTANCES:
            del INSTANCES[channel_id]
    else:

        async def start_next():
            await asyncio.sleep(3)
            if channel_id in INSTANCES:
                await send_host_message(channel_id, "main_round")
                challenge = instance.start_main_round()

                embed = nextcord.Embed(
                    title=f"Round {instance.current_round}",
                    description=challenge.question,
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

                await channel.send(embed=embed)

                schedule_round_evaluation(channel_id, challenge.time_limit)

        asyncio.create_task(start_next())


def schedule_round_evaluation(channel_id: int, delay: int):
    async def delayed_evaluation():
        await asyncio.sleep(delay)
        await auto_evaluate_round(channel_id)

    ROUND_TIMERS[channel_id] = asyncio.create_task(delayed_evaluation())


def create_instance_with_dialogue(channel_id: int, name: str) -> Instance:
    instance = Instance(channel_id=str(channel_id), name=name)
    instance.set_challenge_generator(generate_challenge)
    return instance


@tasks.loop(seconds=30.0)
async def process_waitlist():
    if len(CURRENTLY_WAITING) >= 2:
        players = CURRENTLY_WAITING[:4]
        CURRENTLY_WAITING[:4] = []

        lobby_channel = bot.get_channel(LOBBY_CHANNEL_ID)
        if not lobby_channel:
            return

        game_name = f"Game-{int(time.time())}"

        embed = nextcord.Embed(
            title="Game Starting!",
            description=f"Players: {', '.join([f'<@{p}>' for p in players])}",
            color=nextcord.Color.green(),
        )
        embed.add_field(name="Game Name", value=game_name, inline=True)
        embed.add_field(name="Channel", value=f"<#{LOBBY_CHANNEL_ID}>", inline=True)

        await lobby_channel.send(embed=embed)

        instance = create_instance_with_dialogue(LOBBY_CHANNEL_ID, game_name)
        for player_id in players:
            instance.add_player(str(player_id))

        INSTANCES[LOBBY_CHANNEL_ID] = instance


@process_waitlist.before_loop
async def before_process_waitlist():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    logger.info(f"Discord bot logged in as {bot.user}")
    process_waitlist.start()
    await initialize_app()


@bot.slash_command(name="waitlist", description="Join the game queue")
async def join_game(interaction: Interaction):
    if interaction.channel_id != LOBBY_CHANNEL_ID:
        await interaction.response.send_message(
            "This command can only be used in the lobby channel!", ephemeral=True
        )
        return

    user_id = interaction.user.id
    if user_id in CURRENTLY_WAITING:
        await interaction.response.send_message(
            "You're already in the waitlist!", ephemeral=True
        )
        return

    CURRENTLY_WAITING.append(user_id)

    embed = nextcord.Embed(
        title="Waitlist Updated",
        description=f"<@{user_id}> joined the waitlist!",
        color=nextcord.Color.blue(),
    )
    embed.add_field(name="Players Waiting", value=len(CURRENTLY_WAITING), inline=True)
    embed.add_field(
        name="Players Needed",
        value=f"{2 - len(CURRENTLY_WAITING)} more"
        if len(CURRENTLY_WAITING) < 2
        else "Ready to start!",
        inline=True,
    )

    await interaction.response.send_message(embed=embed)

    if len(CURRENTLY_WAITING) >= 2:
        await process_waitlist()


@bot.slash_command(name="state", description="Check game/queue status")
async def status(interaction: Interaction):
    # user_id = interaction.user.id
    channel_id = interaction.channel_id

    embed = nextcord.Embed(title="Game Status", color=nextcord.Color.blue())

    if channel_id == LOBBY_CHANNEL_ID:
        embed.add_field(
            name="Lobby Status",
            value=f"Players waiting: {len(CURRENTLY_WAITING)}",
            inline=False,
        )
        if CURRENTLY_WAITING:
            waiting_names = [f"<@{uid}>" for uid in CURRENTLY_WAITING]
            embed.add_field(
                name="Waiting Players", value=", ".join(waiting_names), inline=False
            )
    else:
        if channel_id in INSTANCES:
            instance = INSTANCES[channel_id]
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
    channel_id = interaction.channel_id
    user_id = interaction.user.id

    if channel_id in INSTANCES:
        await interaction.response.send_message(
            "A game is already running in this channel!", ephemeral=True
        )
        return

    if channel_id == LOBBY_CHANNEL_ID:
        await interaction.response.send_message(
            "Games cannot be started in the lobby channel!", ephemeral=True
        )
        return

    instance = create_instance_with_dialogue(channel_id, f"Game-{int(time.time())}")
    instance.add_player(str(user_id))
    INSTANCES[channel_id] = instance

    config = create_game_config(1)
    instance.start_game(config)

    await send_host_message(channel_id, "intro")

    embed = nextcord.Embed(
        title="Game Started!",
        description=f"<@{user_id}> started a new game!",
        color=nextcord.Color.green(),
    )
    embed.add_field(name="Game Name", value=instance.name, inline=True)
    embed.add_field(name="Players", value="1", inline=True)

    await interaction.response.send_message(embed=embed)


@bot.slash_command(name="next-round", description="Start the next round")
async def start_next_round(interaction: Interaction):
    channel_id = interaction.channel_id

    if channel_id not in INSTANCES:
        await interaction.response.send_message(
            "No active game in this channel!", ephemeral=True
        )
        return

    instance = INSTANCES[channel_id]
    if instance.state != GameState.IN_PROGRESS:
        await interaction.response.send_message(
            "Game is not in progress!", ephemeral=True
        )
        return

    await send_host_message(channel_id, "main_round")
    challenge = instance.start_main_round()

    embed = nextcord.Embed(
        title=f"Round {instance.current_round}",
        description=challenge.question,
        color=nextcord.Color.purple(),
    )
    embed.add_field(
        name="Time Limit", value=f"{challenge.time_limit} seconds", inline=True
    )
    embed.add_field(
        name="Game Type",
        value=challenge.challenge_type.value.replace("_", " ").title(),
        inline=True,
    )

    await interaction.response.send_message(embed=embed)

    schedule_round_evaluation(channel_id, challenge.time_limit)


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    channel_id = message.channel.id
    user_id = message.author.id

    if channel_id in INSTANCES:
        instance = INSTANCES[channel_id]
        if instance.current_challenge and instance.state == GameState.IN_PROGRESS:
            instance.submit_answer(str(user_id), message.content)

            embed = nextcord.Embed(
                title="Answer Received",
                description=f"<@{user_id}> submitted: {message.content}",
                color=nextcord.Color.green(),
            )
            embed.set_footer(text="Answer will be evaluated when time runs out")

            await message.channel.send(embed=embed, delete_after=5)

    await bot.process_application_commands(message)


@bot.slash_command(
    name="admin-create", description="Create a new game instance (Admin only)"
)
async def admin_create_instance(interaction: Interaction, name: str):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message(
            "Admin access required!", ephemeral=True
        )
        return

    channel_id = interaction.channel_id
    if channel_id in INSTANCES:
        await interaction.response.send_message(
            "A game is already running in this channel!", ephemeral=True
        )
        return

    instance = create_instance_with_dialogue(channel_id, name)
    INSTANCES[channel_id] = instance

    embed = nextcord.Embed(
        title="Game Instance Created",
        description=f"Admin created game: {name}",
        color=nextcord.Color.gold(),
    )
    embed.add_field(name="Channel", value=f"<#{channel_id}>", inline=True)
    embed.add_field(name="Status", value="Waiting for players", inline=True)

    await interaction.response.send_message(embed=embed)


@bot.slash_command(
    name="admin-invite", description="Invite a user to the current game (Admin only)"
)
async def admin_invite_user(interaction: Interaction, user: nextcord.Member):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message(
            "Admin access required!", ephemeral=True
        )
        return

    channel_id = interaction.channel_id
    if channel_id not in INSTANCES:
        await interaction.response.send_message(
            "No active game in this channel!", ephemeral=True
        )
        return

    instance = INSTANCES[channel_id]
    instance.add_player(str(user.id))

    embed = nextcord.Embed(
        title="Player Invited",
        description=f"<@{user.id}> has been invited to the game!",
        color=nextcord.Color.green(),
    )
    embed.add_field(name="Total Players", value=len(instance.players), inline=True)

    await interaction.response.send_message(embed=embed)


async def initialize_app():
    logger.info("Initializing Discord bot...")

    lobby_channel = await bot.fetch_channel(LOBBY_CHANNEL_ID)
    if lobby_channel:
        embed = nextcord.Embed(
            title="Voyager Bot Online",
            description="The party game bot is ready! Use `/waitlist` to join a game.",
            color=nextcord.Color.green(),
        )
        embed.add_field(
            name="Commands",
            value="`/waitlist`, `/state`, `/start`, `/next-round`",
            inline=False,
        )
        embed.add_field(
            name="Admin Commands",
            value="`/admin-create`, `/admin-invite`",
            inline=False,
        )

        await lobby_channel.send(embed=embed)

    logger.info("Discord bot initialization complete")


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
