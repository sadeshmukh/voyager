import logging
import nextcord
from nextcord.ext import commands
from nextcord import Interaction
from typing import Optional

from config import SERVER_CONFIG_OPTIONS, ERROR_RESPONSE
from cogs.events import get_server_state

logger = logging.getLogger("voyager_discord")


class ServerCog(commands.Cog):
    """Server commands for server administrators"""

    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(name="server", description="Server configuration commands")
    async def server_group(self, interaction: Interaction):
        pass

    @server_group.subcommand(
        name="create", description="Create a new game channel (Server Admin only)"
    )
    async def server_create_channel(self, interaction: Interaction, name: str):
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

        from cogs.events import ensure_voyager_category

        server_state = get_server_state(guild.id)
        max_channels = server_state.config.get("max_channels", 10)

        total_channels = len(server_state.all_game_channels)
        if total_channels >= max_channels:
            await interaction.response.send_message(
                f"Maximum number of game channels ({max_channels}) reached! Cannot create more channels.",
                ephemeral=True,
            )
            return

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
                reason=f"Server admin-created game channel: {name}",
                overwrites=overwrites,
            )

            server_state.all_game_channels.append(channel.id)
            server_state.available_game_channels.append(channel.id)

            await interaction.response.send_message(
                f"Game channel created: {name}\n"
                f"Channel: <#{channel.id}>\n"
                f"Status: Available for games\n"
                f"Channels Total: {len(server_state.all_game_channels)}/{max_channels}"
            )

        except Exception as e:
            logger.error(f"Failed to create game channel in {guild.name}: {e}")
            await interaction.response.send_message(
                ERROR_RESPONSE["failed_create_channel"],
                ephemeral=True,
            )

    @server_group.subcommand(name="config", description="View server configuration")
    async def server_config_help(self, interaction: Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        server_state = get_server_state(guild.id)

        embed = nextcord.Embed(
            title="Server Configuration",
            description="Current server settings and their values",
            color=nextcord.Color.blue(),
        )

        for setting_name, setting_info in SERVER_CONFIG_OPTIONS.items():
            current_value = server_state.config.get(
                setting_name, setting_info["default"]
            )
            description = setting_info["description"]

            if setting_info["type"] == "bool":
                value_display = "✅ True" if current_value else "❌ False"
            else:
                value_display = str(current_value)

            embed.add_field(
                name=f"`{setting_name}`",
                value=f"**Current:** {value_display}\n{description}",
                inline=False,
            )

        embed.add_field(
            name="How to Change",
            value="Use `/server conf set [setting] [value]` to change settings",
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @server_group.subcommand(name="conf", description="Configure server settings")
    async def server_conf_group(self, interaction: Interaction):
        pass

    @server_group.subcommand(
        name="setup", description="Quick setup guide for new servers"
    )
    async def server_setup_help(self, interaction: Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        server_state = get_server_state(guild.id)

        embed = nextcord.Embed(
            title="Server Setup Guide",
            description="Steps to get Voyager working in your server",
            color=nextcord.Color.blue(),
        )

        if not server_state.initialized:
            embed.add_field(
                name="Step 1: Create Game Channels",
                value="Use `/server create [name]` to create your first game channel\n"
                "Example: `/server create test` creates `v-inst-test`",
                inline=False,
            )
            embed.add_field(
                name="Step 2: Wait for Auto-Initialization",
                value="Once you have at least one game channel, the server will automatically initialize",
                inline=False,
            )
        else:
            embed.add_field(
                name="✅ Server Ready",
                value="Your server is properly set up and ready for games!",
                inline=False,
            )

        embed.add_field(
            name="Current Status",
            value=f"**Initialized:** {'✅ Yes' if server_state.initialized else '❌ No'}\n"
            f"**Game Channels:** {len(server_state.all_game_channels)}/{server_state.config.get('max_channels', 10)}\n"
            f"**Available Channels:** {len(server_state.available_game_channels)}",
            inline=False,
        )

        embed.add_field(
            name="Next Steps",
            value="• Use `/waitlist` in the lobby to join games\n"
            "• Use `/state` to check current status\n"
            "• Use `/server config` to view all settings",
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @server_conf_group.subcommand(
        name="set", description="Set a server configuration value"
    )
    async def server_conf_set(self, interaction: Interaction, setting: str, value: str):
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

        server_state = get_server_state(guild.id)

        if setting not in SERVER_CONFIG_OPTIONS:
            valid_settings = ", ".join(SERVER_CONFIG_OPTIONS.keys())
            await interaction.response.send_message(
                f"Invalid setting: `{setting}`\nValid settings: `{valid_settings}`",
                ephemeral=True,
            )
            return

        setting_info = SERVER_CONFIG_OPTIONS[setting]
        setting_type = setting_info["type"]

        try:
            if setting_type == "bool":
                if value.lower() in ["true", "1", "yes", "on"]:
                    parsed_value = True
                elif value.lower() in ["false", "0", "no", "off"]:
                    parsed_value = False
                else:
                    await interaction.response.send_message(
                        f"Invalid boolean value: `{value}`\nUse: true/false, 1/0, yes/no, on/off",
                        ephemeral=True,
                    )
                    return
            elif setting_type == "int":
                parsed_value = int(value)
                if "min" in setting_info and parsed_value < setting_info["min"]:
                    await interaction.response.send_message(
                        f"Value too low: `{parsed_value}`\nMinimum: {setting_info['min']}",
                        ephemeral=True,
                    )
                    return
                if "max" in setting_info and parsed_value > setting_info["max"]:
                    await interaction.response.send_message(
                        f"Value too high: `{parsed_value}`\nMaximum: {setting_info['max']}",
                        ephemeral=True,
                    )
                    return
            elif setting_type == "str":
                parsed_value = value
                if (
                    "choices" in setting_info
                    and parsed_value not in setting_info["choices"]
                ):
                    valid_choices = ", ".join(setting_info["choices"])
                    await interaction.response.send_message(
                        f"Invalid choice: `{parsed_value}`\nValid choices: `{valid_choices}`",
                        ephemeral=True,
                    )
                    return
            else:
                await interaction.response.send_message(
                    f"Unknown setting type: {setting_type}",
                    ephemeral=True,
                )
                return

            old_value = server_state.config.get(setting, setting_info["default"])
            server_state.config[setting] = parsed_value

            embed = nextcord.Embed(
                title="Configuration Updated",
                description=f"Setting `{setting}` has been updated",
                color=nextcord.Color.green(),
            )
            embed.add_field(
                name="Old Value",
                value=str(old_value),
                inline=True,
            )
            embed.add_field(
                name="New Value",
                value=str(parsed_value),
                inline=True,
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

        except ValueError:
            await interaction.response.send_message(
                f"Invalid value for {setting_type}: `{value}`",
                ephemeral=True,
            )


def setup(bot):
    bot.add_cog(ServerCog(bot))
