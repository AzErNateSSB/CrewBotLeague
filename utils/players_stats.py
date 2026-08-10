"""
Système de stats joueurs dans les forums players-stats.
Un post (thread) par joueur + un post d'équipe par équipe.
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

class SetMainModal(discord.ui.Modal, title="Définir ton main"):
    personnage = discord.ui.TextInput(
        label="Personnage",
        placeholder="Ex: Kirby, Link, Sora, BanjoKazooie...",
        max_length=60,
    )

    def __init__(self, player_id: int, thread: discord.Thread):
        super().__init__()
        self.player_id = player_id
        self.thread    = thread

    async def on_submit(self, interaction: discord.Interaction):
        raw        = self.personnage.value.strip()
        normalized = raw.lower().replace(" ", "").replace("-", "")
        emoji_str  = CHAR_EMOJIS.get(normalized)
        main_value = emoji_str if emoji_str else raw

        player = _load_player(self.player_id)
        if not player:
            await interaction.response.send_message("❌ Profil joueur introuvable.", ephemeral=True)
            return

        player.setdefault("stats", {})
        player["stats"]["main"] = main_value
        _save_player(player)

        # Met à jour l'embed dans le thread
        async for msg in self.thread.history(limit=5, oldest_first=True):
            if msg.author == interaction.guild.me and msg.embeds:
                old   = msg.embeds[0]
                name  = old.title.removeprefix("📊 ") if old.title else player.get("name", "?")
                stats = player.get("stats", {})
                embed = make_player_embed(
                    name,
                    main_value,
                    stats.get("cb_joues", 0),
                    stats.get("stocks_pris", 0),
                )
                await msg.edit(embed=embed)
                break

        char_display = emoji_str if emoji_str else f"`{raw}`"
        await interaction.response.send_message(f"✅ Main défini : {char_display}", ephemeral=True)


# ---------------------------------------------------------------------------
# Boutons de la View persistante
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
        await interaction.response.send_modal(SetMainModal(view.player_id, thread))


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
        from cogs.teams import (
            find_team_of_player, load_team, save_team,
            load_player as lp, save_player as sp,
        )
        from utils.teams_lu import refresh_team_lu

        view: PlayerStatsView = self.view

        team_sigle = find_team_of_player(view.player_id)
        if not team_sigle:
            await interaction.response.send_message("❌ Tu n'es dans aucune équipe.", ephemeral=True)
            return

        team = load_team(team_sigle)
        if team["leader_id"] == view.player_id:
            await interaction.response.send_message(
                "❌ Tu es le leader — transfère d'abord le leadership.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        if view.player_id in team["members"]:
            team["members"].remove(view.player_id)
        save_team(team)

        player = lp(view.player_id)
        if player:
            player["team"] = None
            sp(player)

        role = interaction.guild.get_role(team["role_id"])
        if role:
            try:
                await interaction.user.remove_roles(role)
            except discord.Forbidden:
                pass

        await refresh_team_lu(interaction.client, interaction.guild_id, team)
        await interaction.followup.send(f"✅ Tu as quitté l'équipe **{team_sigle}**.", ephemeral=True)

        await delete_player_stats_post(interaction.client, interaction.guild_id, view.player_id)
        await refresh_team_stats_post(interaction.client, interaction.guild_id, team_sigle)


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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
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

    twm    = await forum.create_thread(name=f"📈 {sigle} — Stats d'équipe", embed=embed)
    thread = twm.thread

    team["stats_thread_id"] = thread.id
    _save_team(team)


async def refresh_team_stats_post(
    bot: discord.Client,
    guild_id: int,
    sigle: str,
) -> None:
    """Met à jour l'embed du post de stats d'équipe."""
    team = _load_team(sigle)
    if not team:
        return

    thread_id = team.get("stats_thread_id")
    if not thread_id:
        return

    guild  = bot.get_guild(guild_id)
    thread = guild.get_thread(thread_id)
    if not thread:
        try:
            thread = await guild.fetch_channel(thread_id)
        except Exception:
            return

    members_data = _get_members_stats(team)
    embed        = make_team_stats_embed(sigle, members_data)

    async for msg in thread.history(limit=5, oldest_first=True):
        if msg.author == guild.me and msg.embeds:
            await msg.edit(embed=embed)
            return


async def delete_player_stats_post(
    bot: discord.Client,
    guild_id: int,
    player_id: int,
) -> None:
    """Supprime le thread de stats d'un joueur."""
    player = _load_player(player_id)
    if not player:
        return

    thread_id = player.get("stats_thread_id")
    if thread_id:
        guild  = bot.get_guild(guild_id)
        thread = guild.get_thread(thread_id)
        if not thread:
            try:
                thread = await guild.fetch_channel(thread_id)
            except Exception:
                thread = None
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
        twm_team     = await forum.create_thread(name=f"📈 {sigle} — Stats d'équipe", embed=t_embed)
        team["stats_thread_id"] = twm_team.thread.id
        _save_team(team)

        # Posts joueurs
        count = 0
        for member_id in team.get("members", []):
            member = guild.get_member(member_id)
            if not member:
                try:
                    member = await guild.fetch_member(member_id)
                except Exception:
                    continue
            player = _load_player(member_id) or {"discord_id": member_id, "name": member.display_name}
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
            count += 1

        report.append(f"✅ **{sigle}** — {count} joueur(s) + post équipe")

    return "\n".join(report) if report else "Aucune équipe trouvée."


def register_all_views(bot: discord.Client) -> int:
    """Enregistre les PlayerStatsView pour tous les joueurs ayant un stats_thread_id."""
    if not os.path.exists(PLAYERS_DIR):
        return 0
    count = 0
    for filename in os.listdir(PLAYERS_DIR):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(PLAYERS_DIR, filename), encoding="utf-8") as f:
            player = json.load(f)
        if player.get("stats_thread_id"):
            bot.add_view(PlayerStatsView(player["discord_id"]))
            count += 1
    return count


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
