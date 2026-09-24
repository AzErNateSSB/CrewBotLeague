import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from utils.season_data import (
    LEAGUE_NAMES, LEAGUE_CATEGORY_IDS,
    load_season, save_season, new_season,
    generate_round_robin, sorted_standings, compute_barrages,
    save_official_match,
)
from utils.sheets_log import log_command, update_log
from cogs.teams import load_team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_admin(user: discord.Member) -> bool:
    return user.guild_permissions.administrator


def _standings_embed(season: dict, league: str) -> discord.Embed:
    rows = sorted_standings(season, league)
    if not rows:
        desc = "*Aucune équipe ou aucun match joué.*"
    else:
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, (team, s) in enumerate(rows):
            rank  = medals[i] if i < 3 else f"`{i+1}.`"
            diff  = s["lives_scored"] - s["lives_conceded"]
            sign  = "+" if diff >= 0 else ""
            lines.append(
                f"{rank} **{team}** — {s['pts']} pts  "
                f"({s['wins']}V/{s['losses']}D  diff:{sign}{diff})"
            )
        desc = "\n".join(lines)

    return discord.Embed(
        title=f"📊 Classement — {league}",
        description=desc,
        color=discord.Color.gold(),
    )


def _schedule_embed(season: dict, league: str) -> discord.Embed:
    calendar = season["calendar"].get(league, [])
    if not calendar:
        return discord.Embed(title=f"📅 Calendrier — {league}",
                             description="*Calendrier non généré.*",
                             color=discord.Color.blurple())

    embed = discord.Embed(title=f"📅 Calendrier — {league}", color=discord.Color.blurple())
    for i, journee in enumerate(calendar, 1):
        lines = []
        for m in journee:
            if m["result"] is None:
                lines.append(f"• **{m['home']}** vs **{m['away']}**")
            else:
                r = m["result"]
                lines.append(f"• **{r['winner']}** {r['winner_lives']}-0 {r['loser']}  ✅")
        embed.add_field(name=f"Journée {i}", value="\n".join(lines) or "—", inline=False)
    return embed

# ---------------------------------------------------------------------------
# /cbl_teaminfo
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_teaminfo", description="Affiche les informations d'une équipe")
@app_commands.describe(sigle="Sigle de l'équipe")
async def cbl_teaminfo(interaction: discord.Interaction, sigle: str):
    team = load_team(sigle)
    if not team:
        await interaction.response.send_message(f"❌ Équipe `{sigle}` introuvable.", ephemeral=True)
        return

    guild   = interaction.guild
    season  = load_season()
    league  = team.get("league") or "—"
    members = team.get("members", [])

    member_names = []
    for mid in members:
        m = guild.get_member(mid)
        name = m.display_name if m else f"<@{mid}>"
        prefix = "👑 " if mid == team["leader_id"] else "• "
        member_names.append(f"{prefix}{name}")

    embed = discord.Embed(title=f"⚔️ {sigle}", color=discord.Color.blurple())
    embed.add_field(name="Ligue",    value=league, inline=True)
    embed.add_field(name="Membres",  value="\n".join(member_names) or "—", inline=False)

    if season and league in season["standings"] and sigle in season["standings"][league]:
        s    = season["standings"][league][sigle]
        diff = s["lives_scored"] - s["lives_conceded"]
        sign = "+" if diff >= 0 else ""
        embed.add_field(
            name="Stats saison",
            value=f"{s['pts']} pts | {s['wins']}V / {s['losses']}D | diff: {sign}{diff}",
            inline=False,
        )

    await interaction.response.send_message(embed=embed)

# ---------------------------------------------------------------------------
# /cbl_leagueaddteam
# ---------------------------------------------------------------------------

