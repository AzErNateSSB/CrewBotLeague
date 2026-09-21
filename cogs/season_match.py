import discord
from discord.ext import commands
from datetime import date
import json
import os
from typing import Optional

SEASON_MATCHES_DIR = os.path.join("data", "season_matches")

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


def _is_authorized(user_id: int, side_sigle: str) -> bool:
    """Vrai si user_id est le leader ou un admin de side_sigle, ou l'admin bot."""
    from cogs.crewbattle import is_authorized, is_team_authorized
    from cogs.teams import load_team
    team = load_team(side_sigle)
    if not team:
        return is_authorized(user_id)
    return is_team_authorized(user_id, team)

# ---------------------------------------------------------------------------
# Point d'entrée : appelé à la création du thread de match (cf admin_panel.py)
# ---------------------------------------------------------------------------

NB_ACTIVE   = 5
NB_SUBS_MAX = 2


async def start_season_match(guild: discord.Guild, thread_id: int, league: str,
                               home_sigle: str, away_sigle: str):
    """Initialise l'enregistrement d'un match de saison et poste directement
    le sélecteur de composition (LineUp) dans les 2 salons tasks — il n'y a
    plus d'étape de sélection de date au préalable."""
    save_season_match(thread_id, {
        "thread_id": thread_id, "league": league,
        "home_sigle": home_sigle, "away_sigle": away_sigle,
        "confirmed_date": None,
        "roster_home": None, "roster_subs_home": None,
        "roster_away": None, "roster_subs_away": None,
        "ready_home": False, "ready_away": False,
        "roster_msg_home_id": None, "roster_msg_away_id": None,
        "ready_msg_home_id": None, "ready_msg_away_id": None,
    })
    await _start_roster_phase(guild, thread_id)


async def _start_roster_phase(guild: discord.Guild, thread_id: int):
    """Poste le sélecteur de composition (LineUp) dans les 2 salons tasks.
    Utilisé à la création du match, et pour rattraper automatiquement les
    matchs restés bloqués à l'ancienne étape de sélection de date."""
    from cogs.teams import load_team

    data = load_season_match(thread_id)
    if not data or data.get("confirmed_date"):
        return
    data["confirmed_date"] = date.today().isoformat()
    save_season_match(thread_id, data)

    home_ch, away_ch = await _tasks_channels(guild, data)
    home_team = load_team(data["home_sigle"])
    away_team = load_team(data["away_sigle"])

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
                f"📋 **CB de saison — {data['home_sigle']} 🆚 {data['away_sigle']}**\n"
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

# ---------------------------------------------------------------------------
# "Prêt"
# ---------------------------------------------------------------------------


async def _post_lineups_if_both_ready(bot, guild: discord.Guild, thread_id: int):
    """Poste le récap des 2 LineUp (joueurs + main) dans le salon de match dès
    que les 2 équipes ont envoyé leur composition. Idempotent (ne poste qu'une
    fois, marqué via data['lineups_posted'])."""
    data = load_season_match(thread_id)
    if not data or data.get("lineups_posted"):
        return
    if not data.get("roster_home") or not data.get("roster_away"):
        return

    from utils.players_stats import _get_thread
    from cogs.teams import load_player

    thread = await _get_thread(guild, thread_id)
    if not thread:
        return

    def _format_side(active_ids: list, sub_ids: list) -> str:
        lines = []
        for pid_str in active_ids:
            pid = int(pid_str)
            member = guild.get_member(pid)
            player = load_player(pid)
            main = player.get("stats", {}).get("main") if player else None
            name = member.mention if member else ((player or {}).get("name") or str(pid))
            lines.append(f"{main + ' ' if main else ''}{name}")
        if sub_ids:
            lines.append("*Remplaçant(s) :*")
            for pid_str in sub_ids:
                pid = int(pid_str)
                member = guild.get_member(pid)
                player = load_player(pid)
                main = player.get("stats", {}).get("main") if player else None
                name = member.mention if member else ((player or {}).get("name") or str(pid))
                lines.append(f"{main + ' ' if main else ''}{name}")
        return "\n".join(lines) if lines else "*Aucun joueur*"

    embed = discord.Embed(title="📋 LineUps confirmées", color=discord.Color.gold())
    embed.add_field(
        name=data["home_sigle"],
        value=_format_side(data["roster_home"], data.get("roster_subs_home") or []),
        inline=True,
    )
    embed.add_field(
        name=data["away_sigle"],
        value=_format_side(data["roster_away"], data.get("roster_subs_away") or []),
        inline=True,
    )

    try:
        await thread.send(embed=embed)
    except Exception:
        return

    data = load_season_match(thread_id)
    if data:
        data["lineups_posted"] = True
        save_season_match(thread_id, data)


