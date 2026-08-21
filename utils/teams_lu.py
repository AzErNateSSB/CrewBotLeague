import discord
import json
import os
from typing import Optional

CHANNEL_TEAMS_LU = 1532892553786036496
TEAMS_LU_FILE    = os.path.join("data", "teams_lu.json")


# ---------------------------------------------------------------------------
# Persistance des IDs de messages
# ---------------------------------------------------------------------------

def _load_lu() -> dict:
    if not os.path.exists(TEAMS_LU_FILE):
        return {}
    with open(TEAMS_LU_FILE, encoding="utf-8") as f:
        return json.load(f)

def _save_lu(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(TEAMS_LU_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Construction de l'embed
# ---------------------------------------------------------------------------

def _make_embed(team: dict) -> discord.Embed:
    sigle     = team["sigle"]
    leader_id = team.get("leader_id")
    members   = team.get("members", [])
    league    = team.get("league")
    created   = team.get("created_at", "?")
    is_b      = sigle.endswith("²")

    color = discord.Color.purple() if is_b else discord.Color.blurple()
    embed = discord.Embed(title=f"〔{sigle}〕", color=color)

    embed.add_field(name="Leader", value=f"<@{leader_id}>", inline=True)

    if league:
        embed.add_field(name="Ligue", value=league, inline=True)

    lines = []
    for m_id in members:
        lines.append(f"<@{m_id}> 👑" if m_id == leader_id else f"<@{m_id}>")

    embed.add_field(
        name=f"Effectif ({len(members)})",
        value="\n".join(lines) if lines else "*Aucun membre*",
        inline=False,
    )

    # Ajouter les stats de saison si disponibles
    from utils.season_data import load_season
    season = load_season()
    if season and league and league in season.get("standings", {}):
        s = season["standings"][league].get(sigle)
        if s:
            diff = s["lives_scored"] - s["lives_conceded"]
            sign = "+" if diff >= 0 else ""
            embed.add_field(
                name="Saison",
                value=(
                    f"**{s['pts']}** pts | {s['wins']}V / {s['losses']}D | "
                    f"diff: {sign}{diff}"
                ),
                inline=False,
            )

    embed.set_footer(text=f"Créée le {created}")
    return embed


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

async def refresh_team_lu(bot: discord.Client, guild_id: int, team: dict):
    """Crée ou met à jour le message d'une équipe dans teams-lu."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    ch = guild.get_channel(CHANNEL_TEAMS_LU)
    if not ch:
        return

    lu    = _load_lu()
    sigle = team["sigle"]
    embed = _make_embed(team)

    msg_id = lu.get(sigle)
    if msg_id:
        try:
            msg = await ch.fetch_message(msg_id)
            await msg.edit(embed=embed)
            return
        except discord.NotFound:
            pass

    msg = await ch.send(embed=embed)
    lu[sigle] = msg.id
    _save_lu(lu)


async def delete_team_lu(bot: discord.Client, guild_id: int, sigle: str):
    """Supprime le message d'une équipe de teams-lu."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    ch = guild.get_channel(CHANNEL_TEAMS_LU)
    if not ch:
        return

    lu = _load_lu()
    msg_id = lu.pop(sigle, None)
    if msg_id:
        try:
            msg = await ch.fetch_message(msg_id)
            await msg.delete()
        except Exception:
            pass
        _save_lu(lu)


async def rebuild_teams_lu(bot: discord.Client, guild_id: int):
    """Vide teams-lu et reconstruit un message par équipe (ordre alphabétique, B après A)."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    ch = guild.get_channel(CHANNEL_TEAMS_LU)
    if not ch:
        return

    await ch.purge(limit=300)

    teams_dir = os.path.join("data", "teams")
    if not os.path.exists(teams_dir):
        _save_lu({})
        return

    all_teams = []
    for fn in os.listdir(teams_dir):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(teams_dir, fn), encoding="utf-8") as f:
            all_teams.append(json.load(f))

    # Trier : équipes A par ordre alphabétique, B immédiatement après leur A
    teams_a = sorted(
        [t for t in all_teams if not t["sigle"].endswith("²")],
        key=lambda t: t["sigle"].lower(),
    )
    teams_b = {t["sigle"]: t for t in all_teams if t["sigle"].endswith("²")}

    ordered = []
    for t in teams_a:
        ordered.append(t)
        b = teams_b.get(f"{t['sigle']}²")
        if b:
            ordered.append(b)
    # B sans A correspondante
    for t in sorted(teams_b.values(), key=lambda x: x["sigle"].lower()):
        if t not in ordered:
            ordered.append(t)

    lu = {}
    for team in ordered:
        msg = await ch.send(embed=_make_embed(team))
        lu[team["sigle"]] = msg.id

    _save_lu(lu)
