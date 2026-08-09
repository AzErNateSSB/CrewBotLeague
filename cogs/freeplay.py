import discord
from discord.ext import commands
from discord import app_commands
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import json
import re
import os

from cogs.teams import load_team
from utils.sheets_log import log_command
from utils.freeplay_data import save_freeplay_active, del_freeplay_active

CHANNEL_FREEPLAY = 1532382640223551640
FREEPLAY_DIR     = os.path.join("data", "freeplay")
PANELS_FILE      = os.path.join("data", "panels.json")
NOT_SET          = -99

DAYS_FR   = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
MONTHS_FR = ["Jan", "Fév", "Mar", "Avr", "Mai", "Juin",
             "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_slots() -> list[str]:
    today = datetime.now().date()
    slots = []
    for i in range(7):
        d = today + timedelta(days=i)
        label = f"{DAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]}"
        slots.append(f"{label} — Après-midi")
        slots.append(f"{label} — Soir")
    return slots


def get_led_teams(user_id: int) -> list[dict]:
    teams_dir = os.path.join("data", "teams")
    if not os.path.exists(teams_dir):
        return []
    result = []
    for fn in os.listdir(teams_dir):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(teams_dir, fn), encoding="utf-8") as f:
            t = json.load(f)
        if t.get("leader_id") == user_id:
            result.append(t)
    return result


def parse_mentions(text: str) -> list[int]:
    return [int(m) for m in re.findall(r"<@!?(\d+)>", text)]


def subs_split(nb_fight: int, subs_choice: int) -> tuple[int, int]:
    """Returns (nb_active, nb_subs) given the fight format and subs choice."""
    if subs_choice < 0:
        return max(1, nb_fight + subs_choice), abs(subs_choice)
    return nb_fight, subs_choice


# ---------------------------------------------------------------------------
# Persistence: matchmaking posts
# ---------------------------------------------------------------------------

def _fp_path(msg_id: int) -> str:
    return os.path.join(FREEPLAY_DIR, f"post_{msg_id}.json")

