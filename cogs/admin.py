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

            await interaction.response.send_message(
                f"Game channel created: {name}\n"
                f"Channel: <#{channel.id}>\n"
                f"Status: Available for games\n"
                f"Channels Total: {len(server_state.all_game_channels)}/{MAX_CHANNELS}"
            )

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

        await interaction.response.send_message(
            f"Game instance created: {name}\n"
            f"Channel: <#{game_channel.id}>\n"
            f"Status: Waiting for players"
        )

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
                await channel.send(
                    f"{user.mention} has been invited to the game!\n"
                    f"Total Players: {len(instance.players)}"
                )
        except Exception as e:
            logger.error(f"Failed to send welcome message for user {user.id}: {e}")

        await interaction.response.send_message(
            f"{user.mention} has been invited to the game!\n"
            f"Total Players: {len(instance.players)}"
        )

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

        from cogs.events import find_or_create_lobby

        # server_state = get_server_state(guild.id)

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

            await interaction.followup.send(
                f"Lobby purged successfully.\nChannel: <#{lobby_channel.id}>"
            )

        except Exception as e:
            logger.error(f"Failed to purge lobby in {guild.name}: {e}")
            await interaction.followup.send(
                ERROR_RESPONSE["failed_purge_lobby"],
                ephemeral=True,
            )

    @admin_group.subcommand(
        name="purgeroles",
        description="Purge all game roles (Admin only)",
    )
    async def admin_purge_roles(self, interaction: Interaction):
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

        await interaction.response.send_message(
            "Purging all game roles...", ephemeral=True
        )

        deleted_roles = []
        failed_roles = []

        # just straight up delete all game roles
        # all temp
        # I wish there was a tagging system for roles
        for role in guild.roles:
            if role.name.startswith("Voyaging "):
                try:
                    await role.delete(reason="Admin purge of game roles")
                    deleted_roles.append(role.name)
                    logger.info(f"Admin deleted game role: {role.name}")
                except Exception as e:
                    failed_roles.append(f"{role.name} (error: {e})")
                    logger.error(f"Failed to delete role {role.name}: {e}")

        server_state.game_roles.clear()

        if deleted_roles:
            response = f"**Successfully deleted {len(deleted_roles)} game roles:**\n"
            for role_name in deleted_roles:
                response += f"• {role_name}\n"
        else:
            response = "No game roles found to delete.\n"

        if failed_roles:
            response += f"\n**Failed to delete {len(failed_roles)} roles:**\n"
            for role_info in failed_roles:
                response += f"• {role_info}\n"

        await interaction.followup.send(response, ephemeral=True)


def setup(bot):
    bot.add_cog(AdminCog(bot))
