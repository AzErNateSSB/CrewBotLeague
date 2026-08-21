import discord
from discord.ext import commands
from discord import app_commands
from datetime import date
from typing import Optional
import json
import os

from utils.i18n import t
from utils.sheets_log import log_command, update_log
from utils.teams_lu import refresh_team_lu, delete_team_lu, rebuild_teams_lu
from utils.alerts import alert_error

TEAMS_DIR   = os.path.join("data", "teams")
PLAYERS_DIR = os.path.join("data", "players")

# ---------------------------------------------------------------------------
# Filet de sécurité : rollback des objets Discord si la création d'équipe
# échoue après leur création (ex: crash au moment d'écrire le JSON).
# ---------------------------------------------------------------------------

async def rollback_new_team(role=None, category=None, channels=None):
    """Supprime au mieux les objets Discord déjà créés. N'échoue jamais."""
    for ch in (channels or []):
        if ch:
            try:
                await ch.delete()
            except Exception:
                pass
    if category:
        try:
            await category.delete()
        except Exception:
            pass
    if role:
        try:
            await role.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Salon "tasks" : seul le leader peut y écrire (les membres peuvent le lire,
# et interagir avec les boutons quand le bot les y invite explicitement).
# ---------------------------------------------------------------------------

TASKS_CHANNEL_INTRO = (
    "📋 Ce salon sert aux actions qui nécessitent le **leader** de l'équipe "
    "(demandes pour rejoindre l'équipe, etc.).\n"
    "Personne n'y écrit — on n'y interagit qu'avec les boutons que le bot "
    "y poste (le leader valide/refuse, et les membres seront ponctuellement "
    "invités à cliquer eux aussi : sélection de personnage, ban de stage...)."
)


def tasks_channel_overwrites(guild: discord.Guild, role: Optional[discord.Role], leader: Optional[discord.Member]):
    """Personne n'écrit dans ce salon (pas même le leader) — seul le bot y poste,
    tout le monde n'y interagit que via les boutons."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            read_message_history=True, manage_channels=True,
        ),
    }
    if role:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=False, read_message_history=True,
        )
    if leader:
        overwrites[leader] = discord.PermissionOverwrite(
            view_channel=True, send_messages=False, read_message_history=True,
        )
    return overwrites


def players_forum_overwrites(guild: discord.Guild, role: Optional[discord.Role], leader: Optional[discord.Member]):
    """Forum 'players & stats' : personne ne crée de post ni n'écrit — uniquement
    consultation et interaction via les boutons que le bot poste dans chaque post."""
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, send_messages_in_threads=True,
            create_public_threads=True, read_message_history=True,
            manage_channels=True, manage_threads=True,
        ),
    }
    if role:
        overwrites[role] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            send_messages=False, send_messages_in_threads=False, create_public_threads=False,
        )
    if leader:
        overwrites[leader] = discord.PermissionOverwrite(
            view_channel=True, read_message_history=True,
            send_messages=False, send_messages_in_threads=False, create_public_threads=False,
        )
    return overwrites

# ---------------------------------------------------------------------------
# Persistance des demandes de join en attente (boutons Accepter/Refuser)
# ---------------------------------------------------------------------------

JOIN_REQUESTS_FILE = os.path.join("data", "join_requests.json")


def _load_join_requests() -> dict:
    if not os.path.exists(JOIN_REQUESTS_FILE):
        return {}
    with open(JOIN_REQUESTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_join_requests(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(JOIN_REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_join_request(msg_id: int, data: dict):
    all_reqs = _load_join_requests()
    all_reqs[str(msg_id)] = data
    _save_join_requests(all_reqs)


def del_join_request(msg_id: int):
    all_reqs = _load_join_requests()
    if all_reqs.pop(str(msg_id), None) is not None:
        _save_join_requests(all_reqs)

# ---------------------------------------------------------------------------
# Helpers fichiers
# ---------------------------------------------------------------------------

def _team_path(sigle: str) -> str:
    return os.path.join(TEAMS_DIR, f"{sigle}.json")

def _player_path(discord_id: int) -> str:
    return os.path.join(PLAYERS_DIR, f"{discord_id}.json")


def load_team(sigle: str) -> dict | None:
    path = _team_path(sigle)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_team(data: dict):
    os.makedirs(TEAMS_DIR, exist_ok=True)
    with open(_team_path(data["sigle"]), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_player(discord_id: int) -> dict | None:
    path = _player_path(discord_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def save_player(data: dict):
    os.makedirs(PLAYERS_DIR, exist_ok=True)
    with open(_player_path(data["discord_id"]), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def find_team_of_player(discord_id: int) -> str | None:
    """Retourne le sigle de l'équipe du joueur, ou None."""
    if not os.path.exists(TEAMS_DIR):
        return None
    for filename in os.listdir(TEAMS_DIR):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(TEAMS_DIR, filename), encoding="utf-8") as f:
            team = json.load(f)
        if discord_id in team.get("members", []):
            return team["sigle"]
    return None

