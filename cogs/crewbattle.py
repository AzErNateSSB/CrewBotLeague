import discord
from discord.ext import commands
from discord import app_commands
from dataclasses import dataclass, field
from typing import Optional
import random
import re
import json
import os
from enum import Enum, auto
from utils.sheets_log import log_command, update_log
from utils.season_data import load_official_match, delete_official_match, load_season, save_season, update_standings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMOJI_SERVER_ID = 1275018832909434921
ADMIN_ID       = 461601543121010691

CHARACTER_PAGES: list[tuple[str, list[str]]] = [
    ("Smash 64", [
        "00_Random", "01_Mario", "02_DonkeyKong", "03_Link", "04_Samus",
        "05_Yoshi", "06_Kirby", "07_Fox", "08_Pikachu", "09_Luigi",
        "10_Ness", "11_CaptainFalcon", "12_Jigglypuff",
    ]),
    ("Melee", [
        "13_Peach", "14_Bowser", "15_IceClimbers", "16_Sheik", "17_Zelda",
        "18_DrMario", "19_Pichu", "20_Falco", "21_Marth", "22_YoungLink",
        "23_Ganondorf", "24_Mewtwo", "25_Roy", "26_MrGameAndWatch",
    ]),
    ("Brawl", [
        "27_MetaKnight", "28_Pit", "29_ZeroSuitSamus", "30_Wario",
        "31_Snake", "32_Ike", "33x_PokemonTrainer", "36_DiddyKong",
        "37_Lucas", "38_Sonic", "39_KingDedede", "40_Olimar",
        "41_Lucario", "42_ROB", "43_ToonLink", "44_Wolf",
    ]),
    ("Smash 4", [
        "45_Villager", "46_MegaMan", "47_WiiFitTrainer", "48_RosalinaLuma",
        "49_LittleMac", "50_Greninja", "51_MiiBrawler", "52_MiiSwordfighter",
        "53_MiiGunner", "54_Palutena", "55_PacMan", "56_Robin", "57_Shulk",
        "58_BowserJr", "59_DuckHunt", "60_Ryu", "61_Cloud", "62_Corrin",
        "63_Bayonetta",
    ]),
    ("Ultimate", [
        "64_Inkling", "65_Ridley", "66_Simon", "67_KingKRool",
        "68_Isabelle", "69_Incineroar", "70_PiranhaPlant", "71_Joker",
        "72_Hero", "73_BanjoKazooie", "74_Terry", "75_Byleth",
        "76_MinMin", "77_Steve", "78_Sephiroth", "79x_Aegis",
        "81_Kazuya", "82_Sora",
    ]),
    ("Echos", [
        "04e_DarkSamus", "13e_Daisy", "21e_Lucina", "25e_Chrom",
        "28e_DarkPit", "60e_Ken", "66e_Richter",
    ]),
]

STAGES = [
    "Final Destination",
    "Battlefield",
    "Small Battlefield",
    "Pokémon Stadium 2",
    "Kalos Pokémon League",
    "Yoshi's Story",
    "Smashville",
    "Town and City",
    "Hollow Bastion",
]

