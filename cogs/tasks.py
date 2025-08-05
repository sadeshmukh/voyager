import time
import logging
from nextcord.ext import commands, tasks
import nextcord

logger = logging.getLogger("voyager_discord")


_bot = None


def set_bot(bot):
    """Set the bot instance for tasks"""
    global _bot
    _bot = bot


@tasks.loop(seconds=5.0)  # TODO: increase this if public
async def process_waitlist():
    """Process waitlists for all servers"""
    from cogs.events import SERVERS, allocate_game_channel
    from cogs.game import create_instance_with_dialogue, GameControlView

    if _bot is None:
        return

    for guild_id, server_state in SERVERS.items():
        if len(server_state.waiting_users) >= 1:
            guild = _bot.get_guild(guild_id)
            if not guild:
                continue

            players = server_state.waiting_users[:1]
            server_state.waiting_users[:1] = []

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
                description="A player has been allocated to this game instance. Use the buttons below to invite more players and start when ready!",
                color=nextcord.Color.blue(),
            )
            welcome_embed.add_field(
                name="Current Players",
                value=", ".join([f"<@{p}>" for p in players]),
                inline=False,
            )
            welcome_embed.add_field(
                name="Status",
                value="⏳ Waiting for more players (minimum 2 required to start)",
                inline=False,
            )
            # must mention separately (embeds don't mention iirc?)
            player_mentions = " ".join([f"<@{p}>" for p in players])
            await game_channel.send(f"🎮 {player_mentions}", embed=welcome_embed)

            view = GameControlView(guild_id, game_channel.id)
            await game_channel.send(
                "**Game Instance Created!**\n"
                "• **Start Game** - Begin the game (requires 2+ players)\n"
                "• **Invite Player** - Add more players to this game\n"
                "• **Cancel Game** - Delete this game instance",
                view=view,
            )


@process_waitlist.before_loop
async def before_process_waitlist():
    if _bot:
        await _bot.wait_until_ready()


def start_process_waitlist_task():
    """Start the process waitlist task"""
    if not process_waitlist.is_running():
        process_waitlist.start()


class TasksCog(commands.Cog):
    """Background tasks for the bot"""

    def __init__(self, bot):
        self.bot = bot

    async def cleanup(self):
        """Clean up running tasks"""
        if process_waitlist.is_running():
            process_waitlist.cancel()
            logger.info("Stopped process_waitlist task")


def setup(bot):
    bot.add_cog(TasksCog(bot))
