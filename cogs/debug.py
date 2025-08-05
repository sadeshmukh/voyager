import nextcord
from nextcord.ext import commands
from nextcord import Interaction
from config import ERROR_RESPONSE


class DebugCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @nextcord.slash_command(
        name="debug", description="Debug commands for managing games"
    )
    async def debug_group(self, interaction: Interaction):
        pass

    @debug_group.subcommand(
        name="available", description="Show available game channels"
    )
    async def debug_available(self, interaction: Interaction):
        guild = interaction.guild
        if not guild:
            await interaction.response.send_message(
                ERROR_RESPONSE["server_only"], ephemeral=True
            )
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                ERROR_RESPONSE["admin_required"],
                ephemeral=True,
            )
            return

        from cogs.events import get_server_state

        server_state = get_server_state(guild.id)

        available_channels = [
            f"<#{channel_id}>" for channel_id in server_state.available_game_channels
        ]

        await interaction.response.send_message(
            f"Available game channels: {', '.join(available_channels)}"
        )


def setup(bot):
    bot.add_cog(DebugCog(bot))
