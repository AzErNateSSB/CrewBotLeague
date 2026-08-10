"""
Retire les rôles spécifiés de tous les membres du serveur qui les ont.
Usage : python scripts/remove_roles.py
"""

import asyncio
import discord
import os
from dotenv import load_dotenv

load_dotenv()

GUILD_ID = 1472927998301835356

ROLE_IDS = [
    1475929938451234978,
    1475930004398280806,
    1475930038737305800,
    1475930061281562839,
]

intents = discord.Intents.default()
intents.members = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Connecté : {client.user}\n")
    guild = client.get_guild(GUILD_ID)

    roles = [guild.get_role(rid) for rid in ROLE_IDS]
    roles = [r for r in roles if r is not None]

    print("Rôles à retirer :")
    for r in roles:
        print(f"  - [{r.id}] {r.name}")
    print()

    removed = 0
    skipped = 0

    async for member in guild.fetch_members(limit=None):
        member_roles = {r.id for r in member.roles}
        to_remove = [r for r in roles if r.id in member_roles]
        if not to_remove:
            continue
        try:
            await member.remove_roles(*to_remove, reason="Retrait en masse via script admin")
            names = ", ".join(r.name for r in to_remove)
            print(f"✅ {member.display_name} — retiré : {names}")
            removed += 1
        except discord.Forbidden:
            print(f"❌ {member.display_name} — permission refusée")
            skipped += 1
        except Exception as e:
            print(f"❌ {member.display_name} — erreur : {e}")
            skipped += 1

    print(f"\n{'='*40}")
    print(f"✅ Membres traités : {removed}")
    if skipped:
        print(f"❌ Échecs        : {skipped}")

    await client.close()


async def main():
    await client.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
