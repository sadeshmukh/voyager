import time
import asyncio
import logging
from typing import Dict, List, Optional

import nextcord
from nextcord.ext import commands
from nextcord import Interaction

from instance import Instance, GameState, PlayerState, GameType, GameConfig, Challenge
from utils import get_trivia_question, get_riddle
from config import (
    two_player_config,
    multi_player_config,
    host_dialogue,
    dialogue_timing,
    MAX_CHANNELS,
    RESPONSE_TIME_THRESHOLDS,
    SERVER_DEFAULTS,
    ERROR_RESPONSE,
)
import random

logger = logging.getLogger("voyager_discord")


class GameControlView(nextcord.ui.View):
    """Interactive buttons to start, invite, or cancel a waiting game instance."""

    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.channel_id = channel_id

    @nextcord.ui.button(
        label="Start Game", style=nextcord.ButtonStyle.green, custom_id="gc_start"
    )
    async def start_button(self, button: nextcord.ui.Button, interaction: Interaction):
        from cogs.events import get_server_state

        server_state = get_server_state(self.guild_id)
        if self.channel_id not in server_state.instances:
            await interaction.response.send_message(
                ERROR_RESPONSE["game_not_found"], ephemeral=True
            )
            return

        instance = server_state.instances[self.channel_id]
        if instance.state != GameState.WAITING:
            await interaction.response.send_message(
                ERROR_RESPONSE["game_already_started"], ephemeral=True
            )
            return

        if len(instance.players) < 2:
            await interaction.response.send_message(
                ERROR_RESPONSE["need_at_least_2_players"],
                ephemeral=True,
            )
            return

        if str(interaction.user.id) not in instance.players:
            instance.add_player(str(interaction.user.id))
            try:
                await interaction.channel.set_permissions(
                    interaction.user, read_messages=True, send_messages=True
                )
            except Exception:
                pass

        config = create_game_config(len(instance.players))
        instance.start_game(config)
        await send_host_message(self.channel_id, "intro", interaction.client)

        embed = nextcord.Embed(
            title="Game Started!",
            description=f"{interaction.user.mention} started the game!",
            color=nextcord.Color.green(),
        )
        embed.add_field(name="Players", value=str(len(instance.players)), inline=True)
        await interaction.response.send_message(embed=embed)

        async def start_round():
            await asyncio.sleep(5)
            if self.channel_id in server_state.instances:
                await send_host_message(
                    self.channel_id, "main_round", interaction.client
                )
                challenge = instance.start_main_round()
                round_embed = create_round_embed(instance, challenge)
                await interaction.channel.send(embed=round_embed)
                schedule_round_evaluation(
                    self.guild_id,
                    self.channel_id,
                    challenge.time_limit,
                    interaction.client,
                )

        asyncio.create_task(start_round())

    @nextcord.ui.button(
        label="Invite Player", style=nextcord.ButtonStyle.blurple, custom_id="gc_invite"
    )
    async def invite_button(self, button: nextcord.ui.Button, interaction: Interaction):
        from cogs.events import get_server_state

        server_state = get_server_state(self.guild_id)
        if self.channel_id not in server_state.instances:
            await interaction.response.send_message(
                ERROR_RESPONSE["game_not_found"], ephemeral=True
            )
            return

        instance = server_state.instances[self.channel_id]
        if instance.state != GameState.WAITING:
            await interaction.response.send_message(
                ERROR_RESPONSE["cannot_invite_started"], ephemeral=True
            )
            return

        class InviteModal(nextcord.ui.Modal):
            def __init__(self, channel_id: int):
                super().__init__(title="Invite Player to Game")
                self.channel_id = channel_id

                self.user_mention = nextcord.ui.UserSelect(
                    placeholder="Select a user to invite",
                    min_values=1,
                    max_values=1,
                )

                self.add_item(self.user_mention)

            async def callback(self, interaction: Interaction):
                user_input = self.user_mention.values[0]
                user = None

                if user_input.startswith("<@") and user_input.endswith(">"):
                    user_id = user_input[2:-1]
                    if user_id.startswith("!"):
                        user_id = user_id[1:]
                    try:
                        user = interaction.guild.get_member(int(user_id))
                    except ValueError:
                        pass
                else:
                    try:
                        user = interaction.guild.get_member(int(user_input))
                    except ValueError:
                        user = nextcord.utils.get(
                            interaction.guild.members, name=user_input
                        )

                if not user:
                    await interaction.response.send_message(
                        ERROR_RESPONSE["user_not_found"],
                        ephemeral=True,
                    )
                    return

                if user.bot:
                    await interaction.response.send_message(
                        ERROR_RESPONSE["cannot_invite_bots"], ephemeral=True
                    )
                    return

                server_state = get_server_state(interaction.guild.id)
                instance = server_state.instances[self.channel_id]

                if str(user.id) in instance.players:
                    await interaction.response.send_message(
                        ERROR_RESPONSE["already_in_game"].format(
                            user_mention=user.mention
                        ),
                        ephemeral=True,
                    )
                    return

                instance.add_player(str(user.id))

                try:
                    await interaction.channel.set_permissions(
                        user, read_messages=True, send_messages=True
                    )
                except Exception as e:
                    logger.error(f"Failed to set permissions for user {user.id}: {e}")

                await interaction.response.send_message(
                    f"✅ {user.mention} has been invited to the game! Total players: {len(instance.players)}"
                )

        await interaction.response.send_modal(InviteModal(self.channel_id))

    @nextcord.ui.button(
        label="Cancel Game", style=nextcord.ButtonStyle.red, custom_id="gc_cancel"
    )
    async def cancel_button(self, button: nextcord.ui.Button, interaction: Interaction):
        from cogs.events import get_server_state, release_game_channel

        server_state = get_server_state(self.guild_id)
        if self.channel_id not in server_state.instances:
            await interaction.response.send_message(
                ERROR_RESPONSE["game_not_found"], ephemeral=True
            )
            return

        del server_state.instances[self.channel_id]
        guild = interaction.guild
        if guild:
            await release_game_channel(guild, self.channel_id)
        await interaction.response.send_message("Game cancelled and channel reset.")


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


