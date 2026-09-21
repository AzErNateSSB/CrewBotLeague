"""
Rapport manuel du résultat d'une CB de saison, set par set (joueur, perso,
score), pour un admin ou un leader/admin d'équipe — utile quand une CB a été
jouée en dehors du moteur du bot mais qu'on veut quand même garder
l'historique détaillé (comme pour un match normal), plutôt que de se limiter
au score final de `/cbl_force_match_result`.

Seule condition d'éligibilité : le salon de la CB doit encore être ouvert
(pas de résultat déjà enregistré) — pas besoin d'une LineUp validée au
préalable. Les joueurs sont choisis librement parmi les membres enregistrés
de chaque équipe au fil de la saisie.

Chaque équipe dispose d'un total de 15 vies (5v5, 3 vies/joueur), qui diminue
au fil des dégâts encaissés — comme s'il y avait jusqu'à 7 titulaires
potentiels (le vivier de l'équipe), mais le total reste toujours 15 contre
15 : peu importe combien de joueurs distincts sont réellement envoyés, le
total tombe à 0 exactement au moment où le 5e joueur distinct de l'équipe
est éliminé.
"""

import os
import discord
from typing import Optional

from utils.season_data import OFFICIAL_MATCHES_DIR, load_official_match

TEAM_START_LIVES = 15
TITULAIRES = 5


def eligible_matches(user_id: int) -> list[dict]:
    """CB officielles en cours (salon créé, pas encore de résultat). Toutes
    pour un admin bot ; sinon seulement celles où l'utilisateur est
    leader/admin d'une des 2 équipes."""
    from cogs.crewbattle import is_authorized, is_team_authorized
    from cogs.teams import load_team

    if not os.path.exists(OFFICIAL_MATCHES_DIR):
        return []

    is_bot_admin = is_authorized(user_id)
    out = []
    for fn in sorted(os.listdir(OFFICIAL_MATCHES_DIR)):
        if not fn.endswith(".json"):
            continue
        channel_id = int(fn[:-5])
        official = load_official_match(channel_id)
        if not official:
            continue

        if not is_bot_admin:
            home_team = load_team(official["home"])
            away_team = load_team(official["away"])
            allowed = (
                (home_team and is_team_authorized(user_id, home_team))
                or (away_team and is_team_authorized(user_id, away_team))
            )
            if not allowed:
                continue

        out.append({"channel_id": channel_id, **official})
    return out


