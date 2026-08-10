"""
Lit les posts (threads) d'un salon forum Discord.
Usage : python scripts/read_forum.py
"""

import asyncio
import discord
import os
from dotenv import load_dotenv

load_dotenv()

FORUMS = {
    "3QI":  1533874094607695995,
    "SU":   1534668320840876192,
    "SSL":  1533874105328078959,
    "RC":   1533874104002805761,
    "POM":  1535719778272940093,
    "OXVI": 1533874102417489922,
    "MST":  1533874101083570376,
    "LT":   1533874099699318784,
    "JRJ":  1534216812353294386,
    "HoJ":  1533874097849896979,
    "ARK":  1533874096520040588,
    "ADN":  1535719027882852380,
}

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Connecté : {client.user}\n")
    lines = []

    for sigle, forum_id in FORUMS.items():
        channel = client.get_channel(forum_id)
        if not channel:
            lines.append(f"❌ {sigle} — salon introuvable (ID {forum_id})")
            continue

        lines.append(f"{'='*60}")
        lines.append(f"📁 {sigle} — {channel.name}")
        lines.append(f"{'='*60}")

        threads  = list(channel.threads)
        archived = [t async for t in channel.archived_threads(limit=100)]
        all_threads = threads + archived

        if not all_threads:
            lines.append("  (aucun post)")
            lines.append("")
            continue

        for thread in all_threads:
            lines.append(f"  📌 {thread.name}  (ID {thread.id})")
            async for msg in thread.history(limit=30, oldest_first=True):
                if msg.content:
                    lines.append(f"     💬 {msg.author.display_name} : {msg.content[:400]}")
                for embed in msg.embeds:
                    lines.append(f"     📎 Embed titre={repr(embed.title)}")
                    if embed.description:
                        lines.append(f"        desc : {embed.description[:300]}")
                    for field in embed.fields:
                        lines.append(f"        [{field.name}] : {field.value[:200]}")
            lines.append("")

    report_path = os.path.join("scripts", "forums_analysis.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    for l in lines:
        print(l)

    print(f"\n📄 Rapport : {report_path}")
    await client.close()

async def main():
    await client.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