async def send_host_message(channel_id: int, dialogue_key: str, bot=None):
    if bot is None:
        return

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


async def auto_evaluate_round(guild_id: int, channel_id: int, bot=None):
    from cogs.events import get_server_state, release_game_channel

    if bot is None:
        return

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

        await channel.send(f"<@{new_leader}>", embed=leader_embed)

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
        await send_host_message(channel_id, "final_results", bot)
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
        await send_host_message(channel_id, "outro", bot)

        if channel_id in server_state.instances:
            del server_state.instances[channel_id]
            guild = channel.guild
            if guild:
                await release_game_channel(guild, channel_id)
    else:

        async def start_next():
            await asyncio.sleep(3)
            if channel_id in server_state.instances:
                await send_host_message(channel_id, "main_round", bot)
                challenge = instance.start_main_round()
                embed = create_round_embed(instance, challenge)

                await channel.send(embed=embed)

                schedule_round_evaluation(
                    guild_id, channel_id, challenge.time_limit, bot
                )

        asyncio.create_task(start_next())


def schedule_round_evaluation(guild_id: int, channel_id: int, delay: int, bot=None):
    from cogs.events import get_server_state

    server_state = get_server_state(guild_id)

    async def delayed_evaluation():
        await asyncio.sleep(delay)
        await auto_evaluate_round(guild_id, channel_id, bot)

    server_state.round_timers[channel_id] = asyncio.create_task(delayed_evaluation())


def create_instance_with_dialogue(
    guild_id: int, channel_id: int, name: str
) -> Instance:
    # guild_id is kept for future extensibility
    _ = guild_id
    instance = Instance(channel_id=str(channel_id), name=name)
    instance.set_challenge_generator(generate_challenge)
    return instance


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

    if response_time <= RESPONSE_TIME_THRESHOLDS["fast"]:
        await message.add_reaction("⚡")
    elif response_time <= RESPONSE_TIME_THRESHOLDS["medium"]:
        await message.add_reaction("👍")
    else:
        await message.add_reaction("🐌")


