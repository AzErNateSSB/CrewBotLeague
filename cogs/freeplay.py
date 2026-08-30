import discord
from discord.ext import commands
from discord import app_commands
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import json
import os

from cogs.teams import load_team
from utils.sheets_log import log_command
from utils.freeplay_data import save_freeplay_active, del_freeplay_active, load_freeplay_active

CHANNEL_FREEPLAY = 1532382640223551640
FREEPLAY_DIR     = os.path.join("data", "freeplay")
PANELS_FILE      = os.path.join("data", "panels.json")
FREEPLAY_SETUPS_FILE = os.path.join("data", "freeplay_setups.json")
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

# ---------------------------------------------------------------------------
# Persistance des setups Freeplay en cours (survit à un redémarrage du bot)
# ---------------------------------------------------------------------------

def _side_to_dict(s: SideSetup) -> dict:
    return {
        "name": s.name, "sigle": s.sigle,
        "role_id": s.role_id, "captain_id": s.captain_id,
        "nb_avail": s.nb_avail, "subs_choice": s.subs_choice,
        "players":   [{"name": p.name, "discord_id": p.discord_id} for p in s.players],
        "subs_list": [{"name": p.name, "discord_id": p.discord_id} for p in s.subs_list],
    }

def _side_from_dict(d: dict) -> SideSetup:
    from cogs.crewbattle import Player
    return SideSetup(
        name=d["name"], sigle=d["sigle"],
        role_id=d["role_id"], captain_id=d["captain_id"],
        nb_avail=d.get("nb_avail", NOT_SET), subs_choice=d.get("subs_choice", NOT_SET),
        players   =[Player(name=p["name"], discord_id=p["discord_id"]) for p in d.get("players", [])],
        subs_list =[Player(name=p["name"], discord_id=p["discord_id"]) for p in d.get("subs_list", [])],
    )

def _freeplay_setup_to_dict(s: FreeplaySetup) -> dict:
    return {
        "category_id": s.category_id, "slot": s.slot,
        "side_a": _side_to_dict(s.side_a), "side_b": _side_to_dict(s.side_b),
        "ch_tasks": s.ch_tasks, "ch_a": s.ch_a, "ch_b": s.ch_b, "ch_general": s.ch_general,
        "nb_fight": s.nb_fight,
    }

def _freeplay_setup_from_dict(d: dict) -> FreeplaySetup:
    return FreeplaySetup(
        category_id=d["category_id"], slot=d["slot"],
        side_a=_side_from_dict(d["side_a"]), side_b=_side_from_dict(d["side_b"]),
        ch_tasks=d["ch_tasks"], ch_a=d["ch_a"], ch_b=d["ch_b"], ch_general=d["ch_general"],
        nb_fight=d.get("nb_fight", 0),
    )

def _save_setups():
    try:
        data = {str(cat_id): _freeplay_setup_to_dict(s) for cat_id, s in _setups.items()}
        os.makedirs("data", exist_ok=True)
        with open(FREEPLAY_SETUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] save freeplay setups: {e}")

def _load_setups_from_file() -> dict[int, FreeplaySetup]:
    if not os.path.exists(FREEPLAY_SETUPS_FILE):
        return {}
    try:
        with open(FREEPLAY_SETUPS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {int(cat_id): _freeplay_setup_from_dict(s) for cat_id, s in data.items()}
    except Exception as e:
        print(f"[WARN] load freeplay setups: {e}")
        return {}

def _delete_setup(category_id: int):
    _setups.pop(category_id, None)
    _save_setups()

# ---------------------------------------------------------------------------
# Persistance des posts de recherche publics (matchmaking)
# ---------------------------------------------------------------------------

def _load_fp_posts() -> dict[int, dict]:
    if not os.path.exists(FREEPLAY_DIR):
        return {}
    result = {}
    for fn in os.listdir(FREEPLAY_DIR):
        if not fn.startswith("post_") or not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(FREEPLAY_DIR, fn), encoding="utf-8") as f:
                data = json.load(f)
            result[data["msg_id"]] = data
        except Exception as e:
            print(f"[WARN] load fp post {fn}: {e}")
    return result


# ===========================================================================
# PANEL (persistent)
# ===========================================================================

class FreeplayPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚔️ Chercher un adversaire", style=discord.ButtonStyle.primary,
                       custom_id="panel_freeplay_search")
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.teams import find_team_of_player
        sigle = find_team_of_player(interaction.user.id)
        current_team = load_team(sigle) if sigle else None

        if not current_team:
            # Pas dans une équipe → équipe personnalisée d'emblée, pas de choix à faire.
            await interaction.response.send_modal(
                CustomTeamNameModal(mode="search", post_data=None, user_id=interaction.user.id)
            )
            return

        await interaction.response.send_message(
            "Avec quelle équipe veux-tu chercher un adversaire ?",
            view=TeamTypeChoiceView(interaction.user.id, current_team, mode="search"),
            ephemeral=True,
        )


# ===========================================================================
# CHOIX DU TYPE D'ÉQUIPE (équipe actuelle vs équipe personnalisée)
# ===========================================================================