# Noms des emotes custom Discord (doit correspondre exactement au nom sur le serveur)
STAGE_EMOJI_NAMES: dict[str, str] = {
    "Final Destination":   "FD",
    "Battlefield":         "BF",
    "Small Battlefield":   "SBF",
    "Pokémon Stadium 2":   "PS2",
    "Kalos Pokémon League":"Kalos",
    "Yoshi's Story":       "YS",
    "Smashville":          "SV",
    "Town and City":       "TC",
    "Hollow Bastion":      "HB",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def range_str(max_val: int) -> str:
    """Génère '0, 1, 2 ou 3' en fonction du max."""
    vals = list(range(max_val + 1))
    if len(vals) == 1:
        return "0"
    return ", ".join(str(v) for v in vals[:-1]) + f" ou {vals[-1]}"


def get_stage_emoji(stage: str, guild: Optional[discord.Guild]) -> Optional[discord.Emoji]:
    if not guild or stage not in STAGE_EMOJI_NAMES:
        return None
    return discord.utils.get(guild.emojis, name=STAGE_EMOJI_NAMES[stage])


def stage_display(stage: str, guild: Optional[discord.Guild]) -> str:
    """Retourne 'emoji stage' pour les embeds texte."""
    emoji = get_stage_emoji(stage, guild)
    return f"{emoji} {stage}" if emoji else stage


def char_display(char: str) -> str:
    """Retourne l'emote du personnage si disponible, sinon le nom brut."""
    if not char:
        return "?"
    if not _bot_ref:
        return char
    emoji_guild = _bot_ref.get_guild(EMOJI_SERVER_ID)
    if not emoji_guild:
        return char
    emoji = discord.utils.get(emoji_guild.emojis, name=char)
    return str(emoji) if emoji else char


def parse_players(text: str, guild: Optional[discord.Guild]) -> list["Player"]:
    players = []
    for token in text.split(","):
        token = token.strip()
        m = re.match(r"<@!?(\d+)>", token)
        if m and guild:
            member = guild.get_member(int(m.group(1)))
            if member:
                players.append(Player(name=member.display_name))
                continue
        if token:
            players.append(Player(name=token))
    return players



def build_history_lines(match: "Match", guild: Optional[discord.Guild] = None) -> list[str]:
    initial = len(match.team_a.all_players) * 3
    lines = [f"**{match.team_a.name}** [{initial}-{initial}] **{match.team_b.name}**"]
    for rec in match.set_history:
        stage_emoji = get_stage_emoji(rec.stage, guild) if guild else None
        stage_str = str(stage_emoji) if stage_emoji else rec.stage
        lines.append(
            f"**{rec.player_a}** {char_display(rec.char_a)} {rec.score_a}-{rec.score_b}"
            f" {char_display(rec.char_b)} **{rec.player_b}**  |  {stage_str}"
        )
    return lines

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class State(Enum):
    FIRST_PICK     = auto()
    LOSER_PICK     = auto()
    BAN_FIRST      = auto()
    BAN_SECOND     = auto()
    STAGE_PICK     = auto()
    WAITING_RESULT = auto()
    FINISHED       = auto()


def is_authorized(user_id: int, *allowed_ids: int) -> bool:
    """Retourne True si l'utilisateur est admin ou dans la liste des IDs autorisés."""
    return user_id == ADMIN_ID or user_id in allowed_ids


@dataclass
class Player:
    name: str
    lives: int = 3
    character: str = ""
    discord_id: int = 0


@dataclass
class SetRecord:
    player_a: str
    char_a: str
    player_b: str
    char_b: str
    score_a: int   # vies PRISES par A à B  (= vies perdues par B)
    score_b: int   # vies PRISES par B à A  (= vies perdues par A)
    stage: str
    lives_a_after: int
    lives_b_after: int


@dataclass
class Team:
    name: str
    captain_id: int
    players: list[Player] = field(default_factory=list)
    subs: list[Player] = field(default_factory=list)

    @property
    def total_lives(self) -> int:
        return sum(p.lives for p in self.players + self.subs)

    @property
    def all_players(self) -> list[Player]:
        return self.players + self.subs

    @property
    def nb_defeated(self) -> int:
        """Nombre de joueurs (titulaires ou remplaçants entrés en jeu) éliminés."""
        return sum(1 for p in self.all_players if p.lives <= 0)

    @property
    def is_eliminated(self) -> bool:
        """L'équipe a perdu dès que le nombre de titulaires prévus a été battu —
        les remplaçants élargissent juste le vivier de joueurs sélectionnables,
        ils n'augmentent pas le nombre de joueurs à battre pour gagner."""
        return self.nb_defeated >= len(self.players)


@dataclass
class Match:
    team_a: Team
    team_b: Team
    channel_id: int
    set_number: int = 0
    state: State = State.FIRST_PICK
    current_a: Optional[Player] = None
    current_b: Optional[Player] = None
    picked_a: bool = False
    picked_b: bool = False
    banned_stages: list[str] = field(default_factory=list)
    picked_stage: Optional[str] = None
    first_banner: str = ""
    picker: str = ""
    set_history: list[SetRecord] = field(default_factory=list)
    log_row: int = -1


active_matches: dict[int, Match] = {}
_bot_ref: Optional[commands.Bot] = None

SAVE_FILE = "match_state.json"

# ---------------------------------------------------------------------------
# Persistance des matchs
# ---------------------------------------------------------------------------

def _player_to_dict(p: Player) -> dict:
    return {"name": p.name, "lives": p.lives, "character": p.character, "discord_id": p.discord_id}

def _player_from_dict(d: dict) -> Player:
    return Player(name=d["name"], lives=d["lives"], character=d.get("character", ""), discord_id=d.get("discord_id", 0))

def _team_to_dict(t: "Team") -> dict:
    return {
        "name": t.name, "captain_id": t.captain_id,
        "players": [_player_to_dict(p) for p in t.players],
        "subs":    [_player_to_dict(p) for p in t.subs],
    }

def _team_from_dict(d: dict) -> "Team":
    return Team(
        name=d["name"], captain_id=d["captain_id"],
        players=[_player_from_dict(p) for p in d["players"]],
        subs   =[_player_from_dict(p) for p in d["subs"]],
    )

def _match_to_dict(m: "Match") -> dict:
    return {
        "channel_id":   m.channel_id,
        "set_number":   m.set_number,
        "state":        m.state.name,
        "team_a":       _team_to_dict(m.team_a),
        "team_b":       _team_to_dict(m.team_b),
        "current_a":    _player_to_dict(m.current_a) if m.current_a else None,
        "current_b":    _player_to_dict(m.current_b) if m.current_b else None,
        "picked_a":     m.picked_a,
        "picked_b":     m.picked_b,
        "banned_stages":m.banned_stages,
        "picked_stage": m.picked_stage,
        "first_banner": m.first_banner,
        "picker":       m.picker,
        "log_row":     m.log_row,
        "set_history": [
            {"player_a": r.player_a, "char_a": r.char_a,
             "player_b": r.player_b, "char_b": r.char_b,
             "score_a": r.score_a, "score_b": r.score_b,
             "stage": r.stage, "lives_a_after": r.lives_a_after, "lives_b_after": r.lives_b_after}
            for r in m.set_history
        ],
    }

def _match_from_dict(d: dict) -> "Match":
    return Match(
        team_a      =_team_from_dict(d["team_a"]),
        team_b      =_team_from_dict(d["team_b"]),
        channel_id  =d["channel_id"],
        set_number  =d["set_number"],
        state       =State[d["state"]],
        current_a   =_player_from_dict(d["current_a"]) if d.get("current_a") else None,
        current_b   =_player_from_dict(d["current_b"]) if d.get("current_b") else None,
        picked_a    =d["picked_a"],
        picked_b    =d["picked_b"],
        banned_stages=d["banned_stages"],
        picked_stage=d.get("picked_stage"),
        first_banner=d["first_banner"],
        picker      =d["picker"],
        set_history =[SetRecord(**r) for r in d["set_history"]],
        log_row     =d.get("log_row", -1),
    )

def save_matches():
    try:
        data = {str(ch): _match_to_dict(m) for ch, m in active_matches.items()}
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] save_matches: {e}")

def _load_matches_from_file() -> dict[int, "Match"]:
    if not os.path.exists(SAVE_FILE):
        return {}
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {int(ch): _match_from_dict(m) for ch, m in data.items()}
    except Exception as e:
        print(f"[WARN] load_matches: {e}")
        return {}