class GameCog(commands.Cog):
    """Game-related commands and functionality"""

    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="waitlist", description="Join the game queue")
    async def join_game(self, interaction: Interaction):
        from cogs.events import (
            get_server_state,
            find_or_create_lobby,
            allocate_game_channel,
        )

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        server_state = get_server_state(guild.id)

        if not server_state.initialized:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_not_initialized"],
                ephemeral=True,
            )
            return

        lobby_channel = await find_or_create_lobby(guild)
        if interaction.channel_id != lobby_channel.id:
            await interaction.response.send_message(
                ERROR_RESPONSE["command_lobby_only"].format(
                    lobby_mention=lobby_channel.mention
                ),
                ephemeral=True,
            )
            return

        user_id = interaction.user.id
        if user_id in server_state.waiting_users:
            position = server_state.waiting_users.index(user_id) + 1
            await interaction.response.send_message(
                ERROR_RESPONSE["already_in_waitlist"].format(position=position),
                ephemeral=True,
            )
            return

        server_state.waiting_users.append(user_id)

        await interaction.response.send_message(
            ERROR_RESPONSE["on_waitlist"],
            ephemeral=True,
        )
        if len(server_state.waiting_users) >= 1:
            from cogs.tasks import process_waitlist, set_bot

            set_bot(self.bot)
            await process_waitlist()

    @nextcord.slash_command(name="state", description="Check game/queue status")
    async def status(self, interaction: Interaction):
        from cogs.events import get_server_state, find_or_create_lobby

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
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
                [
                    i
                    for i in server_state.instances.values()
                    if i.state == GameState.WAITING
                ]
            )
            embed.add_field(name="Active Games", value=str(active_games), inline=True)
            embed.add_field(
                name="Games Waiting to Start", value=str(waiting_games), inline=True
            )
            embed.add_field(
                name="Available Game Channels",
                value=f"{len(server_state.available_game_channels)}/{MAX_CHANNELS}",
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
                embed.add_field(
                    name="Players", value=state["player_count"], inline=True
                )
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

    @nextcord.slash_command(name="start", description="Start a game in this channel")
    async def start_game(self, interaction: Interaction):
        from cogs.events import get_server_state, find_or_create_lobby

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
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
                    ERROR_RESPONSE["no_game_running"], ephemeral=True
                )
                return
        else:
            await interaction.followup.send(
                ERROR_RESPONSE["use_waitlist_in_lobby"],
                ephemeral=True,
            )
            return

        lobby_channel = await find_or_create_lobby(guild)
        if channel_id == lobby_channel.id:
            await interaction.followup.send(
                ERROR_RESPONSE["games_cannot_start_lobby"], ephemeral=True
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
                    await channel.send(f"<@{user_id}>", embed=player_embed)
            except Exception as e:
                logger.error(
                    f"Failed to send player joined message for user {user_id}: {e}"
                )

        config = create_game_config(len(instance.players))
        instance.start_game(config)

        await send_host_message(channel_id, "intro", self.bot)

        embed = nextcord.Embed(
            title="Game Started!",
            description=f"<@{user_id}> started the game!",
            color=nextcord.Color.green(),
        )
        embed.add_field(name="Game Name", value=instance.name, inline=True)
        embed.add_field(name="Players", value=str(len(instance.players)), inline=True)

        await interaction.followup.send(f"<@{user_id}>", embed=embed)

        async def start_first_round():
            await asyncio.sleep(5)
            if channel_id in server_state.instances:
                await send_host_message(channel_id, "main_round", self.bot)
                challenge = instance.start_main_round()
                embed = create_round_embed(instance, challenge)

                channel = guild.get_channel(channel_id)
                if channel:
                    await channel.send(embed=embed)

                schedule_round_evaluation(
                    guild.id, channel_id, challenge.time_limit, self.bot
                )

        asyncio.create_task(start_first_round())

    @nextcord.slash_command(name="next-round", description="Start the next round")
    async def start_next_round(self, interaction: Interaction):
        from cogs.events import get_server_state

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        server_state = get_server_state(guild.id)
        channel_id = interaction.channel_id

        if channel_id not in server_state.instances:
            await interaction.response.send_message(
                ERROR_RESPONSE["no_active_game"], ephemeral=True
            )
            return

        instance = server_state.instances[channel_id]
        if instance.state != GameState.IN_PROGRESS:
            await interaction.response.send_message(
                ERROR_RESPONSE["game_not_in_progress"], ephemeral=True
            )
            return

        await send_host_message(channel_id, "main_round", self.bot)
        challenge = instance.start_main_round()
        embed = create_round_embed(instance, challenge)

        await interaction.response.send_message(embed=embed)

        schedule_round_evaluation(guild.id, channel_id, challenge.time_limit, self.bot)


def setup(bot):
    bot.add_cog(GameCog(bot))
