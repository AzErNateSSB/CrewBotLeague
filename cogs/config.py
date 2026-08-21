import discord
from discord.ext import commands
from discord import app_commands
from utils.i18n import t, set_lang, SUPPORTED_LANGUAGES
from utils.sheets_log import log_command


@app_commands.command(name="cbl_setserverlanguage", description="Définit la langue du bot sur ce serveur / Set the bot language for this server")
@app_commands.describe(language="Langue / Language (fr, en)")
async def cbl_setserverlanguage(interaction: discord.Interaction, language: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            t(interaction.guild_id, "lang_admin_only"), ephemeral=True
        )
        return

    lang = language.lower().strip()
    if lang not in SUPPORTED_LANGUAGES:
        available = ", ".join(f"`{k}` ({v})" for k, v in SUPPORTED_LANGUAGES.items())
        await interaction.response.send_message(
            t(interaction.guild_id, "lang_unknown", lang=language, available=available),
            ephemeral=True,
        )
        await log_command(interaction.user.display_name,
                          f"cbl_setserverlanguage **{language}**", "Failed",
                          f"Langue `{language}` inconnue")
        return

    set_lang(interaction.guild_id, lang)
    await interaction.response.send_message(t(interaction.guild_id, "lang_set"))
    await log_command(interaction.user.display_name,
                      f"cbl_setserverlanguage **{lang}**", "Completed",
                      f"Langue du serveur définie sur **{SUPPORTED_LANGUAGES[lang]}**")


class Config(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.tree.add_command(cbl_setserverlanguage)


async def setup(bot: commands.Bot):
    await bot.add_cog(Config(bot))
