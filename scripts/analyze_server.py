"""
Analyse le serveur Discord et liste tous les messages avec embeds/boutons.
Permet de voir ce qui est déployé vs ce qu'on a dans le code.

Usage : python scripts/analyze_server.py
"""

import asyncio
import discord
import os
from dotenv import load_dotenv

load_dotenv()

GUILD_ID    = 1472927998301835356
CATEGORY_ID = 1472927998863741066   # catégorie principale à analyser

intents = discord.Intents.default()
intents.members  = True
intents.message_content = True

client = discord.Client(intents=intents)


def fmt_component(c) -> str:
    if isinstance(c, discord.Button):
        parts = [f"[BUTTON] label={repr(c.label)}"]
        if c.custom_id:
            parts.append(f"custom_id={repr(c.custom_id)}")
        if c.style:
            parts.append(f"style={c.style.name}")
        if c.disabled:
            parts.append("DISABLED")
        return " | ".join(parts)
    if isinstance(c, discord.SelectMenu):
        return f"[SELECT] custom_id={repr(c.custom_id)} placeholder={repr(c.placeholder)}"
    return f"[{type(c).__name__}]"


async def scan_channel(channel, prefix="  ") -> list[str]:
    lines = []
    try:
        messages = [m async for m in channel.history(limit=50, oldest_first=True)]
    except (discord.Forbidden, discord.HTTPException):
        lines.append(f"{prefix}⛔ Accès refusé ou erreur")
        return lines

    interesting = [
        m for m in messages
        if m.embeds or m.components or (m.author.bot and m.content)
    ]

    if not interesting:
        lines.append(f"{prefix}(aucun message bot intéressant)")
        return lines

    for m in interesting:
        lines.append(f"{prefix}📨 Message ID: {m.id}  —  {m.created_at.strftime('%Y-%m-%d %H:%M')}")
        if m.content:
            preview = m.content[:120].replace("\n", " ")
            lines.append(f"{prefix}   Contenu : {preview}")
        for i, embed in enumerate(m.embeds):
            lines.append(f"{prefix}   Embed #{i+1} : titre={repr(embed.title)}  couleur={embed.color}")
            if embed.description:
                preview = embed.description[:100].replace("\n", " ")
                lines.append(f"{prefix}             desc : {preview}…")
            for field in embed.fields:
                lines.append(f"{prefix}             champ [{field.name}] : {field.value[:80]}")
        for row in m.components:
            for comp in (row.children if hasattr(row, "children") else [row]):
                lines.append(f"{prefix}   {fmt_component(comp)}")
    return lines


@client.event
async def on_ready():
    print(f"Connecté en tant que {client.user}\n")
    guild = client.get_guild(GUILD_ID)
    if not guild:
        print("❌ Serveur introuvable.")
        await client.close()
        return

    output_lines = [
        f"=== ANALYSE DU SERVEUR : {guild.name} ===",
        f"    Membres : {guild.member_count}",
        f"    Salons   : {len(guild.channels)}",
        "",
    ]

    # ── Catégorie principale ────────────────────────────────────────────────
    main_cat = guild.get_channel(CATEGORY_ID)
    if main_cat:
        output_lines.append(f"📁 CATÉGORIE PRINCIPALE : {main_cat.name} (ID {main_cat.id})")
        for ch in main_cat.channels:
            output_lines.append(f"  └─ #{ch.name}  (ID {ch.id}  type={type(ch).__name__})")
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                output_lines += await scan_channel(ch)
        output_lines.append("")

    # ── Toutes les autres catégories ───────────────────────────────────────
    output_lines.append("=" * 60)
    output_lines.append("📂 TOUTES LES CATÉGORIES ET SALONS AVEC MESSAGES BOT")
    output_lines.append("=" * 60)

    for cat in sorted(guild.categories, key=lambda c: c.position):
        if cat.id == CATEGORY_ID:
            continue   # déjà traitée

        cat_lines = []
        for ch in cat.channels:
            if not isinstance(ch, (discord.TextChannel,)):
                continue
            ch_lines = await scan_channel(ch, prefix="      ")
            has_content = any("📨" in l or "BUTTON" in l or "Embed" in l for l in ch_lines)
            if has_content:
                cat_lines.append(f"  └─ #{ch.name}  (ID {ch.id})")
                cat_lines += ch_lines

        if cat_lines:
            output_lines.append(f"\n📁 {cat.name}  (ID {cat.id})")
            output_lines += cat_lines

    # ── Salons sans catégorie ──────────────────────────────────────────────
    uncategorized = [
        ch for ch in guild.channels
        if isinstance(ch, discord.TextChannel) and ch.category is None
    ]
    if uncategorized:
        output_lines.append("\n📁 (sans catégorie)")
        for ch in uncategorized:
            ch_lines = await scan_channel(ch, prefix="      ")
            has_content = any("📨" in l or "BUTTON" in l or "Embed" in l for l in ch_lines)
            if has_content:
                output_lines.append(f"  └─ #{ch.name}  (ID {ch.id})")
                output_lines += ch_lines

    # ── Écriture du rapport ────────────────────────────────────────────────
    report_path = os.path.join("scripts", "server_analysis.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    # Affichage terminal (résumé)
    for line in output_lines:
        print(line)

    print(f"\n📄 Rapport complet sauvegardé dans : {report_path}")
    await client.close()


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN manquant dans .env")
        return
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