class TeamTypeChoiceView(discord.ui.View):
    """Étape 'avec quelle équipe ?' — proposée à la recherche comme à la réponse.
    mode='search' (lancer une recherche) ou 'respond' (répondre à une recherche)."""

    def __init__(self, user_id: int, current_team: Optional[dict], mode: str,
                 post_data: Optional[dict] = None):
        super().__init__(timeout=180)
        self.user_id      = user_id
        self.current_team = current_team
        self.mode         = mode
        self.post_data    = post_data

        if current_team:
            cur_btn = discord.ui.Button(
                label=f"🏠 Mon équipe actuelle ({current_team['sigle']})",
                style=discord.ButtonStyle.primary,
            )
            cur_btn.callback = self._pick_current
            self.add_item(cur_btn)

        custom_btn = discord.ui.Button(
            label="🎭 Équipe personnalisée",
            style=discord.ButtonStyle.secondary,
        )
        custom_btn.callback = self._pick_custom
        self.add_item(custom_btn)

    async def _pick_current(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ce choix ne t'est pas destiné.", ephemeral=True)
            return

        team = self.current_team
        if interaction.user.id == team["leader_id"]:
            if self.mode == "search":
                await interaction.response.edit_message(
                    content=f"Sélectionne tes disponibilités pour **{team['sigle']}** :",
                    view=DateSelectView(team),
                )
            else:
                await _confirm_matchup(interaction, self.post_data, team)
            return

        # Pas leader → il faut l'accord du leader avant d'engager l'équipe.
        if self.mode == "search":
            await interaction.response.send_message(
                "Sélectionne tes disponibilités (elles seront proposées à ton leader pour confirmation) :",
                view=DateSelectView(team, needs_leader_ok=True),
                ephemeral=True,
            )
        else:
            opponent_sigle = self.post_data["team_sigle"]
            description = (
                f"**{interaction.user.display_name}** souhaite affronter **{opponent_sigle}** "
                f"le **{self.post_data['slot']}** avec **{team['sigle']}**. Confirmer ?"
            )
            await _request_team_confirm(
                interaction, team, action="respond",
                payload={"post_data": self.post_data}, description=description,
            )

    async def _pick_custom(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ce choix ne t'est pas destiné.", ephemeral=True)
            return
        await interaction.response.send_modal(
            CustomTeamNameModal(mode=self.mode, post_data=self.post_data, user_id=interaction.user.id)
        )


class CustomTeamNameModal(discord.ui.Modal, title="Équipe personnalisée"):
    name = discord.ui.TextInput(
        label="Nom de l'équipe",
        placeholder="Ex: Les Invincibles",
        max_length=50,
    )

    def __init__(self, mode: str, post_data: Optional[dict], user_id: int):
        super().__init__()
        self.mode      = mode
        self.post_data = post_data
        self.user_id   = user_id

    async def on_submit(self, interaction: discord.Interaction):
        team = {"sigle": self.name.value.strip(), "leader_id": self.user_id}
        if self.mode == "search":
            await interaction.response.send_message(
                f"Sélectionne tes disponibilités pour **{team['sigle']}** (équipe personnalisée) :",
                view=DateSelectView(team),
                ephemeral=True,
            )
        else:
            await _confirm_matchup(interaction, self.post_data, team)


# ===========================================================================
# CONFIRMATION DU LEADER (quand un non-leader engage l'équipe actuelle)
# ===========================================================================

FREEPLAY_TEAM_CONFIRM_DIR = os.path.join("data", "freeplay_team_confirms")


def _team_confirm_path(msg_id: int) -> str:
    return os.path.join(FREEPLAY_TEAM_CONFIRM_DIR, f"{msg_id}.json")

def _save_team_confirm(msg_id: int, data: dict):
    os.makedirs(FREEPLAY_TEAM_CONFIRM_DIR, exist_ok=True)
    with open(_team_confirm_path(msg_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_team_confirm(msg_id: int) -> Optional[dict]:
    p = _team_confirm_path(msg_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def _del_team_confirm(msg_id: int):
    p = _team_confirm_path(msg_id)
    if os.path.exists(p):
        os.remove(p)


async def _request_team_confirm(interaction: discord.Interaction, team: dict, action: str,
                                  payload: dict, description: str):
    tasks_ch = interaction.guild.get_channel(team["channels"].get("tasks") or team["channels"]["general"])
    if not tasks_ch:
        await interaction.response.send_message("❌ Salon tasks de l'équipe introuvable.", ephemeral=True)
        return

    leader = interaction.guild.get_member(team["leader_id"])
    leader_mention = leader.mention if leader else f"<@{team['leader_id']}>"

    await interaction.response.send_message(
        f"📨 Demande envoyée à {leader_mention} dans {tasks_ch.mention} — en attente de confirmation.",
        ephemeral=True,
    )

    msg = await tasks_ch.send(f"{leader_mention} — {description}")
    view = TeamConfirmView(msg.id)
    await msg.edit(view=view)
    _save_team_confirm(msg.id, {
        "action": action, "team_sigle": team["sigle"],
        "requester_id": interaction.user.id, "channel_id": tasks_ch.id,
        **payload,
    })


class TeamConfirmView(discord.ui.View):
    """Boutons Accepter/Refuser postés dans le salon tasks quand un non-leader
    veut engager l'équipe (recherche ou réponse à une recherche Freeplay)."""

    def __init__(self, msg_id: int):
        super().__init__(timeout=None)
        self.msg_id = msg_id

        accept = discord.ui.Button(
            label="✅ Accepter", style=discord.ButtonStyle.success,
            custom_id=f"fp_teamconfirm_accept_{msg_id}",
        )
        accept.callback = self._accept
        self.add_item(accept)

        refuse = discord.ui.Button(
            label="❌ Refuser", style=discord.ButtonStyle.danger,
            custom_id=f"fp_teamconfirm_refuse_{msg_id}",
        )
        refuse.callback = self._refuse
        self.add_item(refuse)

    async def _accept(self, interaction: discord.Interaction):
        data = _load_team_confirm(self.msg_id)
        if not data:
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content="⌛ Cette demande n'est plus valide.", view=self)
            return

        from cogs.crewbattle import is_authorized

        team = load_team(data["team_sigle"])
        if not team or not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message(
                "❌ Seul le leader de l'équipe peut confirmer.", ephemeral=True
            )
            return

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Confirmé par {interaction.user.mention}.", view=self
        )
        _del_team_confirm(self.msg_id)

        if data["action"] == "search":
            await _publish_search(interaction, team, data["slots"])
        else:
            await _confirm_matchup(interaction, data["post_data"], team)

    async def _refuse(self, interaction: discord.Interaction):
        data = _load_team_confirm(self.msg_id)
        from cogs.crewbattle import is_authorized

        team = load_team(data["team_sigle"]) if data else None
        if not team or not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message(
                "❌ Seul le leader de l'équipe peut refuser.", ephemeral=True
            )
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content=f"❌ Refusé par {interaction.user.mention}.", view=self)
        _del_team_confirm(self.msg_id)


# ===========================================================================
# DATE SELECT (ephemeral)
# ===========================================================================

async def _publish_search(interaction: discord.Interaction, team: dict, slots: list[str]):
    freeplay_ch = interaction.guild.get_channel(CHANNEL_FREEPLAY)
    if not freeplay_ch:
        await interaction.followup.send("❌ Salon freeplay introuvable.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🔍 {team['sigle']} cherche un adversaire !",
        description="Cliquez sur un créneau pour accepter l'affrontement.",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="Disponibilités",
        value="\n".join(f"• {s}" for s in slots),
        inline=False,
    )

    view = MatchmakingView(team["sigle"], team["leader_id"], slots)
    msg = await freeplay_ch.send(embed=embed, view=view)
    save_fp_post(msg.id, {
        "msg_id":      msg.id,
        "team_sigle":  team["sigle"],
        "leader_id":   team["leader_id"],
        "slots":       slots,
    })

    await interaction.followup.send(f"✅ Recherche publiée ! {msg.jump_url}", ephemeral=True)


class DateSelectView(discord.ui.View):
    def __init__(self, team: dict, selected: Optional[list[str]] = None,
                 needs_leader_ok: bool = False):
        super().__init__(timeout=300)
        self.team            = team
        self.slots           = get_slots()
        self.selected        = selected or []
        self.needs_leader_ok = needs_leader_ok
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
            label="📨 Envoyer à mon leader" if self.needs_leader_ok else "✅ Publier ma recherche",
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
        if self.needs_leader_ok:
            description = (
                f"**{interaction.user.display_name}** souhaite lancer une recherche d'adversaire pour "
                f"**{self.team['sigle']}** (créneaux : {', '.join(self.selected)}). Confirmer ?"
            )
            await _request_team_confirm(
                interaction, self.team, action="search",
                payload={"slots": self.selected}, description=description,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await _publish_search(interaction, self.team, self.selected)


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

        cancel_btn = discord.ui.Button(
            label="🚫 Annuler la recherche", style=discord.ButtonStyle.danger, row=4,
        )
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _cancel(self, interaction: discord.Interaction):
        from cogs.crewbattle import ADMIN_ID
        if interaction.user.id not in (self.leader_id, ADMIN_ID):
            await interaction.response.send_message(
                "❌ Seul l'auteur de la recherche (ou un admin) peut l'annuler.", ephemeral=True
            )
            return

        del_fp_post(interaction.message.id)
        await interaction.response.send_message(
            f"🚫 Recherche de **{self.team_sigle}** annulée par {interaction.user.mention}."
        )
        try:
            await interaction.message.delete()
        except Exception:
            pass

    def _make_cb(self, slot: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id == self.leader_id:
                await interaction.response.send_message(
                    "❌ Tu ne peux pas répondre à ta propre recherche.", ephemeral=True
                )
                return
            post_data = {
                "msg_id":     interaction.message.id,
                "team_sigle": self.team_sigle,
                "leader_id":  self.leader_id,
                "slot":       slot,
            }

            from cogs.teams import find_team_of_player
            sigle = find_team_of_player(interaction.user.id)
            current_team = load_team(sigle) if sigle else None

            if not current_team:
                await interaction.response.send_modal(
                    CustomTeamNameModal(mode="respond", post_data=post_data, user_id=interaction.user.id)
                )
                return

            await interaction.response.send_message(
                f"Avec quelle équipe acceptes-tu ce match du **{slot}** ?",
                view=TeamTypeChoiceView(interaction.user.id, current_team, mode="respond", post_data=post_data),
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
        # Groupe libre (le chercheur ne dirigeait aucune équipe enregistrée)
        team_a_data = {"sigle": post_data["team_sigle"], "leader_id": post_data.get("leader_id")}

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
    _save_setups()

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
        _save_setups()

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
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, side_obj.captain_id):
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
    _save_setups()

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
            from cogs.crewbattle import is_authorized
            if not is_authorized(interaction.user.id, side_obj.captain_id):
                await interaction.response.send_message(
                    "❌ Seul le leader peut répondre.", ephemeral=True
                )
                return
            if side_obj.subs_choice != NOT_SET:
                await interaction.response.send_message("✅ Déjà renseigné.", ephemeral=True)
                return

            side_obj.subs_choice = val
            _save_setups()
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

async def _finalize_roster(interaction: discord.Interaction, setup: "FreeplaySetup",
                            side_obj: "SideSetup", active_ids: list[int], sub_ids: list[int]):
    from cogs.crewbattle import Player

    guild = interaction.guild

    players = []
    for disc_id in active_ids:
        m = guild.get_member(disc_id)
        players.append(Player(name=m.display_name if m else str(disc_id), discord_id=disc_id))
    side_obj.players = players

    subs = []
    for disc_id in sub_ids:
        m = guild.get_member(disc_id)
        subs.append(Player(name=m.display_name if m else str(disc_id), discord_id=disc_id))
    side_obj.subs_list = subs
    _save_setups()

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

    summary = (
        f"✅ **{side_obj.name}** enregistrée !\n"
        f"Actifs : {', '.join(p.name for p in side_obj.players)}"
        + (f"\nRemplaçants : {', '.join(p.name for p in side_obj.subs_list)}"
           if side_obj.subs_list else "")
    )
    await interaction.response.edit_message(content=summary, view=None)
    await interaction.channel.send(
        f"✅ Équipe **{side_obj.name}** prête !\n"
        f"Actifs : {', '.join(p.name for p in side_obj.players)}"
        + (f" | Remplaçants : {', '.join(p.name for p in side_obj.subs_list)}"
           if side_obj.subs_list else "")
    )
    await _check_rosters(interaction.client, guild, setup)


class RosterSelectView(discord.ui.View):
    """Composition d'équipe via menu déroulant (équipe enregistrée) ou sélecteur
    de membres du serveur (groupe libre / pickup)."""

    def __init__(self, category_id: int, side: str, nb_active: int, nb_subs: int,
                 member_options: Optional[list[discord.SelectOption]]):
        super().__init__(timeout=300)
        self.category_id = category_id
        self.side         = side
        self.nb_active    = nb_active
        self.nb_subs      = nb_subs
        self.active_ids: list[int] = []
        self.sub_ids: list[int]    = []

        if member_options is not None:
            self.active_select = discord.ui.Select(
                placeholder=f"Joueurs actifs ({nb_active})",
                min_values=nb_active, max_values=nb_active,
                options=member_options,
            )
        else:
            self.active_select = discord.ui.UserSelect(
                placeholder=f"Joueurs actifs ({nb_active})",
                min_values=nb_active, max_values=nb_active,
            )
        self.active_select.callback = self._on_active
        self.add_item(self.active_select)

        self.subs_select = None
        if nb_subs > 0:
            if member_options is not None:
                self.subs_select = discord.ui.Select(
                    placeholder=f"Remplaçants ({nb_subs})",
                    min_values=nb_subs, max_values=nb_subs,
                    options=member_options,
                )
            else:
                self.subs_select = discord.ui.UserSelect(
                    placeholder=f"Remplaçants ({nb_subs})",
                    min_values=nb_subs, max_values=nb_subs,
                )
            self.subs_select.callback = self._on_subs
            self.add_item(self.subs_select)

        self.confirm_btn = discord.ui.Button(
            label="✅ Valider la composition", style=discord.ButtonStyle.success, disabled=True,
        )
        self.confirm_btn.callback = self._confirm
        self.add_item(self.confirm_btn)

    def _values_of(self, select) -> list[int]:
        if isinstance(select, discord.ui.UserSelect):
            return [u.id for u in select.values]
        return [int(v) for v in select.values]

    def _update_confirm_state(self):
        active_ok = len(self.active_ids) == self.nb_active
        subs_ok   = self.nb_subs == 0 or len(self.sub_ids) == self.nb_subs
        overlap   = bool(set(self.active_ids) & set(self.sub_ids))
        self.confirm_btn.disabled = not (active_ok and subs_ok and not overlap)
        self.confirm_btn.label = (
            "⚠️ Un joueur ne peut pas être actif et remplaçant" if overlap
            else "✅ Valider la composition"
        )

    async def _on_active(self, interaction: discord.Interaction):
        self.active_ids = self._values_of(self.active_select)
        self._update_confirm_state()
        await interaction.response.edit_message(view=self)

    async def _on_subs(self, interaction: discord.Interaction):
        self.sub_ids = self._values_of(self.subs_select)
        self._update_confirm_state()
        await interaction.response.edit_message(view=self)

    async def _confirm(self, interaction: discord.Interaction):
        setup = _setups.get(self.category_id)
        if not setup:
            await interaction.response.edit_message(content="❌ Session expirée.", view=None)
            return
        side_obj = setup.side_a if self.side == "a" else setup.side_b
        if side_obj.players:
            await interaction.response.edit_message(content="✅ Équipe déjà enregistrée.", view=None)
            return
        await _finalize_roster(interaction, setup, side_obj, self.active_ids, self.sub_ids)


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
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, side_obj.captain_id):
            await interaction.response.send_message(
                "❌ Seul le leader peut entrer l'équipe.", ephemeral=True
            )
            return
        if side_obj.players:
            await interaction.response.send_message("✅ Équipe déjà enregistrée.", ephemeral=True)
            return

        team = load_team(side_obj.sigle)
        member_options = None
        if team:
            member_ids = team.get("members", [])
            options = []
            for mid in member_ids:
                m = interaction.guild.get_member(mid)
                options.append(discord.SelectOption(
                    label=(m.display_name if m else str(mid))[:100], value=str(mid),
                ))
            needed = self.nb_active + self.nb_subs
            if len(options) < needed:
                await interaction.response.send_message(
                    f"❌ L'équipe **{side_obj.sigle}** n'a que {len(options)} membre(s) enregistré(s), "
                    f"il en faut {needed} ({self.nb_active} actif(s) + {self.nb_subs} remplaçant(s)).",
                    ephemeral=True,
                )
                return
            member_options = options[:25]

        view = RosterSelectView(self.category_id, self.side, self.nb_active, self.nb_subs, member_options)
        await interaction.response.send_message(
            f"Sélectionne la composition de **{side_obj.name}** :", view=view, ephemeral=True,
        )


# ===========================================================================
# ANNULATION DE LA CB (freeplay) — demande + confirmation mutuelle
# ===========================================================================

CANCEL_CB_PROMPT = (
    "Besoin d'annuler cette CrewBattle ? Un leader peut le proposer ci-dessous "
    "(l'autre leader devra confirmer)."
)


class CancelCBView(discord.ui.View):
    """Bouton persistant 'Annuler la CB', posté dans le salon tasks d'un freeplay."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        btn = discord.ui.Button(
            label="🚫 Annuler la CB", style=discord.ButtonStyle.danger,
            custom_id=f"fp_cancel_request_{channel_id}",
        )
        btn.callback = self._request
        self.add_item(btn)

    async def _request(self, interaction: discord.Interaction):
        from cogs.crewbattle import active_matches
        match = active_matches.get(self.channel_id)
        if not match:
            await interaction.response.send_message("❌ Aucune CrewBattle en cours ici.", ephemeral=True)
            return

        if interaction.user.id == match.team_a.captain_id:
            other_team = match.team_b
        elif interaction.user.id == match.team_b.captain_id:
            other_team = match.team_a
        else:
            await interaction.response.send_message("❌ Seul un leader peut demander l'annulation.", ephemeral=True)
            return

        other_id = other_team.captain_id
        other_mention = f"<@{other_id}>" if other_id else other_team.name

        view = CancelConfirmView(self.channel_id, requester_id=interaction.user.id, confirm_id=other_id)
        await interaction.response.send_message(
            f"{other_mention} — **{interaction.user.display_name}** souhaite annuler la CB. "
            f"Appuyez sur **Oui, annuler la CB** pour annuler la CrewBattle en cours.",
            view=view,
        )


class CancelConfirmView(discord.ui.View):
    def __init__(self, channel_id: int, requester_id: int, confirm_id: int):
        super().__init__(timeout=600)
        self.channel_id   = channel_id
        self.requester_id = requester_id
        self.confirm_id   = confirm_id

    @discord.ui.button(label="✅ Oui, annuler la CB", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.confirm_id):
            await interaction.response.send_message(
                "❌ Seul l'autre leader peut confirmer l'annulation.", ephemeral=True
            )
            return

        from cogs.crewbattle import active_matches, save_matches
        from utils.sheets_log import update_log

        match = active_matches.pop(self.channel_id, None)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        if not match:
            await interaction.followup.send("❌ La CrewBattle n'était déjà plus en cours.")
            return

        save_matches()
        await update_log(match.log_row, "Canceled",
                         f"CrewBattle annulée d'un commun accord (confirmé par **{interaction.user.display_name}**)")
        await interaction.followup.send("🛑 CrewBattle annulée d'un commun accord.")

    @discord.ui.button(label="❌ Non, je ne veux pas annuler la CB", style=discord.ButtonStyle.secondary)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.requester_id, self.confirm_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Demande d'annulation retirée — la CrewBattle continue.", view=self
        )


async def restore_all_cancel_views(bot: commands.Bot) -> int:
    """Ré-enregistre les boutons 'Annuler la CB' déjà postés (aucun appel réseau)."""
    from utils.freeplay_data import FREEPLAY_ACT_DIR
    if not os.path.exists(FREEPLAY_ACT_DIR):
        return 0
    count = 0
    for fn in os.listdir(FREEPLAY_ACT_DIR):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(FREEPLAY_ACT_DIR, fn), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("finished"):
            continue
        msg_id = data.get("cancel_msg_id")
        channel_id = data.get("channel_id")
        if not msg_id or not channel_id:
            continue
        bot.add_view(CancelCBView(channel_id), message_id=msg_id)
        count += 1
    return count


async def restore_all_team_confirms(bot: commands.Bot) -> int:
    """Ré-enregistre les boutons 'Accepter/Refuser' des demandes d'engagement
    d'équipe (recherche/réponse Freeplay par un non-leader), aucun appel réseau."""
    if not os.path.exists(FREEPLAY_TEAM_CONFIRM_DIR):
        return 0
    count = 0
    for fn in os.listdir(FREEPLAY_TEAM_CONFIRM_DIR):
        if not fn.endswith(".json"):
            continue
        msg_id = int(fn[:-5])
        bot.add_view(TeamConfirmView(msg_id), message_id=msg_id)
        count += 1
    return count


async def ensure_all_cancel_buttons(bot: commands.Bot) -> int:
    """Vérifie que chaque CB freeplay en cours a bien son message + bouton
    'Annuler la CB' ; le reposte s'il est absent. Utilisé par /cbl_setup_all."""
    from utils.freeplay_data import FREEPLAY_ACT_DIR
    if not os.path.exists(FREEPLAY_ACT_DIR):
        return 0

    count = 0
    for fn in os.listdir(FREEPLAY_ACT_DIR):
        if not fn.endswith(".json"):
            continue
        channel_id = int(fn[:-5])
        data = load_freeplay_active(channel_id)
        if not data or data.get("finished"):
            continue
        channel = bot.get_channel(channel_id)
        if not channel:
            continue

        msg_id = data.get("cancel_msg_id")
        present = False
        if msg_id:
            try:
                await channel.fetch_message(msg_id)
                present = True
            except Exception:
                present = False

        if present:
            continue

        msg = await channel.send(CANCEL_CB_PROMPT, view=CancelCBView(channel_id))
        data["cancel_msg_id"] = msg.id
        save_freeplay_active(channel_id, data)
        count += 1

    return count


# ===========================================================================
# CLÔTURE DE LA CB (freeplay) — bouton "Terminer la CB" + confirmation
# ===========================================================================

FINISH_CB_PROMPT = (
    "✅ CrewBattle terminée ! Les salons restent disponibles pour relire l'historique. "
    "Un leader (ou un admin) peut cliquer ci-dessous pour renvoyer le résumé de la CB."
)


class FinishCBView(discord.ui.View):
    """Bouton persistant 'Terminer la CB', posté dans le salon tasks une fois le
    Freeplay terminé. N'efface rien : envoie juste le résumé, et révèle le
    bouton de suppression (réservé à l'admin)."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        btn = discord.ui.Button(
            label="🏁 Terminer la CB", style=discord.ButtonStyle.primary,
            custom_id=f"fp_finish_request_{channel_id}",
        )
        btn.callback = self._request
        self.add_item(btn)

    async def _request(self, interaction: discord.Interaction):
        from cogs.crewbattle import ADMIN_ID

        info = load_freeplay_active(self.channel_id)
        if not info:
            await interaction.response.send_message(
                "❌ Cette session Freeplay est introuvable (déjà clôturée ?).", ephemeral=True
            )
            return

        allowed = {info.get("captain_a_id"), info.get("captain_b_id"), ADMIN_ID}
        if interaction.user.id not in allowed:
            await interaction.response.send_message(
                "❌ Seuls les leaders des deux équipes (ou un admin) peuvent clore ce Freeplay.",
                ephemeral=True,
            )
            return

        lines = info.get("summary_lines") or ["*Résumé indisponible.*"]
        embed = discord.Embed(
            title="📋 Résumé de la CrewBattle",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

        del_msg = await interaction.channel.send(
            "Un admin peut supprimer les salons de ce Freeplay ci-dessous.",
            view=DeleteChannelsView(self.channel_id),
        )
        info["delete_msg_id"] = del_msg.id
        save_freeplay_active(self.channel_id, info)


class DeleteChannelsView(discord.ui.View):
    """Bouton persistant 'Supprimer les salons' — réservé à l'admin."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        btn = discord.ui.Button(
            label="🗑️ Supprimer les salons", style=discord.ButtonStyle.danger,
            custom_id=f"fp_delete_channels_{channel_id}",
        )
        btn.callback = self._delete
        self.add_item(btn)

    async def _delete(self, interaction: discord.Interaction):
        from cogs.crewbattle import ADMIN_ID
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message(
                "❌ Seul l'admin peut supprimer les salons.", ephemeral=True
            )
            return

        info = load_freeplay_active(self.channel_id)
        if not info:
            await interaction.response.send_message("❌ Session déjà clôturée.", ephemeral=True)
            return

        await interaction.response.defer()

        guild = interaction.guild
        cat = guild.get_channel(info.get("category_id", 0)) if guild else None
        if cat:
            try:
                for ch in list(cat.channels):
                    await ch.delete()
                await cat.delete()
            except Exception:
                pass
        del_freeplay_active(self.channel_id)


async def restore_all_finish_views(bot: commands.Bot) -> int:
    """Ré-enregistre les boutons 'Terminer la CB'/'Supprimer les salons' déjà
    postés (aucun appel réseau)."""
    from utils.freeplay_data import FREEPLAY_ACT_DIR
    if not os.path.exists(FREEPLAY_ACT_DIR):
        return 0
    count = 0
    for fn in os.listdir(FREEPLAY_ACT_DIR):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(FREEPLAY_ACT_DIR, fn), encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("finished"):
            continue
        channel_id = data.get("channel_id")
        if not channel_id:
            continue
        msg_id = data.get("finish_msg_id")
        if msg_id:
            bot.add_view(FinishCBView(channel_id), message_id=msg_id)
            count += 1
        del_msg_id = data.get("delete_msg_id")
        if del_msg_id:
            bot.add_view(DeleteChannelsView(channel_id), message_id=del_msg_id)
            count += 1
    return count


async def ensure_all_finish_buttons(bot: commands.Bot) -> int:
    """Vérifie que chaque Freeplay terminé a bien son message + bouton
    'Terminer la CB' ; le reposte s'il est absent. Utilisé par /cbl_setup_all."""
    from utils.freeplay_data import FREEPLAY_ACT_DIR
    if not os.path.exists(FREEPLAY_ACT_DIR):
        return 0

    count = 0
    for fn in os.listdir(FREEPLAY_ACT_DIR):
        if not fn.endswith(".json"):
            continue
        channel_id = int(fn[:-5])
        data = load_freeplay_active(channel_id)
        if not data or not data.get("finished"):
            continue
        channel = bot.get_channel(channel_id)
        if not channel:
            # Le salon a disparu sans passer par la clôture propre : on nettoie.
            del_freeplay_active(channel_id)
            continue

        msg_id = data.get("finish_msg_id")
        present = False
        if msg_id:
            try:
                await channel.fetch_message(msg_id)
                present = True
            except Exception:
                present = False

        if present:
            continue

        msg = await channel.send(FINISH_CB_PROMPT, view=FinishCBView(channel_id))
        data["finish_msg_id"] = msg.id
        save_freeplay_active(channel_id, data)
        count += 1

    return count

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
        "channel_id":     ch_tasks.id,
        "category_id":    setup.category_id,
        "team_a_sigle":   setup.side_a.sigle,
        "team_b_sigle":   setup.side_b.sigle,
        "captain_a_id":   ta.captain_id,
        "captain_b_id":   tb.captain_id,
    })

    # Libérer la mémoire du setup
    _delete_setup(setup.category_id)

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

    cancel_msg = await ch_tasks.send(CANCEL_CB_PROMPT, view=CancelCBView(ch_tasks.id))
    freeplay_info = load_freeplay_active(ch_tasks.id) or {}
    freeplay_info["cancel_msg_id"] = cancel_msg.id
    save_freeplay_active(ch_tasks.id, freeplay_info)


