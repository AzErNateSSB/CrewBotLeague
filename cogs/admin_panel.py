"""
Panel de configuration administrateur.
Salon  : 1537096187981594714
Owner  : AzErNate (461601543121010691)
"""

import discord
from discord.ext import commands
from discord import app_commands
import json
import os
from datetime import date

from utils.i18n import set_lang
from utils.sheets_log import log_command
from utils.teams_lu import rebuild_teams_lu
from utils.players_stats import rebuild_all_stats, delete_player_stats_post
from utils.season_data import (
    LEAGUE_NAMES, LEAGUE_CATEGORY_IDS, LEAGUE_EMOJIS, LEAGUE_SHORT,
    load_season, save_season, new_season, round_window,
)

CHANNEL_CONFIG        = 1537096187981594714
OWNER_ID              = 461601543121010691
SEASON_PANEL_CATEGORY = 1476173468482539664
PANELS_FILE           = os.path.join("data", "panels.json")
TEAMS_DIR             = os.path.join("data", "teams")
PLAYERS_DIR           = os.path.join("data", "players")

# ─── File helpers ─────────────────────────────────────────────────────────────

def _load_all_teams() -> list[dict]:
    if not os.path.exists(TEAMS_DIR):
        return []
    out = []
    for fn in os.listdir(TEAMS_DIR):
        if fn.endswith(".json"):
            with open(os.path.join(TEAMS_DIR, fn), encoding="utf-8") as f:
                out.append(json.load(f))
    return sorted(out, key=lambda t: t["sigle"].lower())


