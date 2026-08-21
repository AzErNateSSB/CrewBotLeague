import discord
from discord.ext import commands

ERROR_CHANNEL_ID = 1520071761314582718


async def alert_error(bot: commands.Bot, title: str, description: str):
    """Poste une alerte dans le salon technique. Best-effort : ne lève jamais d'exception."""
    channel = bot.get_channel(ERROR_CHANNEL_ID)
    if not channel:
        print(f"[WARN] alert_error: salon {ERROR_CHANNEL_ID} introuvable")
        return
    embed = discord.Embed(title=f"🚨 {title}", description=description[:4000], color=discord.Color.red())
    try:
        await channel.send(embed=embed)
    except Exception as e:
        print(f"[WARN] alert_error: envoi impossible : {e}")