def save_fp_post(msg_id: int, data: dict):
    os.makedirs(FREEPLAY_DIR, exist_ok=True)
    with open(_fp_path(msg_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def del_fp_post(msg_id: int):
    p = _fp_path(msg_id)
    if os.path.exists(p):
        os.remove(p)

# ---------------------------------------------------------------------------
# In-memory setup state
# ---------------------------------------------------------------------------

@dataclass
class SideSetup:
    name: str
    sigle: str
    role_id: int
    captain_id: int
    nb_avail: int = NOT_SET
    subs_choice: int = NOT_SET
    players: list = field(default_factory=list)
    subs_list: list = field(default_factory=list)

@dataclass
class FreeplaySetup:
    category_id: int
    slot: str
    side_a: SideSetup
    side_b: SideSetup
    ch_tasks: int
    ch_a: int
    ch_b: int
    ch_general: int
    nb_fight: int = 0

_setups: dict[int, FreeplaySetup] = {}


def _setup_by_ch(ch_id: int) -> Optional[tuple[int, FreeplaySetup, str]]:
    for cat_id, s in _setups.items():
        if ch_id == s.ch_a:     return cat_id, s, "a"
        if ch_id == s.ch_b:     return cat_id, s, "b"
        if ch_id == s.ch_tasks: return cat_id, s, "tasks"
    return None


# ===========================================================================
# PANEL (persistent)
# ===========================================================================

class FreeplayPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚔️ Chercher un adversaire", style=discord.ButtonStyle.primary,
                       custom_id="panel_freeplay_search")
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        led = get_led_teams(interaction.user.id)
        if not led:
            await interaction.response.send_message(
                "❌ Tu n'es leader d'aucune équipe.", ephemeral=True
            )
            return
        if len(led) == 1:
            await interaction.response.send_message(
                f"Sélectionne tes disponibilités pour **{led[0]['sigle']}** :",
                view=DateSelectView(led[0]),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Pour quelle équipe cherches-tu un adversaire ?",
                view=TeamPickView(led, mode="search"),
                ephemeral=True,
            )


# ===========================================================================
# TEAM PICK (when leader leads multiple teams)
# ===========================================================================

class TeamPickView(discord.ui.View):
    def __init__(self, teams: list[dict], mode: str = "search",
                 post_data: Optional[dict] = None):
        super().__init__(timeout=120)
        self.mode = mode
        self.post_data = post_data
        for team in teams[:5]:
            btn = discord.ui.Button(label=team["sigle"], style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(team)
            self.add_item(btn)

    def _make_cb(self, team: dict):
        async def cb(interaction: discord.Interaction):
            if self.mode == "search":
                await interaction.response.edit_message(
                    content=f"Sélectionne tes disponibilités pour **{team['sigle']}** :",
                    view=DateSelectView(team),
                )
            else:
                await _confirm_matchup(interaction, self.post_data, team)
        return cb


# ===========================================================================
# DATE SELECT (ephemeral)
# ===========================================================================

class DateSelectView(discord.ui.View):
    def __init__(self, team: dict, selected: Optional[list[str]] = None):
        super().__init__(timeout=300)
        self.team     = team
        self.slots    = get_slots()
        self.selected = selected or []
        self._build()

    def _build(self):
        self.clear_items()
        select = discord.ui.Select(
            placeholder="Sélectionne tes disponibilités...",
            min_values=1,
            max_values=len(self.slots),
            options=[
                discord.SelectOption(label=s, value=str(i),
                                     default=(s in self.selected))
                for i, s in enumerate(self.slots)
            ],
        )
        select.callback = self._on_select
        self.add_item(select)

        confirm = discord.ui.Button(
            label="✅ Publier ma recherche",
            style=discord.ButtonStyle.success,
            disabled=not self.selected,
        )
        confirm.callback = self._confirm
        self.add_item(confirm)

    async def _on_select(self, interaction: discord.Interaction):
        indices = [int(v) for v in interaction.data["values"]]
        self.selected = [self.slots[i] for i in indices]
        self._build()
        await interaction.response.edit_message(
            content=f"**{len(self.selected)}** créneaux sélectionnés — publie quand tu es prêt !",
            view=self,
        )

    async def _confirm(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        freeplay_ch = interaction.guild.get_channel(CHANNEL_FREEPLAY)
        if not freeplay_ch:
            await interaction.followup.send("❌ Salon freeplay introuvable.", ephemeral=True)
            return

        team = self.team
        embed = discord.Embed(
            title=f"🔍 {team['sigle']} cherche un adversaire !",
            description=(
                "Cliquez sur un créneau pour accepter l'affrontement.\n"
                "*Seul le leader d'une équipe peut répondre.*"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Disponibilités",
            value="\n".join(f"• {s}" for s in self.selected),
            inline=False,
        )

        view = MatchmakingView(team["sigle"], team["leader_id"], self.selected)
        msg = await freeplay_ch.send(embed=embed, view=view)
        save_fp_post(msg.id, {
            "msg_id":      msg.id,
            "team_sigle":  team["sigle"],
            "leader_id":   team["leader_id"],
            "slots":       self.selected,
        })

        await interaction.edit_original_response(
            content=f"✅ Recherche publiée ! {msg.jump_url}",
            view=None,
        )


# ===========================================================================
# MATCHMAKING VIEW (in freeplay channel)
# ===========================================================================

class MatchmakingView(discord.ui.View):
    def __init__(self, team_sigle: str, leader_id: int, slots: list[str]):
        super().__init__(timeout=None)
        self.team_sigle = team_sigle
        self.leader_id  = leader_id
        self.slots      = slots
        for slot in slots:
            btn = discord.ui.Button(label=slot, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(slot)
            self.add_item(btn)

    def _make_cb(self, slot: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id == self.leader_id:
                await interaction.response.send_message(
                    "❌ Tu ne peux pas répondre à ta propre recherche.", ephemeral=True
                )
                return
            led = [t for t in get_led_teams(interaction.user.id)
                   if t["sigle"] != self.team_sigle]
            if not led:
                await interaction.response.send_message(
                    "❌ Tu n'es leader d'aucune autre équipe.", ephemeral=True
                )
                return
            post_data = {
                "msg_id":     interaction.message.id,
                "team_sigle": self.team_sigle,
                "leader_id":  self.leader_id,
                "slot":       slot,
            }
            if len(led) == 1:
                await _confirm_matchup(interaction, post_data, led[0])
            else:
                await interaction.response.send_message(
                    "Pour quelle équipe acceptes-tu ce match ?",
                    view=TeamPickView(led, mode="respond", post_data=post_data),
                    ephemeral=True,
                )
        return cb


# ===========================================================================
# CONFIRM MATCHUP → création catégorie + channels
# ===========================================================================

async def _confirm_matchup(interaction: discord.Interaction,
                            post_data: dict, team_b_data: dict):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    guild     = interaction.guild
    slot      = post_data["slot"]
    team_a_data = load_team(post_data["team_sigle"])
    if not team_a_data:
        await interaction.followup.send("❌ L'équipe cherchante est introuvable.", ephemeral=True)
        return

    role_a = guild.get_role(team_a_data.get("role_id", 0))
    role_b = guild.get_role(team_b_data.get("role_id", 0))
    cap_a  = guild.get_member(team_a_data["leader_id"])
    cap_b  = guild.get_member(team_b_data["leader_id"])

    # ── Overwrites ────────────────────────────────────────────────────────────
    bot_ow = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                          manage_channels=True, read_message_history=True)
    deny   = discord.PermissionOverwrite(view_channel=False)

    def team_ow(role, cap, write=True):
        ow = {}
        if role:
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=write,
                                                    read_message_history=True)
        elif cap:
            ow[cap] = discord.PermissionOverwrite(view_channel=True, send_messages=write,
                                                   read_message_history=True)
        return ow

    cat_ow = {guild.default_role: deny, guild.me: bot_ow}
    if role_a: cat_ow[role_a] = discord.PermissionOverwrite(view_channel=True)
    if role_b: cat_ow[role_b] = discord.PermissionOverwrite(view_channel=True)
    if not role_a and cap_a: cat_ow[cap_a] = discord.PermissionOverwrite(view_channel=True)
    if not role_b and cap_b: cat_ow[cap_b] = discord.PermissionOverwrite(view_channel=True)

    # ── Créer la catégorie ───────────────────────────────────────────────────
    cat_name = f"Freeplay 〔{post_data['team_sigle']}〕vs〔{team_b_data['sigle']}〕"
    try:
        category = await guild.create_category(cat_name, overwrites=cat_ow)
    except Exception as e:
        await interaction.followup.send(f"❌ Erreur : {e}", ephemeral=True)
        return

    tasks_ow = {guild.default_role: deny, guild.me: bot_ow,
                **team_ow(role_a, cap_a), **team_ow(role_b, cap_b)}
    ch_a_ow  = {guild.default_role: deny, guild.me: bot_ow, **team_ow(role_a, cap_a)}
    ch_b_ow  = {guild.default_role: deny, guild.me: bot_ow, **team_ow(role_b, cap_b)}
    gen_ow   = {guild.default_role: deny, guild.me: bot_ow,
                **team_ow(role_a, cap_a), **team_ow(role_b, cap_b)}

    ch_tasks   = await guild.create_text_channel("tasks",                          category=category, overwrites=tasks_ow)
    ch_a       = await guild.create_text_channel(f"〔{post_data['team_sigle']}〕", category=category, overwrites=ch_a_ow)
    ch_b       = await guild.create_text_channel(f"〔{team_b_data['sigle']}〕",    category=category, overwrites=ch_b_ow)
    ch_general = await guild.create_text_channel("général",                        category=category, overwrites=gen_ow)

    # ── Setup en mémoire ─────────────────────────────────────────────────────
    side_a = SideSetup(name=post_data["team_sigle"], sigle=post_data["team_sigle"],
                       role_id=team_a_data.get("role_id", 0),
                       captain_id=team_a_data["leader_id"])
    side_b = SideSetup(name=team_b_data["sigle"], sigle=team_b_data["sigle"],
                       role_id=team_b_data.get("role_id", 0),
                       captain_id=team_b_data["leader_id"])
    setup = FreeplaySetup(
        category_id=category.id, slot=slot,
        side_a=side_a, side_b=side_b,
        ch_tasks=ch_tasks.id, ch_a=ch_a.id,
        ch_b=ch_b.id, ch_general=ch_general.id,
    )
    _setups[category.id] = setup

    # ── Messages d'accueil ───────────────────────────────────────────────────
    await ch_tasks.send(
        f"⚔️ **Freeplay : {side_a.name} vs {side_b.name}**\n"
        f"📅 Créneau : **{slot}**\n\n"
        "En attente de la configuration des équipes..."
    )
    await ch_general.send(
        f"🎮 Bienvenue dans ce match Freeplay !\n"
        f"**{side_a.name}** vs **{side_b.name}** — {slot}\n\n"
        f"La CB sera lancée depuis {ch_tasks.mention}."
    )

    # ── Demander le nombre de joueurs ────────────────────────────────────────
    await ch_a.send(
        f"{cap_a.mention if cap_a else ''} — "
        f"Combien de joueurs **{side_a.name}** a-t-il de disponibles ?",
        view=PlayerCountView(category.id, "a"),
    )
    await ch_b.send(
        f"{cap_b.mention if cap_b else ''} — "
        f"Combien de joueurs **{side_b.name}** a-t-il de disponibles ?",
        view=PlayerCountView(category.id, "b"),
    )

    # ── Supprimer l'annonce matchmaking ──────────────────────────────────────
    try:
        freeplay_ch = guild.get_channel(CHANNEL_FREEPLAY)
        if freeplay_ch:
            old_msg = await freeplay_ch.fetch_message(post_data["msg_id"])
            await old_msg.delete()
    except Exception:
        pass
    del_fp_post(post_data["msg_id"])

    await interaction.followup.send(
        f"✅ Match accepté ! La catégorie **{cat_name}** a été créée.",
        ephemeral=True,
    )


# ===========================================================================
# PLAYER COUNT
# ===========================================================================

class PlayerCountModal(discord.ui.Modal, title="Joueurs disponibles"):
    count = discord.ui.TextInput(
        label="Nombre de joueurs disponibles",
        placeholder="Ex: 5",
        min_length=1, max_length=2,
    )

    def __init__(self, category_id: int, side: str):
        super().__init__()
        self.category_id = category_id
        self.side        = side

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.count.value.strip())
            if not (1 <= n <= 20):
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "❌ Nombre invalide (entre 1 et 20).", ephemeral=True
            )
            return

        setup = _setups.get(self.category_id)
        if not setup:
            await interaction.response.send_message("❌ Session expirée.", ephemeral=True)
            return

        side_obj = setup.side_a if self.side == "a" else setup.side_b
        side_obj.nb_avail = n

        await interaction.response.send_message(f"✅ {n} joueurs enregistrés.", ephemeral=True)
        await interaction.channel.send(f"✅ **{side_obj.name}** : **{n}** joueurs disponibles.")
        await _check_player_counts(interaction.client, interaction.guild, setup)


class PlayerCountView(discord.ui.View):
    def __init__(self, category_id: int, side: str):
        super().__init__(timeout=None)
        self.category_id = category_id
        self.side        = side

    @discord.ui.button(label="📝 Entrer un nombre", style=discord.ButtonStyle.primary)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        setup = _setups.get(self.category_id)
        if not setup:
            await interaction.response.send_message("❌ Session expirée.", ephemeral=True)
            return
        side_obj = setup.side_a if self.side == "a" else setup.side_b
        if interaction.user.id != side_obj.captain_id:
            await interaction.response.send_message(
                "❌ Seul le leader peut répondre.", ephemeral=True
            )
            return
        if side_obj.nb_avail != NOT_SET:
            await interaction.response.send_message("✅ Déjà renseigné.", ephemeral=True)
            return
        await interaction.response.send_modal(PlayerCountModal(self.category_id, self.side))


async def _check_player_counts(bot, guild, setup: FreeplaySetup):
    if setup.side_a.nb_avail == NOT_SET or setup.side_b.nb_avail == NOT_SET:
        return

    nb_fight = min(setup.side_a.nb_avail, setup.side_b.nb_avail)
    setup.nb_fight = nb_fight

    ch_tasks = guild.get_channel(setup.ch_tasks)
    ch_a     = guild.get_channel(setup.ch_a)
    ch_b     = guild.get_channel(setup.ch_b)

    if ch_tasks:
        await ch_tasks.send(
            f"📊 **Format retenu : {nb_fight}v{nb_fight}**\n"
            f"({setup.side_a.name} : {setup.side_a.nb_avail} dispo | "
            f"{setup.side_b.name} : {setup.side_b.nb_avail} dispo)"
        )
    if ch_a:
        await ch_a.send(
            f"⚔️ La CrewBattle se jouera en **{nb_fight}v{nb_fight}**.\n"
            f"Combien de remplaçants souhaitez-vous ?",
            view=SubsView(setup.category_id, "a", nb_fight),
        )
    if ch_b:
        await ch_b.send(
            f"⚔️ La CrewBattle se jouera en **{nb_fight}v{nb_fight}**.\n"
            f"Combien de remplaçants souhaitez-vous ?",
            view=SubsView(setup.category_id, "b", nb_fight),
        )


# ===========================================================================
# SUBS
# ===========================================================================

SUBS_OPTS = [
    (-2, "−2 actifs  (+2 subs parmi les dispo)"),
    (-1, "−1 actif   (+1 sub parmi les dispo)"),
    ( 0, "0 remplaçant"),
    ( 1, "+1 remplaçant supplémentaire"),
    ( 2, "+2 remplaçants supplémentaires"),
]


class SubsView(discord.ui.View):
    def __init__(self, category_id: int, side: str, nb_fight: int):
        super().__init__(timeout=None)
        self.category_id = category_id
        self.side        = side
        self.nb_fight    = nb_fight
        for val, label in SUBS_OPTS:
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(val)
            self.add_item(btn)

    def _make_cb(self, val: int):
        async def cb(interaction: discord.Interaction):
            setup = _setups.get(self.category_id)
            if not setup:
                await interaction.response.send_message("❌ Session expirée.", ephemeral=True)
                return
            side_obj = setup.side_a if self.side == "a" else setup.side_b
            if interaction.user.id != side_obj.captain_id:
                await interaction.response.send_message(
                    "❌ Seul le leader peut répondre.", ephemeral=True
                )
                return
            if side_obj.subs_choice != NOT_SET:
                await interaction.response.send_message("✅ Déjà renseigné.", ephemeral=True)
                return

            side_obj.subs_choice = val
            nb_active, nb_subs = subs_split(self.nb_fight, val)

            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.channel.send(
                f"✅ **{side_obj.name}** : **{nb_active}** joueurs actifs"
                + (f" + **{nb_subs}** remplaçant(s)" if nb_subs else "") + "."
            )
            await _check_subs(interaction.client, interaction.guild, setup)
        return cb


async def _check_subs(bot, guild, setup: FreeplaySetup):
    if setup.side_a.subs_choice == NOT_SET or setup.side_b.subs_choice == NOT_SET:
        return

    ch_a = guild.get_channel(setup.ch_a)
    ch_b = guild.get_channel(setup.ch_b)

    for ch, side_str in [(ch_a, "a"), (ch_b, "b")]:
        if not ch:
            continue
        side_obj = setup.side_a if side_str == "a" else setup.side_b
        nb_active, nb_subs = subs_split(setup.nb_fight, side_obj.subs_choice)
        desc = f"**{nb_active}** actif(s)"
        if nb_subs:
            desc += f" + **{nb_subs}** remplaçant(s)"
        await ch.send(
            f"🎯 Composition de **{side_obj.name}** : {desc}\n"
            "Entrez maintenant votre équipe.",
            view=RosterView(setup.category_id, side_str, nb_active, nb_subs),
        )


# ===========================================================================
# ROSTER
# ===========================================================================

class RosterModal(discord.ui.Modal, title="Composition de l'équipe"):
    def __init__(self, category_id: int, side: str, nb_active: int, nb_subs: int):
        super().__init__()
        self.category_id = category_id
        self.side        = side
        self.nb_active   = nb_active
        self.nb_subs     = nb_subs

        self.team_field = discord.ui.TextInput(
            label="Nom ou @rôle de l'équipe",
            placeholder="Ex: HoJ   ou   @[HoJ]",
            max_length=100,
        )
        self.players_field = discord.ui.TextInput(
            label=f"Joueurs actifs ({nb_active}) — mentionnez-les",
            placeholder="@Joueur1, @Joueur2...",
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self.team_field)
        self.add_item(self.players_field)

        if nb_subs > 0:
            self.subs_field: Optional[discord.ui.TextInput] = discord.ui.TextInput(
                label=f"Remplaçants ({nb_subs}) — mentionnez-les",
                placeholder="@Remplaçant1...",
                style=discord.TextStyle.paragraph,
                max_length=300,
                required=False,
            )
            self.add_item(self.subs_field)
        else:
            self.subs_field = None

    async def on_submit(self, interaction: discord.Interaction):
        from cogs.crewbattle import Player

        setup = _setups.get(self.category_id)
        if not setup:
            await interaction.response.send_message("❌ Session expirée.", ephemeral=True)
            return

        guild    = interaction.guild
        side_obj = setup.side_a if self.side == "a" else setup.side_b

        # Nom ou rôle
        team_input = self.team_field.value.strip()
        role_match = re.match(r"<@&(\d+)>", team_input)
        if role_match:
            role = guild.get_role(int(role_match.group(1)))
            side_obj.name    = role.name if role else team_input
            side_obj.role_id = int(role_match.group(1))
        elif team_input:
            side_obj.name = team_input

        # Joueurs actifs
        ids = parse_mentions(self.players_field.value)
        if len(ids) < self.nb_active:
            await interaction.response.send_message(
                f"❌ Il faut {self.nb_active} joueur(s) actif(s), "
                f"tu en as mentionné {len(ids)}.",
                ephemeral=True,
            )
            return

        players = []
        for disc_id in ids[:self.nb_active]:
            m = guild.get_member(disc_id)
            players.append(Player(name=m.display_name if m else str(disc_id),
                                  discord_id=disc_id))
        side_obj.players = players

        # Remplaçants
        subs = []
        if self.subs_field and self.subs_field.value:
            for disc_id in parse_mentions(self.subs_field.value)[:self.nb_subs]:
                m = guild.get_member(disc_id)
                subs.append(Player(name=m.display_name if m else str(disc_id),
                                   discord_id=disc_id))
        side_obj.subs_list = subs

        # Accès tasks pour les joueurs mentionnés
        ch_tasks = guild.get_channel(setup.ch_tasks)
        if ch_tasks:
            for p in side_obj.players + side_obj.subs_list:
                member = guild.get_member(p.discord_id)
                if member:
                    try:
                        await ch_tasks.set_permissions(
                            member, view_channel=True, read_message_history=True,
                            send_messages=False,
                        )
                    except Exception:
                        pass

        await interaction.response.send_message(
            f"✅ **{side_obj.name}** enregistrée !\n"
            f"Actifs : {', '.join(p.name for p in side_obj.players)}"
            + (f"\nRemplaçants : {', '.join(p.name for p in side_obj.subs_list)}"
               if side_obj.subs_list else ""),
            ephemeral=True,
        )
        await interaction.channel.send(
            f"✅ Équipe **{side_obj.name}** prête !\n"
            f"Actifs : {', '.join(p.name for p in side_obj.players)}"
            + (f" | Remplaçants : {', '.join(p.name for p in side_obj.subs_list)}"
               if side_obj.subs_list else "")
        )
        await _check_rosters(interaction.client, guild, setup)


class RosterView(discord.ui.View):
    def __init__(self, category_id: int, side: str, nb_active: int, nb_subs: int):
        super().__init__(timeout=None)
        self.category_id = category_id
        self.side        = side
        self.nb_active   = nb_active
        self.nb_subs     = nb_subs

    @discord.ui.button(label="📋 Entrer l'équipe", style=discord.ButtonStyle.primary)
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        setup = _setups.get(self.category_id)
        if not setup:
            await interaction.response.send_message("❌ Session expirée.", ephemeral=True)
            return
        side_obj = setup.side_a if self.side == "a" else setup.side_b
        if interaction.user.id != side_obj.captain_id:
            await interaction.response.send_message(
                "❌ Seul le leader peut entrer l'équipe.", ephemeral=True
            )
            return
        if side_obj.players:
            await interaction.response.send_message("✅ Équipe déjà enregistrée.", ephemeral=True)
            return
        await interaction.response.send_modal(
            RosterModal(self.category_id, self.side, self.nb_active, self.nb_subs)
        )


# ===========================================================================
# LANCEMENT DE LA CREWBATTLE
# ===========================================================================

async def _check_rosters(bot, guild, setup: FreeplaySetup):
    if not setup.side_a.players or not setup.side_b.players:
        return

    from cogs.crewbattle import Team, Match, active_matches, save_matches, FirstPickView

    ch_tasks = guild.get_channel(setup.ch_tasks)
    if not ch_tasks:
        return

    ta = Team(name=setup.side_a.name, captain_id=setup.side_a.captain_id,
              players=setup.side_a.players, subs=setup.side_a.subs_list)
    tb = Team(name=setup.side_b.name, captain_id=setup.side_b.captain_id,
              players=setup.side_b.players, subs=setup.side_b.subs_list)

    match = Match(team_a=ta, team_b=tb, channel_id=ch_tasks.id)
    active_matches[ch_tasks.id] = match

    match.log_row = await log_command(
        "Freeplay",
        f"freeplay **{ta.name}** vs **{tb.name}**",
        "In Progress",
        f"Freeplay CB {ta.name} vs {tb.name} — {setup.slot}",
    )
    save_matches()

    # Sauvegarder pour le résumé post-match
    save_freeplay_active(ch_tasks.id, {
        "channel_id":   ch_tasks.id,
        "category_id":  setup.category_id,
        "team_a_sigle": setup.side_a.sigle,
        "team_b_sigle": setup.side_b.sigle,
    })

    # Libérer la mémoire du setup
    del _setups[setup.category_id]

    def team_val(team: Team) -> str:
        lines = [f"• {p.name}" for p in team.players]
        if team.subs:
            lines.append(f"*Remplaçants : {', '.join(p.name for p in team.subs)}*")
        return "\n".join(lines)

    cap_a = guild.get_member(ta.captain_id)
    cap_b = guild.get_member(tb.captain_id)

    embed = discord.Embed(
        title=f"⚔️ Freeplay — {ta.name} vs {tb.name}",
        color=discord.Color.blue(),
    )
    embed.add_field(name=f"{ta.name}  (cap. {cap_a.display_name if cap_a else '?'})",
                    value=team_val(ta), inline=True)
    embed.add_field(name=f"{tb.name}  (cap. {cap_b.display_name if cap_b else '?'})",
                    value=team_val(tb), inline=True)
    embed.add_field(name="Créneau", value=setup.slot, inline=False)

    msg = await ch_tasks.send(embed=embed)
    try:
        await msg.pin()
    except Exception:
        pass

    view = FirstPickView(match=match)
    pick_msg = await ch_tasks.send(
        f"📢 {cap_a.mention if cap_a else f'<@{ta.captain_id}>'} ({ta.name}) "
        f"et {cap_b.mention if cap_b else f'<@{tb.captain_id}>'} ({tb.name}), "
        f"choisissez votre premier joueur !",
        view=view,
    )
    view.message = pick_msg


# ===========================================================================
# /cbl_setup_freeplay  +  Cog
# ===========================================================================

@app_commands.command(name="cbl_setup_freeplay",
                      description="[ADMIN] Poste le panel de recherche Freeplay")
async def cbl_setup_freeplay(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    ch    = guild.get_channel(CHANNEL_FREEPLAY)
    if not ch:
        await interaction.followup.send(
            f"❌ Salon freeplay introuvable (ID {CHANNEL_FREEPLAY}).", ephemeral=True
        )
        return

    # Vérifier si le panel existe déjà
    if not os.path.exists(PANELS_FILE):
        panels = {}
    else:
        with open(PANELS_FILE, encoding="utf-8") as f:
            panels = json.load(f)

    existing_id = panels.get("freeplay_panel_msg")
    if existing_id:
        try:
            await ch.fetch_message(existing_id)
            await interaction.followup.send(
                "✓ Panel déjà présent — rien à faire.", ephemeral=True
            )
            return
        except discord.NotFound:
            pass

    embed = discord.Embed(
        title="⚔️ Freeplay CrewBattle",
        description=(
            "Tu veux faire une CrewBattle en dehors du calendrier officiel ?\n\n"
            "Clique sur le bouton ci-dessous pour chercher un adversaire.\n"
            "Le bot te proposera des créneaux sur les **7 prochains jours**.\n\n"
            "*(Réservé aux leaders d'équipe)*"
        ),
        color=discord.Color.red(),
    )
    msg = await ch.send(embed=embed, view=FreeplayPanelView())

    panels["freeplay_panel_msg"] = msg.id
    os.makedirs("data", exist_ok=True)
    with open(PANELS_FILE, "w", encoding="utf-8") as f:
        json.dump(panels, f, indent=2)

    await interaction.followup.send(f"✅ Panel freeplay posté dans {ch.mention}.", ephemeral=True)


class Freeplay(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(FreeplayPanelView())
        self.bot.tree.add_command(cbl_setup_freeplay)


async def setup(bot: commands.Bot):
    await bot.add_cog(Freeplay(bot))