# ---------------------------------------------------------------------------
# /cbl_newplayer
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_newplayer", description="Crée ton profil joueur pour l'événement")
async def cbl_newplayer(interaction: discord.Interaction):
    gid  = interaction.guild_id
    user = interaction.user

    if load_player(user.id):
        await interaction.response.send_message(t(gid, "player_already_exists"), ephemeral=True)
        await log_command(
            user.display_name, "cbl_newplayer", "Failed",
            f"**{user.display_name}** a déjà un profil joueur"
        )
        return

    save_player({"discord_id": user.id, "name": user.display_name, "team": None})

    await interaction.response.send_message(t(gid, "player_created", name=user.display_name), ephemeral=True)
    await log_command(
        user.display_name, "cbl_newplayer", "Completed",
        f"Profil joueur créé pour **{user.display_name}**"
    )

# ---------------------------------------------------------------------------
# /cbl_newteam
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_newteam", description="Crée une nouvelle équipe et ses salons Discord")
@app_commands.describe(
    sigle="Sigle / tag de l'équipe (ex: HoJ)",
    equipe_b="Créer l'équipe B de ce sigle (ajoute ² au nom)",
)
async def cbl_newteam(
    interaction: discord.Interaction,
    sigle: str,
    equipe_b: bool = False,
):
    await interaction.response.defer(ephemeral=True)
    guild      = interaction.guild
    gid        = interaction.guild_id
    user       = interaction.user
    full_sigle = f"{sigle}²" if equipe_b else sigle
    cmd_label  = f"cbl_newteam **{full_sigle}**"

    if load_team(full_sigle):
        await interaction.followup.send(t(gid, "team_already_exists"), ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          f"La Team **{full_sigle}** existe déjà")
        return

    existing = find_team_of_player(user.id)
    if existing:
        await interaction.followup.send(t(gid, "team_already_in_team"), ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          f"**{user.display_name}** est déjà dans l'équipe **{existing}**")
        return

    # ── Équipe B : salon historique² dans la catégorie de l'équipe A ─────────
    if equipe_b:
        team_a = load_team(sigle)
        if not team_a:
            await interaction.followup.send(
                f"❌ L'équipe A **{sigle}** n'existe pas encore.", ephemeral=True
            )
            return
        category = guild.get_channel(team_a["category_id"])
        role     = guild.get_role(team_a["role_id"])
        if not category or not role:
            await interaction.followup.send("❌ Catégorie ou rôle de l'équipe A introuvable.", ephemeral=True)
            return
        try:
            await user.add_roles(role)
        except discord.Forbidden:
            pass
        try:
            forum_b = await guild.create_forum(
                name=f"〔{sigle}²〕historique", category=category
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur création du salon : {e}", ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed", str(e))
            return

        try:
            team_data = {
                "sigle":       full_sigle,
                "leader_id":   user.id,
                "role_id":     team_a["role_id"],
                "category_id": team_a["category_id"],
                "channels": {
                    "general":       team_a["channels"]["general"],
                    "historique":    forum_b.id,
                    "vocal":         team_a["channels"]["vocal"],
                    "tasks":         team_a["channels"].get("tasks"),
                    "players_forum": team_a["channels"].get("players_forum"),
                },
                "members":    [user.id],
                "league":     None,
                "created_at": date.today().isoformat(),
            }
            save_team(team_data)
            player = load_player(user.id) or {"discord_id": user.id, "name": user.display_name}
            player["team"] = full_sigle
            save_player(player)
            await refresh_team_lu(interaction.client, interaction.guild_id, team_data)

            from cogs.playerstats import ensure_team_stats_post
            await ensure_team_stats_post(guild, team_data)

            embed = discord.Embed(title=f"⚔️ Équipe **{full_sigle}** créée !", color=discord.Color.blurple())
            embed.add_field(name="Leader", value=user.mention, inline=True)
            embed.add_field(name="Rôle",   value=role.mention, inline=True)
            embed.add_field(name="Salon historique", value=forum_b.mention, inline=False)
            await interaction.followup.send(embed=embed)

            general_ch = guild.get_channel(team_a["channels"]["general"])
            if general_ch:
                await general_ch.send(
                    f"🎉 L'équipe **{full_sigle}** a été créée ! {user.mention} en est le leader."
                )
            await log_command(user.display_name, cmd_label, "Completed",
                              f"La Team **{full_sigle}** créée (équipe B de **{sigle}**) — leader : **{user.display_name}**")
        except Exception as e:
            await rollback_new_team(channels=[forum_b])
            await interaction.followup.send(f"❌ Erreur lors de la création de l'équipe : {e}", ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed", f"Erreur après création du salon : {e}")
            await alert_error(
                interaction.client,
                "Échec création équipe (B)",
                f"Commande `{cmd_label}` par **{user.display_name}**\nErreur : `{e}`\n"
                f"Le salon historique² a été supprimé (rollback).",
            )
        return

    # ── Équipe A : création complète ─────────────────────────────────────────
    try:
        role = await guild.create_role(name=f"[{sigle}]", mentionable=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ Permission refusée pour créer le rôle.", ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          "Permission refusée — création du rôle Discord")
        return

    try:
        await user.add_roles(role)
    except discord.Forbidden:
        pass

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        role: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            read_message_history=True, connect=True, speak=True,
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True,
            read_message_history=True, manage_channels=True,
        ),
    }

    try:
        category = await guild.create_category(name=f"〔{sigle}〕", overwrites=overwrites)
        general  = await guild.create_text_channel( name=f"〔{sigle}〕général",    category=category)
        forum    = await guild.create_forum(         name=f"〔{sigle}〕historique", category=category)
        vocal    = await guild.create_voice_channel( name=f"〔{sigle}〕vocal",      category=category)
        tasks    = await guild.create_text_channel(
            name=f"〔{sigle}〕tasks", category=category,
            overwrites=tasks_channel_overwrites(guild, role, user),
        )
        players_forum = await guild.create_forum(
            name=f"〔{sigle}〕players & stats", category=category,
            overwrites=players_forum_overwrites(guild, role, user),
        )
    except discord.Forbidden:
        await role.delete()
        await interaction.followup.send("❌ Permission refusée pour créer les salons.", ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          "Permission refusée — création des salons Discord")
        return
    except Exception as e:
        await role.delete()
        await interaction.followup.send(f"❌ Erreur lors de la création des salons : {e}", ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed", str(e))
        return

    try:
        team_data = {
            "sigle":       sigle,
            "leader_id":   user.id,
            "role_id":     role.id,
            "category_id": category.id,
            "channels": {
                "general":       general.id,
                "historique":    forum.id,
                "vocal":         vocal.id,
                "tasks":         tasks.id,
                "players_forum": players_forum.id,
            },
            "members":    [user.id],
            "league":     None,
            "created_at": date.today().isoformat(),
        }
        save_team(team_data)

        player = load_player(user.id) or {"discord_id": user.id, "name": user.display_name}
        player["team"] = sigle
        save_player(player)
        await refresh_team_lu(interaction.client, interaction.guild_id, team_data)

        from cogs.playerstats import ensure_player_post, ensure_team_stats_post
        await ensure_player_post(guild, user, team_data)
        await ensure_team_stats_post(guild, team_data)

        embed = discord.Embed(title=f"⚔️ Équipe **{sigle}** créée !", color=discord.Color.blurple())
        embed.add_field(name="Leader", value=user.mention, inline=True)
        embed.add_field(name="Rôle",   value=role.mention, inline=True)
        embed.add_field(
            name="Salons",
            value=f"{general.mention} · {forum.mention} · {vocal.mention} · {tasks.mention} · {players_forum.mention}",
            inline=False,
        )
        await interaction.followup.send(embed=embed)

        await general.send(
            f"🎉 Bienvenue dans le salon de **{sigle}** ! "
            f"{user.mention} en est le leader.\n"
            f"Les membres peuvent rejoindre l'équipe via `/cbl_join {sigle}`."
        )
        await tasks.send(TASKS_CHANNEL_INTRO)

        await log_command(
            user.display_name, cmd_label, "Completed",
            f"La Team **{sigle}** a été créée avec **{user.display_name}** pour Leader"
        )
    except Exception as e:
        await rollback_new_team(role=role, category=category, channels=[general, forum, vocal, tasks, players_forum])
        await interaction.followup.send(f"❌ Erreur lors de la création de l'équipe : {e}", ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed", f"Erreur après création des salons : {e}")
        await alert_error(
            interaction.client,
            "Échec création équipe",
            f"Commande `{cmd_label}` par **{user.display_name}**\nErreur : `{e}`\n"
            f"Rôle, catégorie et salons ont été supprimés (rollback).",
        )

# ---------------------------------------------------------------------------
# /cbl_join
# ---------------------------------------------------------------------------

class JoinRequestView(discord.ui.View):
    def __init__(self, applicant: discord.Member, team: dict, gid: int, log_row: int):
        super().__init__(timeout=None)
        self.applicant = applicant
        self.team      = team
        self.gid       = gid
        self.log_row   = log_row
        self.message: discord.Message | None = None

    async def _resolve(self, kept: discord.ui.Button):
        """Ne garde que le bouton cliqué (désactivé), retire l'autre, et efface la persistance."""
        self.clear_items()
        kept.disabled = True
        self.add_item(kept)
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass
            del_join_request(self.message.id)

    @discord.ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.team["leader_id"]:
            await interaction.response.send_message("❌ Seul le leader peut valider.", ephemeral=True)
            return

        current = find_team_of_player(self.applicant.id)
        if current:
            await interaction.response.send_message(
                f"❌ **{self.applicant.display_name}** a déjà rejoint une équipe.", ephemeral=True
            )
            await self._resolve(button)
            await update_log(self.log_row, "Failed",
                             f"**{self.applicant.display_name}** a rejoint **{current}** entre-temps")
            return

        team = load_team(self.team["sigle"])
        if self.applicant.id not in team["members"]:
            team["members"].append(self.applicant.id)
            save_team(team)

        player = load_player(self.applicant.id) or {
            "discord_id": self.applicant.id, "name": self.applicant.display_name
        }
        player["team"] = team["sigle"]
        save_player(player)

        role = interaction.guild.get_role(team["role_id"])
        if role:
            try:
                await self.applicant.add_roles(role)
            except discord.Forbidden:
                pass

        await refresh_team_lu(interaction.client, interaction.guild_id, team)

        from cogs.playerstats import ensure_player_post
        await ensure_player_post(interaction.guild, self.applicant, team)

        await self._resolve(button)
        await interaction.response.send_message(
            t(self.gid, "join_accepted", player=self.applicant.display_name, team=team["sigle"])
        )
        try:
            await self.applicant.send(
                t(self.gid, "join_accepted", player=self.applicant.display_name, team=team["sigle"])
            )
        except discord.Forbidden:
            pass

        await update_log(self.log_row, "Completed",
                         f"**{self.applicant.display_name}** a rejoint **{team['sigle']}** "
                         f"(accepté par **{interaction.user.display_name}**)")

    @discord.ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger)
    async def refuse(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.team["leader_id"]:
            await interaction.response.send_message("❌ Seul le leader peut refuser.", ephemeral=True)
            return

        await self._resolve(button)
        await interaction.response.send_message(
            f"❌ Demande de **{self.applicant.display_name}** refusée."
        )
        try:
            await self.applicant.send(t(self.gid, "join_refused", team=self.team["sigle"]))
        except discord.Forbidden:
            pass

        await update_log(self.log_row, "Canceled",
                         f"Demande de **{self.applicant.display_name}** refusée par **{interaction.user.display_name}**")


@app_commands.command(name="cbl_join", description="Envoie une demande pour rejoindre une équipe")
@app_commands.describe(sigle="Sigle de l'équipe à rejoindre (ex: HoJ)")
async def cbl_join(interaction: discord.Interaction, sigle: str):
    gid  = interaction.guild_id
    user = interaction.user
    cmd_label = f"cbl_join **{sigle}**"

    if not load_player(user.id):
        await interaction.response.send_message(t(gid, "join_no_profile"), ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          f"**{user.display_name}** n'a pas de profil joueur")
        return

    current = find_team_of_player(user.id)
    if current:
        await interaction.response.send_message(t(gid, "team_already_in_team"), ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          f"**{user.display_name}** est déjà dans **{current}**")
        return

    team = load_team(sigle)
    if not team:
        await interaction.response.send_message(t(gid, "team_not_found", sigle=sigle), ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          f"L'équipe **{sigle}** est introuvable")
        return

    target_channel_id = team["channels"].get("tasks") or team["channels"]["general"]
    target_channel = interaction.guild.get_channel(target_channel_id)
    if not target_channel:
        await interaction.response.send_message("❌ Salon de l'équipe introuvable.", ephemeral=True)
        await log_command(user.display_name, cmd_label, "Failed",
                          f"Salon tasks/général de **{sigle}** introuvable")
        return

    # Log In Progress — sera mis à jour par la View
    log_row = await log_command(
        user.display_name, cmd_label, "In Progress",
        f"**{user.display_name}** a demandé à rejoindre **{sigle}** — en attente du leader"
    )

    leader = interaction.guild.get_member(team["leader_id"])
    leader_mention = leader.mention if leader else f"<@{team['leader_id']}>"

    view = JoinRequestView(applicant=user, team=team, gid=gid, log_row=log_row)
    msg  = await target_channel.send(
        f"{leader_mention} — {t(gid, 'join_request_received', player=user.display_name, team=sigle)}",
        view=view,
    )
    view.message = msg
    save_join_request(msg.id, {
        "channel_id":   target_channel.id,
        "applicant_id": user.id,
        "team_sigle":   sigle,
        "log_row":      log_row,
    })

    await interaction.response.send_message(t(gid, "join_request_sent", team=sigle), ephemeral=True)

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

async def _remove_player_from_team(
    interaction: discord.Interaction,
    target: discord.Member,
    cmd_label: str,
):
    """Logique commune : retire target de son équipe."""
    gid = interaction.guild_id

    team_sigle = find_team_of_player(target.id)
    if not team_sigle:
        await interaction.followup.send(
            f"❌ **{target.display_name}** n'est dans aucune équipe.", ephemeral=True
        )
        await log_command(interaction.user.display_name, cmd_label, "Failed",
                          f"**{target.display_name}** n'est dans aucune équipe")
        return

    team = load_team(team_sigle)

    # Retirer des membres
    if target.id in team["members"]:
        team["members"].remove(target.id)
    save_team(team)

    # Mettre à jour le profil joueur
    player = load_player(target.id)
    if player:
        player["team"] = None
        save_player(player)

    # Retirer le rôle Discord
    role = interaction.guild.get_role(team["role_id"])
    if role:
        try:
            await target.remove_roles(role)
        except discord.Forbidden:
            pass

    await refresh_team_lu(interaction.client, interaction.guild_id, team)

    if player:
        from cogs.playerstats import archive_player_post
        await archive_player_post(interaction.guild, player)

    await interaction.followup.send(
        f"✅ **{target.display_name}** a été retiré de l'équipe **{team_sigle}**.", ephemeral=True
    )
    await log_command(interaction.user.display_name, cmd_label, "Completed",
                      f"**{target.display_name}** retiré de **{team_sigle}** par **{interaction.user.display_name}**")


async def _dissolve_team(
    interaction: discord.Interaction,
    team: dict,
    cmd_label: str,
):
    """Logique commune : dissout complètement une équipe."""
    guild = interaction.guild
    sigle = team["sigle"]
    is_b  = sigle.endswith("²")

    # Mettre à jour tous les profils membres (et archiver leur post stats)
    from cogs.playerstats import archive_player_post
    for member_id in team.get("members", []):
        player = load_player(member_id)
        if player:
            player["team"] = None
            save_player(player)
            await archive_player_post(guild, player)

    if is_b:
        # Équipe B : supprimer uniquement le salon historique² (catégorie et rôle partagés)
        hist_ch = guild.get_channel(team["channels"]["historique"])
        if hist_ch:
            try:
                await hist_ch.delete()
            except Exception:
                pass
    else:
        # Équipe A : supprimer rôle, salons et catégorie
        role = guild.get_role(team["role_id"])
        if role:
            try:
                await role.delete()
            except Exception:
                pass
        category = guild.get_channel(team["category_id"])
        if category:
            for ch in category.channels:
                try:
                    await ch.delete()
                except Exception:
                    pass
            try:
                await category.delete()
            except Exception:
                pass

    # Supprimer le fichier JSON de l'équipe
    path = _team_path(sigle)
    if os.path.exists(path):
        os.remove(path)

    await delete_team_lu(interaction.client, interaction.guild_id, sigle)
    await interaction.followup.send(
        f"✅ L'équipe **{sigle}** a été dissoute.", ephemeral=True
    )
    await log_command(interaction.user.display_name, cmd_label, "Completed",
                      f"L'équipe **{sigle}** dissoute par **{interaction.user.display_name}**")


@app_commands.command(name="cbl_del_team",
                      description="Dissout ton équipe (leader uniquement)")
async def cbl_del_team(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    cmd_label = "cbl_del_team"

    team_sigle = find_team_of_player(interaction.user.id)
    if not team_sigle:
        await interaction.followup.send("❌ Tu n'es dans aucune équipe.", ephemeral=True)
        return

    team = load_team(team_sigle)
    if team["leader_id"] != interaction.user.id:
        await interaction.followup.send("❌ Seul le leader peut dissoudre l'équipe.", ephemeral=True)
        await log_command(interaction.user.display_name, cmd_label, "Failed",
                          f"**{interaction.user.display_name}** n'est pas leader de **{team_sigle}**")
        return

    await _dissolve_team(interaction, team, cmd_label)


@app_commands.command(name="cbl_adm_del_team",
                      description="[ADMIN] Dissout une équipe par son sigle")
@app_commands.describe(sigle="Sigle de l'équipe à dissoudre")
async def cbl_adm_del_team(interaction: discord.Interaction, sigle: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    cmd_label = f"cbl_adm_del_team **{sigle}**"

    team = load_team(sigle)
    if not team:
        await interaction.followup.send(f"❌ L'équipe **{sigle}** est introuvable.", ephemeral=True)
        await log_command(interaction.user.display_name, cmd_label, "Failed",
                          f"L'équipe **{sigle}** introuvable")
        return

    await _dissolve_team(interaction, team, cmd_label)


@app_commands.command(name="cbl_remove_player",
                      description="Retire un joueur de ton équipe (leader uniquement)")
@app_commands.describe(joueur="Joueur à retirer")
async def cbl_remove_player(interaction: discord.Interaction, joueur: discord.Member):
    await interaction.response.defer(ephemeral=True)
    cmd_label = f"cbl_remove_player **{joueur.display_name}**"

    # Trouver l'équipe du joueur cible
    team_sigle = find_team_of_player(joueur.id)
    if not team_sigle:
        await interaction.followup.send(
            f"❌ **{joueur.display_name}** n'est dans aucune équipe.", ephemeral=True
        )
        return

    team = load_team(team_sigle)

    # Vérifier que l'auteur est le leader de cette équipe
    if interaction.user.id != team["leader_id"]:
        await interaction.followup.send(
            "❌ Tu n'es pas le leader de l'équipe de ce joueur.", ephemeral=True
        )
        await log_command(interaction.user.display_name, cmd_label, "Failed",
                          f"**{interaction.user.display_name}** n'est pas le leader de **{team_sigle}**")
        return

    # Empêcher le leader de se retirer lui-même
    if joueur.id == interaction.user.id:
        await interaction.followup.send(
            "❌ Tu ne peux pas te retirer toi-même. Transfère d'abord le leadership.", ephemeral=True
        )
        return

    await _remove_player_from_team(interaction, joueur, cmd_label)


@app_commands.command(name="cbl_adm_remove_player",
                      description="[ADMIN] Retire un joueur de son équipe actuelle")
@app_commands.describe(joueur="Joueur à retirer")
async def cbl_adm_remove_player(interaction: discord.Interaction, joueur: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    cmd_label = f"cbl_adm_remove_player **{joueur.display_name}**"
    await _remove_player_from_team(interaction, joueur, cmd_label)


@app_commands.command(name="cbl_adm_rename_team",
                      description="[ADMIN] Renomme une équipe et met à jour tous ses salons/rôles")
@app_commands.describe(
    sigle="Sigle actuel de l'équipe",
    nouveau_sigle="Nouveau sigle",
)
async def cbl_adm_rename_team(interaction: discord.Interaction, sigle: str, nouveau_sigle: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild     = interaction.guild
    cmd_label = f"cbl_adm_rename_team **{sigle}** → **{nouveau_sigle}**"

    team = load_team(sigle)
    if not team:
        await interaction.followup.send(f"❌ L'équipe **{sigle}** est introuvable.", ephemeral=True)
        return

    if load_team(nouveau_sigle):
        await interaction.followup.send(f"❌ Une équipe **{nouveau_sigle}** existe déjà.", ephemeral=True)
        return

    report = []

    async def _rename_one(t_data: dict, old: str, new: str):
        """Renomme une équipe (A ou B) sur Discord et en JSON."""
        # Rôle (seulement pour l'équipe A, la B partage)
        if not old.endswith("²"):
            role = guild.get_role(t_data["role_id"])
            if role:
                try:
                    await role.edit(name=f"[{new}]")
                    report.append(f"✏️ Rôle renommé → `[{new}]`")
                except Exception as e:
                    report.append(f"⚠️ Rôle : {e}")

        # Catégorie (seulement pour l'équipe A)
        if not old.endswith("²"):
            category = guild.get_channel(t_data["category_id"])
            if category:
                try:
                    await category.edit(name=f"〔{new}〕")
                    report.append(f"✏️ Catégorie renommée → `〔{new}〕`")
                except Exception as e:
                    report.append(f"⚠️ Catégorie : {e}")

            # Salons partagés (général, vocal) — renommés par l'équipe A seulement
            for ch_key, suffix in [("general", "général"), ("vocal", "vocal"), ("tasks", "tasks")]:
                ch_id = t_data["channels"].get(ch_key)
                ch = guild.get_channel(ch_id) if ch_id else None
                if ch:
                    try:
                        await ch.edit(name=f"〔{new}〕{suffix}")
                    except Exception:
                        pass

            # Historique A
            hist_a = guild.get_channel(t_data["channels"]["historique"])
            if hist_a:
                try:
                    await hist_a.edit(name=f"〔{new}〕historique")
                except Exception:
                    pass
        else:
            # Historique B
            hist_b = guild.get_channel(t_data["channels"]["historique"])
            if hist_b:
                try:
                    await hist_b.edit(name=f"〔{new}²〕historique")
                except Exception:
                    pass

        # Mettre à jour les profils joueurs
        for member_id in t_data.get("members", []):
            p = load_player(member_id)
            if p and p.get("team") == old:
                p["team"] = new
                save_player(p)

        # Mettre à jour et déplacer le JSON
        t_data["sigle"] = new
        old_path = _team_path(old)
        save_team(t_data)          # écrit sous le nouveau nom
        if os.path.exists(old_path):
            os.remove(old_path)    # supprime l'ancien fichier

    # ── Renommer l'équipe A ──────────────────────────────────────────────────
    await _rename_one(team, sigle, nouveau_sigle)
    await delete_team_lu(interaction.client, interaction.guild_id, sigle)
    new_team_a = load_team(nouveau_sigle)
    if new_team_a:
        await refresh_team_lu(interaction.client, interaction.guild_id, new_team_a)
    report.append(f"✅ Équipe **{sigle}** → **{nouveau_sigle}**")

    # ── Renommer l'équipe B si elle existe ───────────────────────────────────
    team_b = load_team(f"{sigle}²")
    if team_b:
        await _rename_one(team_b, f"{sigle}²", f"{nouveau_sigle}²")
        await delete_team_lu(interaction.client, interaction.guild_id, f"{sigle}²")
        new_team_b = load_team(f"{nouveau_sigle}²")
        if new_team_b:
            await refresh_team_lu(interaction.client, interaction.guild_id, new_team_b)
        report.append(f"✅ Équipe **{sigle}²** → **{nouveau_sigle}²**")

    await interaction.followup.send("\n".join(report), ephemeral=True)
    await log_command(interaction.user.display_name, cmd_label, "Completed",
                      f"**{sigle}** renommée en **{nouveau_sigle}** par **{interaction.user.display_name}**")


@app_commands.command(
    name="cbl_refresh_teams_lu",
    description="[ADMIN] Reconstruit le salon teams-lu (un embed par équipe existante)",
)
async def cbl_refresh_teams_lu(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await rebuild_teams_lu(interaction.client, interaction.guild_id)
    await interaction.followup.send("✅ Salon `teams-lu` reconstruit.", ephemeral=True)
    await log_command(interaction.user.display_name, "cbl_refresh_teams_lu", "Completed",
                      f"Salon teams-lu reconstruit par **{interaction.user.display_name}**")

# ---------------------------------------------------------------------------
# Reprise des demandes de join en attente (après redémarrage, ou migration
# vers le salon "tasks" pour une demande postée avant sa création)
# ---------------------------------------------------------------------------

async def restore_all_join_requests(bot: commands.Bot) -> int:
    """Reposte chaque demande de join en attente dans le bon salon de l'équipe
    (le salon 'tasks' si disponible, sinon 'général'), avec des boutons frais.

    Utilisé au démarrage du bot ET par /cbl_setup_all. Renvoie le nombre de
    demandes restaurées/migrées.
    """
    await bot.wait_until_ready()
    if not bot.guilds:
        return 0
    guild = bot.guilds[0]

    requests = _load_join_requests()
    count = 0
    for msg_id_str, data in requests.items():
        try:
            team = load_team(data["team_sigle"])
            applicant = guild.get_member(data["applicant_id"])
            if not team or not applicant:
                del_join_request(int(msg_id_str))
                continue

            target_channel_id = team["channels"].get("tasks") or team["channels"]["general"]
            target_channel = guild.get_channel(target_channel_id)
            if not target_channel:
                continue

            old_channel = guild.get_channel(data["channel_id"])
            if old_channel:
                try:
                    old_msg = await old_channel.fetch_message(int(msg_id_str))
                    await old_msg.delete()
                except Exception:
                    pass

            leader = guild.get_member(team["leader_id"])
            leader_mention = leader.mention if leader else f"<@{team['leader_id']}>"

            view = JoinRequestView(applicant=applicant, team=team, gid=guild.id, log_row=data.get("log_row", -1))
            msg = await target_channel.send(
                f"{leader_mention} — {t(guild.id, 'join_request_received', player=applicant.display_name, team=team['sigle'])}",
                view=view,
            )
            view.message = msg

            del_join_request(int(msg_id_str))
            save_join_request(msg.id, {**data, "channel_id": target_channel.id})
            count += 1
        except Exception as e:
            print(f"[WARN] restore join request {msg_id_str}: {e}")

    return count


class Teams(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.tree.add_command(cbl_newplayer)
        self.bot.tree.add_command(cbl_newteam)
        self.bot.tree.add_command(cbl_join)
        self.bot.tree.add_command(cbl_remove_player)
        self.bot.tree.add_command(cbl_adm_remove_player)
        self.bot.tree.add_command(cbl_del_team)
        self.bot.tree.add_command(cbl_adm_del_team)
        self.bot.tree.add_command(cbl_adm_rename_team)
        self.bot.tree.add_command(cbl_refresh_teams_lu)
        self.bot.loop.create_task(restore_all_join_requests(self.bot))


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
