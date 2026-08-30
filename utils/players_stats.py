"""
Système de stats joueurs dans les forums players-stats.
Un post (thread) par joueur + un post d'équipe par équipe.

Basé sur la version développée en parallèle sur GitHub pendant l'indisponibilité
du PC local ; complété avec le suivi réel des stats (cb_joues/stocks_pris,
incrémentés après chaque set de CrewBattle) et le transfert de leadership avant
de quitter l'équipe, absents de la version GitHub.
"""

import discord
import os
import json

PLAYERS_DIR = os.path.join("data", "players")
TEAMS_DIR   = os.path.join("data", "teams")

# Rempli au démarrage via build_char_emojis()
CHAR_EMOJIS: dict[str, str] = {}


def build_char_emojis(guild: discord.Guild):
    """Construit la map {nom_normalisé: emoji_str} depuis les emojis custom du serveur."""
    global CHAR_EMOJIS
    CHAR_EMOJIS.clear()
    for emoji in guild.emojis:
        # Noms du type "06_Kirby", "25e_Chrom", "73_BanjoKazooie"
        parts = emoji.name.split("_", 1)
        if len(parts) == 2:
            char_name = parts[1].lower().replace(" ", "").replace("-", "")
            CHAR_EMOJIS[char_name] = str(emoji)


# ---------------------------------------------------------------------------
# Helpers JSON
# ---------------------------------------------------------------------------