async def analyze_season_matches(bot, guild: discord.Guild) -> str:
    """Parcourt tous les CB de saison en négociation/préparation, rattrape le
    post des LineUp dans le salon de match si les 2 compositions sont déjà
    envoyées mais que ça n'avait pas été fait, et retourne un rapport
    d'avancement (composition/prêt) par match."""
    if not os.path.exists(SEASON_MATCHES_DIR):
        return "Aucune CB de saison en cours."

    def _state(flag) -> str:
        return "✅" if flag else "❌"

    lines: list[str] = []
    posted = 0
    for fn in sorted(os.listdir(SEASON_MATCHES_DIR)):
        if not fn.endswith(".json"):
            continue
        thread_id = int(fn[:-5])
        data = load_season_match(thread_id)
        if not data:
            continue

        home, away = data["home_sigle"], data["away_sigle"]

        if not data.get("confirmed_date"):
            lines.append(f"⏳ **{home} 🆚 {away}** — date pas encore confirmée")
            continue

        if data.get("roster_home") and data.get("roster_away") and not data.get("lineups_posted"):
            await _post_lineups_if_both_ready(bot, guild, thread_id)
            data = load_season_match(thread_id) or data
            if data.get("lineups_posted"):
                posted += 1

        lines.append(
            f"**{home} 🆚 {away}** — Composition : {home} {_state(data.get('roster_home'))} "
            f"/ {away} {_state(data.get('roster_away'))} · "
            f"Prêt : {home} {_state(data.get('ready_home'))} / {away} {_state(data.get('ready_away'))}"
        )

    header = f"**{posted}** LineUp(s) rattrapée(s) et postée(s) dans leur salon de match.\n\n" if posted else ""
    return header + ("\n".join(lines) if lines else "Aucune CB de saison en cours.")


