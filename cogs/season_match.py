import discord
from discord.ext import commands
from datetime import date, timedelta
import json
import os
from typing import Optional

SEASON_MATCHES_DIR = os.path.join("data", "season_matches")

DAYS_FR   = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
MONTHS_FR = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin",
             "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]


def _fmt_date(d: date) -> str:
    return f"{DAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]}"


def _date_range(period_start: date, deadline: date) -> list[date]:
    days = (deadline - period_start).days
    return [period_start + timedelta(days=i) for i in range(days + 1)]

# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------

def _path(thread_id: int) -> str:
    return os.path.join(SEASON_MATCHES_DIR, f"{thread_id}.json")

def save_season_match(thread_id: int, data: dict):
    os.makedirs(SEASON_MATCHES_DIR, exist_ok=True)
    with open(_path(thread_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_season_match(thread_id: int) -> Optional[dict]:
    p = _path(thread_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def del_season_match(thread_id: int):
    p = _path(thread_id)
    if os.path.exists(p):
        os.remove(p)


async def _tasks_channels(guild: discord.Guild, data: dict):
    from cogs.teams import load_team
    home_team = load_team(data["home_sigle"])
    away_team = load_team(data["away_sigle"])
    home_ch = (guild.get_channel(home_team["channels"].get("tasks") or home_team["channels"]["general"])
               if home_team else None)
    away_ch = (guild.get_channel(away_team["channels"].get("tasks") or away_team["channels"]["general"])
               if away_team else None)
    return home_ch, away_ch


def _leader_id(side_sigle: str) -> Optional[int]:
    from cogs.teams import load_team
    team = load_team(side_sigle)
    return team["leader_id"] if team else None


def _is_authorized(user_id: int, side_sigle: str) -> bool:
    """Vrai si user_id est le leader de side_sigle, ou l'admin."""
    from cogs.crewbattle import is_authorized
    return is_authorized(user_id, _leader_id(side_sigle))

# ---------------------------------------------------------------------------
# Point d'entrée : appelé à la création du thread de match (cf admin_panel.py)
# ---------------------------------------------------------------------------

async def post_date_selection(guild: discord.Guild, thread_id: int, league: str,
                               home_sigle: str, away_sigle: str,
                               period_start: date, deadline: date):
    """Poste le sélecteur de disponibilités dans les 2 salons tasks."""
    from cogs.teams import load_team

    save_season_match(thread_id, {
        "thread_id": thread_id, "league": league,
        "home_sigle": home_sigle, "away_sigle": away_sigle,
        "period_start": period_start.isoformat(), "deadline": deadline.isoformat(),
        "avail_home": None, "avail_away": None,
        "confirmed_date": None,
        "roster_home": None, "roster_subs_home": None,
        "roster_away": None, "roster_subs_away": None,
        "ready_home": False, "ready_away": False,
        "avail_msg_home_id": None, "avail_msg_away_id": None,
        "propose_msg_id": None, "propose_by": None, "propose_date": None,
        "roster_msg_home_id": None, "roster_msg_away_id": None,
        "ready_msg_home_id": None, "ready_msg_away_id": None,
    })

    for side, sigle, opp_sigle in (("home", home_sigle, away_sigle), ("away", away_sigle, home_sigle)):
        team = load_team(sigle)
        if not team:
            continue
        tasks_ch = guild.get_channel(team["channels"].get("tasks") or team["channels"]["general"])
        if not tasks_ch:
            continue
        view = SeasonDateSelectView(thread_id, side)
        try:
            msg = await tasks_ch.send(
                f"📅 **CB de saison — {sigle} 🆚 {opp_sigle}**\n"
                f"Sélectionnez vos disponibilités (là où vous avez au moins 5 joueurs libres) "
                f"entre le **{_fmt_date(period_start)}** et le **{_fmt_date(deadline)}** :",
                view=view,
            )
            view.message = msg
            data = load_season_match(thread_id)
            data[f"avail_msg_{side}_id"] = msg.id
            save_season_match(thread_id, data)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Sélection des disponibilités (persistant, modifiable tant que rien n'est
# confirmé)
# ---------------------------------------------------------------------------

class SeasonDateSelectView(discord.ui.View):
    """Réservé au leader. Reste actif après validation : re-sélectionner puis
    re-valider permet d'ajouter/enlever des dates tant qu'aucune n'est confirmée."""

    def __init__(self, thread_id: int, side: str):
        super().__init__(timeout=None)
        self.thread_id = thread_id
        self.side = side
        self.message: Optional[discord.Message] = None

        data = load_season_match(thread_id) or {}
        period_start = date.fromisoformat(data["period_start"])
        deadline     = date.fromisoformat(data["deadline"])
        current      = data.get(f"avail_{side}") or []
        self._selected = list(current)

        options = [
            discord.SelectOption(label=_fmt_date(d), value=d.isoformat(), default=(d.isoformat() in current))
            for d in _date_range(period_start, deadline)
        ][:25]

        select = discord.ui.Select(
            placeholder="Sélectionne tes disponibilités...",
            min_values=1, max_values=len(options),
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

        confirm = discord.ui.Button(label="✅ Valider mes disponibilités", style=discord.ButtonStyle.success)
        confirm.callback = self._confirm
        self.add_item(confirm)

    async def _on_select(self, interaction: discord.Interaction):
        if not _is_authorized(interaction.user.id, self._sigle()):
            await interaction.response.send_message(
                "❌ Seul le leader peut renseigner les disponibilités.", ephemeral=True
            )
            return
        self._selected = interaction.data["values"]
        await interaction.response.defer()

    def _sigle(self) -> str:
        data = load_season_match(self.thread_id) or {}
        return data.get(f"{self.side}_sigle", "")

    async def _confirm(self, interaction: discord.Interaction):
        if not _is_authorized(interaction.user.id, self._sigle()):
            await interaction.response.send_message(
                "❌ Seul le leader peut valider les disponibilités.", ephemeral=True
            )
            return

        data = load_season_match(self.thread_id)
        if not data:
            await interaction.response.send_message("❌ Match introuvable.", ephemeral=True)
            return
        if data.get("confirmed_date"):
            await interaction.response.send_message(
                "❌ Une date a déjà été confirmée pour ce match, impossible de modifier les disponibilités.",
                ephemeral=True,
            )
            return
        if not self._selected:
            await interaction.response.send_message("❌ Sélectionne au moins une date.", ephemeral=True)
            return

        data[f"avail_{self.side}"] = self._selected
        save_season_match(self.thread_id, data)

        await interaction.response.send_message(
            "✅ Disponibilités enregistrées : "
            + ", ".join(_fmt_date(date.fromisoformat(d)) for d in sorted(self._selected)),
            ephemeral=True,
        )

        await _check_dates(interaction.client, interaction.guild, self.thread_id)

# ---------------------------------------------------------------------------
# Comparaison des disponibilités et négociation
# ---------------------------------------------------------------------------

async def _check_dates(bot, guild: discord.Guild, thread_id: int):
    data = load_season_match(thread_id)
    if not data or data.get("confirmed_date"):
        return
    avail_home = data.get("avail_home")
    avail_away = data.get("avail_away")
    if not avail_home or not avail_away:
        return  # les 2 équipes n'ont pas encore soumis leurs disponibilités

    home_ch, away_ch = await _tasks_channels(guild, data)
    if not home_ch or not away_ch:
        return

    common = sorted(set(avail_home) & set(avail_away))

    if len(common) == 1:
        await _confirm_date(bot, guild, thread_id, common[0])
        return

    dates_str = ", ".join(_fmt_date(date.fromisoformat(d)) for d in common) if common else ""

    if len(common) > 1:
        view_home = SeasonDateProposeView(thread_id, common, proposer_side="home")
        view_away = SeasonDateProposeView(thread_id, common, proposer_side="away")
        msg_h = await home_ch.send(
            f"📅 Plusieurs dates communes sont possibles : {dates_str}.\n"
            f"Sélectionnez celle que vous préférez :", view=view_home,
        )
        msg_a = await away_ch.send(
            f"📅 Plusieurs dates communes sont possibles : {dates_str}.\n"
            f"Sélectionnez celle que vous préférez :", view=view_away,
        )
        view_home.message, view_away.message = msg_h, msg_a
        return

    # Aucune date en commun
    view_home = SeasonNoCommonView(thread_id, avail_away, side="home")
    view_away = SeasonNoCommonView(thread_id, avail_home, side="away")
    await home_ch.send(
        "❌ Aucune date commune n'a été trouvée. Voici les disponibilités de l'équipe adverse — "
        "veuillez en sélectionner une, ou modifier vos disponibilités.", view=view_home,
    )
    await away_ch.send(
        "❌ Aucune date commune n'a été trouvée. Voici les disponibilités de l'équipe adverse — "
        "veuillez en sélectionner une, ou modifier vos disponibilités.", view=view_away,
    )


class SeasonDateProposeView(discord.ui.View):
    """Plusieurs dates en commun : chaque leader peut en proposer une (ou
    contre-proposer), ça part en 'Accepter ?' dans le salon de l'autre équipe."""

    def __init__(self, thread_id: int, dates: list[str], proposer_side: str):
        super().__init__(timeout=None)
        self.thread_id = thread_id
        self.proposer_side = proposer_side
        self.message: Optional[discord.Message] = None
        for d in dates[:20]:
            btn = discord.ui.Button(label=_fmt_date(date.fromisoformat(d)), style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(d)
            self.add_item(btn)

    def _sigle(self) -> str:
        data = load_season_match(self.thread_id) or {}
        return data.get(f"{self.proposer_side}_sigle", "")

    def _make_cb(self, chosen_date: str):
        async def cb(interaction: discord.Interaction):
            if not _is_authorized(interaction.user.id, self._sigle()):
                await interaction.response.send_message("❌ Seul le leader peut proposer une date.", ephemeral=True)
                return

            data = load_season_match(self.thread_id)
            if not data or data.get("confirmed_date"):
                await interaction.response.send_message("❌ Cette négociation n'est plus active.", ephemeral=True)
                return

            await interaction.response.send_message(
                f"📨 Proposition envoyée : **{_fmt_date(date.fromisoformat(chosen_date))}**.", ephemeral=True
            )

            proposer_sigle = data[f"{self.proposer_side}_sigle"]
            home_ch_, away_ch_ = await _tasks_channels(interaction.guild, data)
            other_ch = away_ch_ if self.proposer_side == "home" else home_ch_
            if not other_ch:
                return

            verb = "préférerait" if data.get("propose_date") else "propose"
            accept_view = SeasonDateAcceptView(self.thread_id, chosen_date)
            msg = await other_ch.send(
                f"📅 **{proposer_sigle}** {verb} le **{_fmt_date(date.fromisoformat(chosen_date))}**.",
                view=accept_view,
            )
            accept_view.message = msg

            data["propose_date"]   = chosen_date
            data["propose_by"]     = self.proposer_side
            data["propose_msg_id"] = msg.id
            save_season_match(self.thread_id, data)
        return cb


class SeasonDateAcceptView(discord.ui.View):
    def __init__(self, thread_id: int, proposed_date: str):
        super().__init__(timeout=None)
        self.thread_id = thread_id
        self.proposed_date = proposed_date
        self.message: Optional[discord.Message] = None

        btn = discord.ui.Button(label="✅ Accepter", style=discord.ButtonStyle.success)
        btn.callback = self._accept
        self.add_item(btn)

    async def _accept(self, interaction: discord.Interaction):
        data = load_season_match(self.thread_id)
        if not data or data.get("confirmed_date"):
            await interaction.response.send_message("❌ Cette négociation n'est plus active.", ephemeral=True)
            return

        proposer_side = data.get("propose_by")
        accepter_side = "away" if proposer_side == "home" else "home"
        accepter_sigle = data.get(f"{accepter_side}_sigle", "")
        if not _is_authorized(interaction.user.id, accepter_sigle):
            await interaction.response.send_message(
                "❌ Seul le leader de l'équipe qui reçoit la proposition peut l'accepter.", ephemeral=True
            )
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        await _confirm_date(interaction.client, interaction.guild, self.thread_id, self.proposed_date)


class SeasonNoCommonView(discord.ui.View):
    """Aucune date en commun : propose les dispos de l'adversaire, un clic confirme direct
    (l'adversaire a déjà déclaré cette date comme disponible)."""

    def __init__(self, thread_id: int, opponent_dates: list[str], side: str):
        super().__init__(timeout=None)
        self.thread_id = thread_id
        self.side = side
        self.message: Optional[discord.Message] = None
        for d in sorted(opponent_dates)[:20]:
            btn = discord.ui.Button(label=_fmt_date(date.fromisoformat(d)), style=discord.ButtonStyle.primary)
            btn.callback = self._make_cb(d)
            self.add_item(btn)

    def _sigle(self) -> str:
        data = load_season_match(self.thread_id) or {}
        return data.get(f"{self.side}_sigle", "")

    def _make_cb(self, chosen_date: str):
        async def cb(interaction: discord.Interaction):
            if not _is_authorized(interaction.user.id, self._sigle()):
                await interaction.response.send_message("❌ Seul le leader peut sélectionner une date.", ephemeral=True)
                return
            data = load_season_match(self.thread_id)
            if not data or data.get("confirmed_date"):
                await interaction.response.send_message("❌ Cette négociation n'est plus active.", ephemeral=True)
                return
            await _confirm_date(interaction.client, interaction.guild, self.thread_id, chosen_date)
        return cb

# ---------------------------------------------------------------------------
# Confirmation de la date + "Prêt"
# ---------------------------------------------------------------------------

NB_ACTIVE   = 5
NB_SUBS_MAX = 2


async def _confirm_date(bot, guild: discord.Guild, thread_id: int, chosen_date: str):
    from cogs.teams import load_team

    data = load_season_match(thread_id)
    if not data or data.get("confirmed_date"):
        return
    data["confirmed_date"] = chosen_date
    save_season_match(thread_id, data)

    home_ch, away_ch = await _tasks_channels(guild, data)
    home_team = load_team(data["home_sigle"])
    away_team = load_team(data["away_sigle"])
    d_str = _fmt_date(date.fromisoformat(chosen_date))

    for side, ch, team in (("home", home_ch, home_team), ("away", away_ch, away_team)):
        if not ch or not team:
            continue

        members = team.get("members", [])
        if len(members) < NB_ACTIVE:
            try:
                await ch.send(
                    f"❌ **{team['sigle']}** n'a que {len(members)} membre(s) enregistré(s), "
                    f"il en faut au moins {NB_ACTIVE} pour former une équipe. Contacte un admin."
                )
            except Exception:
                pass
            continue

        options = []
        for mid in members[:25]:
            member = guild.get_member(mid)
            options.append(discord.SelectOption(
                label=(member.display_name if member else str(mid))[:100], value=str(mid),
            ))

        view = SeasonRosterSelectView(thread_id, side, options)
        try:
            msg = await ch.send(
                f"✅ La date a été confirmée au **{d_str}** !\n"
                f"Composez votre équipe : **{NB_ACTIVE} titulaires** (obligatoire) "
                f"+ jusqu'à **{NB_SUBS_MAX} remplaçant(s)** (optionnel) :",
                view=view,
            )
            view.message = msg
            d2 = load_season_match(thread_id)
            if d2:
                d2[f"roster_msg_{side}_id"] = msg.id
                save_season_match(thread_id, d2)
        except Exception:
            pass


class SeasonReadyView(discord.ui.View):
    def __init__(self, thread_id: int, side: str):
        super().__init__(timeout=None)
        self.thread_id = thread_id
        self.side = side
        self.message: Optional[discord.Message] = None

        data = load_season_match(thread_id) or {}
        already_ready = bool(data.get(f"ready_{side}"))

        btn = discord.ui.Button(
            label="✅ Prêt !" if already_ready else "✅ Prêt",
            style=discord.ButtonStyle.success,
            disabled=already_ready,
        )
        btn.callback = self._ready
        self.add_item(btn)

    def _sigle(self) -> str:
        data = load_season_match(self.thread_id) or {}
        return data.get(f"{self.side}_sigle", "")

    async def _ready(self, interaction: discord.Interaction):
        if not _is_authorized(interaction.user.id, self._sigle()):
            await interaction.response.send_message(
                "❌ Seul le leader peut confirmer que l'équipe est prête.", ephemeral=True
            )
            return

        data = load_season_match(self.thread_id)
        if not data:
            await interaction.response.send_message("❌ Match introuvable.", ephemeral=True)
            return

        data[f"ready_{self.side}"] = True
        save_season_match(self.thread_id, data)

        for item in self.children:
            item.disabled = True
            item.label = "✅ Prêt !"
        await interaction.response.edit_message(view=self)

        if data.get("ready_home") and data.get("ready_away"):
            await _start_match_engine(interaction.client, interaction.guild, self.thread_id)


class SeasonRosterSelectView(discord.ui.View):
    """Composition d'équipe : {NB_ACTIVE} titulaires (obligatoire) + jusqu'à
    {NB_SUBS_MAX} remplaçants (optionnel). Réservé au leader. Une fois validée,
    révèle le bouton "Prêt" dans le même salon."""

    def __init__(self, thread_id: int, side: str, options: list[discord.SelectOption]):
        super().__init__(timeout=None)
        self.thread_id = thread_id
        self.side = side
        self.message: Optional[discord.Message] = None
        self.active_ids: list[str] = []
        self.sub_ids: list[str] = []

        self.active_select = discord.ui.Select(
            placeholder=f"Joueurs actifs ({NB_ACTIVE})",
            min_values=NB_ACTIVE, max_values=min(NB_ACTIVE, len(options)),
            options=options,
        )
        self.active_select.callback = self._on_active
        self.add_item(self.active_select)

        self.subs_select = discord.ui.Select(
            placeholder=f"Remplaçants (0 à {NB_SUBS_MAX})",
            min_values=0, max_values=min(NB_SUBS_MAX, len(options)),
            options=options,
        )
        self.subs_select.callback = self._on_subs
        self.add_item(self.subs_select)

        self.confirm_btn = discord.ui.Button(
            label="✅ Valider la composition", style=discord.ButtonStyle.success, disabled=True,
        )
        self.confirm_btn.callback = self._confirm
        self.add_item(self.confirm_btn)

    def _sigle(self) -> str:
        data = load_season_match(self.thread_id) or {}
        return data.get(f"{self.side}_sigle", "")

    def _update_confirm_state(self):
        active_ok = len(self.active_ids) == NB_ACTIVE
        overlap = bool(set(self.active_ids) & set(self.sub_ids))
        self.confirm_btn.disabled = not (active_ok and not overlap)
        self.confirm_btn.label = (
            "⚠️ Un joueur ne peut pas être titulaire et remplaçant" if overlap
            else "✅ Valider la composition"
        )

    async def _on_active(self, interaction: discord.Interaction):
        if not _is_authorized(interaction.user.id, self._sigle()):
            await interaction.response.send_message("❌ Seul le leader peut composer l'équipe.", ephemeral=True)
            return
        self.active_ids = interaction.data["values"]
        self._update_confirm_state()
        await interaction.response.edit_message(view=self)

    async def _on_subs(self, interaction: discord.Interaction):
        if not _is_authorized(interaction.user.id, self._sigle()):
            await interaction.response.send_message("❌ Seul le leader peut composer l'équipe.", ephemeral=True)
            return
        self.sub_ids = interaction.data["values"]
        self._update_confirm_state()
        await interaction.response.edit_message(view=self)

    async def _confirm(self, interaction: discord.Interaction):
        if not _is_authorized(interaction.user.id, self._sigle()):
            await interaction.response.send_message("❌ Seul le leader peut composer l'équipe.", ephemeral=True)
            return

        data = load_season_match(self.thread_id)
        if not data or data.get(f"roster_{self.side}"):
            await interaction.response.send_message("❌ Composition déjà envoyée.", ephemeral=True)
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        data[f"roster_{self.side}"] = self.active_ids
        data[f"roster_subs_{self.side}"] = self.sub_ids
        save_season_match(self.thread_id, data)

        ready_view = SeasonReadyView(self.thread_id, self.side)
        try:
            msg = await interaction.channel.send(
                "✅ Composition enregistrée ! Cliquez sur \"Prêt\" quand votre équipe est prête à commencer.",
                view=ready_view,
            )
            ready_view.message = msg
            data2 = load_season_match(self.thread_id)
            if data2:
                data2[f"ready_msg_{self.side}_id"] = msg.id
                save_season_match(self.thread_id, data2)
        except Exception:
            pass


async def _start_match_engine(bot, guild: discord.Guild, thread_id: int):
    """Construit le Match/Team crewbattle et lance le premier choix de joueur,
    un côté par salon tasks (voir SeasonFirstPickView)."""
    from cogs.crewbattle import Team, Match, Player, active_matches, save_matches
    from cogs.teams import load_team
    from utils.sheets_log import log_command

    data = load_season_match(thread_id)
    if not data:
        return

    home_ch, away_ch = await _tasks_channels(guild, data)
    home_team_data = load_team(data["home_sigle"])
    away_team_data = load_team(data["away_sigle"])
    if not home_team_data or not away_team_data:
        return

    def build_players(ids: list[str]) -> list["Player"]:
        players = []
        for pid_str in ids:
            pid = int(pid_str)
            m = guild.get_member(pid)
            players.append(Player(name=m.display_name if m else str(pid), discord_id=pid))
        return players

    ta = Team(name=data["home_sigle"], captain_id=home_team_data["leader_id"],
              players=build_players(data["roster_home"]), subs=build_players(data.get("roster_subs_home") or []))
    tb = Team(name=data["away_sigle"], captain_id=away_team_data["leader_id"],
              players=build_players(data["roster_away"]), subs=build_players(data.get("roster_subs_away") or []))

    match = Match(
        team_a=ta, team_b=tb, channel_id=thread_id,
        channel_a_id=home_ch.id if home_ch else None,
        channel_b_id=away_ch.id if away_ch else None,
    )
    active_matches[thread_id] = match

    match.log_row = await log_command(
        "Saison", f"season_match **{ta.name}** vs **{tb.name}**", "In Progress",
        f"CB de saison {ta.name} vs {tb.name} (thread {thread_id})",
    )
    save_matches()

    view_a = SeasonFirstPickView(match, "A", ta.name)
    view_b = SeasonFirstPickView(match, "B", tb.name)
    if home_ch:
        view_a.message = await home_ch.send("📢 Choisissez votre premier joueur !", view=view_a)
    if away_ch:
        view_b.message = await away_ch.send("📢 Choisissez votre premier joueur !", view=view_b)

    thread = guild.get_thread(thread_id)
    if not thread:
        try:
            thread = await guild.fetch_channel(thread_id)
        except Exception:
            thread = None
    if thread and thread.name and thread.name[0] == "🔴":
        try:
            await thread.edit(name="🟠" + thread.name[1:])
        except Exception:
            pass


class SeasonFirstPickView(discord.ui.View):
    """Équivalent de FirstPickView (crewbattle.py), mais pour un salon dédié à
    une seule équipe (salons tasks séparés en saison)."""

    def __init__(self, match, side: str, team_name: str):
        super().__init__(timeout=None)
        self.match = match
        self.side = side
        self.message: Optional[discord.Message] = None

        btn = discord.ui.Button(label=f"⚔️ {team_name} — Choisir votre joueur", style=discord.ButtonStyle.primary)
        btn.callback = self._pick
        self.add_item(btn)

        # CharacterSelectView (mode="first") s'attend à parent_view.btn_a / .btn_b.
        if side == "A":
            self.btn_a, self.btn_b = btn, discord.ui.Button()
        else:
            self.btn_b, self.btn_a = btn, discord.ui.Button()

    async def _pick(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_authorized, PlayerSelectView

        team = self.match.team_a if self.side == "A" else self.match.team_b
        if not is_authorized(interaction.user.id, team.captain_id):
            await interaction.response.send_message("❌ Seul le leader peut agir ici.", ephemeral=True)
            return
        already = self.match.picked_a if self.side == "A" else self.match.picked_b
        if already:
            await interaction.response.send_message("✅ Joueur déjà soumis.", ephemeral=True)
            return

        view = PlayerSelectView(self.match, self.side, self, team.all_players, mode="first")
        await interaction.response.send_message(
            f"**{team.name}** — Quel joueur envoyer ?", view=view, ephemeral=True,
        )
        view.message = await interaction.original_response()

# ---------------------------------------------------------------------------
# Reprise après redémarrage du bot
# ---------------------------------------------------------------------------

async def restore_all_season_matches(bot: commands.Bot) -> int:
    """Ré-enregistre les boutons persistants encore actifs (aucun appel réseau,
    hormis résoudre les membres pour reconstruire le menu de composition).
    Limite connue : les messages de proposition/négociation intermédiaires
    (dates multiples en commun, ou aucune date en commun) ne sont pas
    ré-enregistrés individuellement."""
    if not os.path.exists(SEASON_MATCHES_DIR):
        return 0
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None

    from cogs.teams import load_team

    count = 0
    for fn in os.listdir(SEASON_MATCHES_DIR):
        if not fn.endswith(".json"):
            continue
        thread_id = int(fn[:-5])
        data = load_season_match(thread_id)
        if not data:
            continue

        if not data.get("confirmed_date"):
            for side in ("home", "away"):
                msg_id = data.get(f"avail_msg_{side}_id")
                if msg_id:
                    bot.add_view(SeasonDateSelectView(thread_id, side), message_id=msg_id)
                    count += 1
            propose_msg_id = data.get("propose_msg_id")
            propose_date   = data.get("propose_date")
            if propose_msg_id and propose_date:
                bot.add_view(SeasonDateAcceptView(thread_id, propose_date), message_id=propose_msg_id)
                count += 1
            continue

        for side in ("home", "away"):
            if data.get(f"roster_{side}"):
                continue
            msg_id = data.get(f"roster_msg_{side}_id")
            if not msg_id or not guild:
                continue
            sigle = data.get(f"{side}_sigle", "")
            team = load_team(sigle)
            if not team:
                continue
            options = []
            for mid in team.get("members", [])[:25]:
                member = guild.get_member(mid)
                options.append(discord.SelectOption(
                    label=(member.display_name if member else str(mid))[:100], value=str(mid),
                ))
            bot.add_view(SeasonRosterSelectView(thread_id, side, options), message_id=msg_id)
            count += 1

        for side in ("home", "away"):
            if not data.get(f"roster_{side}") or data.get(f"ready_{side}"):
                continue
            msg_id = data.get(f"ready_msg_{side}_id")
            if msg_id:
                bot.add_view(SeasonReadyView(thread_id, side), message_id=msg_id)
                count += 1
    return count
