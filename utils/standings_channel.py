"""
Gestion du salon standings/schedule (ID fixe).
Posté automatiquement après chaque match, début et fin de saison.
"""

import discord
import json
import os

from utils.season_data import LEAGUE_NAMES, load_season, sorted_standings, compute_barrages

CHANNEL_STANDINGS  = 1537124049778516108
STANDINGS_FILE     = os.path.join("data", "standings_msgs.json")

_LEAGUE_COLORS = {
    "Ligue-1": discord.Color.yellow(),
    "Ligue-2": discord.Color.blue(),
    "Ligue-3": discord.Color.red(),
    "Ligue-4": discord.Color.green(),
}


# ─── File helpers ─────────────────────────────────────────────────────────────

def _load_msgs() -> dict:
    if not os.path.exists(STANDINGS_FILE):
        return {}
    with open(STANDINGS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_msgs(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(STANDINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ─── Embed builders ───────────────────────────────────────────────────────────

def _standings_embed(season: dict, league: str) -> discord.Embed:
    rows = sorted_standings(season, league)
    if not rows:
        desc = "*Aucune équipe ou aucun match joué.*"
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (team, s) in enumerate(rows):
            rank = medals[i] if i < 3 else f"`{i + 1}.`"
            diff = s["lives_scored"] - s["lives_conceded"]
            sign = "+" if diff >= 0 else ""
            lines.append(
                f"{rank} **{team}** — {s['pts']} pts "
                f"({s['wins']}V/{s['losses']}D  diff:{sign}{diff})"
            )
        desc = "\n".join(lines)

    return discord.Embed(
        title=f"📊 Classement — {league}",
        description=desc,
        color=_LEAGUE_COLORS.get(league, discord.Color.greyple()),
    )


def _schedule_embed(season: dict, league: str) -> discord.Embed:
    calendar = season["calendar"].get(league, [])
    embed = discord.Embed(
        title=f"📅 Calendrier — {league}",
        color=_LEAGUE_COLORS.get(league, discord.Color.greyple()),
    )
    if not calendar:
        embed.description = "*Calendrier non généré.*"
        return embed

    for i, journee in enumerate(calendar, 1):
        lines = []
        for m in journee:
            if m["result"] is None:
                lines.append(f"• **{m['home']}** vs **{m['away']}**")
            else:
                r = m["result"]
                lines.append(
                    f"• **{r['winner']}** {r['winner_lives']}-0 **{r['loser']}** ✅"
                )
        embed.add_field(name=f"Journée {i}", value="\n".join(lines) or "—", inline=False)

    return embed


def _barrages_embed(season: dict) -> discord.Embed:
    barrages = season.get("barrages", [])
    if not barrages:
        return None

    lines = []
    for b in barrages:
        if b["result"] is None:
            status = "⏳ À jouer"
        else:
            status = f"✅ **{b['result']['winner']}** gagne"
        lines.append(
            f"• **{b['team_high']}** ({b['league_high']}) vs "
            f"**{b['team_low']}** ({b['league_low']}) — {status}"
        )

    return discord.Embed(
        title="⚔️ Barrages",
        description="\n".join(lines),
        color=discord.Color.orange(),
    )


# ─── Main refresh ─────────────────────────────────────────────────────────────

async def refresh_standings_channel(guild: discord.Guild) -> None:
    """Met à jour (ou crée) les embeds standings+schedule dans le salon dédié."""
    if not guild:
        return
    ch = guild.get_channel(CHANNEL_STANDINGS)
    if not ch:
        return

    season = load_season()
    msgs   = _load_msgs()

    # ── Mise à jour ou création d'un message par ligue ────────────────────────
    for league in LEAGUE_NAMES:
        embeds = [_standings_embed(season, league), _schedule_embed(season, league)]

        msg_id = msgs.get(league)
        if msg_id:
            try:
                msg = await ch.fetch_message(msg_id)
                await msg.edit(embeds=embeds)
                continue
            except discord.NotFound:
                pass

        # Nouveau message
        msg = await ch.send(embeds=embeds)
        msgs[league] = msg.id

    # ── Message barrages (affiché seulement si saison terminée + barrages) ────
    barrage_embed = _barrages_embed(season) if season else None

    if barrage_embed:
        bid = msgs.get("barrages")
        if bid:
            try:
                msg = await ch.fetch_message(bid)
                await msg.edit(embed=barrage_embed)
            except discord.NotFound:
                msg = await ch.send(embed=barrage_embed)
                msgs["barrages"] = msg.id
        else:
            msg = await ch.send(embed=barrage_embed)
            msgs["barrages"] = msg.id
    else:
        # Supprimer le message barrages s'il existe mais n'est plus pertinent
        bid = msgs.pop("barrages", None)
        if bid:
            try:
                msg = await ch.fetch_message(bid)
                await msg.delete()
            except Exception:
                pass

    _save_msgs(msgs)