async def _restore_match_view(bot: commands.Bot, match: "Match"):
    """Envoie la bonne vue dans le salon en fonction de l'état sauvegardé."""
    channel = bot.get_channel(match.channel_id)
    if not channel:
        return
    guild = getattr(channel, "guild", None)
    state = match.state

    await channel.send("🔄 **Match en cours repris après redémarrage.**")

    if state == State.FIRST_PICK:
        cap_a = guild.get_member(match.team_a.captain_id) if guild else None
        cap_b = guild.get_member(match.team_b.captain_id) if guild else None
        view = FirstPickView(match=match)
        msg = await channel.send(
            f"📢 {cap_a.mention if cap_a else ''} ({match.team_a.name}) "
            f"et {cap_b.mention if cap_b else ''} ({match.team_b.name}), choisissez votre premier joueur !",
            view=view,
        )
        view.message = msg

    elif state == State.LOSER_PICK:
        loser_side = match.picker
        loser_team = match.team_a if loser_side == "A" else match.team_b
        view = LoserPickView(match=match, loser_side=loser_side)
        msg = await channel.send(f"⚔️ **{loser_team.name}** — choisissez votre prochain joueur !", view=view)
        view.message = msg

    elif state in (State.BAN_FIRST, State.BAN_SECOND, State.STAGE_PICK):
        view = StageBanView(match=match, guild=guild)
        msg = await channel.send(embed=view._make_embed(), view=view)
        view.message = msg

    elif state == State.WAITING_RESULT:
        ca, cb = match.current_a, match.current_b
        view = ScoreView(match=match)
        msg = await channel.send(
            f"Quel est le résultat du match ?\n"
            f"**{ca.name}** `[vies prises]` — `[vies prises]` **{cb.name}**",
            view=view,
        )
        view.message = msg

# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class PlayerSelectView(discord.ui.View):
    """Sélection d'un joueur par boutons, suivie d'un modal pour le personnage."""

    def __init__(self, match: Match, side: str, parent_view, available: list[Player], mode: str = "first"):
        super().__init__(timeout=None)
        self.match = match
        self.side = side
        self.parent_view = parent_view
        self.available = available
        self.mode = mode  # "first" ou "loser"
        self.selected: Optional[Player] = None
        self.message: Optional[discord.Message] = None
        self._build()

    def _build(self):
        self.clear_items()
        for i, player in enumerate(self.available):
            btn = discord.ui.Button(
                label=player.name,
                style=discord.ButtonStyle.success if player is self.selected else discord.ButtonStyle.secondary,
                row=i // 4,
            )
            btn.callback = self._make_select_cb(player)
            self.add_item(btn)

        confirm = discord.ui.Button(
            label="Confirmer",
            style=discord.ButtonStyle.primary,
            disabled=self.selected is None,
            row=4,
        )
        confirm.callback = self._confirm
        self.add_item(confirm)

    def _make_select_cb(self, player: Player):
        async def cb(interaction: discord.Interaction):
            team = self.match.team_a if self.side == "A" else self.match.team_b
            if not is_authorized(interaction.user.id, team.captain_id):
                await interaction.response.send_message("❌ Ce n'est pas votre tour.", ephemeral=True)
                return
            self.selected = player
            self._build()
            await interaction.response.edit_message(view=self)
        return cb

    async def _confirm(self, interaction: discord.Interaction):
        team = self.match.team_a if self.side == "A" else self.match.team_b
        if not is_authorized(interaction.user.id, team.captain_id):
            await interaction.response.send_message("❌ Ce n'est pas votre tour.", ephemeral=True)
            return

        player = self.selected
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ **{player.name}** sélectionné — en attente de son choix de personnage.",
            view=self,
        )

        # Le personnage est choisi par le joueur lui-même, pas par le leader —
        # il faut donc un nouveau message PUBLIC (le leader a un message éphémère
        # que le joueur sélectionné ne peut pas voir).
        char_view = CharacterSelectView(
            self.match, self.side, player, self.parent_view, self, self.mode, interaction.client
        )
        member = interaction.guild.get_member(player.discord_id) if player.discord_id else None
        mention = member.mention if member else f"**{player.name}**"
        msg = await interaction.channel.send(
            f"{mention} — Quel personnage vas-tu jouer ?",
            view=char_view,
        )
        char_view.message = msg


class CharacterSelectView(discord.ui.View):
    """Sélection du personnage par pages de boutons avec emotes."""

    def __init__(self, match: Match, side: str, player: Player, parent_view,
                 select_view: "PlayerSelectView", mode: str, bot):
        super().__init__(timeout=None)
        self.match = match
        self.side = side
        self.player = player
        self.parent_view = parent_view
        self.select_view = select_view
        self.mode = mode
        self.bot = bot
        self.page = 0
        self.selected_char: Optional[str] = None
        self.message: Optional[discord.Message] = None
        self._build()

    def _get_emoji(self, name: str) -> Optional[discord.Emoji]:
        guild = self.bot.get_guild(EMOJI_SERVER_ID)
        return discord.utils.get(guild.emojis, name=name) if guild else None

    def _build(self):
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
            if not is_authorized(interaction.user.id, self.player.discord_id):
                await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
                return
            self.selected_char = char
            self._build()
            await interaction.response.edit_message(view=self)
        return cb

    async def _prev(self, interaction: discord.Interaction):
        if not is_authorized(interaction.user.id, self.player.discord_id):
            await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
            return
        self.page -= 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        if not is_authorized(interaction.user.id, self.player.discord_id):
            await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
            return
        self.page += 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _confirm(self, interaction: discord.Interaction):
        if not is_authorized(interaction.user.id, self.player.discord_id):
            await interaction.response.send_message("❌ Ce n'est pas à toi de choisir.", ephemeral=True)
            return

        team = self.match.team_a if self.side == "A" else self.match.team_b
        char = self.selected_char
        player = self.player
        match = self.match
        side = self.side
        player.character = char

        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

        await interaction.response.send_message(
            f"✅ **{player.name}** ({char}) enregistré !", ephemeral=True
        )

        if self.mode == "first":
            if side == "A":
                match.current_a = player
                match.picked_a = True
                self.parent_view.btn_a.label = f"✅ {team.name} — Joueur prêt"
                self.parent_view.btn_a.style = discord.ButtonStyle.success
                self.parent_view.btn_a.disabled = True
            else:
                match.current_b = player
                match.picked_b = True
                self.parent_view.btn_b.label = f"✅ {team.name} — Joueur prêt"
                self.parent_view.btn_b.style = discord.ButtonStyle.success
                self.parent_view.btn_b.disabled = True
            save_matches()

            if self.parent_view.message:
                try:
                    await self.parent_view.message.edit(view=self.parent_view)
                except Exception:
                    pass

            if match.picked_a and match.picked_b:
                await start_ban_phase(interaction.channel, match)

        else:  # loser
            if side == "A":
                match.current_a = player
            else:
                match.current_b = player

            match.state = State.BAN_FIRST

            for item in self.parent_view.children:
                item.disabled = True
            if self.parent_view.message:
                try:
                    await self.parent_view.message.edit(view=self.parent_view)
                except Exception:
                    pass

            await start_ban_phase(interaction.channel, match)


