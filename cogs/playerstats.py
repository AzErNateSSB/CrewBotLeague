import json
import os
import discord
from discord.ext import commands
from discord import app_commands

from cogs.teams import load_player, save_player, load_team, save_team, find_team_of_player
from utils.sheets_log import log_command
from utils.teams_lu import refresh_team_lu

DEFAULT_STATS = {
    "sets_played": 0,
    "sets_won": 0,
    "sets_lost": 0,
    "stocks_taken": 0,
    "stocks_lost": 0,
}

# ---------------------------------------------------------------------------
# Schéma de données (data/players/*.json étendu)
# ---------------------------------------------------------------------------

def ensure_player_defaults(player: dict) -> dict:
    player.setdefault("main", None)
    player.setdefault("stats", {})
    for k, v in DEFAULT_STATS.items():
        player["stats"].setdefault(k, v)
    player.setdefault("post_channel_id", None)
    player.setdefault("post_message_id", None)
    player.setdefault("post_archived", False)
    return player


async def _get_thread(guild: discord.Guild, thread_id):
    if not thread_id:
        return None
    thread = guild.get_thread(thread_id)
    if thread:
        return thread
    try:
        return await guild.fetch_channel(thread_id)
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

def _main_display(player: dict) -> str:
    """Emote du perso si disponible (via le serveur d'emotes de crewbattle.py), sinon nom brut."""
    if not player.get("main"):
        return "*Non défini*"
    from cogs.crewbattle import char_display
    return char_display(player["main"])


def build_player_embed(player: dict) -> discord.Embed:
    ensure_player_defaults(player)
    stats = player["stats"]
    embed = discord.Embed(title=f"📊 {player.get('name', '?')}", color=discord.Color.blurple())
    embed.add_field(name="Main", value=_main_display(player), inline=True)
    embed.add_field(name="CB jouées", value=str(stats["sets_played"]), inline=True)
    embed.add_field(name="Stocks pris", value=str(stats["stocks_taken"]), inline=True)
    return embed