def _load_team(sigle: str) -> dict | None:
    path = os.path.join(TEAMS_DIR, f"{sigle}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_player(discord_id: int) -> dict | None:
    path = os.path.join(PLAYERS_DIR, f"{discord_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_panels() -> dict:
    if not os.path.exists(PANELS_FILE):
        return {}
    with open(PANELS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_panels(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(PANELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ─── Admin remove player (full deletion) ─────────────────────────────────────

async def _adm_remove_player(bot: discord.Client, guild: discord.Guild, player_id: int) -> str:
    """Retire totalement un joueur du bot (équipe, stats, JSON)."""
    from cogs.teams import load_team, save_team, find_team_of_player
    from utils.teams_lu import refresh_team_lu
    from utils.players_stats import refresh_team_stats_post

    player = _load_player(player_id)
    if not player:
        return "❌ Joueur introuvable dans les données du bot."

    name = player.get("name", str(player_id))
    team_sigle = find_team_of_player(player_id)

    if team_sigle:
        team = load_team(team_sigle)
        if team:
            if player_id in team["members"]:
                team["members"].remove(player_id)
            save_team(team)
            role = guild.get_role(team["role_id"])
            member = guild.get_member(player_id)
            if role and member:
                try:
                    await member.remove_roles(role)
                except discord.Forbidden:
                    pass
            await delete_player_stats_post(bot, guild.id, player_id)
            await refresh_team_stats_post(bot, guild.id, team_sigle)
            await refresh_team_lu(bot, guild.id, team)

    path = os.path.join(PLAYERS_DIR, f"{player_id}.json")
    if os.path.exists(path):
        os.remove(path)

    return f"✅ Joueur **{name}** supprimé du bot."


# ─── Season panel helpers ─────────────────────────────────────────────────────

_LEAGUE_COLORS = {
    "Ligue-1": discord.Color.yellow(),
    "Ligue-2": discord.Color.blue(),
    "Ligue-3": discord.Color.red(),
    "Ligue-4": discord.Color.green(),
}


def _league_embed(league: str, season: dict) -> discord.Embed:
    teams = season["leagues"].get(league, [])
    desc  = "\n".join(f"• **{t}**" for t in teams) or "*Aucune équipe.*"
    embed = discord.Embed(
        title=f"📋 {league}",
        description=desc,
        color=_LEAGUE_COLORS.get(league, discord.Color.greyple()),
    )
    embed.set_footer(text=f"Saison {season['name']} — {len(teams)} équipe(s)")
    return embed


async def _refresh_league_msg(bot: discord.Client, guild_id: int, league: str):
    panels  = _load_panels()
    msg_id  = panels.get("season_league_msgs", {}).get(league)
    chan_id = panels.get("season_panel_channel")
    if not msg_id or not chan_id:
        return
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    ch = guild.get_channel(chan_id)
    if not ch:
        return
    season = load_season()
    if not season:
        return
    try:
        msg = await ch.fetch_message(msg_id)
        await msg.edit(embed=_league_embed(league, season))
    except Exception:
        pass


# ─── Season panel views (persistent) ─────────────────────────────────────────

class SeasonLeagueView(discord.ui.View):
    """Boutons ➕ Ajouter / ➖ Retirer pour un message de ligue dans le panel saison."""

    def __init__(self, league: str):
        super().__init__(timeout=None)
        self.league = league

        add = discord.ui.Button(
            label="➕ Ajouter une équipe",
            style=discord.ButtonStyle.success,
            custom_id=f"slg_add:{league}",
        )
        add.callback = self._add_callback
        self.add_item(add)

        rem = discord.ui.Button(
            label="➖ Retirer une équipe",
            style=discord.ButtonStyle.danger,
            custom_id=f"slg_del:{league}",
        )
        rem.callback = self._del_callback
        self.add_item(rem)

    async def _add_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
            return

        season = load_season()
        if not season:
            await interaction.response.send_message("❌ Aucune saison en cours.", ephemeral=True)
            return
        if season["status"] == "active":
            await interaction.response.send_message("❌ La saison est déjà active.", ephemeral=True)
            return

        in_league = set(season["leagues"].get(self.league, []))
        available = [t for t in _load_all_teams() if t["sigle"] not in in_league]
        if not available:
            await interaction.response.send_message(
                f"ℹ️ Toutes les équipes sont déjà dans **{self.league}**.", ephemeral=True
            )
            return

        options = [discord.SelectOption(label=t["sigle"], value=t["sigle"]) for t in available[:25]]
        league  = self.league

        class _AddView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                sel = discord.ui.Select(placeholder=f"Ajouter à {league}...", options=options)
                sel.callback = self._on_select
                self.add_item(sel)

            async def _on_select(self, inter: discord.Interaction):
                from cogs.teams import load_team, save_team
                sigle  = inter.data["values"][0]
                s2     = load_season()
                lst    = s2["leagues"].setdefault(league, [])
                if sigle not in lst:
                    lst.append(sigle)
                    t_data = load_team(sigle)
                    if t_data:
                        t_data["league"] = league
                        save_team(t_data)
                    save_season(s2)
                    await _refresh_league_msg(inter.client, inter.guild_id, league)
                    await inter.response.edit_message(
                        content=f"✅ **{sigle}** ajoutée à **{league}**.", view=None
                    )
                    await log_command(inter.user.display_name,
                                      f"season_add {sigle} → {league}", "Completed", "")
                else:
                    await inter.response.edit_message(
                        content=f"**{sigle}** est déjà dans **{league}**.", view=None
                    )

        await interaction.response.send_message(view=_AddView(), ephemeral=True)

    async def _del_callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
            return

        season = load_season()
        if not season:
            await interaction.response.send_message("❌ Aucune saison en cours.", ephemeral=True)
            return
        if season["status"] == "active":
            await interaction.response.send_message("❌ La saison est déjà active.", ephemeral=True)
            return

        in_league = season["leagues"].get(self.league, [])
        if not in_league:
            await interaction.response.send_message(
                f"ℹ️ Aucune équipe dans **{self.league}**.", ephemeral=True
            )
            return

        options = [discord.SelectOption(label=s, value=s) for s in in_league[:25]]
        league  = self.league

        class _DelView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                sel = discord.ui.Select(placeholder=f"Retirer de {league}...", options=options)
                sel.callback = self._on_select
                self.add_item(sel)

            async def _on_select(self, inter: discord.Interaction):
                from cogs.teams import load_team, save_team
                sigle = inter.data["values"][0]
                s2    = load_season()
                lst   = s2["leagues"].get(league, [])
                if sigle in lst:
                    lst.remove(sigle)
                    t_data = load_team(sigle)
                    if t_data:
                        t_data["league"] = None
                        save_team(t_data)
                    save_season(s2)
                    await _refresh_league_msg(inter.client, inter.guild_id, league)
                    await inter.response.edit_message(
                        content=f"✅ **{sigle}** retirée de **{league}**.", view=None
                    )
                    await log_command(inter.user.display_name,
                                      f"season_del {sigle} ← {league}", "Completed", "")
                else:
                    await inter.response.edit_message(
                        content=f"**{sigle}** n'était plus dans **{league}**.", view=None
                    )

        await interaction.response.send_message(view=_DelView(), ephemeral=True)


class StartSeasonView(discord.ui.View):
    """Bouton 'Démarrer la Saison' — réservé à OWNER_ID."""

    def __init__(self):
        super().__init__(timeout=None)
        btn = discord.ui.Button(
            label="🚀 Démarrer la Saison",
            style=discord.ButtonStyle.danger,
            custom_id="season_start",
        )
        btn.callback = self._callback
        self.add_item(btn)

    async def _callback(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Réservé à AzErNate.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        season = load_season()
        if not season:
            await interaction.followup.send("❌ Aucune saison configurée.", ephemeral=True)
            return
        if season["status"] == "active":
            await interaction.followup.send("❌ La saison est déjà active.", ephemeral=True)
            return

        # Avertissement : équipes sans ligue
        all_teams = _load_all_teams()
        unassigned = [t["sigle"] for t in all_teams if not t.get("league")]
        warning = ""
        if unassigned:
            warning = f"\n\n⚠️ Équipes sans ligue : {', '.join(f'**{s}**' for s in unassigned)}"

        from utils.season_data import generate_round_robin, save_official_match
        from cogs.crewbattle import STAGES, get_stage_emoji
        import os as _os

        guild  = interaction.guild
        report = []

        teams_data: dict[str, dict] = {}
        if _os.path.exists(TEAMS_DIR):
            for fname in _os.listdir(TEAMS_DIR):
                if not fname.endswith(".json"):
                    continue
                with open(_os.path.join(TEAMS_DIR, fname), encoding="utf-8") as f:
                    td = json.load(f)
                teams_data[td["sigle"]] = td
                lg = td.get("league")
                if lg and lg in LEAGUE_NAMES and td["sigle"] not in season["leagues"][lg]:
                    season["leagues"][lg].append(td["sigle"])

        for lg in LEAGUE_NAMES:
            teams = season["leagues"][lg]
            if len(teams) < 2:
                report.append(f"⚠️ **{lg}** : {len(teams)} équipe(s), calendrier ignoré (min. 2)")
                continue

            for t in teams:
                season["standings"][lg][t] = {
                    "pts": 0, "wins": 0, "losses": 0,
                    "lives_scored": 0, "lives_conceded": 0,
                }

            calendar = generate_round_robin(teams)
            season["calendar"][lg] = calendar

            # Forum de la ligue (créé une fois, réutilisé si déjà présent)
            forum_id = season["forums"].get(lg)
            forum = guild.get_channel(forum_id) if forum_id else None
            if not isinstance(forum, discord.ForumChannel):
                cat_id   = LEAGUE_CATEGORY_IDS.get(lg)
                category = guild.get_channel(cat_id) if cat_id else None
                forum_name = f"〔{LEAGUE_EMOJIS[lg]}〕{lg.lower()}〔{season['name'].lower()}〕"
                try:
                    forum = await guild.create_forum(name=forum_name, category=category)
                    season["forums"][lg] = forum.id
                except Exception as e:
                    report.append(f"❌ Forum **{lg}** : {e}")
                    continue

            stage_line = " ".join(str(get_stage_emoji(s, guild) or f":{s}:") for s in STAGES)

            nb_channels = 0
            for ji, journee in enumerate(calendar, 1):
                for mi, match in enumerate(journee, 1):
                    home, away = match["home"], match["away"]
                    home_data  = teams_data.get(home, {})
                    away_data  = teams_data.get(away, {})
                    role_home  = guild.get_role(home_data.get("role_id", 0))
                    role_away  = guild.get_role(away_data.get("role_id", 0))
                    emoji_home = discord.utils.get(guild.emojis, name=home)
                    emoji_away = discord.utils.get(guild.emojis, name=away)

                    period_start, deadline = round_window(season["start_date"], ji - 1)

                    thread_name = (
                        f"🔴{LEAGUE_SHORT[lg]}-{season['name'].upper()} · "
                        f"Match {ji}-{mi} 〔{home} 🆚 {away}〕"
                    )[:100]

                    content = (
                        f"# {role_home.mention if role_home else f'**{home}**'} {emoji_home or ''}\n"
                        f"# {role_away.mention if role_away else f'**{away}**'} {emoji_away or ''}\n\n"
                        f"**Maps disponibles :** {stage_line}\n\n"
                        f"🗺️ Bans en **3-4-Pick** pour le premier match, "
                        f"puis en **3-Pick** à partir du 2e match.\n\n"
                        f"📅 Date limite pour jouer cette CB : **{deadline.strftime('%d/%m/%Y')}**\n"
                        f"*(dates sélectionnables du {period_start.strftime('%d/%m')} "
                        f"au {deadline.strftime('%d/%m')})*"
                    )

                    try:
                        twm    = await forum.create_thread(name=thread_name, content=content)
                        thread = twm.thread
                        match["channel_id"] = thread.id
                        save_official_match(thread.id, {
                            "league": lg, "home": home, "away": away,
                            "journee_idx": ji - 1,
                            "match_idx":   mi - 1,
                            "is_barrage":  False,
                        })
                        from cogs.season_match import post_date_selection
                        await post_date_selection(
                            guild, thread.id, lg, home, away, period_start, deadline
                        )
                        nb_channels += 1
                    except Exception as e:
                        report.append(f"❌ Thread `{thread_name}` : {e}")

            nb_j = len(calendar)
            nb_m = sum(len(j) for j in calendar)
            report.append(
                f"✅ **{lg}** : {len(teams)} équipes — {nb_j} journées, {nb_m} matchs, {nb_channels} threads"
            )

        season["status"] = "active"
        from utils.season_data import save_season as _ss
        _ss(season)

        from utils.standings_channel import refresh_standings_channel
        await refresh_standings_channel(interaction.guild)

        embed = discord.Embed(
            title=f"🚀 Saison **{season['name']}** lancée !",
            description="\n".join(report) + warning,
            color=discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        await log_command(interaction.user.display_name,
                          f"season_start {season['name']}", "Completed",
                          " | ".join(report))


# ─── Setup Season Modal ───────────────────────────────────────────────────────

class SetupSeasonModal(discord.ui.Modal, title="Configurer une saison"):
    saison = discord.ui.TextInput(
        label="Identifiant de la saison",
        placeholder="Ex : 26spr  (pour Printemps 2026)",
        min_length=2,
        max_length=20,
    )
    date_debut = discord.ui.TextInput(
        label="Date de début (journée 1)",
        placeholder="JJ/MM/AAAA — ex : 31/08/2026",
        min_length=8,
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild  = interaction.guild
        name   = self.saison.value.strip()

        try:
            d, m, y = self.date_debut.value.strip().split("/")
            start_date = date(int(y), int(m), int(d)).isoformat()
        except Exception:
            await interaction.followup.send(
                "❌ Date invalide. Utilise le format JJ/MM/AAAA (ex : 31/08/2026).", ephemeral=True
            )
            return

        existing = load_season()
        if existing and existing["status"] == "active":
            await interaction.followup.send(
                "❌ Une saison est déjà active. Clôturez-la d'abord.", ephemeral=True
            )
            return

        panels = _load_panels()

        # Créer / trouver le salon "panel〔sigle〕"
        existing_chan_id = panels.get("season_panel_channel")
        chan = guild.get_channel(existing_chan_id) if existing_chan_id else None
        if chan and chan.name != f"panel〔{name}〕":
            chan = None  # mauvaise saison → on en crée un nouveau

        if not chan:
            category = guild.get_channel(SEASON_PANEL_CATEGORY)
            try:
                chan = await guild.create_text_channel(
                    name=f"panel〔{name}〕", category=category
                )
            except Exception as e:
                await interaction.followup.send(f"❌ Impossible de créer le salon : {e}", ephemeral=True)
                return

        panels["season_panel_channel"] = chan.id

        # Créer / réinitialiser la saison dans les données
        season = new_season(name, start_date)
        # Récupérer les ligues déjà assignées depuis les JSONs d'équipes
        for t in _load_all_teams():
            lg = t.get("league")
            if lg and lg in LEAGUE_NAMES and t["sigle"] not in season["leagues"][lg]:
                season["leagues"][lg].append(t["sigle"])
        save_season(season)

        # Poster un message par ligue
        league_msg_ids: dict[str, int] = {}
        for lg in LEAGUE_NAMES:
            msg = await chan.send(embed=_league_embed(lg, season), view=SeasonLeagueView(lg))
            league_msg_ids[lg] = msg.id

        # Poster le bouton "Démarrer la Saison"
        start_embed = discord.Embed(
            title="🚀 Démarrer la Saison",
            description=(
                "Une fois les équipes assignées aux ligues, clique sur le bouton ci-dessous.\n\n"
                "Le calendrier sera généré automatiquement et les salons de match créés.\n\n"
                "*(Seul AzErNate peut démarrer la saison)*"
            ),
            color=discord.Color.orange(),
        )
        await chan.send(embed=start_embed, view=StartSeasonView())

        panels["season_league_msgs"] = league_msg_ids
        _save_panels(panels)

        await interaction.followup.send(
            f"✅ Panel de la saison **{name}** créé dans {chan.mention}.", ephemeral=True
        )
        await log_command(interaction.user.display_name,
                          f"setup_season {name}", "Completed",
                          f"Panel saison créé : {chan.id}")


# ─── Config panel sub-flows ───────────────────────────────────────────────────

class _ConfirmView(discord.ui.View):
    """Vue générique Confirmer / Annuler (ephemeral, timeout 60 s)."""

    def __init__(self, on_confirm):
        super().__init__(timeout=60)
        self._on_confirm = on_confirm

    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._on_confirm(interaction)

    @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Annulé.", view=None)


# ── Retirer une Équipe ────────────────────────────────────────────────────────

class _TeamSelectDissolveView(discord.ui.View):
    def __init__(self, teams: list[dict]):
        super().__init__(timeout=120)
        opts = [discord.SelectOption(label=t["sigle"], value=t["sigle"]) for t in teams[:25]]
        sel = discord.ui.Select(placeholder="Choisir une équipe à dissoudre...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        sigle = interaction.data["values"][0]

        async def _do(inter: discord.Interaction):
            await inter.response.defer(ephemeral=True)
            from cogs.teams import dissolve_team_logic
            report = await dissolve_team_logic(inter.client, inter.guild, sigle)
            await inter.edit_original_response(content=report, view=None)
            await log_command(inter.user.display_name, f"adm_del_team {sigle}", "Completed", report)

        await interaction.response.edit_message(
            content=f"⚠️ Dissoudre **{sigle}** ? Cette action est **irréversible**.",
            view=_ConfirmView(_do),
        )


# ── Retirer un Joueur (complet) ───────────────────────────────────────────────

class _PlayerSelectView(discord.ui.View):
    def __init__(self, team: dict):
        super().__init__(timeout=120)
        opts = []
        for mid in team.get("members", [])[:25]:
            p     = _load_player(mid)
            name  = p.get("name", str(mid)) if p else str(mid)
            label = ("👑 " if mid == team["leader_id"] else "") + name
            opts.append(discord.SelectOption(label=label[:100], value=str(mid)))
        if not opts:
            opts = [discord.SelectOption(label="(aucun membre)", value="none")]
        sel = discord.ui.Select(placeholder="Choisir un joueur...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        if val == "none":
            await interaction.response.edit_message(content="Aucun membre.", view=None)
            return
        pid   = int(val)
        player = _load_player(pid)
        name   = player.get("name", str(pid)) if player else str(pid)

        async def _do(inter: discord.Interaction):
            await inter.response.defer(ephemeral=True)
            report = await _adm_remove_player(inter.client, inter.guild, pid)
            await inter.edit_original_response(content=report, view=None)
            await log_command(inter.user.display_name, f"adm_remove_player {name}", "Completed", report)

        await interaction.response.edit_message(
            content=f"⚠️ Supprimer totalement **{name}** du bot ? (équipe, stats, profil)",
            view=_ConfirmView(_do),
        )


class _TeamSelectForPlayerView(discord.ui.View):
    def __init__(self, teams: list[dict]):
        super().__init__(timeout=120)
        opts = [discord.SelectOption(label=t["sigle"], value=t["sigle"]) for t in teams[:25]]
        sel  = discord.ui.Select(placeholder="Équipe du joueur...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        sigle = interaction.data["values"][0]
        team  = _load_team(sigle)
        if not team:
            await interaction.response.edit_message(content="❌ Équipe introuvable.", view=None)
            return
        await interaction.response.edit_message(
            content=f"Joueur à retirer de **{sigle}** :",
            view=_PlayerSelectView(team),
        )


# ── Renommer une Équipe ───────────────────────────────────────────────────────

class _RenameModal(discord.ui.Modal, title="Renommer l'équipe"):
    nouveau = discord.ui.TextInput(
        label="Nouveau sigle",
        placeholder="Ex : HoJ2",
        min_length=1,
        max_length=20,
    )

    def __init__(self, old_sigle: str):
        super().__init__()
        self.old_sigle = old_sigle

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new = self.nouveau.value.strip()
        if _load_team(new):
            await interaction.followup.send(f"❌ Une équipe **{new}** existe déjà.", ephemeral=True)
            return
        from cogs.teams import rename_team_logic
        lines = await rename_team_logic(interaction.client, interaction.guild, self.old_sigle, new)
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        await log_command(interaction.user.display_name,
                          f"adm_rename {self.old_sigle}→{new}", "Completed",
                          " | ".join(lines))


class _TeamSelectForRenameView(discord.ui.View):
    def __init__(self, teams: list[dict]):
        super().__init__(timeout=120)
        opts = [discord.SelectOption(label=t["sigle"], value=t["sigle"]) for t in teams[:25]]
        sel  = discord.ui.Select(placeholder="Équipe à renommer...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        sigle = interaction.data["values"][0]
        await interaction.response.send_modal(_RenameModal(sigle))


# ── Définir la Langue ─────────────────────────────────────────────────────────

class _LangSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        opts = [
            discord.SelectOption(label="🇫🇷 Français", value="fr"),
            discord.SelectOption(label="🇬🇧 English",  value="en"),
        ]
        sel = discord.ui.Select(placeholder="Choisir une langue...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        lang = interaction.data["values"][0]
        set_lang(interaction.guild_id, lang)
        label = "Français 🇫🇷" if lang == "fr" else "English 🇬🇧"
        await interaction.response.edit_message(
            content=f"✅ Langue définie sur **{label}** pour ce serveur.", view=None
        )
        await log_command(interaction.user.display_name, f"set_lang {lang}", "Completed", "")


# ─── Republier les panels ─────────────────────────────────────────────────────

async def _republish_all_panels(bot: discord.Client, guild: discord.Guild) -> list[str]:
    from cogs.panels import (
        CHANNEL_CREATETEAM, CHANNEL_JOINTEAM,
        CreateTeamView, JoinTeamView,
    )
    from cogs.freeplay import FreeplayPanelView, CHANNEL_FREEPLAY

    panels = _load_panels()
    report = []

    configs = [
        (
            "create_team_msg", CHANNEL_CREATETEAM,
            discord.Embed(
                title="⚔️ Créer une équipe",
                description=(
                    "Tu souhaites créer une équipe pour l'événement ?\n\n"
                    "Clique sur le bouton ci-dessous, renseigne le sigle de ton équipe "
                    "et le bot s'occupe du reste !\n\n"
                    "*(Rôle, catégorie, salons — tout est créé automatiquement.)*\n\n"
                    "Si tu es déjà leader d'une équipe et que tu entres le même sigle, "
                    "l'équipe **B** sera créée automatiquement."
                ),
                color=discord.Color.blurple(),
            ),
            CreateTeamView(),
        ),
        (
            "join_team_msg", CHANNEL_JOINTEAM,
            discord.Embed(
                title="🎮 Rejoindre une équipe",
                description=(
                    "Tu veux participer à l'événement ?\n\n"
                    "Clique sur le bouton ci-dessous, indique le sigle de l'équipe "
                    "que tu souhaites rejoindre et une demande sera envoyée au leader.\n\n"
                    "*(Si tu n'as pas encore de profil joueur, il sera créé automatiquement.)*"
                ),
                color=discord.Color.green(),
            ),
            JoinTeamView(),
        ),
        (
            "freeplay_panel_msg", CHANNEL_FREEPLAY,
            discord.Embed(
                title="⚔️ Freeplay CrewBattle",
                description=(
                    "Tu veux faire une CrewBattle en dehors du calendrier officiel ?\n\n"
                    "Clique sur le bouton ci-dessous pour chercher un adversaire.\n"
                    "Le bot te proposera des créneaux sur les **7 prochains jours**.\n\n"
                    "*(Réservé aux leaders d'équipe)*"
                ),
                color=discord.Color.red(),
            ),
            FreeplayPanelView(),
        ),
    ]

    for key, chan_id, embed, view in configs:
        ch = guild.get_channel(chan_id)
        if not ch:
            report.append(f"❌ Salon introuvable (ID {chan_id})")
            continue
        old_id = panels.get(key)
        if old_id:
            try:
                old = await ch.fetch_message(old_id)
                await old.delete()
            except Exception:
                pass
        msg = await ch.send(embed=embed, view=view)
        panels[key] = msg.id
        report.append(f"✅ Panel republié dans {ch.mention}")

    _save_panels(panels)
    return report


# ─── Config Panel View (persistent) ──────────────────────────────────────────

class ConfigPanelView(discord.ui.View):
    """Panel principal de configuration admin. Posté dans CHANNEL_CONFIG."""

    def __init__(self):
        super().__init__(timeout=None)

        defs = [
            # (label, style, custom_id, row, callback)
            ("🗑️ Retirer une Équipe",   discord.ButtonStyle.danger,    "cfg_del_team",      0, self._del_team),
            ("👤 Retirer un Joueur",    discord.ButtonStyle.danger,    "cfg_del_player",    0, self._del_player),
            ("✏️ Renommer une Équipe",  discord.ButtonStyle.secondary, "cfg_rename_team",   0, self._rename_team),
            ("📊 Rafraîchir les Stats", discord.ButtonStyle.primary,   "cfg_refresh_stats", 1, self._refresh_stats),
            ("🔄 Rafraîchir les LU",    discord.ButtonStyle.primary,   "cfg_refresh_lu",    1, self._refresh_lu),
            ("📋 Republier les Panels", discord.ButtonStyle.primary,   "cfg_republish",     1, self._republish),
            ("🗓️ Setup la Saison",      discord.ButtonStyle.success,   "cfg_setup_season",  2, self._setup_season),
            ("🌐 Définir la Langue",    discord.ButtonStyle.secondary, "cfg_set_lang",      2, self._set_lang),
            ("⏹️ Éteindre le bot",      discord.ButtonStyle.danger,    "cfg_shutdown",      2, self._shutdown),
        ]

        for label, style, cid, row, cb in defs:
            btn = discord.ui.Button(label=label, style=style, custom_id=cid, row=row)
            btn.callback = cb
            self.add_item(btn)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _del_team(self, interaction: discord.Interaction):
        teams = _load_all_teams()
        if not teams:
            await interaction.response.send_message("ℹ️ Aucune équipe enregistrée.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Sélectionne l'équipe à dissoudre :", view=_TeamSelectDissolveView(teams), ephemeral=True
        )

    async def _del_player(self, interaction: discord.Interaction):
        teams = _load_all_teams()
        if not teams:
            await interaction.response.send_message("ℹ️ Aucune équipe enregistrée.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Sélectionne d'abord l'équipe du joueur :",
            view=_TeamSelectForPlayerView(teams),
            ephemeral=True,
        )

    async def _rename_team(self, interaction: discord.Interaction):
        teams = _load_all_teams()
        if not teams:
            await interaction.response.send_message("ℹ️ Aucune équipe enregistrée.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Sélectionne l'équipe à renommer :", view=_TeamSelectForRenameView(teams), ephemeral=True
        )

    async def _refresh_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        report = await rebuild_all_stats(interaction.client, interaction.guild_id)
        await interaction.followup.send(f"✅ Stats reconstruites :\n{report}", ephemeral=True)
        await log_command(interaction.user.display_name, "cfg_refresh_stats", "Completed", report)

    async def _refresh_lu(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await rebuild_teams_lu(interaction.client, interaction.guild_id)
        await interaction.followup.send("✅ Salon `teams-lu` reconstruit.", ephemeral=True)
        await log_command(interaction.user.display_name, "cfg_refresh_lu", "Completed", "")

    async def _republish(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lines = await _republish_all_panels(interaction.client, interaction.guild)
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        await log_command(interaction.user.display_name, "cfg_republish", "Completed", " | ".join(lines))

    async def _setup_season(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SetupSeasonModal())

    async def _set_lang(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=_LangSelectView(), ephemeral=True)

    async def _shutdown(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Réservé à AzErNate.", ephemeral=True)
            return
        await interaction.response.send_message("🔌 Arrêt du bot en cours...", ephemeral=True)
        await interaction.client.close()


# ─── Cog ─────────────────────────────────────────────────────────────────────

class AdminPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(ConfigPanelView())
        for lg in LEAGUE_NAMES:
            self.bot.add_view(SeasonLeagueView(lg))
        self.bot.add_view(StartSeasonView())
        self.bot.tree.add_command(cbl_setup_config)

        from cogs.season_match import restore_all_season_matches
        self.bot.loop.create_task(restore_all_season_matches(self.bot))

    @commands.Cog.listener()
    async def on_ready(self):
        pass


@app_commands.command(
    name="cbl_setup_config",
    description="[ADMIN] Poste le panel de configuration dans le salon config",
)
async def cbl_setup_config(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild   = interaction.guild
    panels  = _load_panels()
    channel = guild.get_channel(CHANNEL_CONFIG)

    if not channel:
        await interaction.followup.send(
            f"❌ Salon de config introuvable (ID {CHANNEL_CONFIG}).", ephemeral=True
        )
        return

    old_id = panels.get("config_panel_msg")
    if old_id:
        try:
            old = await channel.fetch_message(old_id)
            await old.delete()
        except Exception:
            pass

    embed = discord.Embed(
        title="⚙️ Panel de Configuration",
        description=(
            "Bienvenue dans le panel admin du CrewBotLeague.\n\n"
            "**Gestion des équipes**\n"
            "🗑️ Retirer une Équipe — dissout une équipe complètement\n"
            "👤 Retirer un Joueur — supprime un joueur du bot\n"
            "✏️ Renommer une Équipe — change le sigle d'une équipe\n\n"
            "**Maintenance**\n"
            "📊 Rafraîchir les Stats — recrée tous les posts players-stats\n"
            "🔄 Rafraîchir les LU — reconstruit les embeds du salon teams-lu\n"
            "📋 Republier les Panels — reposte les 3 panels interactifs\n\n"
            "**Saison**\n"
            "🗓️ Setup la Saison — crée le panel de configuration de saison\n\n"
            "**Serveur**\n"
            "🌐 Définir la Langue — change la langue du bot\n"
            "⏹️ Éteindre le bot — arrêt propre *(AzErNate uniquement)*"
        ),
        color=discord.Color.dark_grey(),
    )

    msg = await channel.send(embed=embed, view=ConfigPanelView())
    panels["config_panel_msg"] = msg.id
    _save_panels(panels)

    await interaction.followup.send(
        f"✅ Panel de configuration posté dans {channel.mention}.", ephemeral=True
    )
    await log_command(interaction.user.display_name, "cbl_setup_config", "Completed",
                      f"Panel posté dans {channel.id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminPanel(bot))