class AdminReportMatchSelectView(discord.ui.View):
    """Étape 1 : choix de la CB à rapporter."""

    def __init__(self, admin_id: int, matches: list[dict]):
        super().__init__(timeout=600)
        self.admin_id = admin_id
        self._matches = {str(m["channel_id"]): m for m in matches}

        options = [
            discord.SelectOption(
                label=f"{m['home']} 🆚 {m['away']}"[:100],
                description=(m.get("league") or "")[:100],
                value=str(m["channel_id"]),
            )
            for m in matches[:25]
        ]
        select = discord.ui.Select(placeholder="Choisis la CB à rapporter", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ Ce n'est pas ta sélection.", ephemeral=True)
            return
        info = self._matches[interaction.data["values"][0]]
        await _start_report(interaction, info)


async def _start_report(interaction: discord.Interaction, info: dict):
    from cogs.crewbattle import Team, Match
    from cogs.teams import load_team
    from utils.sheets_log import log_command

    channel_id = info["channel_id"]
    home_team_data = load_team(info["home"])
    away_team_data = load_team(info["away"])
    if not home_team_data or not away_team_data:
        await interaction.response.send_message("❌ Données introuvables pour cette CB.", ephemeral=True)
        return

    ta = Team(name=info["home"], captain_id=home_team_data.get("leader_id", 0))
    tb = Team(name=info["away"], captain_id=away_team_data.get("leader_id", 0))
    match = Match(team_a=ta, team_b=tb, channel_id=channel_id)
    match.log_row = await log_command(
        interaction.user.display_name, f"admin_report {ta.name} vs {tb.name}", "In Progress",
        f"Rapport manuel de score pour {ta.name} vs {tb.name} (salon {channel_id})",
    )

    session = _ReportSession(
        admin_id=interaction.user.id, match=match,
        home_members=home_team_data.get("members", []),
        away_members=away_team_data.get("members", []),
    )
    await session.prompt_player(interaction, "A", first_response=True)


class _ReportSession:
    """Porte l'état d'une saisie manuelle de CB en cours (un `Match` réel,
    peuplé set par set) et enchaîne les vues/modals jusqu'à ce qu'une équipe
    tombe à 0 vie, puis délègue la clôture à `end_crewbattle` — exactement
    comme un match joué normalement (classement, historique, stats, tout est
    géré)."""

    def __init__(self, admin_id: int, match: "Match", home_members: list, away_members: list):
        self.admin_id = admin_id
        self.match = match
        self.members = {"A": home_members, "B": away_members}
        self.used_ids: dict[str, set] = {"A": set(), "B": set()}
        self.picked_player = None

    def _pool_remaining(self, side: str) -> int:
        history = self.match.set_history
        damage = sum((r.score_b if side == "A" else r.score_a) for r in history)
        return TEAM_START_LIVES - damage

    def _available_members(self, interaction: discord.Interaction, side: str) -> list[tuple[int, str]]:
        guild = interaction.guild
        out = []
        for mid in self.members[side]:
            if mid in self.used_ids[side]:
                continue
            member = guild.get_member(mid) if guild else None
            out.append((mid, member.display_name if member else str(mid)))
        return out

    async def prompt_player(self, interaction: discord.Interaction, side: str, first_response: bool = False):
        match = self.match
        team = match.team_a if side == "A" else match.team_b
        available = self._available_members(interaction, side)

        if not available:
            msg = (
                f"❌ Plus aucun joueur enregistré disponible pour **{team.name}** "
                f"— ajoute des joueurs à l'équipe avant de continuer ce rapport."
            )
            if first_response:
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.response.edit_message(content=msg, embed=None, view=None)
            return

        view = _PlayerPickView(self, side, available)
        pool = self._pool_remaining(side)
        content = f"**Set {match.set_number + 1}** — quel joueur pour **{team.name}** ? (`{pool}` vies restantes)"
        if first_response:
            await interaction.response.send_message(content, view=view, ephemeral=True)
        else:
            await interaction.response.edit_message(content=content, embed=None, view=view)

    async def on_player_picked(self, interaction: discord.Interaction, side: str, discord_id: int, name: str):
        from cogs.crewbattle import Player
        self.picked_player = Player(name=name, discord_id=discord_id)
        team = self.match.team_a if side == "A" else self.match.team_b
        view = _CharPickView(self, side, interaction.client)
        await interaction.response.edit_message(
            content=f"**{name}** ({team.name}) — quel personnage ?", view=view,
        )

    async def on_char_picked(self, interaction: discord.Interaction, side: str, char: str):
        match = self.match
        player = self.picked_player
        player.character = char
        if side == "A":
            match.current_a = player
            if match.current_b is None:
                await self.prompt_player(interaction, "B")
                return
        else:
            match.current_b = player
            if match.current_a is None:
                await self.prompt_player(interaction, "A")
                return
        await interaction.response.send_modal(_ScoreModal(self))

    async def on_score_submitted(self, interaction: discord.Interaction, takes_a: int, takes_b: int):
        from cogs.crewbattle import SetRecord

        match = self.match
        ca, cb = match.current_a, match.current_b
        new_a = ca.lives - takes_b
        new_b = cb.lives - takes_a

        match.set_history.append(SetRecord(
            player_a=ca.name, char_a=ca.character,
            player_b=cb.name, char_b=cb.character,
            score_a=takes_a, score_b=takes_b,
            stage="", lives_a_after=new_a, lives_b_after=new_b,
        ))
        ca.lives = new_a
        cb.lives = new_b
        match.set_number += 1
        loser_side = "A" if new_a == 0 else "B"
        loser_player = ca if loser_side == "A" else cb
        if loser_player.discord_id:
            self.used_ids[loser_side].add(loser_player.discord_id)

        from utils.players_stats import record_set_result, refresh_after_set
        record_set_result(ca.discord_id, cb.discord_id, takes_a, takes_b)

        await interaction.response.defer(ephemeral=True)
        if interaction.guild:
            await refresh_after_set(interaction.client, interaction.guild.id, ca.discord_id, cb.discord_id)

        pool_a = self._pool_remaining("A")
        pool_b = self._pool_remaining("B")
        if pool_a <= 0 or pool_b <= 0:
            await self._finish(interaction)
            return

        if loser_side == "A":
            match.current_a = None
        else:
            match.current_b = None

        loser_team = match.team_a if loser_side == "A" else match.team_b
        available = self._available_members(interaction, loser_side)
        if not available:
            await interaction.followup.send(
                f"❌ Set {match.set_number} enregistré (`{pool_a}` — `{pool_b}`), mais plus aucun joueur "
                f"enregistré disponible pour **{loser_team.name}** pour continuer ce rapport.",
                ephemeral=True,
            )
            return

        view = _PlayerPickView(self, loser_side, available)
        await interaction.followup.send(
            f"Set {match.set_number} enregistré (`{pool_a}` — `{pool_b}`). "
            f"Quel joueur pour **{loser_team.name}** (côté perdant) ?",
            view=view, ephemeral=True,
        )

    async def _finish(self, interaction: discord.Interaction):
        from cogs.crewbattle import end_crewbattle, Player
        match = self.match

        def _fill(pool: int) -> list["Player"]:
            remaining = max(0, pool)
            out = []
            for _ in range(TITULAIRES):
                take = min(3, remaining)
                out.append(Player(name="", lives=take))
                remaining -= take
            return out

        match.team_a.players = _fill(self._pool_remaining("A"))
        match.team_b.players = _fill(self._pool_remaining("B"))

        guild = interaction.guild
        channel = guild.get_channel(match.channel_id) or guild.get_thread(match.channel_id)
        if not channel:
            try:
                channel = await interaction.client.fetch_channel(match.channel_id)
            except Exception:
                channel = None
        if not channel:
            await interaction.followup.send(
                "❌ Salon du match introuvable — impossible de finaliser la CB.", ephemeral=True
            )
            return

        await end_crewbattle(channel, match)
        await interaction.followup.send("✅ CB enregistrée et clôturée.", ephemeral=True)


class _PlayerPickView(discord.ui.View):
    def __init__(self, session: "_ReportSession", side: str, available: list[tuple[int, str]]):
        super().__init__(timeout=600)
        self.session = session
        self.side = side
        self._names = {str(mid): name for mid, name in available}

        options = [
            discord.SelectOption(label=name[:100], value=str(mid))
            for mid, name in available[:25]
        ]
        select = discord.ui.Select(placeholder="Choisis le joueur", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.admin_id:
            await interaction.response.send_message("❌ Ce n'est pas ta sélection.", ephemeral=True)
            return
        value = interaction.data["values"][0]
        await self.session.on_player_picked(interaction, self.side, int(value), self._names[value])


class _CharPickView(discord.ui.View):
    """Sélection du personnage par pages de boutons — même principe que
    CharacterSelectView (cogs/crewbattle.py), mais pilotée entièrement par
    l'admin (pas d'authorization par joueur)."""

    def __init__(self, session: "_ReportSession", side: str, bot, page: int = 0):
        super().__init__(timeout=600)
        self.session = session
        self.side = side
        self.bot = bot
        self.page = page
        self.selected_char: Optional[str] = None
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
            btn.callback = self._make_cb(char)
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

    def _authorized(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.session.admin_id

    def _make_cb(self, char: str):
        async def cb(interaction: discord.Interaction):
            if not self._authorized(interaction):
                await interaction.response.send_message("❌ Ce n'est pas ta sélection.", ephemeral=True)
                return
            self.selected_char = char
            self._build()
            await interaction.response.edit_message(view=self)
        return cb

    async def _prev(self, interaction: discord.Interaction):
        if not self._authorized(interaction):
            await interaction.response.send_message("❌ Ce n'est pas ta sélection.", ephemeral=True)
            return
        self.page -= 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        if not self._authorized(interaction):
            await interaction.response.send_message("❌ Ce n'est pas ta sélection.", ephemeral=True)
            return
        self.page += 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _confirm(self, interaction: discord.Interaction):
        if not self._authorized(interaction):
            await interaction.response.send_message("❌ Ce n'est pas ta sélection.", ephemeral=True)
            return
        await self.session.on_char_picked(interaction, self.side, self.selected_char)


class _ScoreModal(discord.ui.Modal):
    def __init__(self, session: "_ReportSession"):
        super().__init__(title="Résultat du set")
        self.session = session
        match = session.match
        ca, cb = match.current_a, match.current_b

        self.score_a_input = discord.ui.TextInput(
            label=f"Vies prises par {ca.name}"[:45],
            placeholder=f"0 à {cb.lives}", min_length=1, max_length=1,
        )
        self.score_b_input = discord.ui.TextInput(
            label=f"Vies prises par {cb.name}"[:45],
            placeholder=f"0 à {ca.lives}", min_length=1, max_length=1,
        )
        self.add_item(self.score_a_input)
        self.add_item(self.score_b_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.session.admin_id:
            await interaction.response.send_message("❌ Ce n'est pas ta sélection.", ephemeral=True)
            return

        match = self.session.match
        ca, cb = match.current_a, match.current_b
        try:
            takes_a = int(self.score_a_input.value.strip())
            takes_b = int(self.score_b_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Valeurs invalides (entiers attendus).", ephemeral=True)
            return

        if takes_a < 0 or takes_b < 0:
            await interaction.response.send_message("❌ Les valeurs ne peuvent pas être négatives.", ephemeral=True)
            return
        if takes_a > cb.lives or takes_b > ca.lives:
            await interaction.response.send_message(
                f"❌ Impossible : {ca.name} a {ca.lives}♥, {cb.name} a {cb.lives}♥.", ephemeral=True,
            )
            return

        new_a = ca.lives - takes_b
        new_b = cb.lives - takes_a
        if new_a == 0 and new_b == 0:
            await interaction.response.send_message(
                "❌ Les deux joueurs ne peuvent pas être éliminés simultanément.", ephemeral=True
            )
            return
        if new_a != 0 and new_b != 0:
            await interaction.response.send_message(
                "❌ Le set doit se terminer par l'élimination d'un joueur (0 vie).", ephemeral=True
            )
            return

        await self.session.on_score_submitted(interaction, takes_a, takes_b)