def build_detailed_stats_embed(player: dict) -> discord.Embed:
    ensure_player_defaults(player)
    stats = player["stats"]

    embed = discord.Embed(
        title=f"📊 Statistiques détaillées — {player.get('name', '?')}",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Main", value=_main_display(player), inline=False)
    embed.add_field(name="CB jouées", value=str(stats["sets_played"]), inline=True)
    embed.add_field(name="Stocks pris", value=str(stats["stocks_taken"]), inline=True)
    return embed


def build_team_stats_embed(team: dict) -> discord.Embed:
    from utils.season_data import load_season

    sigle = team["sigle"]
    season = load_season()
    embed = discord.Embed(title=f"📈 Statistiques — {sigle}", color=discord.Color.gold())

    league = team.get("league")
    standings = season.get("standings", {}) if season else {}
    if league and league in standings and sigle in standings[league]:
        s = standings[league][sigle]
        diff = s["lives_scored"] - s["lives_conceded"]
        sign = "+" if diff >= 0 else ""
        embed.add_field(
            name=f"Saison en cours ({league})",
            value=(
                f"{s['pts']} pts | {s['wins']}V / {s['losses']}D | "
                f"stocks : {s['lives_scored']} pris / {s['lives_conceded']} perdus (diff {sign}{diff})"
            ),
            inline=False,
        )
    else:
        embed.add_field(name="Saison en cours", value="*Pas encore de match officiel joué.*", inline=False)

    ranked = []
    for mid in team.get("members", []):
        p = load_player(mid)
        if p:
            ensure_player_defaults(p)
            ranked.append((p.get("name", str(mid)), p["stats"]["stocks_taken"]))
    ranked.sort(key=lambda r: r[1], reverse=True)
    if ranked:
        lines = [f"**{name}** — {taken} stocks pris" for name, taken in ranked[:10]]
        embed.add_field(name="Top joueurs (stocks pris, à vie)", value="\n".join(lines), inline=False)

    return embed

# ---------------------------------------------------------------------------
# Cycle de vie du post joueur
# ---------------------------------------------------------------------------

async def create_player_post(guild: discord.Guild, forum: discord.ForumChannel, member: discord.Member, team: dict):
    player = load_player(member.id) or {"discord_id": member.id, "name": member.display_name, "team": team["sigle"]}
    ensure_player_defaults(player)

    view = PlayerPostView(member.id)
    thread, message = await forum.create_thread(
        name=member.display_name,
        embed=build_player_embed(player),
        view=view,
    )

    player["post_channel_id"] = thread.id
    player["post_message_id"] = message.id
    player["post_archived"] = False
    save_player(player)
    return thread, message


async def ensure_player_post(guild: discord.Guild, member: discord.Member, team: dict):
    """Crée le post du joueur s'il n'en a pas déjà un actif."""
    player = load_player(member.id)
    if player:
        ensure_player_defaults(player)
        if player.get("post_message_id") and not player.get("post_archived"):
            return

    forum_id = team["channels"].get("players_forum")
    forum = guild.get_channel(forum_id) if forum_id else None
    if not isinstance(forum, discord.ForumChannel):
        return

    await create_player_post(guild, forum, member, team)


async def archive_player_post(guild: discord.Guild, player: dict):
    if not player or not player.get("post_channel_id") or player.get("post_archived"):
        return
    thread = await _get_thread(guild, player["post_channel_id"])
    if thread:
        try:
            await thread.edit(archived=True, locked=True, reason="Joueur a quitté l'équipe")
        except Exception:
            pass
    player["post_archived"] = True
    save_player(player)


async def refresh_player_post(guild: discord.Guild, player: dict):
    if not player or not player.get("post_message_id") or player.get("post_archived"):
        return
    thread = await _get_thread(guild, player["post_channel_id"])
    if not thread:
        return
    try:
        msg = await thread.fetch_message(player["post_message_id"])
        await msg.edit(embed=build_player_embed(player))
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Cycle de vie du post stats d'équipe (épinglé)
# ---------------------------------------------------------------------------

async def create_team_stats_post(guild: discord.Guild, forum: discord.ForumChannel, team: dict):
    thread, message = await forum.create_thread(
        name=f"📈 {team['sigle']} — Stats d'équipe",
        embed=build_team_stats_embed(team),
    )
    team["stats_channel_id"] = thread.id
    team["stats_message_id"] = message.id
    save_team(team)
    try:
        await thread.edit(pinned=True)
    except Exception:
        pass
    return thread, message


async def ensure_team_stats_post(guild: discord.Guild, team: dict):
    if team.get("stats_message_id"):
        return
    forum_id = team["channels"].get("players_forum")
    forum = guild.get_channel(forum_id) if forum_id else None
    if not isinstance(forum, discord.ForumChannel):
        return
    await create_team_stats_post(guild, forum, team)


async def refresh_team_stats_post(guild: discord.Guild, team_sigle: str):
    team = load_team(team_sigle)
    if not team or not team.get("stats_message_id"):
        return
    thread = await _get_thread(guild, team.get("stats_channel_id"))
    if not thread:
        return
    try:
        msg = await thread.fetch_message(team["stats_message_id"])
        await msg.edit(embed=build_team_stats_embed(team))
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Stats de set (appelé depuis crewbattle.py après chaque set)
# ---------------------------------------------------------------------------

def _update_one(discord_id: int, took: int, lost: int, won: bool):
    if not discord_id:
        return
    player = load_player(discord_id) or {"discord_id": discord_id, "name": str(discord_id), "team": None}
    ensure_player_defaults(player)
    player["stats"]["sets_played"] += 1
    player["stats"]["sets_won" if won else "sets_lost"] += 1
    player["stats"]["stocks_taken"] += took
    player["stats"]["stocks_lost"] += lost
    save_player(player)


def record_set_result(discord_id_a: int, discord_id_b: int, takes_a: int, takes_b: int, a_won: bool):
    """Met à jour les stats persistantes des deux joueurs après un set (pas d'appel Discord ici)."""
    _update_one(discord_id_a, took=takes_a, lost=takes_b, won=a_won)
    _update_one(discord_id_b, took=takes_b, lost=takes_a, won=not a_won)


async def refresh_after_set(guild: discord.Guild, discord_id_a: int, discord_id_b: int):
    """Rafraîchit les posts joueur des deux participants (best-effort)."""
    for discord_id in (discord_id_a, discord_id_b):
        if not discord_id:
            continue
        player = load_player(discord_id)
        if player:
            await refresh_player_post(guild, player)

# ---------------------------------------------------------------------------
# Vues persistantes
# ---------------------------------------------------------------------------

class PlayerPostView(discord.ui.View):
    def __init__(self, discord_id: int):
        super().__init__(timeout=None)
        self.discord_id = discord_id

        main_btn = discord.ui.Button(
            label="🎮 Définir un Main", style=discord.ButtonStyle.secondary,
            custom_id=f"pstats_main_{discord_id}",
        )
        main_btn.callback = self._main
        stats_btn = discord.ui.Button(
            label="📊 Statistiques", style=discord.ButtonStyle.primary,
            custom_id=f"pstats_stats_{discord_id}",
        )
        stats_btn.callback = self._stats
        leave_btn = discord.ui.Button(
            label="🚪 Quitter l'équipe", style=discord.ButtonStyle.danger,
            custom_id=f"pstats_leave_{discord_id}",
        )
        leave_btn.callback = self._leave

        self.add_item(main_btn)
        self.add_item(stats_btn)
        self.add_item(leave_btn)

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.discord_id

    async def _main(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Ce n'est pas ton post.", ephemeral=True)
            return
        view = MainPickView(self.discord_id)
        await interaction.response.send_message("Choisis ton main :", view=view, ephemeral=True)

    async def _stats(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Ce n'est pas ton post.", ephemeral=True)
            return
        player = load_player(self.discord_id) or {"discord_id": self.discord_id, "name": interaction.user.display_name, "team": None}
        await interaction.response.send_message(embed=build_detailed_stats_embed(player), ephemeral=True)

    async def _leave(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message("❌ Ce n'est pas ton post.", ephemeral=True)
            return
        await _handle_leave_click(interaction, self.discord_id)


class MainPickView(discord.ui.View):
    def __init__(self, discord_id: int, page: int = 0):
        super().__init__(timeout=120)
        self.discord_id = discord_id
        self.page = page
        self._build()

    def _get_emoji(self, client: discord.Client, name: str):
        from cogs.crewbattle import EMOJI_SERVER_ID
        guild = client.get_guild(EMOJI_SERVER_ID)
        return discord.utils.get(guild.emojis, name=name) if guild else None

    def _build(self, client: discord.Client = None):
        from cogs.crewbattle import CHARACTER_PAGES
        self.clear_items()
        page_name, chars = CHARACTER_PAGES[self.page]

        for i, char in enumerate(chars):
            emoji = self._get_emoji(client, char) if client else None
            btn = discord.ui.Button(
                label="​" if emoji else char, emoji=emoji or None,
                style=discord.ButtonStyle.secondary, row=i // 5,
            )
            btn.callback = self._make_cb(char)
            self.add_item(btn)

        nb_pages = len(CHARACTER_PAGES)
        prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary,
                                     disabled=self.page == 0, row=4)
        prev_btn.callback = self._prev
        self.add_item(prev_btn)
        info_btn = discord.ui.Button(
            label=f"{page_name} ({self.page + 1}/{nb_pages})",
            style=discord.ButtonStyle.secondary, disabled=True, row=4,
        )
        self.add_item(info_btn)
        next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary,
                                     disabled=self.page == nb_pages - 1, row=4)
        next_btn.callback = self._next
        self.add_item(next_btn)

    def _make_cb(self, char: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.discord_id:
                await interaction.response.send_message("❌ Ce n'est pas ton profil.", ephemeral=True)
                return
            player = load_player(self.discord_id) or {
                "discord_id": self.discord_id, "name": interaction.user.display_name, "team": None
            }
            ensure_player_defaults(player)
            player["main"] = char
            save_player(player)
            await interaction.response.edit_message(content=f"✅ Main défini : **{char}**", view=None)
            await refresh_player_post(interaction.guild, player)
        return cb

    async def _prev(self, interaction: discord.Interaction):
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("❌ Ce n'est pas ton profil.", ephemeral=True)
            return
        self.page -= 1
        self._build(interaction.client)
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("❌ Ce n'est pas ton profil.", ephemeral=True)
            return
        self.page += 1
        self._build(interaction.client)
        await interaction.response.edit_message(view=self)


class LeaveConfirmView(discord.ui.View):
    def __init__(self, team_sigle: str, discord_id: int):
        super().__init__(timeout=60)
        self.team_sigle = team_sigle
        self.discord_id = discord_id

    @discord.ui.button(label="✅ Oui, quitter", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await _leave_team(interaction, self.discord_id, self.team_sigle)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Annulé.", view=self)


class SuccessorPickView(discord.ui.View):
    def __init__(self, team_sigle: str, discord_id: int, candidates: list[tuple[int, str]]):
        super().__init__(timeout=120)
        self.team_sigle = team_sigle
        self.discord_id = discord_id

        select = discord.ui.Select(
            placeholder="Choisis le prochain leader...",
            options=[discord.SelectOption(label=name, value=str(mid)) for mid, name in candidates[:25]],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.discord_id:
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        new_leader_id = int(interaction.data["values"][0])
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await _transfer_leadership_and_leave(interaction, self.team_sigle, self.discord_id, new_leader_id)

# ---------------------------------------------------------------------------
# Logique "Quitter l'équipe"
# ---------------------------------------------------------------------------

async def _handle_leave_click(interaction: discord.Interaction, discord_id: int):
    team_sigle = find_team_of_player(discord_id)
    if not team_sigle:
        await interaction.response.send_message("❌ Tu n'es dans aucune équipe.", ephemeral=True)
        return
    team = load_team(team_sigle)

    if team["leader_id"] == discord_id:
        guild = interaction.guild
        others = [m for m in team.get("members", []) if m != discord_id]
        if not others:
            await interaction.response.send_message(
                "❌ Tu es seul dans l'équipe. Utilise `/cbl_del_team` pour la dissoudre.",
                ephemeral=True,
            )
            return
        candidates = []
        for mid in others:
            m = guild.get_member(mid)
            candidates.append((mid, m.display_name if m else str(mid)))
        view = SuccessorPickView(team_sigle, discord_id, candidates)
        await interaction.response.send_message(
            "👑 Tu es le leader. Choisis qui reprend le leadership avant de partir :",
            view=view, ephemeral=True,
        )
        return

    view = LeaveConfirmView(team_sigle, discord_id)
    await interaction.response.send_message(
        f"⚠️ Confirmes-tu vouloir quitter **{team_sigle}** ?", view=view, ephemeral=True,
    )


async def _leave_team(interaction: discord.Interaction, discord_id: int, team_sigle: str):
    team = load_team(team_sigle)
    if team and discord_id in team.get("members", []):
        team["members"].remove(discord_id)
        save_team(team)

    player = load_player(discord_id)
    if player:
        player["team"] = None
        save_player(player)

    guild = interaction.guild
    member = guild.get_member(discord_id)
    role = guild.get_role(team["role_id"]) if team else None
    if role and member:
        try:
            await member.remove_roles(role)
        except discord.Forbidden:
            pass

    if team:
        await refresh_team_lu(interaction.client, interaction.guild_id, team)
    if player:
        await archive_player_post(guild, player)

    await interaction.followup.send(f"👋 Tu as quitté **{team_sigle}**.", ephemeral=True)
    await log_command(
        interaction.user.display_name, f"cbl_leave **{team_sigle}**", "Completed",
        f"**{interaction.user.display_name}** a quitté **{team_sigle}**",
    )


async def _transfer_leadership_and_leave(interaction: discord.Interaction, team_sigle: str,
                                          old_leader_id: int, new_leader_id: int):
    team = load_team(team_sigle)
    team["leader_id"] = new_leader_id
    save_team(team)
    await refresh_team_lu(interaction.client, interaction.guild_id, team)

    guild = interaction.guild
    new_leader = guild.get_member(new_leader_id)
    old_leader = guild.get_member(old_leader_id)

    tasks_ch_id = team["channels"].get("tasks")
    tasks_ch = guild.get_channel(tasks_ch_id) if tasks_ch_id else None
    if tasks_ch:
        try:
            await tasks_ch.send(
                f"👑 **{new_leader.display_name if new_leader else new_leader_id}** est le nouveau leader de "
                f"**{team_sigle}** (succède à **{old_leader.display_name if old_leader else old_leader_id}**)."
            )
        except Exception:
            pass

    await _leave_team(interaction, old_leader_id, team_sigle)
    await log_command(
        interaction.user.display_name, f"cbl_leave (leadership) **{team_sigle}**", "Completed",
        f"**{old_leader.display_name if old_leader else old_leader_id}** a transmis le leadership à "
        f"**{new_leader.display_name if new_leader else new_leader_id}** puis a quitté **{team_sigle}**",
    )

# ---------------------------------------------------------------------------
# Reprise des vues persistantes (aucun appel réseau nécessaire)
# ---------------------------------------------------------------------------

def restore_all_player_views(bot: commands.Bot) -> int:
    players_dir = os.path.join("data", "players")
    if not os.path.exists(players_dir):
        return 0
    count = 0
    for fn in os.listdir(players_dir):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(players_dir, fn), encoding="utf-8") as f:
            player = json.load(f)
        if player.get("post_archived") or not player.get("post_message_id"):
            continue
        view = PlayerPostView(player["discord_id"])
        bot.add_view(view, message_id=player["post_message_id"])
        count += 1
    return count

# ---------------------------------------------------------------------------
# /cbl_refresh_all_stats
# ---------------------------------------------------------------------------

@app_commands.command(
    name="cbl_refresh_all_stats",
    description="[ADMIN] Réactualise tous les posts stats (joueurs + équipes)",
)
async def cbl_refresh_all_stats(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    nb_players = 0
    players_dir = os.path.join("data", "players")
    if os.path.exists(players_dir):
        for fn in sorted(os.listdir(players_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(players_dir, fn), encoding="utf-8") as f:
                player = json.load(f)
            if player.get("post_archived") or not player.get("post_message_id"):
                continue
            await refresh_player_post(guild, player)
            nb_players += 1

    nb_teams = 0
    teams_dir = os.path.join("data", "teams")
    if os.path.exists(teams_dir):
        for fn in sorted(os.listdir(teams_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(teams_dir, fn), encoding="utf-8") as f:
                team = json.load(f)
            if not team.get("stats_message_id"):
                continue
            await refresh_team_stats_post(guild, team["sigle"])
            nb_teams += 1

    await interaction.followup.send(
        f"✅ {nb_players} post(s) joueur et {nb_teams} post(s) d'équipe réactualisés.",
        ephemeral=True,
    )
    await log_command(
        interaction.user.display_name, "cbl_refresh_all_stats", "Completed",
        f"{nb_players} posts joueur + {nb_teams} posts équipe réactualisés par **{interaction.user.display_name}**",
    )

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class PlayerStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        restore_all_player_views(self.bot)
        self.bot.tree.add_command(cbl_refresh_all_stats)


async def setup(bot: commands.Bot):
    await bot.add_cog(PlayerStats(bot))