async def refresh_pending_rosters(guild: discord.Guild) -> str:
    """Efface et reposte les messages de composition (SeasonRosterSelectView)
    encore en attente, avec une liste de joueurs sélectionnables à jour —
    utile quand un joueur a rejoint une équipe après l'envoi du message
    (le menu déroulant est figé sur les membres au moment du post)."""
    if not os.path.exists(SEASON_MATCHES_DIR):
        return "Aucune CB de saison en cours."
    from cogs.teams import load_team

    lines: list[str] = []
    refreshed = 0
    for fn in sorted(os.listdir(SEASON_MATCHES_DIR)):
        if not fn.endswith(".json"):
            continue
        thread_id = int(fn[:-5])
        data = load_season_match(thread_id)
        if not data or not data.get("confirmed_date"):
            continue

        home_ch, away_ch = await _tasks_channels(guild, data)
        for side, ch in (("home", home_ch), ("away", away_ch)):
            if not ch or data.get(f"roster_{side}"):
                continue

            sigle = data.get(f"{side}_sigle", "")
            team = load_team(sigle)
            if not team:
                continue
            members = team.get("members", [])
            if len(members) < NB_ACTIVE:
                lines.append(
                    f"❌ **{sigle}** — seulement {len(members)} membre(s), "
                    f"il en faut au moins {NB_ACTIVE}."
                )
                continue

            old_msg_id = data.get(f"roster_msg_{side}_id")
            if old_msg_id:
                try:
                    old_msg = await ch.fetch_message(old_msg_id)
                    await old_msg.delete()
                except Exception:
                    pass

            options = []
            for mid in members[:25]:
                member = guild.get_member(mid)
                options.append(discord.SelectOption(
                    label=(member.display_name if member else str(mid))[:100], value=str(mid),
                ))

            view = SeasonRosterSelectView(thread_id, side, options)
            try:
                msg = await ch.send(
                    "🔄 Composition à refaire (liste des joueurs mise à jour) : "
                    f"**{NB_ACTIVE} titulaires** (obligatoire) "
                    f"+ jusqu'à **{NB_SUBS_MAX} remplaçant(s)** (optionnel) :",
                    view=view,
                )
                view.message = msg
            except Exception as e:
                lines.append(f"❌ **{sigle}** — envoi du nouveau message impossible ({e})")
                continue

            data2 = load_season_match(thread_id)
            if data2:
                data2[f"roster_msg_{side}_id"] = msg.id
                save_season_match(thread_id, data2)

            refreshed += 1
            lines.append(f"✅ **{sigle}** ({data['home_sigle']} 🆚 {data['away_sigle']}) — composition rafraîchie")

    header = f"**{refreshed}** composition(s) rafraîchie(s).\n\n" if refreshed else ""
    return header + ("\n".join(lines) if lines else "Aucune composition en attente à rafraîchir.")


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
            custom_id=f"season_ready_{thread_id}_{side}",
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
        self._base_options = options
        self.active_ids: list[str] = []
        self.sub_ids: list[str] = []

        self.active_select = discord.ui.Select(
            placeholder=f"Joueurs actifs ({NB_ACTIVE})",
            min_values=NB_ACTIVE, max_values=min(NB_ACTIVE, len(options)),
            options=self._mark_defaults(options, []),
            custom_id=f"season_roster_active_{thread_id}_{side}",
        )
        self.active_select.callback = self._on_active
        self.add_item(self.active_select)

        self.subs_select = discord.ui.Select(
            placeholder=f"Remplaçants (0 à {NB_SUBS_MAX})",
            min_values=0, max_values=min(NB_SUBS_MAX, len(options)),
            options=self._mark_defaults(options, []),
            custom_id=f"season_roster_subs_{thread_id}_{side}",
        )
        self.subs_select.callback = self._on_subs
        self.add_item(self.subs_select)

        self.confirm_btn = discord.ui.Button(
            label="✅ Valider la composition", style=discord.ButtonStyle.success, disabled=True,
            custom_id=f"season_roster_confirm_{thread_id}_{side}",
        )
        self.confirm_btn.callback = self._confirm
        self.add_item(self.confirm_btn)

    def _sigle(self) -> str:
        data = load_season_match(self.thread_id) or {}
        return data.get(f"{self.side}_sigle", "")

    @staticmethod
    def _mark_defaults(options: list[discord.SelectOption], selected: list[str]) -> list[discord.SelectOption]:
        return [
            discord.SelectOption(label=o.label, value=o.value, default=(o.value in selected))
            for o in options
        ]

    def _refresh_select_options(self):
        """Réaffiche la sélection courante dans les 2 menus (sinon ils semblent
        se vider après chaque choix, alors que l'état est bien conservé)."""
        self.active_select.options = self._mark_defaults(self._base_options, self.active_ids)
        self.subs_select.options   = self._mark_defaults(self._base_options, self.sub_ids)

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
        self._refresh_select_options()
        self._update_confirm_state()
        try:
            await interaction.response.edit_message(view=self)
        except discord.NotFound:
            # Interaction expirée (ex: rafale de messages posés juste avant par
            # une migration/rafraîchissement) — rien à faire, l'utilisateur peut
            # juste recliquer.
            pass

    async def _on_subs(self, interaction: discord.Interaction):
        if not _is_authorized(interaction.user.id, self._sigle()):
            await interaction.response.send_message("❌ Seul le leader peut composer l'équipe.", ephemeral=True)
            return
        self.sub_ids = interaction.data["values"]
        self._refresh_select_options()
        self._update_confirm_state()
        try:
            await interaction.response.edit_message(view=self)
        except discord.NotFound:
            pass

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
        try:
            await interaction.response.edit_message(view=self)
        except discord.NotFound:
            pass

        data[f"roster_{self.side}"] = self.active_ids
        data[f"roster_subs_{self.side}"] = self.sub_ids
        save_season_match(self.thread_id, data)

        await _post_lineups_if_both_ready(interaction.client, interaction.guild, self.thread_id)

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
        from cogs.crewbattle import is_authorized, PlayerSelectView, _sync_team_subs, save_matches

        team = self.match.team_a if self.side == "A" else self.match.team_b
        if not is_authorized(interaction.user.id, team.captain_id):
            await interaction.response.send_message("❌ Seul le leader peut agir ici.", ephemeral=True)
            return
        already = self.match.picked_a if self.side == "A" else self.match.picked_b
        if already:
            await interaction.response.send_message("✅ Joueur déjà soumis.", ephemeral=True)
            return

        _sync_team_subs(team, interaction.guild)
        save_matches()
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
    Fait aussi office de migration : tout match resté sur l'ancienne étape de
    sélection de date (supprimée) est automatiquement avancé à la phase de
    composition (LineUp)."""
    if not os.path.exists(SEASON_MATCHES_DIR):
        return 0
    await bot.wait_until_ready()
    from utils.config import GUILD_ID
    guild = bot.get_guild(GUILD_ID)

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
            if guild:
                await _start_roster_phase(guild, thread_id)
                count += 1
            continue

        roster_home = roster_away = None
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
            view = SeasonRosterSelectView(thread_id, side, options)
            bot.add_view(view, message_id=msg_id)
            count += 1

            # Même filet de rattrapage que pour les messages de dispos : réattache
            # une vue fraîche si les custom_id figés sur le message sont obsolètes.
            if roster_home is None and roster_away is None:
                roster_home, roster_away = await _tasks_channels(guild, data)
            ch = roster_home if side == "home" else roster_away
            if not ch:
                continue
            try:
                live_msg = await ch.fetch_message(msg_id)
                live_ids = {
                    getattr(c, "custom_id", None)
                    for row in live_msg.components
                    for c in getattr(row, "children", [])
                }
                expected_ids = {item.custom_id for item in view.children}
                if live_ids != expected_ids:
                    await live_msg.edit(view=view)
            except Exception:
                pass

        ready_home = ready_away = None
        for side in ("home", "away"):
            if not data.get(f"roster_{side}") or data.get(f"ready_{side}"):
                continue
            msg_id = data.get(f"ready_msg_{side}_id")
            if not msg_id:
                continue
            view = SeasonReadyView(thread_id, side)
            bot.add_view(view, message_id=msg_id)
            count += 1

            if ready_home is None and ready_away is None:
                ready_home, ready_away = await _tasks_channels(guild, data)
            ch = ready_home if side == "home" else ready_away
            if not ch:
                continue
            try:
                live_msg = await ch.fetch_message(msg_id)
                live_ids = {
                    getattr(c, "custom_id", None)
                    for row in live_msg.components
                    for c in getattr(row, "children", [])
                }
                expected_ids = {item.custom_id for item in view.children}
                if live_ids != expected_ids:
                    await live_msg.edit(view=view)
            except Exception:
                pass
    print(f"{count} vue(s) de CB de saison réenregistrée(s)")
    return count