class ScoreModal(discord.ui.Modal):
    def __init__(self, match: Match, view):
        super().__init__(title="Résultat du match")
        self.match = match
        self.score_view = view

        ca = match.current_a
        cb = match.current_b

        # takes_a = vies prises par A à B  → B perd takes_a  → max = cb.lives
        # takes_b = vies prises par B à A  → A perd takes_b  → max = ca.lives
        label_a = f"Vies prises par {ca.name}"[:45]
        label_b = f"Vies prises par {cb.name}"[:45]

        self.score_a_input = discord.ui.TextInput(
            label=label_a,
            placeholder=range_str(cb.lives),
            min_length=1,
            max_length=1,
        )
        self.score_b_input = discord.ui.TextInput(
            label=label_b,
            placeholder=range_str(ca.lives),
            min_length=1,
            max_length=1,
        )
        self.add_item(self.score_a_input)
        self.add_item(self.score_b_input)

    async def on_submit(self, interaction: discord.Interaction):
        match = self.match
        if match.state != State.WAITING_RESULT:
            await interaction.response.send_message(
                "⚠️ Ce set a déjà été reporté entre-temps. Le salon devrait afficher la suite du match "
                "(sélection du prochain joueur, bans, etc.) — regarde le message le plus récent.",
                ephemeral=True,
            )
            return

        try:
            takes_a = int(self.score_a_input.value.strip())
            takes_b = int(self.score_b_input.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ Valeurs invalides (entiers attendus).", ephemeral=True)
            return

        ca = match.current_a
        cb = match.current_b

        if takes_a < 0 or takes_b < 0:
            await interaction.response.send_message("❌ Les valeurs ne peuvent pas être négatives.", ephemeral=True)
            return
        if takes_a > cb.lives or takes_b > ca.lives:
            await interaction.response.send_message(
                f"❌ Impossible : {ca.name} a {ca.lives}♥, {cb.name} a {cb.lives}♥.",
                ephemeral=True,
            )
            return

        # takes_a = vies A a prises à B  → B perd takes_a
        # takes_b = vies B a prises à A  → A perd takes_b
        new_a = ca.lives - takes_b
        new_b = cb.lives - takes_a

        if new_a == 0 and new_b == 0:
            await interaction.response.send_message(
                "❌ Les deux joueurs ne peuvent pas être éliminés simultanément.", ephemeral=True
            )
            return
        if new_a != 0 and new_b != 0:
            await interaction.response.send_message(
                "❌ Le set doit se terminer par l'élimination d'un joueur (un des joueurs doit atteindre 0 vie).",
                ephemeral=True,
            )
            return

        loser_side = "A" if new_a == 0 else "B"

        # On acquitte tout de suite : les appels réseau qui suivent (édition de
        # messages, rafraîchissement des stats) peuvent prendre plus longtemps
        # que la fenêtre de 3s d'une réponse d'interaction initiale.
        await interaction.response.defer()

        match.set_history.append(SetRecord(
            player_a=ca.name, char_a=ca.character,
            player_b=cb.name, char_b=cb.character,
            score_a=takes_a, score_b=takes_b,
            stage=match.picked_stage,
            lives_a_after=new_a,
            lives_b_after=new_b,
        ))

        ca.lives = new_a
        cb.lives = new_b
        match.set_number += 1
        match.banned_stages = []
        match.picked_stage = None

        from utils.players_stats import record_set_result
        record_set_result(ca.discord_id, cb.discord_id, takes_a, takes_b)

        for item in self.score_view.children:
            item.disabled = True
        if self.score_view.message:
            try:
                await self.score_view.message.edit(view=self.score_view)
            except Exception:
                pass

        channel = interaction.channel
        guild = getattr(channel, "guild", None)

        if guild and _bot_ref:
            from utils.players_stats import refresh_after_set
            await refresh_after_set(_bot_ref, guild.id, ca.discord_id, cb.discord_id)

        # Résumé public du set terminé
        winner_name = cb.name if loser_side == "A" else ca.name
        rec = match.set_history[-1]
        stage_str = stage_display(rec.stage, guild)

        history_lines = build_history_lines(match, guild)
        current_score = (
            f"**{match.team_a.name}** `{match.team_a.total_lives}`"
            f" — `{match.team_b.total_lives}` **{match.team_b.name}**"
        )

        embed = discord.Embed(
            title=f"Set {match.set_number} terminé — {winner_name} gagne !",
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Résultat",
            value=(
                f"**{rec.player_a}** {char_display(rec.char_a)} **{rec.score_a}**-**{rec.score_b}**"
                f" {char_display(rec.char_b)} **{rec.player_b}**\n{stage_str}"
            ),
            inline=False,
        )
        embed.add_field(name="Score global", value=current_score, inline=False)
        _hist = "\n".join(history_lines)
        if len(_hist) > 1024:
            _hist = "…\n" + _hist[-1021:].split("\n", 1)[-1]
        embed.add_field(name="Historique", value=_hist, inline=False)

        await interaction.followup.send(embed=embed)

        if match.team_a.is_eliminated or match.team_b.is_eliminated:
            await end_crewbattle(channel, match)
            return

        winner_side = "B" if loser_side == "A" else "A"
        match.first_banner = winner_side
        match.picker = loser_side

        loser_team = match.team_a if loser_side == "A" else match.team_b
        match.state = State.LOSER_PICK
        save_matches()

        view = LoserPickView(match=match, loser_side=loser_side)
        msg = await channel.send(
            f"⚔️ **{loser_team.name}** — choisissez votre prochain joueur !",
            view=view,
        )
        view.message = msg

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class FirstPickView(discord.ui.View):
    def __init__(self, match: Match):
        super().__init__(timeout=None)
        self.match = match
        self.message: Optional[discord.Message] = None

        self.btn_a = discord.ui.Button(
            label=f"🅰️ {match.team_a.name} — Choisir votre joueur",
            style=discord.ButtonStyle.primary,
        )
        self.btn_b = discord.ui.Button(
            label=f"🅱️ {match.team_b.name} — Choisir votre joueur",
            style=discord.ButtonStyle.primary,
        )
        self.btn_a.callback = self._pick_a
        self.btn_b.callback = self._pick_b
        self.add_item(self.btn_a)
        self.add_item(self.btn_b)

    async def _pick_a(self, interaction: discord.Interaction):
        if not is_authorized(interaction.user.id, self.match.team_a.captain_id):
            await interaction.response.send_message("❌ Seul le capitaine de l'équipe A peut agir ici.", ephemeral=True)
            return
        if self.match.picked_a:
            await interaction.response.send_message("✅ Joueur déjà soumis.", ephemeral=True)
            return
        view = PlayerSelectView(self.match, "A", self, self.match.team_a.all_players)
        await interaction.response.send_message(
            f"**{self.match.team_a.name}** — Quel joueur envoyer ?",
            view=view, ephemeral=True,
        )
        view.message = await interaction.original_response()

    async def _pick_b(self, interaction: discord.Interaction):
        if not is_authorized(interaction.user.id, self.match.team_b.captain_id):
            await interaction.response.send_message("❌ Seul le capitaine de l'équipe B peut agir ici.", ephemeral=True)
            return
        if self.match.picked_b:
            await interaction.response.send_message("✅ Joueur déjà soumis.", ephemeral=True)
            return
        view = PlayerSelectView(self.match, "B", self, self.match.team_b.all_players)
        await interaction.response.send_message(
            f"**{self.match.team_b.name}** — Quel joueur envoyer ?",
            view=view, ephemeral=True,
        )
        view.message = await interaction.original_response()


class LoserPickView(discord.ui.View):
    def __init__(self, match: Match, loser_side: str):
        super().__init__(timeout=None)
        self.match = match
        self.loser_side = loser_side
        self.message: Optional[discord.Message] = None

        team = match.team_a if loser_side == "A" else match.team_b
        btn = discord.ui.Button(
            label=f"⚔️ {team.name} — Envoyer votre joueur",
            style=discord.ButtonStyle.primary,
        )
        btn.callback = self._pick
        self.add_item(btn)

    async def _pick(self, interaction: discord.Interaction):
        team = self.match.team_a if self.loser_side == "A" else self.match.team_b
        if not is_authorized(interaction.user.id, team.captain_id):
            await interaction.response.send_message("❌ Seul le capitaine de votre équipe peut agir ici.", ephemeral=True)
            return
        available = [p for p in team.all_players if p.lives > 0]
        view = PlayerSelectView(self.match, self.loser_side, self, available, mode="loser")
        await interaction.response.send_message(
            f"**{team.name}** — Quel joueur envoyer ?",
            view=view, ephemeral=True,
        )
        view.message = await interaction.original_response()


class StageButton(discord.ui.Button):
    def __init__(self, stage: str, **kwargs):
        super().__init__(**kwargs)
        self.stage_name = stage

    async def callback(self, interaction: discord.Interaction):
        await self.view.on_stage_click(interaction, self.stage_name)


class StageBanView(discord.ui.View):
    def __init__(self, match: Match, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=None)
        self.match = match
        self.guild = guild
        self.selected: list[str] = []
        self.message: Optional[discord.Message] = None
        self._build()

    def _build(self):
        self.clear_items()
        for i, stage in enumerate(STAGES):
            banned = stage in self.match.banned_stages
            selected = stage in self.selected
            emoji = get_stage_emoji(stage, self.guild)

            if banned:
                style, label, disabled = discord.ButtonStyle.danger, stage, True
            elif selected:
                style = discord.ButtonStyle.success if self.match.state == State.STAGE_PICK else discord.ButtonStyle.danger
                label, disabled = stage, False
            else:
                style, label, disabled = discord.ButtonStyle.secondary, stage, False

            self.add_item(StageButton(stage=stage, label=label, style=style, disabled=disabled, emoji=emoji, row=i // 5))

        max_s = self._max_select()
        count = len(self.selected)
        validate = discord.ui.Button(
            label=f"Valider ({count}/{max_s})",
            style=discord.ButtonStyle.success,
            disabled=(count != max_s),
            row=2,
        )
        validate.callback = self._validate
        self.add_item(validate)

    def _max_select(self) -> int:
        if self.match.state == State.BAN_FIRST:
            return 3
        if self.match.state == State.BAN_SECOND:
            return 4
        return 1

    def _active_player_id(self) -> int:
        """ID Discord du joueur actif pour les bans/picks (fallback sur capitaine si non défini)."""
        state = self.match.state
        if state == State.BAN_FIRST:
            side = self.match.first_banner
        elif state == State.BAN_SECOND:
            side = "B" if self.match.first_banner == "A" else "A"
        else:
            side = self.match.picker
        if side == "A":
            player = self.match.current_a
            return player.discord_id if player and player.discord_id else self.match.team_a.captain_id
        else:
            player = self.match.current_b
            return player.discord_id if player and player.discord_id else self.match.team_b.captain_id

    def _active_team_name(self) -> str:
        state = self.match.state
        if state == State.BAN_FIRST:
            side = self.match.first_banner
        elif state == State.BAN_SECOND:
            side = "B" if self.match.first_banner == "A" else "A"
        else:
            side = self.match.picker
        return self.match.team_a.name if side == "A" else self.match.team_b.name

    async def on_stage_click(self, interaction: discord.Interaction, stage: str):
        if not is_authorized(interaction.user.id, self._active_player_id()):
            await interaction.response.send_message("❌ Ce n'est pas votre tour.", ephemeral=True)
            return

        if stage in self.selected:
            self.selected.remove(stage)
        else:
            if self.match.state == State.STAGE_PICK:
                self.selected.clear()
            if len(self.selected) < self._max_select():
                self.selected.append(stage)

        self._build()
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    async def _validate(self, interaction: discord.Interaction):
        if not is_authorized(interaction.user.id, self._active_player_id()):
            await interaction.response.send_message("❌ Ce n'est pas votre tour.", ephemeral=True)
            return

        state = self.match.state

        if state == State.BAN_FIRST:
            self.match.banned_stages.extend(self.selected)
            self.selected = []
            self.match.state = State.BAN_SECOND if self.match.set_number == 0 else State.STAGE_PICK
            self._build()
            await interaction.response.edit_message(embed=self._make_embed(), view=self)

        elif state == State.BAN_SECOND:
            self.match.banned_stages.extend(self.selected)
            self.selected = []
            self.match.state = State.STAGE_PICK
            self._build()
            await interaction.response.edit_message(embed=self._make_embed(), view=self)

        elif state == State.STAGE_PICK:
            self.match.picked_stage = self.selected[0]
            self.match.state = State.WAITING_RESULT
            self.selected = []
            save_matches()
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(embed=self._make_embed(), view=self)
            await announce_set(interaction.channel, self.match)

    def _make_embed(self) -> discord.Embed:
        state = self.match.state
        match = self.match

        if state == State.BAN_FIRST:
            action = f"**{self._active_team_name()}** — Bannissez 3 stages"
        elif state == State.BAN_SECOND:
            action = f"**{self._active_team_name()}** — Bannissez 4 stages"
        elif state == State.STAGE_PICK:
            action = f"**{self._active_team_name()}** — Choisissez le stage"
        else:
            action = f"Stage sélectionné : **{stage_display(match.picked_stage, self.guild)}**"

        embed = discord.Embed(title="🗺️ Sélection du stage", description=action, color=discord.Color.gold())

        if match.banned_stages:
            embed.add_field(
                name=f"Bannis ({len(match.banned_stages)})",
                value="\n".join(f"❌ {stage_display(s, self.guild)}" for s in match.banned_stages),
                inline=True,
            )

        remaining = [s for s in STAGES if s not in match.banned_stages]
        if remaining:
            embed.add_field(
                name=f"Disponibles ({len(remaining)})",
                value="\n".join(f"• {stage_display(s, self.guild)}" for s in remaining),
                inline=True,
            )

        if self.selected:
            label = "Sélectionné" if state == State.STAGE_PICK else "En cours de ban"
            embed.add_field(
                name=label,
                value="\n".join(f"🔴 {stage_display(s, self.guild)}" for s in self.selected),
                inline=False,
            )

        if match.picked_stage and state == State.WAITING_RESULT:
            embed.add_field(
                name="Stage choisi",
                value=f"🎮 **{stage_display(match.picked_stage, self.guild)}**",
                inline=False,
            )

        return embed


class ScoreView(discord.ui.View):
    def __init__(self, match: Match):
        super().__init__(timeout=None)
        self.match = match
        self.message: Optional[discord.Message] = None

        btn = discord.ui.Button(label="📝 Entrer les scores", style=discord.ButtonStyle.primary)
        btn.callback = self._open_modal
        self.add_item(btn)

    async def _open_modal(self, interaction: discord.Interaction):
        valid = {self.match.team_a.captain_id, self.match.team_b.captain_id}
        if not is_authorized(interaction.user.id, *valid):
            await interaction.response.send_message("❌ Seuls les capitaines peuvent entrer les scores.", ephemeral=True)
            return
        if self.match.state != State.WAITING_RESULT:
            await interaction.response.send_message(
                "⚠️ Ce set a déjà été reporté. Le salon devrait afficher la suite du match "
                "(sélection du prochain joueur, bans, etc.) — regarde le message le plus récent.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ScoreModal(self.match, self))

# ---------------------------------------------------------------------------
# Flow helpers
# ---------------------------------------------------------------------------

async def start_ban_phase(channel: discord.TextChannel, match: Match):
    guild = getattr(channel, "guild", None)
    ca = match.current_a
    cb = match.current_b

    if match.set_number == 0:
        # Set 1 : équipe aléatoire banne en premier
        title = f"**{ca.name}** {char_display(ca.character)} — {char_display(cb.character)} **{cb.name}**"
        match.first_banner = random.choice(["A", "B"])
        match.picker = match.first_banner
    else:
        title = (
            f"({ca.lives}♥) **{ca.name}** {char_display(ca.character)}"
            f" — {char_display(cb.character)} **{cb.name}** ({cb.lives}♥)"
        )

    match.state = State.BAN_FIRST
    save_matches()

    embed = discord.Embed(
        title=f"⚔️ Set {match.set_number + 1} — Matchup",
        description=title,
        color=discord.Color.blue(),
    )
    await channel.send(embed=embed)

    view = StageBanView(match=match, guild=guild)
    msg = await channel.send(embed=view._make_embed(), view=view)
    view.message = msg


async def announce_set(channel: discord.TextChannel, match: Match):
    guild = getattr(channel, "guild", None)
    ca = match.current_a
    cb = match.current_b

    if match.set_number == 0:
        desc = f"**{ca.name}** {char_display(ca.character)} — {char_display(cb.character)} **{cb.name}**"
    else:
        desc = (
            f"({ca.lives}♥) **{ca.name}** {char_display(ca.character)}"
            f" — {char_display(cb.character)} **{cb.name}** ({cb.lives}♥)"
        )

    stage_str = stage_display(match.picked_stage, guild)
    embed = discord.Embed(
        title=f"🎮 Set {match.set_number + 1} — {stage_str}",
        description=desc,
        color=discord.Color.green(),
    )
    await channel.send(embed=embed)

    view = ScoreView(match=match)
    msg = await channel.send(
        f"Quel est le résultat du match ?\n"
        f"**{ca.name}** `[vies prises]` — `[vies prises]` **{cb.name}**",
        view=view,
    )
    view.message = msg


async def end_crewbattle(channel: discord.TextChannel, match: Match):
    guild = getattr(channel, "guild", None)
    match.state = State.FINISHED
    if match.channel_id in active_matches:
        del active_matches[match.channel_id]
    save_matches()

    winner = match.team_b if match.team_a.is_eliminated else match.team_a
    loser  = match.team_a if winner is match.team_b else match.team_b
    await update_log(match.log_row, "Completed",
                     f"**{winner.name}** remporte la CrewBattle contre **{loser.name}**")

    # Si match officiel : mettre à jour le classement
    official = load_official_match(match.channel_id)
    if official:
        season = load_season()
        if season:
            league = official["league"]
            winner_lives = winner.total_lives
            update_standings(season, league, winner.name, loser.name, winner_lives)

            # Marquer le match comme joué dans le calendrier
            ji, mi = official["journee_idx"], official["match_idx"]
            try:
                season["calendar"][league][ji][mi]["result"] = {
                    "winner": winner.name,
                    "loser":  loser.name,
                    "winner_lives": winner_lives,
                }
            except (IndexError, KeyError):
                pass

            # Résoudre un barrage si c'en est un
            if official.get("is_barrage"):
                for b in season.get("barrages", []):
                    teams = {b["team_high"], b["team_low"]}
                    if winner.name in teams and loser.name in teams:
                        b["result"] = {"winner": winner.name, "loser": loser.name}
                        # Swap de ligue si le challenger (team_low) gagne
                        if winner.name == b["team_low"]:
                            season["leagues"][b["league_high"]].append(b["team_low"])
                            season["leagues"][b["league_low"]].remove(b["team_low"])
                            season["leagues"][b["league_low"]].append(b["team_high"])
                            season["leagues"][b["league_high"]].remove(b["team_high"])
                        break

            save_season(season)

            if guild:
                from utils.players_stats import refresh_team_stats_post
                from utils.standings_channel import refresh_standings_channel
                if _bot_ref:
                    await refresh_team_stats_post(_bot_ref, guild.id, winner.name)
                    await refresh_team_stats_post(_bot_ref, guild.id, loser.name)
                await refresh_standings_channel(guild)

        delete_official_match(match.channel_id)

        # Supprimer le salon après le match officiel
        try:
            await channel.delete(reason="Match officiel terminé")
        except Exception:
            pass

    # ── Résumé freeplay ──────────────────────────────────────────────────────
    from utils.freeplay_data import load_freeplay_active, save_freeplay_active
    from cogs.teams import load_team as _lt
    freeplay_info = load_freeplay_active(match.channel_id)
    if freeplay_info:
        for sigle_key in ("team_a_sigle", "team_b_sigle"):
            sigle = freeplay_info.get(sigle_key, "")
            team_data = _lt(sigle) if sigle else None
            if not team_data:
                continue
            hist_ch = guild.get_channel(team_data["channels"]["historique"]) if guild else None
            if not hist_ch:
                continue
            try:
                hist_lines = build_history_lines(match, guild)
                hist_lines.append("")
                hist_lines.append(
                    f"**{match.team_a.name}** [{match.team_a.total_lives}"
                    f"-{match.team_b.total_lives}] **{match.team_b.name}**"
                )
                hist_lines.append(f"🏆 **{winner.name}** remporte le Freeplay !")
                hist_embed = discord.Embed(
                    title=f"📋 Freeplay — {match.team_a.name} vs {match.team_b.name}",
                    description="\n".join(hist_lines),
                    color=discord.Color.gold(),
                )
                await hist_ch.send(embed=hist_embed)
            except Exception as e:
                print(f"[WARN] freeplay hist post ({sigle}): {e}")

    lines = build_history_lines(match, guild)
    lines.append("")
    lines.append(
        f"**{match.team_a.name}** [{match.team_a.total_lives}-{match.team_b.total_lives}] **{match.team_b.name}**"
    )
    lines.append(f"🏆 **{winner.name}** remporte la CrewBattle !")

    embed = discord.Embed(
        title="🏆 Fin de la CrewBattle !",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    msg = await channel.send(embed=embed)
    try:
        await msg.pin()
    except Exception:
        pass

    if freeplay_info:
        # Les salons restent en place : "Terminer la CB" ne fait qu'envoyer le
        # résumé ; seul un admin peut ensuite supprimer les salons.
        freeplay_info["finished"] = True
        freeplay_info["summary_lines"] = lines
        save_freeplay_active(match.channel_id, freeplay_info)

        if guild:
            from cogs.freeplay import FinishCBView, FINISH_CB_PROMPT
            try:
                finish_msg = await channel.send(FINISH_CB_PROMPT, view=FinishCBView(match.channel_id))
                freeplay_info["finish_msg_id"] = finish_msg.id
                save_freeplay_active(match.channel_id, freeplay_info)
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_force_score", description="[ADMIN] Saisie manuelle du score du set en cours")
@app_commands.describe(
    vies_prises_a=f"Vies prises par le joueur A",
    vies_prises_b=f"Vies prises par le joueur B",
)
async def cbl_force_score(interaction: discord.Interaction, vies_prises_a: int, vies_prises_b: int):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("❌ Commande réservée à l'admin.", ephemeral=True)
        return

    match = active_matches.get(interaction.channel_id)
    if not match:
        await interaction.response.send_message("❌ Aucune CrewBattle en cours.", ephemeral=True)
        return
    if match.state != State.WAITING_RESULT:
        await interaction.response.send_message(
            f"❌ Le match n'attend pas de résultat (état : {match.state.name}).", ephemeral=True
        )
        return

    ca = match.current_a
    cb = match.current_b

    if vies_prises_a < 0 or vies_prises_b < 0:
        await interaction.response.send_message("❌ Les valeurs ne peuvent pas être négatives.", ephemeral=True)
        return
    if vies_prises_a > cb.lives or vies_prises_b > ca.lives:
        await interaction.response.send_message(
            f"❌ Impossible : {ca.name} a {ca.lives}♥, {cb.name} a {cb.lives}♥.", ephemeral=True
        )
        return

    new_a = ca.lives - vies_prises_b
    new_b = cb.lives - vies_prises_a

    if new_a == 0 and new_b == 0:
        await interaction.response.send_message("❌ Les deux joueurs ne peuvent pas être éliminés simultanément.", ephemeral=True)
        return
    if new_a != 0 and new_b != 0:
        await interaction.response.send_message("❌ Un des joueurs doit atteindre 0 vie.", ephemeral=True)
        return

    loser_side = "A" if new_a == 0 else "B"

    await interaction.response.defer()

    match.set_history.append(SetRecord(
        player_a=ca.name, char_a=ca.character,
        player_b=cb.name, char_b=cb.character,
        score_a=vies_prises_a, score_b=vies_prises_b,
        stage=match.picked_stage or "?",
        lives_a_after=new_a, lives_b_after=new_b,
    ))

    ca.lives = new_a
    cb.lives = new_b
    match.set_number += 1
    match.banned_stages = []
    match.picked_stage = None

    from utils.players_stats import record_set_result
    record_set_result(ca.discord_id, cb.discord_id, vies_prises_a, vies_prises_b)

    channel = interaction.channel
    guild = getattr(channel, "guild", None)

    if guild and _bot_ref:
        from utils.players_stats import refresh_after_set
        await refresh_after_set(_bot_ref, guild.id, ca.discord_id, cb.discord_id)

    winner_name = cb.name if loser_side == "A" else ca.name
    rec = match.set_history[-1]
    history_lines = build_history_lines(match, guild)
    current_score = (
        f"**{match.team_a.name}** `{match.team_a.total_lives}`"
        f" — `{match.team_b.total_lives}` **{match.team_b.name}**"
    )

    embed = discord.Embed(title=f"⚙️ [FORCE] Set {match.set_number} — {winner_name} gagne !", color=discord.Color.orange())
    embed.add_field(
        name="Résultat",
        value=(
            f"**{rec.player_a}** {char_display(rec.char_a)} **{rec.score_a}**-**{rec.score_b}**"
            f" {char_display(rec.char_b)} **{rec.player_b}**"
        ),
        inline=False,
    )
    embed.add_field(name="Score global", value=current_score, inline=False)
    embed.add_field(name="Historique", value="\n".join(history_lines), inline=False)
    await interaction.followup.send(embed=embed)
    await log_command(interaction.user.display_name,
                      f"cbl_force_score {vies_prises_a}-{vies_prises_b}", "Completed",
                      f"[FORCE] Set {match.set_number} : **{ca.name}** {vies_prises_a}-{vies_prises_b} **{cb.name}**")

    if match.team_a.is_eliminated or match.team_b.is_eliminated:
        await end_crewbattle(channel, match)
        return

    winner_side = "B" if loser_side == "A" else "A"
    match.first_banner = winner_side
    match.picker = loser_side
    loser_team = match.team_a if loser_side == "A" else match.team_b
    match.state = State.LOSER_PICK
    save_matches()

    view = LoserPickView(match=match, loser_side=loser_side)
    msg = await channel.send(f"⚔️ **{loser_team.name}** — choisissez votre prochain joueur !", view=view)
    view.message = msg

# ---------------------------------------------------------------------------
# MatchControlView  (boutons ▶️ Lancer / 📊 Statut dans les salons de match)
# Portée depuis la version GitHub ; le bug current_player (attribut absent de
# Team) a été corrigé pour utiliser match.current_a/current_b comme le reste
# du fichier.
# ---------------------------------------------------------------------------

class MatchControlView(discord.ui.View):
    """View persistante postée dans chaque salon de match officiel."""

    def __init__(self):
        super().__init__(timeout=None)

        start_btn = discord.ui.Button(
            label="▶️ Lancer le match",
            style=discord.ButtonStyle.success,
            custom_id="match_start",
        )
        start_btn.callback = self._start
        self.add_item(start_btn)

        status_btn = discord.ui.Button(
            label="📊 Statut",
            style=discord.ButtonStyle.secondary,
            custom_id="match_status",
        )
        status_btn.callback = self._status
        self.add_item(status_btn)

    async def _start(self, interaction: discord.Interaction):
        if interaction.channel_id in active_matches:
            await interaction.response.send_message(
                "❌ Une CrewBattle est déjà en cours sur ce salon.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "❌ Le lancement manuel d'une CrewBattle depuis ce salon n'est plus disponible.",
            ephemeral=True,
        )

    async def _status(self, interaction: discord.Interaction):
        match = active_matches.get(interaction.channel_id)
        if not match:
            await interaction.response.send_message("ℹ️ Aucune CrewBattle en cours.", ephemeral=True)
            return

        guild = interaction.guild
        embed = discord.Embed(title="📊 État de la CrewBattle", color=discord.Color.blurple())
        embed.add_field(
            name="Score global",
            value=(
                f"**{match.team_a.name}** `{match.team_a.total_lives}` — "
                f"`{match.team_b.total_lives}` **{match.team_b.name}**"
            ),
            inline=False,
        )

        def team_field(team: Team, current: Optional[Player]) -> str:
            lines = []
            for p in team.players:
                if p.lives == 0:
                    status = "💀"
                elif current and p.name == current.name:
                    status = "⚔️"
                else:
                    status = f"`{p.lives}` stock(s)"
                lines.append(f"• **{p.name}** — {status}")
            if team.subs:
                lines.append(f"*Remplaçants : {', '.join(p.name for p in team.subs)}*")
            return "\n".join(lines) or "—"

        embed.add_field(
            name=f"Équipe {match.team_a.name}",
            value=team_field(match.team_a, match.current_a),
            inline=True,
        )
        embed.add_field(
            name=f"Équipe {match.team_b.name}",
            value=team_field(match.team_b, match.current_b),
            inline=True,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

async def restore_all_matches(bot: commands.Bot) -> int:
    """Recharge match_state.json et reposte les boutons de chaque match en cours.

    Utilisé au démarrage du bot ET par /cbl_setup_all (reprise manuelle).
    Renvoie le nombre de matchs restaurés.
    """
    global active_matches
    await bot.wait_until_ready()
    loaded = _load_matches_from_file()
    active_matches.update(loaded)
    for match in loaded.values():
        try:
            await _restore_match_view(bot, match)
        except Exception as e:
            print(f"[WARN] restore match {match.channel_id}: {e}")
    return len(loaded)


class CrewBattle(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        global _bot_ref
        _bot_ref = self.bot
        self.bot.loop.create_task(restore_all_matches(self.bot))
        self.bot.add_view(MatchControlView())
        self.bot.tree.add_command(cbl_force_score)


async def setup(bot: commands.Bot):
    await bot.add_cog(CrewBattle(bot))
