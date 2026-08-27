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
        "ready_home": False, "ready_away": False,
        "avail_msg_home_id": None, "avail_msg_away_id": None,
        "propose_msg_id": None, "propose_by": None, "propose_date": None,
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
        if interaction.user.id != _leader_id(self._sigle()):
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
        if interaction.user.id != _leader_id(self._sigle()):
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
            if interaction.user.id != _leader_id(self._sigle()):
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
        if interaction.user.id != _leader_id(accepter_sigle):
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
            if interaction.user.id != _leader_id(self._sigle()):
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

async def _confirm_date(bot, guild: discord.Guild, thread_id: int, chosen_date: str):
    data = load_season_match(thread_id)
    if not data or data.get("confirmed_date"):
        return
    data["confirmed_date"] = chosen_date
    save_season_match(thread_id, data)

    home_ch, away_ch = await _tasks_channels(guild, data)
    d_str = _fmt_date(date.fromisoformat(chosen_date))

    for side, ch in (("home", home_ch), ("away", away_ch)):
        if not ch:
            continue
        view = SeasonReadyView(thread_id, side)
        try:
            msg = await ch.send(
                f"✅ La date a été confirmée au **{d_str}** ! Veuillez appuyer sur \"Prêt\" !",
                view=view,
            )
            view.message = msg
            d2 = load_season_match(thread_id)
            if d2:
                d2[f"ready_msg_{side}_id"] = msg.id
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
        if interaction.user.id != _leader_id(self._sigle()):
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
            await _launch_season_match(interaction.client, interaction.guild, self.thread_id)


async def _launch_season_match(bot, guild: discord.Guild, thread_id: int):
    """Les 2 équipes sont prêtes. Le lancement réel du moteur de match (étape 3)
    n'est pas encore branché ; on se contente de le signaler pour l'instant."""
    data = load_season_match(thread_id)
    if not data:
        return
    home_ch, away_ch = await _tasks_channels(guild, data)
    for ch in (home_ch, away_ch):
        if ch:
            try:
                await ch.send("🚀 Les deux équipes sont prêtes ! (Lancement de la CB — à venir à l'étape 3)")
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Reprise après redémarrage du bot
# ---------------------------------------------------------------------------

async def restore_all_season_matches(bot: commands.Bot) -> int:
    """Ré-enregistre les boutons persistants encore actifs (aucun appel réseau).
    Limite connue : les messages de proposition/négociation intermédiaires
    (dates multiples en commun, ou aucune date en commun) ne sont pas
    ré-enregistrés individuellement — seuls le sélecteur de disponibilités,
    la dernière proposition en attente d'acceptation, et le bouton Prêt le sont."""
    if not os.path.exists(SEASON_MATCHES_DIR):
        return 0
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
        else:
            for side in ("home", "away"):
                if data.get(f"ready_{side}"):
                    continue
                msg_id = data.get(f"ready_msg_{side}_id")
                if msg_id:
                    bot.add_view(SeasonReadyView(thread_id, side), message_id=msg_id)
                    count += 1
    return count
