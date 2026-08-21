import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


GUILD_ID = 1472927998301835356

@bot.event
async def on_ready():
    print(f"Bot connecté en tant que {bot.user}")
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"{len(synced)} commande(s) synchronisée(s) sur le serveur")
    except Exception as e:
        print(f"Erreur sync: {e}")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    import traceback
    print(f"[ERREUR] Commande : {interaction.command.name if interaction.command else '?'}")
    traceback.print_exception(type(error), error, error.__traceback__)
    if not interaction.response.is_done():
        await interaction.response.send_message(f"❌ Erreur : {error}", ephemeral=True)


async def load_cogs():
    await bot.load_extension("cogs.config")
    await bot.load_extension("cogs.teams")
    await bot.load_extension("cogs.league")
    await bot.load_extension("cogs.panels")
    await bot.load_extension("cogs.freeplay")
    await bot.load_extension("cogs.crewbattle")
    await bot.load_extension("cogs.season")
    await bot.load_extension("cogs.playerstats")


async def main():
    discord.utils.setup_logging()
    async with bot:
        await load_cogs()
        await bot.start(os.getenv("DISCORD_TOKEN"))


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