def _load_player(discord_id: int) -> dict | None:
    path = os.path.join(PLAYERS_DIR, f"{discord_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _save_player(data: dict):
    os.makedirs(PLAYERS_DIR, exist_ok=True)
    with open(os.path.join(PLAYERS_DIR, f"{data['discord_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_team(sigle: str) -> dict | None:
    path = os.path.join(TEAMS_DIR, f"{sigle}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def _save_team(data: dict):
    os.makedirs(TEAMS_DIR, exist_ok=True)
    with open(os.path.join(TEAMS_DIR, f"{data['sigle']}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def _get_thread(guild: discord.Guild, thread_id: int | None) -> "discord.Thread | None":
    """Résout un thread par ID (y compris hors cache). None si absent/introuvable/supprimé."""
    if not thread_id or not guild:
        return None
    thread = guild.get_thread(thread_id)
    if thread:
        return thread
    try:
        return await guild.fetch_channel(thread_id)
    except Exception:
        return None


async def team_stats_post_exists(bot: discord.Client, guild_id: int, sigle: str) -> bool:
    """Vérifie sur Discord (pas juste dans le JSON) que le post de stats de l'équipe existe encore."""
    team = _load_team(sigle)
    if not team:
        return False
    guild = bot.get_guild(guild_id)
    return await _get_thread(guild, team.get("stats_thread_id")) is not None


async def player_stats_post_exists(bot: discord.Client, guild_id: int, player_id: int) -> bool:
    """Vérifie sur Discord (pas juste dans le JSON) que le post de stats du joueur existe encore."""
    player = _load_player(player_id)
    if not player:
        return False
    guild = bot.get_guild(guild_id)
    return await _get_thread(guild, player.get("stats_thread_id")) is not None


# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

def make_player_embed(display_name: str, main: str, cb_joues: int, stocks_pris: int) -> discord.Embed:
    embed = discord.Embed(title=f"📊 {display_name}", color=discord.Color.gold())
    embed.add_field(name="Main",        value=main or "*Non défini*", inline=True)
    embed.add_field(name="CB jouées",   value=str(cb_joues),          inline=True)
    embed.add_field(name="Stocks pris", value=str(stocks_pris),       inline=True)
    return embed


def make_team_stats_embed(sigle: str, members_stats: list[dict]) -> discord.Embed:
    """members_stats: liste de {name, stocks_pris} triée par stocks_pris décroissant."""
    embed = discord.Embed(title=f"📈 Statistiques — {sigle}", color=discord.Color.green())
    embed.add_field(name="Saison en cours", value="*Pas encore de match officiel joué.*", inline=False)
    if members_stats:
        top = "\n".join(f"**{m['name']}** — {m['stocks_pris']} stocks pris" for m in members_stats)
    else:
        top = "*Aucun joueur.*"
    embed.add_field(name="Top joueurs (stocks pris, à vie)", value=top, inline=False)
    return embed


# ---------------------------------------------------------------------------
# Modal "Définir un Main"
# ---------------------------------------------------------------------------

class MainSelectView(discord.ui.View):
    """Sélection du main par pages de boutons avec emotes (même style que la sélection
    de personnage en CrewBattle)."""

    def __init__(self, player_id: int, thread: discord.Thread, bot):
        super().__init__(timeout=180)
        self.player_id     = player_id
        self.thread        = thread
        self.bot           = bot
        self.page           = 0
        self.selected_char: str | None = None
        self._build()

    def _get_emoji(self, name: str):
        from cogs.crewbattle import EMOJI_SERVER_ID
        guild = self.bot.get_guild(EMOJI_SERVER_ID)
        return discord.utils.get(guild.emojis, name=name) if guild else None

    def _build(self):
        from cogs.crewbattle import CHARACTER_PAGES
        self.clear_items()
        _page_name, chars = CHARACTER_PAGES[self.page]

        for i, char in enumerate(chars):
            emoji = self._get_emoji(char)
            btn = discord.ui.Button(
                label="​" if emoji else char,
                emoji=emoji or None,
                style=discord.ButtonStyle.success if char == self.selected_char else discord.ButtonStyle.secondary,
                row=i // 5,
            )
            btn.callback = self._make_char_cb(char)
            self.add_item(btn)

        nb_pages = len(CHARACTER_PAGES)
        page_name, _ = CHARACTER_PAGES[self.page]

        prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary,
                                     disabled=self.page == 0, row=4)
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        info_btn = discord.ui.Button(
            label=f"{page_name}  ({self.page + 1}/{nb_pages})",
            style=discord.ButtonStyle.secondary, disabled=True, row=4,
        )
        self.add_item(info_btn)

        next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary,
                                     disabled=self.page == nb_pages - 1, row=4)
        next_btn.callback = self._next
        self.add_item(next_btn)

        confirm_btn = discord.ui.Button(
            label="✅ Confirmer", style=discord.ButtonStyle.primary,
            disabled=self.selected_char is None, row=4,
        )
        confirm_btn.callback = self._confirm
        self.add_item(confirm_btn)

    def _make_char_cb(self, char: str):
        async def cb(interaction: discord.Interaction):
            from cogs.crewbattle import is_authorized
            if not is_authorized(interaction.user.id, self.player_id):
                await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
                return
            self.selected_char = char
            self._build()
            await interaction.response.edit_message(view=self)
        return cb

    async def _prev(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.player_id):
            await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
            return
        self.page -= 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.player_id):
            await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
            return
        self.page += 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _confirm(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.player_id):
            await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
            return

        emoji = self._get_emoji(self.selected_char)
        main_value = str(emoji) if emoji else self.selected_char

        player = _load_player(self.player_id)
        if not player:
            await interaction.response.send_message("❌ Profil joueur introuvable.", ephemeral=True)
            return

        player.setdefault("stats", {})
        player["stats"]["main"] = main_value
        _save_player(player)

        await _refresh_player_thread_embed(self.thread, player)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"✅ Main défini : {main_value}", view=self)


async def _refresh_player_thread_embed(thread: discord.Thread, player: dict):
    """Met à jour l'embed du 1er message (posté par le bot) dans le post d'un joueur."""
    stats = player.get("stats", {})
    embed = make_player_embed(
        player.get("name", "?"),
        stats.get("main"),
        stats.get("cb_joues", 0),
        stats.get("stocks_pris", 0),
    )
    try:
        async for msg in thread.history(limit=5, oldest_first=True):
            if msg.author == thread.guild.me and msg.embeds:
                await msg.edit(embed=embed)
                return
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Embed mini-membre (pour le post d'équipe)
# ---------------------------------------------------------------------------

def make_member_mini_embed(player_data: dict, is_leader: bool) -> discord.Embed:
    stats  = player_data.get("stats", {})
    name   = player_data.get("name", "?")
    main   = stats.get("main") or "*Non défini*"
    color  = discord.Color.gold() if is_leader else discord.Color.light_grey()
    prefix = "👑 " if is_leader else "• "
    embed  = discord.Embed(title=f"{prefix}{name}", color=color)
    embed.add_field(name="Main",        value=main,                              inline=True)
    embed.add_field(name="CB jouées",   value=str(stats.get("cb_joues", 0)),     inline=True)
    embed.add_field(name="Stocks pris", value=str(stats.get("stocks_pris", 0)), inline=True)
    return embed


# ---------------------------------------------------------------------------
# View persistante du post d'équipe (boutons leader)
# ---------------------------------------------------------------------------

class _TeamRenameModal(discord.ui.Modal, title="Renommer l'équipe"):
    nouveau = discord.ui.TextInput(label="Nouveau sigle", placeholder="Ex : HoJ2",
                                   min_length=1, max_length=20)

    def __init__(self, old_sigle: str):
        super().__init__()
        self.old_sigle = old_sigle

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        new = self.nouveau.value.strip()
        if os.path.exists(os.path.join(TEAMS_DIR, f"{new}.json")):
            await interaction.followup.send(f"❌ Une équipe **{new}** existe déjà.", ephemeral=True)
            return
        from cogs.teams import rename_team_logic
        lines = await rename_team_logic(interaction.client, interaction.guild, self.old_sigle, new)
        await interaction.followup.send("\n".join(lines), ephemeral=True)


class TeamStatsView(discord.ui.View):
    """Boutons 'Dissoudre' et 'Renommer' dans le 1er message du post de stats d'équipe."""

    def __init__(self, sigle: str):
        super().__init__(timeout=None)
        self.sigle = sigle

        dissolve = discord.ui.Button(
            label="🔥 Dissoudre l'équipe",
            style=discord.ButtonStyle.danger,
            custom_id=f"ts_dissolve:{sigle}",
        )
        dissolve.callback = self._dissolve
        self.add_item(dissolve)

        rename = discord.ui.Button(
            label="✏️ Renommer l'équipe",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ts_rename:{sigle}",
        )
        rename.callback = self._rename
        self.add_item(rename)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from cogs.crewbattle import is_authorized
        team = _load_team(self.sigle)
        if not team or not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message("❌ Réservé au leader de l'équipe.", ephemeral=True)
            return False
        return True

    async def _dissolve(self, interaction: discord.Interaction):
        sigle = self.sigle

        class _Confirm(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.danger)
            async def confirm(self_, inter, button):
                await inter.response.defer(ephemeral=True)
                from cogs.teams import dissolve_team_logic
                report = await dissolve_team_logic(inter.client, inter.guild, sigle)
                await inter.edit_original_response(content=report, view=None)

            @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.secondary)
            async def cancel(self_, inter, button):
                await inter.response.edit_message(content="Annulé.", view=None)

        await interaction.response.send_message(
            f"⚠️ Dissoudre **{sigle}** ? Cette action est **irréversible**.",
            view=_Confirm(), ephemeral=True,
        )

    async def _rename(self, interaction: discord.Interaction):
        await interaction.response.send_modal(_TeamRenameModal(self.sigle))


def _twin_sigle(sigle: str) -> str:
    """Sigle de l'équipe jumelle (A <-> B) — n'implique pas qu'elle existe."""
    return sigle[:-1] if sigle.endswith("²") else f"{sigle}²"


class _MoveTeamBtn(discord.ui.Button):
    """Déplace un membre vers l'équipe jumelle (A -> B ou B -> A)."""

    def __init__(self, sigle: str, member_id: int, target_sigle: str):
        super().__init__(
            label=f"🔀 Déplacer vers {target_sigle}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ts_move:{sigle}:{member_id}",
        )
        self.sigle        = sigle
        self.member_id    = member_id
        self.target_sigle = target_sigle

    async def callback(self, interaction: discord.Interaction):
        team = _load_team(self.sigle)
        if not team:
            await interaction.response.send_message("❌ Équipe introuvable.", ephemeral=True)
            return
        if self.member_id == team["leader_id"]:
            await interaction.response.send_message(
                "❌ Tu ne peux pas déplacer le leader. Transfère d'abord le leadership.", ephemeral=True
            )
            return

        target_team = _load_team(self.target_sigle)
        if not target_team:
            await interaction.response.send_message(
                f"❌ Équipe **{self.target_sigle}** introuvable.", ephemeral=True
            )
            return
        if self.member_id in target_team.get("members", []):
            await interaction.response.send_message(
                f"ℹ️ Ce joueur est déjà dans **{self.target_sigle}**.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if self.member_id in team.get("members", []):
            team["members"].remove(self.member_id)
        _save_team(team)

        target_team.setdefault("members", []).append(self.member_id)
        _save_team(target_team)

        player = _load_player(self.member_id)
        name = player.get("name", str(self.member_id)) if player else str(self.member_id)
        if player:
            player["team"] = self.target_sigle
            _save_player(player)

        from utils.teams_lu import refresh_team_lu
        await refresh_team_lu(interaction.client, interaction.guild_id, team)
        await refresh_team_lu(interaction.client, interaction.guild_id, target_team)
        await refresh_team_stats_post(interaction.client, interaction.guild_id, self.sigle)
        await refresh_team_stats_post(interaction.client, interaction.guild_id, self.target_sigle)

        await interaction.followup.send(
            f"✅ **{name}** déplacé de **{self.sigle}** vers **{self.target_sigle}**.", ephemeral=True
        )


class TeamMemberView(discord.ui.View):
    """Bouton 'Retirer de l'équipe' (+ 'Déplacer vers A/B' si l'équipe jumelle
    existe) par membre dans le post de stats d'équipe."""

    def __init__(self, sigle: str, member_id: int):
        super().__init__(timeout=None)
        self.sigle     = sigle
        self.member_id = member_id

        btn = discord.ui.Button(
            label="❌ Retirer de l'équipe",
            style=discord.ButtonStyle.danger,
            custom_id=f"ts_remove:{sigle}:{member_id}",
        )
        btn.callback = self._remove
        self.add_item(btn)

        twin_sigle = _twin_sigle(sigle)
        if _load_team(twin_sigle):
            self.add_item(_MoveTeamBtn(sigle, member_id, twin_sigle))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from cogs.crewbattle import is_authorized
        team = _load_team(self.sigle)
        if not team or not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message("❌ Réservé au leader de l'équipe.", ephemeral=True)
            return False
        return True

    async def _remove(self, interaction: discord.Interaction):
        team = _load_team(self.sigle)
        if not team:
            await interaction.response.send_message("❌ Équipe introuvable.", ephemeral=True)
            return
        if self.member_id == team["leader_id"]:
            await interaction.response.send_message(
                "❌ Tu ne peux pas retirer le leader. Transfère d'abord le leadership.", ephemeral=True
            )
            return

        player = _load_player(self.member_id)
        name      = player.get("name", str(self.member_id)) if player else str(self.member_id)
        member_id = self.member_id
        sigle     = self.sigle

        class _Confirm(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.danger)
            async def confirm(self_, inter, button):
                await inter.response.defer(ephemeral=True)
                from cogs.teams import load_team as lt, save_team as st
                from cogs.teams import load_player as lp, save_player as sp
                from utils.teams_lu import refresh_team_lu

                t = lt(sigle)
                if not t:
                    await inter.edit_original_response(content="❌ Équipe introuvable.", view=None)
                    return
                if member_id in t["members"]:
                    t["members"].remove(member_id)
                st(t)

                p = lp(member_id)
                if p:
                    p["team"] = None
                    sp(p)

                role = inter.guild.get_role(t["role_id"])
                m    = inter.guild.get_member(member_id)
                if role and m:
                    try:
                        await m.remove_roles(role)
                    except discord.Forbidden:
                        pass

                await refresh_team_lu(inter.client, inter.guild_id, t)
                await delete_player_stats_post(inter.client, inter.guild_id, member_id)
                await refresh_team_stats_post(inter.client, inter.guild_id, sigle)
                await inter.edit_original_response(
                    content=f"✅ **{name}** retiré de **{sigle}**.", view=None
                )

            @discord.ui.button(label="❌ Annuler", style=discord.ButtonStyle.secondary)
            async def cancel(self_, inter, button):
                await inter.response.edit_message(content="Annulé.", view=None)

        await interaction.response.send_message(
            f"⚠️ Retirer **{name}** de **{sigle}** ?",
            view=_Confirm(), ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Boutons de la View persistante (player posts)
# ---------------------------------------------------------------------------

class _MainBtn(discord.ui.Button):
    def __init__(self, player_id: int):
        super().__init__(
            label="🎮 Définir un Main",
            style=discord.ButtonStyle.primary,
            custom_id=f"ps_main:{player_id}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("❌ Ce bouton ne fonctionne qu'dans un post.", ephemeral=True)
            return
        view: PlayerStatsView = self.view
        select_view = MainSelectView(view.player_id, thread, interaction.client)
        await interaction.response.send_message(
            "🎮 Choisis ton main :", view=select_view, ephemeral=True
        )


class _StatsBtn(discord.ui.Button):
    def __init__(self, player_id: int):
        super().__init__(
            label="📊 Statistiques",
            style=discord.ButtonStyle.secondary,
            custom_id=f"ps_stats:{player_id}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view:   PlayerStatsView = self.view
        player = _load_player(view.player_id)
        if not player:
            await interaction.response.send_message("❌ Profil introuvable.", ephemeral=True)
            return
        stats = player.get("stats", {})
        embed = discord.Embed(
            title=f"📊 Statistiques — {player.get('name', '?')}",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Main",        value=stats.get("main") or "*Non défini*", inline=True)
        embed.add_field(name="CB jouées",   value=str(stats.get("cb_joues", 0)),       inline=True)
        embed.add_field(name="Stocks pris", value=str(stats.get("stocks_pris", 0)),    inline=True)
        embed.add_field(name="Équipe",      value=player.get("team") or "*Aucune*",    inline=True)

        by_opp = stats.get("stocks_par_adversaire", {})
        if by_opp:
            ranked = sorted(by_opp.items(), key=lambda kv: kv[1], reverse=True)[:10]
            lines = []
            for opp_id_str, count in ranked:
                opp_id = int(opp_id_str)
                opp = _load_player(opp_id)
                opp_name = opp.get("name") if opp else None
                if not opp_name:
                    m = interaction.guild.get_member(opp_id) if interaction.guild else None
                    opp_name = m.display_name if m else str(opp_id)
                lines.append(f"• **{opp_name}** — {count}")
            embed.add_field(name="Stocks pris par adversaire", value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


class _LeaveBtn(discord.ui.Button):
    def __init__(self, player_id: int):
        super().__init__(
            label="🚪 Quitter l'équipe",
            style=discord.ButtonStyle.danger,
            custom_id=f"ps_leave:{player_id}",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        from cogs.teams import find_team_of_player, load_team

        view: PlayerStatsView = self.view

        team_sigle = find_team_of_player(view.player_id)
        if not team_sigle:
            await interaction.response.send_message("❌ Tu n'es dans aucune équipe.", ephemeral=True)
            return

        team = load_team(team_sigle)

        if team["leader_id"] == view.player_id:
            others = [m for m in team.get("members", []) if m != view.player_id]
            if not others:
                await interaction.response.send_message(
                    "❌ Tu es seul dans l'équipe. Utilise le bouton **🔥 Dissoudre l'équipe** "
                    "dans le post de stats d'équipe à la place.",
                    ephemeral=True,
                )
                return
            candidates = []
            for mid in others:
                m = interaction.guild.get_member(mid)
                candidates.append((mid, m.display_name if m else str(mid)))
            select_view = _SuccessorPickView(team_sigle, view.player_id, candidates)
            await interaction.response.send_message(
                "👑 Tu es le leader. Choisis qui reprend le leadership avant de partir :",
                view=select_view, ephemeral=True,
            )
            return

        select_view = _LeaveConfirmView(team_sigle, view.player_id)
        await interaction.response.send_message(
            f"⚠️ Confirmes-tu vouloir quitter **{team_sigle}** ?", view=select_view, ephemeral=True,
        )


async def _leave_team(interaction: discord.Interaction, player_id: int, team_sigle: str):
    from cogs.teams import load_team, save_team, load_player as lp, save_player as sp
    from utils.teams_lu import refresh_team_lu

    team = load_team(team_sigle)
    if team and player_id in team.get("members", []):
        team["members"].remove(player_id)
        save_team(team)

    player = lp(player_id)
    if player:
        player["team"] = None
        sp(player)

    guild  = interaction.guild
    member = guild.get_member(player_id)
    role   = guild.get_role(team["role_id"]) if team else None
    if role and member:
        try:
            await member.remove_roles(role)
        except discord.Forbidden:
            pass

    if team:
        await refresh_team_lu(interaction.client, interaction.guild_id, team)

    await delete_player_stats_post(interaction.client, interaction.guild_id, player_id)
    if team:
        await refresh_team_stats_post(interaction.client, interaction.guild_id, team_sigle)

    await interaction.followup.send(f"👋 Tu as quitté **{team_sigle}**.", ephemeral=True)


class _LeaveConfirmView(discord.ui.View):
    def __init__(self, team_sigle: str, player_id: int):
        super().__init__(timeout=60)
        self.team_sigle = team_sigle
        self.player_id  = player_id

    @discord.ui.button(label="✅ Oui, quitter", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.player_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await _leave_team(interaction, self.player_id, self.team_sigle)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.player_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Annulé.", view=self)


async def _transfer_leadership(interaction: discord.Interaction, team_sigle: str,
                                new_leader_id: int, old_leader_id: int):
    """Change le leader d'une équipe et notifie dans le salon tasks. Ne fait rien d'autre
    (le nouvel ex-leader reste membre de l'équipe — c'est à l'appelant de décider s'il part)."""
    from cogs.teams import load_team, save_team
    from utils.teams_lu import refresh_team_lu

    team = load_team(team_sigle)
    team["leader_id"] = new_leader_id
    save_team(team)
    await refresh_team_lu(interaction.client, interaction.guild_id, team)

    guild       = interaction.guild
    new_leader  = guild.get_member(new_leader_id)
    old_leader  = guild.get_member(old_leader_id)
    tasks_ch_id = team["channels"].get("tasks")
    tasks_ch    = guild.get_channel(tasks_ch_id) if tasks_ch_id else None
    if tasks_ch:
        try:
            await tasks_ch.send(
                f"👑 **{new_leader.display_name if new_leader else new_leader_id}** est le nouveau leader de "
                f"**{team_sigle}** (succède à **{old_leader.display_name if old_leader else old_leader_id}**)."
            )
        except Exception:
            pass

    await refresh_team_stats_post(interaction.client, interaction.guild_id, team_sigle)


class _SuccessorPickView(discord.ui.View):
    def __init__(self, team_sigle: str, player_id: int, candidates: list[tuple[int, str]]):
        super().__init__(timeout=120)
        self.team_sigle = team_sigle
        self.player_id  = player_id

        select = discord.ui.Select(
            placeholder="Choisis le prochain leader...",
            options=[discord.SelectOption(label=name, value=str(mid)) for mid, name in candidates[:25]],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.player_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        new_leader_id = int(interaction.data["values"][0])
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        await _transfer_leadership(interaction, self.team_sigle, new_leader_id, self.player_id)
        await _leave_team(interaction, self.player_id, self.team_sigle)


class _TransferConfirmView(discord.ui.View):
    """Confirmation avant de faire de view.player_id le nouveau leader (bouton dédié dans le post joueur)."""

    def __init__(self, team_sigle: str, old_leader_id: int, new_leader_id: int):
        super().__init__(timeout=120)
        self.team_sigle    = team_sigle
        self.old_leader_id = old_leader_id
        self.new_leader_id = new_leader_id

    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.old_leader_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        await _transfer_leadership(interaction, self.team_sigle, self.new_leader_id, self.old_leader_id)

        new_leader = interaction.guild.get_member(self.new_leader_id)
        await interaction.followup.send(
            f"✅ Leadership transféré à **{new_leader.display_name if new_leader else self.new_leader_id}**.",
            ephemeral=True,
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.old_leader_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Annulé.", view=self)


class _TransferLeadershipBtn(discord.ui.Button):
    def __init__(self, player_id: int):
        super().__init__(
            label="🔄 Transférer le leadership",
            style=discord.ButtonStyle.primary,
            custom_id=f"ps_transfer:{player_id}",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        from cogs.teams import find_team_of_player, load_team

        view: PlayerStatsView = self.view
        team_sigle = find_team_of_player(view.player_id)
        if not team_sigle:
            await interaction.response.send_message("❌ Ce joueur n'est dans aucune équipe.", ephemeral=True)
            return

        team = load_team(team_sigle)
        old_leader_id = team["leader_id"]
        if old_leader_id == view.player_id:
            await interaction.response.send_message(
                "❌ Ce joueur est déjà le leader de l'équipe.", ephemeral=True
            )
            return

        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, old_leader_id):
            await interaction.response.send_message(
                "❌ Seul le leader de l'équipe peut transférer le leadership.", ephemeral=True
            )
            return

        target = interaction.guild.get_member(view.player_id)
        target_name = target.display_name if target else str(view.player_id)

        await interaction.response.send_message(
            f"⚠️ Confirmer le transfert du leadership de **{team_sigle}** à **{target_name}** ?",
            view=_TransferConfirmView(team_sigle, old_leader_id, view.player_id),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# View persistante
# ---------------------------------------------------------------------------

class PlayerStatsView(discord.ui.View):
    def __init__(self, player_id: int):
        super().__init__(timeout=None)
        self.player_id = player_id
        self.add_item(_MainBtn(player_id))
        self.add_item(_StatsBtn(player_id))
        self.add_item(_LeaveBtn(player_id))
        self.add_item(_TransferLeadershipBtn(player_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.player_id):
            await interaction.response.send_message(
                "❌ Ces boutons ne sont pas pour toi.", ephemeral=True
            )
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        print(f"[PlayerStatsView] Erreur bouton : {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Une erreur est survenue.", ephemeral=True)


# ---------------------------------------------------------------------------
# Fonctions publiques
# ---------------------------------------------------------------------------

async def create_player_stats_post(
    bot: discord.Client,
    guild_id: int,
    team: dict,
    member: discord.Member,
) -> None:
    """Crée le post de stats d'un joueur dans le forum players-stats de son équipe."""
    forum_id = team.get("channels", {}).get("players_stats")
    if not forum_id:
        return

    guild = bot.get_guild(guild_id)
    forum = guild.get_channel(forum_id)
    if not isinstance(forum, discord.ForumChannel):
        return

    player = _load_player(member.id)
    if not player:
        player = {"discord_id": member.id, "name": member.display_name, "team": team["sigle"]}

    stats      = player.setdefault("stats", {})
    display    = player.get("name", member.display_name)
    main       = stats.get("main") or "*Non défini*"
    cb_joues   = stats.get("cb_joues", 0)
    stocks     = stats.get("stocks_pris", 0)

    embed = make_player_embed(display, main, cb_joues, stocks)
    view  = PlayerStatsView(member.id)

    twm    = await forum.create_thread(name=display, embed=embed, view=view)
    thread = twm.thread
    bot.add_view(view, message_id=thread.id)

    player["stats_thread_id"] = thread.id
    _save_player(player)

    await refresh_team_stats_post(bot, guild_id, team["sigle"])


async def create_team_stats_post(
    bot: discord.Client,
    guild_id: int,
    team: dict,
) -> None:
    """Crée le post de stats d'équipe dans le forum players-stats."""
    forum_id = team.get("channels", {}).get("players_stats")
    if not forum_id:
        return

    guild = bot.get_guild(guild_id)
    forum = guild.get_channel(forum_id)
    if not isinstance(forum, discord.ForumChannel):
        return

    sigle        = team["sigle"]
    members_data = _get_members_stats(team)
    embed        = make_team_stats_embed(sigle, members_data)

    view   = TeamStatsView(sigle)
    twm    = await forum.create_thread(name=f"📈 {sigle} — Stats d'équipe", embed=embed, view=view)
    thread = twm.thread

    team["stats_thread_id"]     = thread.id
    team["stats_member_msg_ids"] = {}

    # Envoyer un message par membre
    for member_id in team.get("members", []):
        player = _load_player(member_id)
        if not player:
            player = {"discord_id": member_id, "name": str(member_id), "stats": {}}
        is_leader   = (member_id == team["leader_id"])
        member_view = TeamMemberView(sigle, member_id)
        msg = await thread.send(embed=make_member_mini_embed(player, is_leader), view=member_view)
        team["stats_member_msg_ids"][str(member_id)] = msg.id

    _save_team(team)


async def refresh_team_stats_post(
    bot: discord.Client,
    guild_id: int,
    sigle: str,
    changed_member_ids: set[int] | None = None,
) -> None:
    """Met à jour l'embed d'équipe et synchronise les messages membres."""
    team = _load_team(sigle)
    if not team:
        return

    guild  = bot.get_guild(guild_id)
    thread = await _get_thread(guild, team.get("stats_thread_id"))
    if not thread:
        return

    # ── 1. Rafraîchir l'embed d'équipe (1er message du bot) ──────────────────
    members_data = _get_members_stats(team)
    team_embed   = make_team_stats_embed(sigle, members_data)

    async for msg in thread.history(limit=10, oldest_first=True):
        if msg.author == guild.me and msg.embeds:
            await msg.edit(embed=team_embed)
            break

    # ── 2. Synchroniser les messages membres ──────────────────────────────────
    msg_ids: dict[str, int] = team.get("stats_member_msg_ids", {})
    current_members = {str(mid) for mid in team.get("members", [])}
    stored_members  = set(msg_ids.keys())

    # Supprimer les messages des membres qui ne sont plus dans l'équipe
    for mid_str in stored_members - current_members:
        old_msg_id = msg_ids.pop(mid_str)
        try:
            old_msg = await thread.fetch_message(old_msg_id)
            await old_msg.delete()
        except Exception:
            pass

    # Ajouter les messages pour les nouveaux membres
    for mid_str in current_members - stored_members:
        mid    = int(mid_str)
        player = _load_player(mid)
        if not player:
            player = {"discord_id": mid, "name": str(mid), "stats": {}}
        is_leader   = (mid == team["leader_id"])
        member_view = TeamMemberView(sigle, mid)
        try:
            msg = await thread.send(
                embed=make_member_mini_embed(player, is_leader),
                view=member_view,
            )
            msg_ids[mid_str] = msg.id
        except Exception:
            pass

    # Mettre à jour les embeds des membres existants (main ou stats changé)
    to_update = current_members & stored_members
    if changed_member_ids is not None:
        to_update &= {str(mid) for mid in changed_member_ids}
    for mid_str in to_update:
        mid    = int(mid_str)
        player = _load_player(mid)
        if not player:
            continue
        is_leader = (mid == team["leader_id"])
        try:
            existing_msg = await thread.fetch_message(msg_ids[mid_str])
            await existing_msg.edit(embed=make_member_mini_embed(player, is_leader))
        except Exception:
            pass

    team["stats_member_msg_ids"] = msg_ids
    _save_team(team)


async def delete_player_stats_post(
    bot: discord.Client,
    guild_id: int,
    player_id: int,
) -> None:
    """Supprime le thread de stats d'un joueur."""
    player = _load_player(player_id)
    if not player:
        return

    guild  = bot.get_guild(guild_id)
    thread = await _get_thread(guild, player.get("stats_thread_id"))
    if thread:
        try:
            await thread.delete()
        except Exception:
            pass

    player.pop("stats_thread_id", None)
    _save_player(player)


async def rebuild_all_stats(bot: discord.Client, guild_id: int) -> str:
    """[ADMIN] Recrée tous les posts stats pour toutes les équipes.
    Retourne un rapport texte."""
    guild   = bot.get_guild(guild_id)
    report  = []

    for filename in os.listdir(TEAMS_DIR):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(TEAMS_DIR, filename), encoding="utf-8") as f:
            team = json.load(f)

        forum_id = team.get("channels", {}).get("players_stats")
        if not forum_id:
            report.append(f"⚠️ **{team['sigle']}** — pas de forum players_stats")
            continue

        forum = guild.get_channel(forum_id)
        if not isinstance(forum, discord.ForumChannel):
            report.append(f"⚠️ **{team['sigle']}** — forum introuvable")
            continue

        # Purge des threads existants
        threads = list(forum.threads)
        for t in threads:
            try:
                await t.delete()
            except Exception:
                pass

        sigle = team["sigle"]

        # Post d'équipe
        members_data = _get_members_stats(team)
        t_embed      = make_team_stats_embed(sigle, members_data)
        ts_view      = TeamStatsView(sigle)
        twm_team     = await forum.create_thread(name=f"📈 {sigle} — Stats d'équipe", embed=t_embed, view=ts_view)
        team_thread  = twm_team.thread
        team["stats_thread_id"]      = team_thread.id
        team["stats_member_msg_ids"] = {}

        # Posts joueurs
        count = 0
        for member_id in team.get("members", []):
            member = guild.get_member(member_id)
            if not member:
                try:
                    member = await guild.fetch_member(member_id)
                except Exception:
                    continue
            player    = _load_player(member_id) or {"discord_id": member_id, "name": member.display_name}
            stats     = player.setdefault("stats", {})
            display   = player.get("name", member.display_name)
            embed     = make_player_embed(
                display,
                stats.get("main", "*Non défini*"),
                stats.get("cb_joues", 0),
                stats.get("stocks_pris", 0),
            )
            view      = PlayerStatsView(member_id)
            twm       = await forum.create_thread(name=display, embed=embed, view=view)
            thread_id = twm.thread.id
            bot.add_view(view, message_id=thread_id)
            player["stats_thread_id"] = thread_id
            _save_player(player)

            # Message membre dans le post d'équipe
            is_leader   = (member_id == team["leader_id"])
            member_view = TeamMemberView(sigle, member_id)
            mm = await team_thread.send(embed=make_member_mini_embed(player, is_leader), view=member_view)
            team["stats_member_msg_ids"][str(member_id)] = mm.id

            count += 1

        _save_team(team)
        report.append(f"✅ **{sigle}** — {count} joueur(s) + post équipe")

    return "\n".join(report) if report else "Aucune équipe trouvée."


def register_all_views(bot: discord.Client) -> int:
    """Enregistre toutes les views persistantes (PlayerStats, TeamStats, TeamMember)."""
    count = 0

    # PlayerStatsView — une par joueur avec un post
    if os.path.exists(PLAYERS_DIR):
        for filename in os.listdir(PLAYERS_DIR):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(PLAYERS_DIR, filename), encoding="utf-8") as f:
                player = json.load(f)
            if player.get("stats_thread_id"):
                bot.add_view(PlayerStatsView(player["discord_id"]))
                count += 1

    # TeamStatsView + TeamMemberView — une par équipe/membre avec un post
    if os.path.exists(TEAMS_DIR):
        for filename in os.listdir(TEAMS_DIR):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(TEAMS_DIR, filename), encoding="utf-8") as f:
                team = json.load(f)
            sigle = team.get("sigle", "")
            if not sigle or not team.get("stats_thread_id"):
                continue
            bot.add_view(TeamStatsView(sigle))
            count += 1
            for mid_str in team.get("stats_member_msg_ids", {}):
                bot.add_view(TeamMemberView(sigle, int(mid_str)))
                count += 1

    return count


# ---------------------------------------------------------------------------
# Suivi des stats (appelé depuis crewbattle.py après chaque set) — absent de
# la version GitHub, portée depuis la session locale.
# ---------------------------------------------------------------------------

def _update_one_stat(discord_id: int, stocks_taken: int, opponent_id: int):
    if not discord_id:
        return
    player = _load_player(discord_id) or {"discord_id": discord_id, "name": str(discord_id), "team": None}
    stats = player.setdefault("stats", {})
    stats["cb_joues"]    = stats.get("cb_joues", 0) + 1
    stats["stocks_pris"] = stats.get("stocks_pris", 0) + stocks_taken
    if stocks_taken > 0 and opponent_id:
        by_opp = stats.setdefault("stocks_par_adversaire", {})
        key = str(opponent_id)
        by_opp[key] = by_opp.get(key, 0) + stocks_taken
    _save_player(player)


def record_set_result(discord_id_a: int, discord_id_b: int, takes_a: int, takes_b: int):
    """Incrémente cb_joues (+1), stocks_pris (+stocks pris ce set) et le détail
    par adversaire (stocks_par_adversaire) pour les deux joueurs."""
    _update_one_stat(discord_id_a, takes_a, discord_id_b)
    _update_one_stat(discord_id_b, takes_b, discord_id_a)


async def refresh_after_set(bot: discord.Client, guild_id: int, discord_id_a: int, discord_id_b: int):
    """Rafraîchit les posts (joueur + équipe) des deux participants après un set."""
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    for discord_id in (discord_id_a, discord_id_b):
        if not discord_id:
            continue
        player = _load_player(discord_id)
        if not player:
            continue

        thread_id = player.get("stats_thread_id")
        if thread_id:
            thread = guild.get_thread(thread_id)
            if not thread:
                try:
                    thread = await guild.fetch_channel(thread_id)
                except Exception:
                    thread = None
            if thread:
                await _refresh_player_thread_embed(thread, player)

        team_sigle = player.get("team")
        if team_sigle:
            await refresh_team_stats_post(bot, guild_id, team_sigle, changed_member_ids={discord_id})


# ---------------------------------------------------------------------------
# Helper interne
# ---------------------------------------------------------------------------

def _get_members_stats(team: dict) -> list[dict]:
    """Retourne la liste des stats membres triée par stocks pris décroissant."""
    result = []
    for member_id in team.get("members", []):
        player = _load_player(member_id)
        if not player:
            continue
        stats = player.get("stats", {})
        result.append({
            "name":        player.get("name", str(member_id)),
            "stocks_pris": stats.get("stocks_pris", 0),
            "cb_joues":    stats.get("cb_joues", 0),
        })
    result.sort(key=lambda x: x["stocks_pris"], reverse=True)
    return result
