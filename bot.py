import sys
sys.dont_write_bytecode = True  # évite tout risque de bytecode caché obsolète entre 2 lancements

import socket
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

from utils.config import GUILD_ID

load_dotenv()

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


def _acquire_singleton_lock():
    """Empêche 2 processus du bot de tourner en même temps avec le même token
    (Discord diffuse les interactions à TOUTES les connexions actives d'un même
    token — un ancien processus non tué pouvait alors "gagner la course" et
    répondre avec du vieux code, invisible dans le terminal actuel).
    N'est appelé que depuis le point d'entrée réel (bas de fichier), jamais lors
    d'un simple `from bot import GUILD_ID` fait par un cog."""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", 47651))
    except OSError:
        print("❌ Une autre instance de CrewBotLeague tourne déjà (verrou mono-instance port 47651). Arrêt.")
        sys.exit(1)
    return lock

@bot.event
async def on_ready():
    print(f"Bot connecté en tant que {bot.user}")

    from utils.players_stats import register_all_views, build_char_emojis
    guild_obj = bot.get_guild(GUILD_ID)
    if guild_obj:
        build_char_emojis(guild_obj)
        n = register_all_views(bot)
        print(f"{n} PlayerStatsView(s)/TeamStatsView(s) enregistrée(s)")

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
    await bot.load_extension("cogs.admin_panel")


async def main():
    discord.utils.setup_logging()
    async with bot:
        await load_cogs()
        await bot.start(os.getenv("DISCORD_TOKEN"))


if __name__ == "__main__":
    import asyncio
    _singleton_lock = _acquire_singleton_lock()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