# ===========================================================================
# Reprise après redémarrage du bot
# ===========================================================================

async def _restore_setup(bot: commands.Bot, setup: FreeplaySetup):
    """Reposte la bonne vue dans les bons salons selon l'étape où le setup s'est arrêté."""
    await bot.wait_until_ready()

    ch_a     = bot.get_channel(setup.ch_a)
    ch_b     = bot.get_channel(setup.ch_b)
    ch_tasks = bot.get_channel(setup.ch_tasks)
    guild = getattr(ch_tasks, "guild", None) or getattr(ch_a, "guild", None) or getattr(ch_b, "guild", None)

    if not guild:
        # Catégorie/salons supprimés entre-temps : setup obsolète
        _delete_setup(setup.category_id)
        return

    async def notice(ch):
        if ch:
            try:
                await ch.send("🔄 **Configuration reprise après redémarrage du bot.**")
            except Exception:
                pass

    if setup.side_a.nb_avail == NOT_SET or setup.side_b.nb_avail == NOT_SET:
        for side_str, ch, side_obj in (("a", ch_a, setup.side_a), ("b", ch_b, setup.side_b)):
            if side_obj.nb_avail == NOT_SET and ch:
                await notice(ch)
                cap = guild.get_member(side_obj.captain_id)
                await ch.send(
                    f"{cap.mention if cap else ''} — "
                    f"Combien de joueurs **{side_obj.name}** a-t-il de disponibles ?",
                    view=PlayerCountView(setup.category_id, side_str),
                )
        return

    if setup.side_a.subs_choice == NOT_SET or setup.side_b.subs_choice == NOT_SET:
        for side_str, ch, side_obj in (("a", ch_a, setup.side_a), ("b", ch_b, setup.side_b)):
            if side_obj.subs_choice == NOT_SET and ch:
                await notice(ch)
                await ch.send(
                    f"⚔️ La CrewBattle se jouera en **{setup.nb_fight}v{setup.nb_fight}**.\n"
                    f"Combien de remplaçants souhaitez-vous ?",
                    view=SubsView(setup.category_id, side_str, setup.nb_fight),
                )
        return

    if not setup.side_a.players or not setup.side_b.players:
        for side_str, ch, side_obj in (("a", ch_a, setup.side_a), ("b", ch_b, setup.side_b)):
            if not side_obj.players and ch:
                nb_active, nb_subs = subs_split(setup.nb_fight, side_obj.subs_choice)
                await notice(ch)
                await ch.send(
                    f"🎯 Composition de **{side_obj.name}** à renseigner "
                    f"({nb_active} actif(s)" + (f" + {nb_subs} remplaçant(s)" if nb_subs else "") + ") :",
                    view=RosterView(setup.category_id, side_str, nb_active, nb_subs),
                )
        return

    # Les deux rosters étaient complets : soit le crash a eu lieu juste avant le
    # lancement de la CrewBattle, soit juste après (Match déjà créé et sauvegardé
    # côté crewbattle.py, qui se charge alors lui-même de sa reprise).
    from cogs.crewbattle import active_matches
    if ch_tasks and ch_tasks.id in active_matches:
        _delete_setup(setup.category_id)
        return
    await _check_rosters(bot, guild, setup)


