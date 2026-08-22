import discord
from discord.ext import commands
from discord import app_commands
import json
import os

from cogs.teams import (
    load_player, save_player, load_team,
    find_team_of_player, save_team, rollback_new_team,
    tasks_channel_overwrites, players_forum_overwrites, TASKS_CHANNEL_INTRO,
    save_join_request, restore_all_join_requests,
)
from utils.sheets_log import log_command, update_log
from utils.i18n import t
from utils.teams_lu import refresh_team_lu
from utils.alerts import alert_error
from utils.players_stats import create_team_stats_post, create_player_stats_post, refresh_team_stats_post

PANELS_FILE        = os.path.join("data", "panels.json")
CHANNEL_CREATETEAM = 1532064361324089525
CHANNEL_JOINTEAM   = 1532064572167553174

# ---------------------------------------------------------------------------
# Persistance des IDs de messages
# ---------------------------------------------------------------------------

def load_panels() -> dict:
    if not os.path.exists(PANELS_FILE):
        return {}
    with open(PANELS_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_panels(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(PANELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# ---------------------------------------------------------------------------
# Panel CREATE-TEAM
# ---------------------------------------------------------------------------

class CreateTeamModal(discord.ui.Modal, title="Créer mon équipe"):
    sigle = discord.ui.TextInput(
        label="Sigle de l'équipe",
        placeholder="Ex: HoJ",
        min_length=1,
        max_length=20,
    )

    def __init__(self):
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction):
        from datetime import date
        guild     = interaction.guild
        gid       = interaction.guild_id
        user      = interaction.user
        raw_sigle = self.sigle.value.strip()

        await interaction.response.defer(ephemeral=True)

        # Déterminer si on crée l'équipe A ou B
        existing_a = load_team(raw_sigle)
        if existing_a:
            # L'équipe A existe — seul son leader peut créer la B
            if existing_a["leader_id"] != user.id:
                await interaction.followup.send(t(gid, "team_already_exists"), ephemeral=True)
                await log_command(user.display_name, f"cbl_newteam **{raw_sigle}**", "Failed",
                                  f"La Team **{raw_sigle}** existe déjà")
                return
            # C'est le leader → on crée l'équipe B dans la même catégorie
            full_sigle = f"{raw_sigle}²"
            if load_team(full_sigle):
                await interaction.followup.send(
                    f"❌ L'équipe **{full_sigle}** existe déjà.", ephemeral=True
                )
                await log_command(user.display_name, f"cbl_newteam **{full_sigle}**", "Failed",
                                  f"La Team **{full_sigle}** existe déjà")
                return

            cmd_label = f"cbl_newteam **{full_sigle}**"

            # Récupérer la catégorie et le rôle existants de l'équipe A
            category = guild.get_channel(existing_a["category_id"])
            role     = guild.get_role(existing_a["role_id"])
            if not category or not role:
                await interaction.followup.send("❌ Catégorie ou rôle de l'équipe A introuvable.", ephemeral=True)
                return

            try:
                await user.add_roles(role)
            except discord.Forbidden:
                pass

            # Créer uniquement le salon historique² dans la catégorie partagée
            try:
                forum_b = await guild.create_forum(
                    name=f"〔{raw_sigle}²〕historique", category=category
                )
            except Exception as e:
                await interaction.followup.send(f"❌ Erreur création du salon : {e}", ephemeral=True)
                await log_command(user.display_name, cmd_label, "Failed", str(e))
                return

            try:
                team_data = {
                    "sigle":       full_sigle,
                    "leader_id":   user.id,
                    "role_id":     existing_a["role_id"],
                    "category_id": existing_a["category_id"],
                    "channels": {
                        "general":       existing_a["channels"]["general"],
                        "historique":    forum_b.id,
                        "vocal":         existing_a["channels"]["vocal"],
                        "tasks":         existing_a["channels"].get("tasks"),
                        "players_stats": existing_a["channels"].get("players_stats"),
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
                await create_team_stats_post(interaction.client, interaction.guild_id, team_data)
                # L'équipe A gagne le bouton "Déplacer vers B" sur ses membres existants
                await refresh_team_stats_post(interaction.client, interaction.guild_id, raw_sigle)

                general_ch = guild.get_channel(existing_a["channels"]["general"])
                embed = discord.Embed(title=f"⚔️ Équipe **{full_sigle}** créée !", color=discord.Color.blurple())
                embed.add_field(name="Leader", value=user.mention, inline=True)
                embed.add_field(name="Rôle",   value=role.mention, inline=True)
                embed.add_field(name="Salon historique",
                                value=forum_b.mention, inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)

                if general_ch:
                    await general_ch.send(
                        f"🎉 L'équipe **{full_sigle}** a été créée ! "
                        f"{user.mention} en est le leader."
                    )
                await log_command(user.display_name, cmd_label, "Completed",
                                  f"La Team **{full_sigle}** créée (équipe B de **{raw_sigle}**) — leader : **{user.display_name}**")
            except Exception as e:
                await rollback_new_team(channels=[forum_b])
                await interaction.followup.send(f"❌ Erreur lors de la création de l'équipe : {e}", ephemeral=True)
                await log_command(user.display_name, cmd_label, "Failed", f"Erreur après création du salon : {e}")
                await alert_error(
                    interaction.client,
                    "Échec création équipe (B) — panel",
                    f"Commande `{cmd_label}` par **{user.display_name}**\nErreur : `{e}`\n"
                    f"Le salon historique² a été supprimé (rollback).",
                )
            return

        else:
            full_sigle = raw_sigle

        cmd_label = f"cbl_newteam **{full_sigle}**"

        if find_team_of_player(user.id):
            existing = find_team_of_player(user.id)
            await interaction.followup.send(t(gid, "team_already_in_team"), ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed",
                              f"**{user.display_name}** est déjà dans **{existing}**")
            return

        try:
            role = await guild.create_role(name=f"[{full_sigle}]", mentionable=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Permission refusée pour créer le rôle.", ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed", "Permission refusée — rôle")
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
            category     = await guild.create_category(name=f"〔{full_sigle}〕", overwrites=overwrites)
            tasks        = await guild.create_text_channel(
                name=f"〔{full_sigle}〕tasks", category=category,
                overwrites=tasks_channel_overwrites(guild, role, user),
            )
            general      = await guild.create_text_channel( name=f"〔{full_sigle}〕général",    category=category)
            forum        = await guild.create_forum(         name=f"〔{full_sigle}〕historique", category=category)
            players_stat = await guild.create_forum(
                name=f"〔{full_sigle}〕players--stats", category=category,
                overwrites=players_forum_overwrites(guild, role, user),
            )
            vocal        = await guild.create_voice_channel( name=f"〔{full_sigle}〕vocal",      category=category)
        except Exception as e:
            await role.delete()
            await interaction.followup.send(f"❌ Erreur création des salons : {e}", ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed", str(e))
            return

        try:
            team_data = {
                "sigle":       full_sigle,
                "leader_id":   user.id,
                "role_id":     role.id,
                "category_id": category.id,
                "channels": {
                    "general":       general.id,
                    "historique":    forum.id,
                    "tasks":         tasks.id,
                    "players_stats": players_stat.id,
                    "vocal":         vocal.id,
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
            await create_team_stats_post(interaction.client, interaction.guild_id, team_data)
            from cogs.teams import load_team as lt
            team_data = lt(full_sigle) or team_data
            await create_player_stats_post(interaction.client, interaction.guild_id, team_data, user)

            embed = discord.Embed(title=f"⚔️ Équipe **{full_sigle}** créée !", color=discord.Color.blurple())
            embed.add_field(name="Leader", value=user.mention, inline=True)
            embed.add_field(name="Rôle",   value=role.mention, inline=True)
            embed.add_field(name="Salons",
                            value=f"{general.mention} · {forum.mention} · {vocal.mention} · {tasks.mention} · {players_stat.mention}",
                            inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

            await general.send(
                f"🎉 Bienvenue dans le salon de **{full_sigle}** ! "
                f"{user.mention} en est le leader.\n"
                f"Les membres peuvent rejoindre l'équipe via le salon <#{CHANNEL_JOINTEAM}>."
            )
            await tasks.send(TASKS_CHANNEL_INTRO)
            await log_command(user.display_name, cmd_label, "Completed",
                              f"La Team **{full_sigle}** a été créée avec **{user.display_name}** pour Leader")
        except Exception as e:
            await rollback_new_team(role=role, category=category, channels=[general, forum, players_stat, vocal, tasks])
            await interaction.followup.send(f"❌ Erreur lors de la création de l'équipe : {e}", ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed", f"Erreur après création des salons : {e}")
            await alert_error(
                interaction.client,
                "Échec création équipe — panel",
                f"Commande `{cmd_label}` par **{user.display_name}**\nErreur : `{e}`\n"
                f"Rôle, catégorie et salons ont été supprimés (rollback).",
            )


class CreateTeamView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛡️ Créer mon équipe", style=discord.ButtonStyle.primary,
                       custom_id="panel_create_team")
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateTeamModal())

# ---------------------------------------------------------------------------
# Panel JOIN-TEAM
# ---------------------------------------------------------------------------

class JoinTeamModal(discord.ui.Modal, title="Rejoindre une équipe"):
    sigle = discord.ui.TextInput(
        label="Sigle de l'équipe",
        placeholder="Ex: HoJ",
        min_length=1,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction):
        gid  = interaction.guild_id
        user = interaction.user
        sigle     = self.sigle.value.strip()
        cmd_label = f"cbl_join **{sigle}**"

        await interaction.response.defer(ephemeral=True)

        # Créer le profil si inexistant
        if not load_player(user.id):
            save_player({"discord_id": user.id, "name": user.display_name, "team": None})
            await log_command(user.display_name, "cbl_newplayer", "Completed",
                              f"Profil joueur auto-créé pour **{user.display_name}**")

        if find_team_of_player(user.id):
            current = find_team_of_player(user.id)
            await interaction.followup.send(t(gid, "team_already_in_team"), ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed",
                              f"**{user.display_name}** est déjà dans **{current}**")
            return

        team = load_team(sigle)
        if not team:
            await interaction.followup.send(t(gid, "team_not_found", sigle=sigle), ephemeral=True)
            await log_command(user.display_name, cmd_label, "Failed",
                              f"L'équipe **{sigle}** est introuvable")
            return

        tasks_channel = interaction.guild.get_channel(team["channels"].get("tasks") or team["channels"]["general"])
        if not tasks_channel:
            await interaction.followup.send("❌ Salon de l'équipe introuvable.", ephemeral=True)
            return

        log_row = await log_command(
            user.display_name, cmd_label, "In Progress",
            f"**{user.display_name}** a demandé à rejoindre **{sigle}** — en attente du leader"
        )

        leader         = interaction.guild.get_member(team["leader_id"])
        leader_mention = leader.mention if leader else f"<@{team['leader_id']}>"

        from cogs.teams import JoinRequestView
        view = JoinRequestView(applicant=user, team=team, gid=gid, log_row=log_row)
        try:
            msg = await tasks_channel.send(
                f"{leader_mention} — {t(gid, 'join_request_received', player=user.display_name, team=sigle)}",
                view=view,
            )
            view.message = msg
            save_join_request(msg.id, {
                "channel_id":   tasks_channel.id,
                "applicant_id": user.id,
                "team_sigle":   sigle,
                "log_row":      log_row,
            })
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Le bot n'a pas la permission d'écrire dans le salon tasks de cette équipe.", ephemeral=True
            )
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Erreur lors de l'envoi de la demande : {e}", ephemeral=True)
            return

        await interaction.followup.send(t(gid, "join_request_sent", team=sigle), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        import traceback
        traceback.print_exception(type(error), error, error.__traceback__)
        msg = f"❌ Erreur inattendue : {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


class JoinTeamView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎮 Rejoindre une équipe", style=discord.ButtonStyle.primary,
                       custom_id="panel_join_team")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(JoinTeamModal())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _ensure_panel(
    guild: discord.Guild,
    panels: dict,
    key: str,
    channel_id: int,
    embed: discord.Embed,
    view: discord.ui.View,
) -> str:
    """Poste le panel uniquement s'il est absent ou si le message a été supprimé."""
    channel = guild.get_channel(channel_id)
    if not channel:
        return f"❌ Salon introuvable (ID {channel_id})"

    existing_id = panels.get(key)
    if existing_id:
        try:
            await channel.fetch_message(existing_id)
            return f"✓ Panel déjà présent dans {channel.mention} — rien à faire"
        except discord.NotFound:
            pass  # message supprimé → on le reposte
        except Exception:
            pass

    msg = await channel.send(embed=embed, view=view)
    panels[key] = msg.id
    return f"✅ Panel posté dans {channel.mention}"


async def _backfill_team_tasks_channels(guild: discord.Guild) -> list[str]:
    """Crée le salon 'tasks' des équipes existantes qui n'en ont pas encore
    (ou en réapplique les permissions). Les équipes B réutilisent le salon
    tasks de leur équipe A (partagé, comme général/vocal)."""
    teams_dir = os.path.join("data", "teams")
    report: list[str] = []
    if not os.path.exists(teams_dir):
        return report

    all_teams = []
    for fn in sorted(os.listdir(teams_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(teams_dir, fn), encoding="utf-8") as f:
            all_teams.append(json.load(f))

    teams_a = [t for t in all_teams if not t["sigle"].endswith("²")]
    teams_b = [t for t in all_teams if t["sigle"].endswith("²")]

    for team in teams_a:
        sigle = team["sigle"]

        category = guild.get_channel(team.get("category_id", 0))
        if not category:
            report.append(f"❌ **{sigle}** : catégorie introuvable, salon tasks non créé")
            continue

        role   = guild.get_role(team.get("role_id", 0))
        leader = guild.get_member(team.get("leader_id", 0))

        existing_id = team["channels"].get("tasks")
        existing_ch = guild.get_channel(existing_id) if existing_id else None

        if existing_ch:
            try:
                await existing_ch.edit(overwrites=tasks_channel_overwrites(guild, role, leader))
                report.append(f"🔧 Permissions du salon tasks de **{sigle}** vérifiées")
            except Exception as e:
                report.append(f"❌ **{sigle}** : erreur mise à jour permissions tasks : {e}")
            continue

        try:
            tasks_ch = await guild.create_text_channel(
                name=f"〔{sigle}〕tasks", category=category,
                overwrites=tasks_channel_overwrites(guild, role, leader),
            )
            await tasks_ch.send(TASKS_CHANNEL_INTRO)
        except Exception as e:
            report.append(f"❌ **{sigle}** : erreur création salon tasks : {e}")
            continue

        team["channels"]["tasks"] = tasks_ch.id
        save_team(team)
        report.append(f"✅ Salon tasks créé pour **{sigle}**")

    for team in teams_b:
        sigle = team["sigle"]
        existing_id = team["channels"].get("tasks")
        if existing_id and guild.get_channel(existing_id):
            continue

        parent = load_team(sigle[:-1])
        parent_tasks_id = parent["channels"].get("tasks") if parent else None
        if parent_tasks_id and guild.get_channel(parent_tasks_id):
            team["channels"]["tasks"] = parent_tasks_id
            save_team(team)
            report.append(f"✅ **{sigle}** relié au salon tasks de l'équipe A")
        else:
            report.append(f"⚠️ **{sigle}** : salon tasks de l'équipe A pas encore disponible, ignoré")

    return report


async def _backfill_player_stats(guild: discord.Guild, bot: commands.Bot) -> list[str]:
    """Crée le forum 'players--stats' manquant, le post stats de chaque équipe,
    et le post individuel de chaque joueur qui n'en a pas encore."""
    from utils.players_stats import register_all_views, team_stats_post_exists, player_stats_post_exists

    teams_dir = os.path.join("data", "teams")
    report: list[str] = []
    if not os.path.exists(teams_dir):
        return report

    all_teams = []
    for fn in sorted(os.listdir(teams_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(teams_dir, fn), encoding="utf-8") as f:
            all_teams.append(json.load(f))

    teams_a = [t for t in all_teams if not t["sigle"].endswith("²")]
    teams_b = [t for t in all_teams if t["sigle"].endswith("²")]

    # ── Forum players--stats (créé sur l'équipe A, partagé avec la B) ───────
    for team in teams_a:
        sigle = team["sigle"]
        existing_id = team["channels"].get("players_stats")
        if existing_id and guild.get_channel(existing_id):
            continue

        category = guild.get_channel(team.get("category_id", 0))
        if not category:
            report.append(f"❌ **{sigle}** : catégorie introuvable, forum players--stats non créé")
            continue

        role   = guild.get_role(team.get("role_id", 0))
        leader = guild.get_member(team.get("leader_id", 0))
        try:
            forum = await guild.create_forum(
                name=f"〔{sigle}〕players--stats", category=category,
                overwrites=players_forum_overwrites(guild, role, leader),
            )
        except Exception as e:
            report.append(f"❌ **{sigle}** : erreur création forum players--stats : {e}")
            continue

        team["channels"]["players_stats"] = forum.id
        save_team(team)
        report.append(f"✅ Forum players--stats créé pour **{sigle}**")

    for team in teams_b:
        sigle = team["sigle"]
        existing_id = team["channels"].get("players_stats")
        if existing_id and guild.get_channel(existing_id):
            continue
        parent = load_team(sigle[:-1])
        parent_forum_id = parent["channels"].get("players_stats") if parent else None
        if parent_forum_id and guild.get_channel(parent_forum_id):
            team["channels"]["players_stats"] = parent_forum_id
            save_team(team)
            report.append(f"✅ **{sigle}** relié au forum players--stats de l'équipe A")
        else:
            report.append(f"⚠️ **{sigle}** : forum de l'équipe A pas encore disponible, ignoré")

    # ── Post stats d'équipe — une par équipe (A et B) ────────────────────────
    # Vérifie l'existence réelle sur Discord (pas juste la présence d'un
    # stats_thread_id dans le JSON) pour rattraper les posts supprimés à la main.
    nb_team_posts = 0
    for team in (load_team(t["sigle"]) for t in all_teams):
        if not team or await team_stats_post_exists(bot, guild.id, team["sigle"]):
            continue
        try:
            await create_team_stats_post(bot, guild.id, team)
            if load_team(team["sigle"]).get("stats_thread_id"):
                nb_team_posts += 1
        except Exception as e:
            report.append(f"❌ **{team['sigle']}** : erreur création post stats d'équipe : {e}")
    if nb_team_posts:
        report.append(f"✅ {nb_team_posts} post(s) stats d'équipe créé(s) ou recréé(s)")

    # ── Post individuel de chaque joueur affilié à une équipe ───────────────
    players_dir = os.path.join("data", "players")
    nb_player_posts = 0
    if os.path.exists(players_dir):
        for fn in sorted(os.listdir(players_dir)):
            if not fn.endswith(".json"):
                continue
            with open(os.path.join(players_dir, fn), encoding="utf-8") as f:
                player = json.load(f)
            team_sigle = player.get("team")
            if not team_sigle:
                continue
            if await player_stats_post_exists(bot, guild.id, player["discord_id"]):
                continue
            team = load_team(team_sigle)
            member = guild.get_member(player["discord_id"])
            if not team or not member:
                continue
            try:
                await create_player_stats_post(bot, guild.id, team, member)
                nb_player_posts += 1
            except Exception as e:
                report.append(f"❌ Post de **{member.display_name}** : erreur : {e}")
    if nb_player_posts:
        report.append(f"✅ {nb_player_posts} post(s) joueur créé(s) ou recréé(s)")

    # ── Ré-enregistrer les boutons de tous les posts actifs ─────────────────
    nb_views = register_all_views(bot)
    report.append(f"🔄 {nb_views} vue(s) players-stats — boutons ré-enregistrés")

    return report

# ---------------------------------------------------------------------------
# /cbl_setup_all
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_setup_all",
                      description="[ADMIN] Vérifie et (re)poste tous les panels interactifs manquants")
async def cbl_setup_all(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild  = interaction.guild
    panels = load_panels()
    report = []

    # ── Panel : Créer une équipe ─────────────────────────────────────────────
    report.append(await _ensure_panel(
        guild, panels,
        key="create_team_msg",
        channel_id=CHANNEL_CREATETEAM,
        embed=discord.Embed(
            title="⚔️ Créer une équipe",
            description=(
                "Tu souhaites créer une équipe pour l'événement ?\n\n"
                "Clique sur le bouton ci-dessous, renseigne le sigle de ton équipe "
                "et le bot s'occupe du reste !\n\n"
                "*(Rôle, catégorie, salons — tout est créé automatiquement.)*\n\n"
                "Si tu es déjà leader d'une équipe et que tu entres le même sigle, "
                "l'équipe **B** sera créée automatiquement."
            ),
            color=discord.Color.blurple(),
        ),
        view=CreateTeamView(),
    ))

    # ── Panel : Freeplay ─────────────────────────────────────────────────────
    from cogs.freeplay import FreeplayPanelView, CHANNEL_FREEPLAY as _CH_FP
    report.append(await _ensure_panel(
        guild, panels,
        key="freeplay_panel_msg",
        channel_id=_CH_FP,
        embed=discord.Embed(
            title="⚔️ Freeplay CrewBattle",
            description=(
                "Tu veux faire une CrewBattle en dehors du calendrier officiel ?\n\n"
                "Clique sur le bouton ci-dessous pour chercher un adversaire.\n"
                "Le bot te proposera des créneaux sur les **7 prochains jours**.\n\n"
                "*(Réservé aux leaders d'équipe)*"
            ),
            color=discord.Color.red(),
        ),
        view=FreeplayPanelView(),
    ))

    # ── Panel : Rejoindre une équipe ─────────────────────────────────────────
    report.append(await _ensure_panel(
        guild, panels,
        key="join_team_msg",
        channel_id=CHANNEL_JOINTEAM,
        embed=discord.Embed(
            title="🎮 Rejoindre une équipe",
            description=(
                "Tu veux participer à l'événement ?\n\n"
                "Clique sur le bouton ci-dessous, indique le sigle de l'équipe "
                "que tu souhaites rejoindre et une demande sera envoyée au leader.\n\n"
                "*(Si tu n'as pas encore de profil joueur, il sera créé automatiquement.)*"
            ),
            color=discord.Color.green(),
        ),
        view=JoinTeamView(),
    ))

    save_panels(panels)

    # ── Salon "tasks" manquant sur les équipes existantes ────────────────────
    report.extend(await _backfill_team_tasks_channels(guild))

    # ── Forum "players--stats" + posts joueurs/équipe manquants ──────────────
    report.extend(await _backfill_player_stats(guild, interaction.client))

    # ── Reprise de tous les flux interactifs en cours (boutons cassés) ───────
    from cogs.crewbattle import restore_all_matches
    from cogs.freeplay import restore_all_freeplay_setups, restore_all_fp_posts, ensure_all_cancel_buttons

    nb_matches = await restore_all_matches(interaction.client)
    nb_setups  = await restore_all_freeplay_setups(interaction.client)
    nb_posts   = await restore_all_fp_posts(interaction.client)
    nb_joins   = await restore_all_join_requests(interaction.client)
    nb_cancel  = await ensure_all_cancel_buttons(interaction.client)

    report.append(f"🔄 {nb_matches} CrewBattle(s) en cours — boutons reposés")
    report.append(f"🔄 {nb_setups} configuration(s) Freeplay en cours — boutons reposés")
    report.append(f"🔄 {nb_posts} recherche(s) d'adversaire — boutons reposés")
    report.append(f"🔄 {nb_joins} demande(s) de join — boutons reposés / migrés vers tasks")
    report.append(f"🔄 {nb_cancel} bouton(s) \"Annuler la CB\" manquant(s) — reposté(s)")

    await interaction.followup.send("\n".join(report), ephemeral=True)

# ---------------------------------------------------------------------------
# /cbl_setup_panels  (force re-publication)
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_setup_panels",
                      description="[ADMIN] Force la re-publication de tous les panels (remplace les existants)")
async def cbl_setup_panels(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild   = interaction.guild
    panels  = load_panels()
    report  = []

    for key, channel_id, embed, view in [
        (
            "create_team_msg", CHANNEL_CREATETEAM,
            discord.Embed(
                title="⚔️ Créer une équipe",
                description=(
                    "Tu souhaites créer une équipe pour l'événement ?\n\n"
                    "Clique sur le bouton ci-dessous, renseigne le sigle de ton équipe "
                    "et le bot s'occupe du reste !\n\n"
                    "*(Rôle, catégorie, salons — tout est créé automatiquement.)*\n\n"
                    "Si tu es déjà leader d'une équipe et que tu entres le même sigle, "
                    "l'équipe **B** sera créée automatiquement."
                ),
                color=discord.Color.blurple(),
            ),
            CreateTeamView(),
        ),
        (
            "join_team_msg", CHANNEL_JOINTEAM,
            discord.Embed(
                title="🎮 Rejoindre une équipe",
                description=(
                    "Tu veux participer à l'événement ?\n\n"
                    "Clique sur le bouton ci-dessous, indique le sigle de l'équipe "
                    "que tu souhaites rejoindre et une demande sera envoyée au leader.\n\n"
                    "*(Si tu n'as pas encore de profil joueur, il sera créé automatiquement.)*"
                ),
                color=discord.Color.green(),
            ),
            JoinTeamView(),
        ),
    ]:
        channel = guild.get_channel(channel_id)
        if not channel:
            report.append(f"❌ Salon introuvable (ID {channel_id})")
            continue
        old_id = panels.get(key)
        if old_id:
            try:
                old_msg = await channel.fetch_message(old_id)
                await old_msg.delete()
            except Exception:
                pass
        msg = await channel.send(embed=embed, view=view)
        panels[key] = msg.id
        report.append(f"✅ Panel republié dans {channel.mention}")

    save_panels(panels)
    await interaction.followup.send("\n".join(report), ephemeral=True)

# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Panels(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(CreateTeamView())
        self.bot.add_view(JoinTeamView())
        self.bot.tree.add_command(cbl_setup_all)
        self.bot.tree.add_command(cbl_setup_panels)


async def setup(bot: commands.Bot):
    await bot.add_cog(Panels(bot))
