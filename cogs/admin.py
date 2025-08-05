import logging
import nextcord
from nextcord.ext import commands
from nextcord import Interaction

from config import MAX_CHANNELS, ERROR_RESPONSE

logger = logging.getLogger("voyager_discord")


class AdminCog(commands.Cog):
    """Admin commands for managing games"""

    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(
        name="admin", description="Admin commands for managing games"
    )
    async def admin_group(self, interaction: Interaction):
        pass

    @admin_group.subcommand(
        name="create", description="Create a new game channel (Admin only)"
    )
    async def admin_create_channel(self, interaction: Interaction, name: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                ERROR_RESPONSE["admin_required"],
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        from cogs.events import get_server_state, ensure_voyager_category

        server_state = get_server_state(guild.id)

        total_channels = len(server_state.all_game_channels)
        if total_channels >= MAX_CHANNELS:
            await interaction.response.send_message(
                ERROR_RESPONSE["max_channels_reached"],
                ephemeral=True,
            )
            return

        # create new game channel
        try:
            category = await ensure_voyager_category(guild)
            channel_name = f"v-inst-{name.lower().replace(' ', '-')}"
            overwrites = {
                guild.default_role: nextcord.PermissionOverwrite(
                    view_channel=False, send_messages=False
                ),
                guild.me: nextcord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True
                ),
            }

            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                topic="Available game channel - waiting for assignment",
                reason=f"Admin-created game channel: {name}",
                overwrites=overwrites,
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
                value=f"{len(server_state.all_game_channels)}/{MAX_CHANNELS}",
                inline=True,
            )

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Failed to create game channel in {guild.name}: {e}")
            await interaction.response.send_message(
                ERROR_RESPONSE["failed_create_channel"],
                ephemeral=True,
            )

    @admin_group.subcommand(
        name="instance", description="Create a new game instance (Admin only)"
    )
    async def admin_create_instance(self, interaction: Interaction, name: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                ERROR_RESPONSE["admin_required"],
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        from cogs.events import get_server_state, allocate_game_channel
        from cogs.game import create_instance_with_dialogue

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
                ERROR_RESPONSE["no_available_channels"],
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
    async def admin_invite_user(self, interaction: Interaction, user: nextcord.Member):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                ERROR_RESPONSE["admin_required"],
                ephemeral=True,
            )

            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        from cogs.events import get_server_state

        server_state = get_server_state(guild.id)
        channel_id = interaction.channel_id

        if channel_id not in server_state.instances:
            await interaction.response.send_message(
                ERROR_RESPONSE["no_active_game"], ephemeral=True
            )
            return

        instance = server_state.instances[channel_id]

        if str(user.id) in instance.players:
            await interaction.response.send_message(
                ERROR_RESPONSE["already_in_game"].format(user_mention=user.mention),
                ephemeral=True,
            )
            return

        instance.add_player(str(user.id))

        try:
            channel = guild.get_channel(channel_id)
            if channel:
                await channel.set_permissions(
                    user, read_messages=True, send_messages=True
                )
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
                await channel.send(f"{user.mention}", embed=welcome_embed)
        except Exception as e:
            logger.error(f"Failed to send welcome message for user {user.id}: {e}")

        embed = nextcord.Embed(
            title="Player Invited",
            description=f"{user.mention} has been invited to the game!",
            color=nextcord.Color.green(),
        )
        embed.add_field(name="Total Players", value=len(instance.players), inline=True)

        await interaction.response.send_message(embed=embed)

    @admin_group.subcommand(
        name="purgelobby",
        description="Purge all messages from the lobby channel (Admin only)",
    )
    async def admin_purge_lobby(self, interaction: Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                ERROR_RESPONSE["admin_required"],
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        from cogs.events import get_server_state, find_or_create_lobby

        server_state = get_server_state(guild.id)

        try:
            lobby_channel = await find_or_create_lobby(guild)

            await interaction.response.send_message(
                ERROR_RESPONSE["purging_lobby"], ephemeral=True
            )

            try:
                await lobby_channel.purge(bulk=True)
                logger.info(f"Admin purged lobby channel in {guild.name}")
            except Exception as e:
                logger.warning(
                    f"Bulk delete failed in lobby, falling back to individual: {e}"
                )
                deleted_count = 0
                async for message in lobby_channel.history(limit=None):
                    try:
                        await message.delete()
                        deleted_count += 1
                    except Exception as e:
                        logger.debug(f"Failed to delete message in lobby: {e}")

                logger.info(
                    f"Admin purged {deleted_count} messages from lobby in {guild.name}"
                )

            embed = nextcord.Embed(
                title="Lobby Purged",
                description="All messages have been removed from the lobby channel.",
                color=nextcord.Color.red(),
            )
            embed.add_field(name="Channel", value=f"<#{lobby_channel.id}>", inline=True)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Failed to purge lobby in {guild.name}: {e}")
            await interaction.followup.send(
                ERROR_RESPONSE["failed_purge_lobby"],
                ephemeral=True,
            )


def setup(bot):
    bot.add_cog(AdminCog(bot))