async def _restore_fp_post(bot: commands.Bot, old_msg_id: int, data: dict):
    """Reposte une annonce de recherche d'adversaire (les anciens boutons sont morts)."""
    await bot.wait_until_ready()

    ch = bot.get_channel(CHANNEL_FREEPLAY)
    if not ch:
        return

    try:
        old_msg = await ch.fetch_message(old_msg_id)
        await old_msg.delete()
    except Exception:
        pass

    embed = discord.Embed(
        title=f"🔍 {data['team_sigle']} cherche un adversaire !",
        description=(
            "Cliquez sur un créneau pour accepter l'affrontement.\n"
            "*Seul le leader d'une équipe peut répondre.*\n"
            "🔄 *Recherche reprise après redémarrage du bot.*"
        ),
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="Disponibilités",
        value="\n".join(f"• {s}" for s in data["slots"]),
        inline=False,
    )

    view = MatchmakingView(data["team_sigle"], data["leader_id"], data["slots"])
    msg = await ch.send(embed=embed, view=view)

    del_fp_post(old_msg_id)
    save_fp_post(msg.id, {**data, "msg_id": msg.id})

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


async def restore_all_freeplay_setups(bot: commands.Bot) -> int:
    """Recharge les setups Freeplay en cours et reposte les boutons de l'étape en attente.

    Utilisé au démarrage du bot ET par /cbl_setup_all (reprise manuelle).
    Renvoie le nombre de setups restaurés.
    """
    await bot.wait_until_ready()
    loaded = _load_setups_from_file()
    _setups.update(loaded)
    for setup in list(loaded.values()):
        try:
            await _restore_setup(bot, setup)
        except Exception as e:
            print(f"[WARN] restore freeplay setup {setup.category_id}: {e}")
    return len(loaded)