class MoveTeamView(discord.ui.View):
    def __init__(self, team_sigle: str, old_league: str, new_league: str):
        super().__init__(timeout=60)
        self.team_sigle = team_sigle
        self.old_league = old_league
        self.new_league = new_league

    async def _disable(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="✅ Oui, déplacer", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _is_admin(interaction.user):
            await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
            return
        season = load_season()
        if self.team_sigle in season["leagues"].get(self.old_league, []):
            season["leagues"][self.old_league].remove(self.team_sigle)
        if self.team_sigle not in season["leagues"][self.new_league]:
            season["leagues"][self.new_league].append(self.team_sigle)

        # Mettre à jour le fichier team
        from cogs.teams import load_team, save_team
        team = load_team(self.team_sigle)
        if team:
            team["league"] = self.new_league
            save_team(team)

        save_season(season)
        await self._disable(interaction)
        await interaction.followup.send(
            f"✅ **{self.team_sigle}** déplacée de **{self.old_league}** vers **{self.new_league}**."
        )
        await log_command(interaction.user.display_name,
                          f"cbl_leagueaddteam **{self.new_league}** **{self.team_sigle}**",
                          "Completed",
                          f"**{self.team_sigle}** déplacée de **{self.old_league}** vers **{self.new_league}**")

    @discord.ui.button(label="❌ Non, annuler", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._disable(interaction)
        await interaction.followup.send("Annulé.")
        await log_command(interaction.user.display_name,
                          f"cbl_leagueaddteam **{self.new_league}** **{self.team_sigle}**",
                          "Canceled", "Déplacement annulé par l'admin")


@app_commands.command(name="cbl_leagueaddteam", description="[ADMIN] Ajoute une équipe dans une ligue")
@app_commands.describe(ligue="Ligue cible (Ligue-1 à Ligue-4)", sigle="Sigle de l'équipe")
async def cbl_leagueaddteam(interaction: discord.Interaction, ligue: str, sigle: str):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Commande réservée aux administrateurs.", ephemeral=True)
        return

    if ligue not in LEAGUE_NAMES:
        await interaction.response.send_message(
            f"❌ Ligue invalide. Disponibles : {', '.join(LEAGUE_NAMES)}", ephemeral=True
        )
        return

    team = load_team(sigle)
    if not team:
        await interaction.response.send_message(f"❌ Équipe `{sigle}` introuvable.", ephemeral=True)
        return

    season = load_season()
    if not season:
        await interaction.response.send_message(
            "❌ Aucune saison en cours. Créez-en une d'abord avec `/cbl_setup_season`.",
            ephemeral=True,
        )
        return

    if season["status"] == "active":
        await interaction.response.send_message(
            "❌ Impossible de modifier les ligues pendant une saison active.", ephemeral=True
        )
        return

    # Chercher si l'équipe est déjà dans une ligue
    current_league = next((lg for lg, teams in season["leagues"].items() if sigle in teams), None)

    if current_league == ligue:
        await interaction.response.send_message(
            f"❌ **{sigle}** est déjà dans **{ligue}**.", ephemeral=True
        )
        return

    if current_league:
        view = MoveTeamView(sigle, current_league, ligue)
        await interaction.response.send_message(
            f"⚠️ **{sigle}** est déjà dans **{current_league}**. La déplacer vers **{ligue}** ?",
            view=view,
        )
        return

    # Ajouter directement
    season["leagues"][ligue].append(sigle)
    team["league"] = ligue
    from cogs.teams import save_team
    save_team(team)
    save_season(season)

    await interaction.response.send_message(f"✅ **{sigle}** ajoutée à **{ligue}**.")
    await log_command(interaction.user.display_name,
                      f"cbl_leagueaddteam **{ligue}** **{sigle}**", "Completed",
                      f"**{sigle}** ajoutée à **{ligue}**")

# ---------------------------------------------------------------------------
# /cbl_leagueremoveteam
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_leagueremoveteam", description="[ADMIN] Retire une équipe de sa ligue")
@app_commands.describe(sigle="Sigle de l'équipe")
async def cbl_leagueremoveteam(interaction: discord.Interaction, sigle: str):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Commande réservée aux administrateurs.", ephemeral=True)
        return

    season = load_season()
    if not season:
        await interaction.response.send_message("❌ Aucune saison en cours.", ephemeral=True)
        return

    if season["status"] == "active":
        await interaction.response.send_message(
            "❌ Impossible de modifier les ligues pendant une saison active.", ephemeral=True
        )
        return

    current_league = next((lg for lg, teams in season["leagues"].items() if sigle in teams), None)
    if not current_league:
        await interaction.response.send_message(
            f"❌ **{sigle}** n'est dans aucune ligue.", ephemeral=True
        )
        return

    season["leagues"][current_league].remove(sigle)
    team = load_team(sigle)
    if team:
        team["league"] = None
        from cogs.teams import save_team
        save_team(team)
    save_season(season)

    await interaction.response.send_message(
        f"✅ **{sigle}** retirée de **{current_league}**."
    )
    await log_command(interaction.user.display_name,
                      f"cbl_leagueremoveteam **{sigle}**", "Completed",
                      f"**{sigle}** retirée de **{current_league}**")

# ---------------------------------------------------------------------------
# /cbl_standings
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_standings", description="Affiche le classement d'une ligue")
@app_commands.describe(ligue="Ligue à afficher (toutes si vide)")
async def cbl_standings(interaction: discord.Interaction, ligue: Optional[str] = None):
    season = load_season()
    if not season:
        await interaction.response.send_message("❌ Aucune saison en cours.", ephemeral=True)
        return

    if ligue:
        if ligue not in LEAGUE_NAMES:
            await interaction.response.send_message(
                f"❌ Ligue invalide. Disponibles : {', '.join(LEAGUE_NAMES)}", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=_standings_embed(season, ligue))
    else:
        embeds = [_standings_embed(season, lg) for lg in LEAGUE_NAMES]
        await interaction.response.send_message(embeds=embeds)

# ---------------------------------------------------------------------------
# /cbl_schedule
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_schedule", description="Affiche le calendrier des matchs d'une ligue")
@app_commands.describe(ligue="Ligue à afficher")
async def cbl_schedule(interaction: discord.Interaction, ligue: str):
    season = load_season()
    if not season:
        await interaction.response.send_message("❌ Aucune saison en cours.", ephemeral=True)
        return
    if ligue not in LEAGUE_NAMES:
        await interaction.response.send_message(
            f"❌ Ligue invalide. Disponibles : {', '.join(LEAGUE_NAMES)}", ephemeral=True
        )
        return
    await interaction.response.send_message(embed=_schedule_embed(season, ligue))


# ---------------------------------------------------------------------------
# /cbl_admin_end_season
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_admin_end_season", description="[ADMIN] Clôture la saison et calcule les mouvements")
async def cbl_admin_end_season(interaction: discord.Interaction):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Commande réservée aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer()

    season = load_season()
    if not season or season["status"] != "active":
        await interaction.followup.send("❌ Aucune saison active.")
        return

    season["status"] = "finished"
    barrages = compute_barrages(season)
    save_season(season)

    from utils.standings_channel import refresh_standings_channel
    await refresh_standings_channel(interaction.guild)

    lines = ["**Mouvements directs :**"]
    for lg_high, lg_low in zip(LEAGUE_NAMES, LEAGUE_NAMES[1:]):
        high = sorted_standings(season, lg_high)
        low  = sorted_standings(season, lg_low)
        if high:
            lines.append(f"🔻 {high[-1][0]} descend de **{lg_high}** → **{lg_low}**")
        if low:
            lines.append(f"🔺 {low[0][0]} monte de **{lg_low}** → **{lg_high}**")

    if barrages:
        lines.append("\n**Barrages :**")
        for b in barrages:
            lines.append(f"⚔️ **{b['team_high']}** ({b['league_high']}) vs **{b['team_low']}** ({b['league_low']})")

    embed = discord.Embed(
        title="🏁 Fin de saison !",
        description="\n".join(lines),
        color=discord.Color.red(),
    )
    await interaction.followup.send(embed=embed)
    await log_command(interaction.user.display_name,
                      f"cbl_admin_end_season", "Completed",
                      f"Saison **{season['name']}** clôturée — {len(barrages)} barrage(s)")

# ---------------------------------------------------------------------------
# /cbl_barrages
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_barrages", description="Affiche les matchs de barrage")
async def cbl_barrages(interaction: discord.Interaction):
    season = load_season()
    if not season or not season.get("barrages"):
        await interaction.response.send_message("❌ Aucun barrage en cours.", ephemeral=True)
        return

    lines = []
    for b in season["barrages"]:
        if b["result"] is None:
            status = "⏳ À jouer"
        else:
            status = f"✅ **{b['result']['winner']}** gagne"
        lines.append(
            f"• **{b['team_high']}** ({b['league_high']}) vs **{b['team_low']}** ({b['league_low']}) — {status}"
        )

    embed = discord.Embed(
        title="⚔️ Barrages",
        description="\n".join(lines),
        color=discord.Color.orange(),
    )
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class League(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.tree.add_command(cbl_teaminfo)
        self.bot.tree.add_command(cbl_leagueaddteam)
        self.bot.tree.add_command(cbl_leagueremoveteam)
        self.bot.tree.add_command(cbl_standings)
        self.bot.tree.add_command(cbl_schedule)
        self.bot.tree.add_command(cbl_admin_end_season)
        self.bot.tree.add_command(cbl_barrages)


async def setup(bot: commands.Bot):
    await bot.add_cog(League(bot))