async def restore_all_fp_posts(bot: commands.Bot) -> int:
    """Recharge les annonces de recherche d'adversaire et les reposte avec des boutons frais.

    Utilisé au démarrage du bot ET par /cbl_setup_all (reprise manuelle).
    Renvoie le nombre d'annonces restaurées.
    """
    await bot.wait_until_ready()
    loaded = _load_fp_posts()
    for msg_id, data in loaded.items():
        try:
            await _restore_fp_post(bot, msg_id, data)
        except Exception as e:
            print(f"[WARN] restore freeplay post {msg_id}: {e}")
    return len(loaded)

# ---------------------------------------------------------------------------
# /admcbl_kill_cb
# ---------------------------------------------------------------------------

@app_commands.command(
    name="admcbl_kill_cb",
    description="[ADMIN] Annule de force la CrewBattle Freeplay de cette catégorie",
)
async def admcbl_kill_cb(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    category = getattr(interaction.channel, "category", None)
    if not category:
        await interaction.response.send_message("❌ Ce salon n'appartient à aucune catégorie.", ephemeral=True)
        return

    from utils.freeplay_data import FREEPLAY_ACT_DIR, del_freeplay_active

    target_channel_id = None
    if os.path.exists(FREEPLAY_ACT_DIR):
        for fn in os.listdir(FREEPLAY_ACT_DIR):
            if not fn.endswith(".json"):
                continue
            channel_id = int(fn[:-5])
            data = load_freeplay_active(channel_id)
            if data and data.get("category_id") == category.id:
                target_channel_id = channel_id
                break

    if target_channel_id is None:
        await interaction.response.send_message(
            "❌ Aucune CrewBattle Freeplay en cours dans cette catégorie.", ephemeral=True
        )
        return

    from cogs.crewbattle import active_matches, save_matches
    from utils.sheets_log import update_log

    match = active_matches.pop(target_channel_id, None)
    del_freeplay_active(target_channel_id)

    if not match:
        await interaction.response.send_message(
            "⚠️ Aucun match actif trouvé pour cette catégorie (nettoyage effectué quand même).",
            ephemeral=True,
        )
        return

    save_matches()
    await update_log(match.log_row, "Canceled",
                     f"CrewBattle Freeplay annulée de force par **{interaction.user.display_name}** (/admcbl_kill_cb)")
    await log_command(interaction.user.display_name, "admcbl_kill_cb", "Completed",
                      f"CrewBattle Freeplay **{match.team_a.name}** vs **{match.team_b.name}** annulée de force")

    await interaction.response.send_message(
        f"🛑 CrewBattle Freeplay **{match.team_a.name}** vs **{match.team_b.name}** annulée de force."
    )

    tasks_ch = interaction.guild.get_channel(target_channel_id)
    if tasks_ch and tasks_ch.id != interaction.channel_id:
        try:
            await tasks_ch.send(
                f"🛑 Cette CrewBattle a été annulée de force par un administrateur "
                f"(**{interaction.user.display_name}**)."
            )
        except Exception:
            pass


class Freeplay(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(FreeplayPanelView())
        self.bot.tree.add_command(cbl_setup_freeplay)
        self.bot.tree.add_command(admcbl_kill_cb)
        self.bot.loop.create_task(restore_all_freeplay_setups(self.bot))
        self.bot.loop.create_task(restore_all_fp_posts(self.bot))
        await restore_all_cancel_views(self.bot)
        await restore_all_finish_views(self.bot)
        await restore_all_team_confirms(self.bot)


async def setup(bot: commands.Bot):
    await bot.add_cog(Freeplay(bot))
