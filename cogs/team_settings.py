"""
Salon "settings" par équipe : boutons de gestion réservés au leader (+ admins
d'équipe pour ce qui n'affecte pas la gouvernance). Transférer le Leadership,
Définir des Admins, Définir un Main, Ajouter Joueur.
"""

import discord
from discord.ext import commands

NB_ADMINS_MAX = 2


def _team_members_options(team: dict, guild: discord.Guild, exclude: set = frozenset()) -> list:
    from cogs.teams import load_player
    options = []
    for mid in team.get("members", []):
        if mid in exclude:
            continue
        member = guild.get_member(mid)
        player = load_player(mid)
        name = member.display_name if member else (player.get("name") if player else str(mid))
        options.append(discord.SelectOption(label=str(name)[:100], value=str(mid)))
    return options[:25]


async def post_team_settings_panel(guild: discord.Guild, team: dict):
    ch_id = team.get("channels", {}).get("settings")
    if not ch_id:
        return
    channel = guild.get_channel(ch_id)
    if not channel:
        return
    embed = discord.Embed(
        title=f"⚙️ Paramètres — {team['sigle']}",
        description="Réservé au leader et aux admins de l'équipe.",
        color=discord.Color.blurple(),
    )
    await channel.send(embed=embed, view=TeamSettingsView(team["sigle"]))


# ---------------------------------------------------------------------------
# Panel principal
# ---------------------------------------------------------------------------

class TeamSettingsView(discord.ui.View):
    def __init__(self, sigle: str):
        super().__init__(timeout=None)
        self.sigle = sigle

        btn1 = discord.ui.Button(
            label="🔄 Transférer le Leadership", style=discord.ButtonStyle.danger,
            custom_id=f"ts_transfer_{sigle}",
        )
        btn1.callback = self._transfer
        self.add_item(btn1)

        btn2 = discord.ui.Button(
            label="🛡️ Définir des Admins", style=discord.ButtonStyle.primary,
            custom_id=f"ts_admins_{sigle}",
        )
        btn2.callback = self._admins
        self.add_item(btn2)

        btn3 = discord.ui.Button(
            label="🎮 Définir un Main", style=discord.ButtonStyle.secondary,
            custom_id=f"ts_main_{sigle}",
        )
        btn3.callback = self._main
        self.add_item(btn3)

        btn4 = discord.ui.Button(
            label="➕ Ajouter Joueur", style=discord.ButtonStyle.success,
            custom_id=f"ts_addplayer_{sigle}",
        )
        btn4.callback = self._add_player
        self.add_item(btn4)

    def _team(self):
        from cogs.teams import load_team
        return load_team(self.sigle)

    async def _transfer(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_authorized
        team = self._team()
        if not team:
            await interaction.response.send_message("❌ Équipe introuvable.", ephemeral=True)
            return
        if not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message(
                "❌ Seul le leader peut transférer le leadership.", ephemeral=True
            )
            return

        options = _team_members_options(team, interaction.guild, exclude={team["leader_id"]})
        if not options:
            await interaction.response.send_message("❌ Aucun autre membre dans l'équipe.", ephemeral=True)
            return

        await interaction.response.send_message(
            "👑 Choisis le nouveau leader :", view=_TransferPickView(self.sigle, options), ephemeral=True
        )

    async def _admins(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_authorized
        team = self._team()
        if not team:
            await interaction.response.send_message("❌ Équipe introuvable.", ephemeral=True)
            return
        if not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message(
                "❌ Seul le leader peut définir les admins.", ephemeral=True
            )
            return

        options = _team_members_options(team, interaction.guild, exclude={team["leader_id"]})
        if not options:
            await interaction.response.send_message("❌ Aucun autre membre dans l'équipe.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"🛡️ Choisis jusqu'à {NB_ADMINS_MAX} admin(s) :",
            view=_AdminsPickView(self.sigle, options, team.get("admin_ids", [])),
            ephemeral=True,
        )

    async def _main(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_team_authorized
        team = self._team()
        if not team:
            await interaction.response.send_message("❌ Équipe introuvable.", ephemeral=True)
            return
        if not is_team_authorized(interaction.user.id, team):
            await interaction.response.send_message("❌ Réservé au leader et aux admins.", ephemeral=True)
            return

        options = _team_members_options(team, interaction.guild)
        if not options:
            await interaction.response.send_message("❌ Aucun membre dans l'équipe.", ephemeral=True)
            return

        await interaction.response.send_message(
            "🎮 Choisis le joueur dont tu veux définir le main :",
            view=_MainPlayerPickView(self.sigle, options), ephemeral=True,
        )

    async def _add_player(self, interaction: discord.Interaction):
        from cogs.crewbattle import is_team_authorized
        from cogs.teams import load_player, find_team_of_player, PLAYERS_DIR
        import os

        team = self._team()
        if not team:
            await interaction.response.send_message("❌ Équipe introuvable.", ephemeral=True)
            return
        if not is_team_authorized(interaction.user.id, team):
            await interaction.response.send_message("❌ Réservé au leader et aux admins.", ephemeral=True)
            return

        options = []
        if os.path.exists(PLAYERS_DIR):
            for fn in os.listdir(PLAYERS_DIR):
                if not fn.endswith(".json"):
                    continue
                pid = int(fn[:-5])
                if find_team_of_player(pid):
                    continue
                member = interaction.guild.get_member(pid)
                if not member:
                    continue
                player = load_player(pid)
                name = (player.get("name") if player else None) or member.display_name
                options.append(discord.SelectOption(label=str(name)[:100], value=str(pid)))
                if len(options) >= 25:
                    break

        if not options:
            await interaction.response.send_message("❌ Aucun joueur libre sur le serveur.", ephemeral=True)
            return

        await interaction.response.send_message(
            "➕ Choisis le joueur à inviter :", view=_AddPlayerPickView(self.sigle, options), ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Transférer le Leadership
# ---------------------------------------------------------------------------

class _TransferPickView(discord.ui.View):
    def __init__(self, sigle: str, options: list):
        super().__init__(timeout=120)
        self.sigle = sigle
        select = discord.ui.Select(placeholder="Nouveau leader...", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        from cogs.teams import load_team
        from cogs.crewbattle import is_authorized
        team = load_team(self.sigle)
        if not team or not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        new_leader_id = int(interaction.data["values"][0])
        member = interaction.guild.get_member(new_leader_id)
        name = member.display_name if member else str(new_leader_id)
        await interaction.response.edit_message(
            content=f"⚠️ Confirmer le transfert du leadership de **{self.sigle}** à **{name}** ?",
            view=_TransferConfirmView(self.sigle, team["leader_id"], new_leader_id),
        )


class _TransferConfirmView(discord.ui.View):
    def __init__(self, sigle: str, old_leader_id: int, new_leader_id: int):
        super().__init__(timeout=120)
        self.sigle = sigle
        self.old_leader_id = old_leader_id
        self.new_leader_id = new_leader_id

    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.old_leader_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        from utils.players_stats import _transfer_leadership
        await _transfer_leadership(interaction, self.sigle, self.new_leader_id, self.old_leader_id)

        new_leader = interaction.guild.get_member(self.new_leader_id)
        await interaction.followup.send(
            f"✅ Leadership transféré à **{new_leader.display_name if new_leader else self.new_leader_id}**.",
            ephemeral=True,
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        from cogs.crewbattle import is_authorized
        if not is_authorized(interaction.user.id, self.old_leader_id):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Annulé.", view=self)


# ---------------------------------------------------------------------------
# Définir des Admins
# ---------------------------------------------------------------------------

class _AdminsPickView(discord.ui.View):
    def __init__(self, sigle: str, options: list, current_admin_ids: list):
        super().__init__(timeout=120)
        self.sigle = sigle
        self.selected = [str(i) for i in current_admin_ids]

        marked = [
            discord.SelectOption(label=o.label, value=o.value, default=(o.value in self.selected))
            for o in options
        ]
        select = discord.ui.Select(
            placeholder=f"Jusqu'à {NB_ADMINS_MAX} admin(s)...",
            min_values=0, max_values=min(NB_ADMINS_MAX, len(options)),
            options=marked,
        )
        select.callback = self._on_select
        self.add_item(select)

        confirm = discord.ui.Button(label="✅ Confirmer", style=discord.ButtonStyle.success)
        confirm.callback = self._confirm
        self.add_item(confirm)

        cancel = discord.ui.Button(label="Annuler", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(cancel)

    async def _authorized(self, interaction: discord.Interaction):
        from cogs.teams import load_team
        from cogs.crewbattle import is_authorized
        team = load_team(self.sigle)
        if not team or not is_authorized(interaction.user.id, team["leader_id"]):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return None
        return team

    async def _on_select(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        self.selected = interaction.data["values"]
        await interaction.response.defer()

    async def _confirm(self, interaction: discord.Interaction):
        team = await self._authorized(interaction)
        if not team:
            return

        from cogs.teams import save_team, sync_settings_channel_permissions
        team["admin_ids"] = [int(i) for i in self.selected]
        save_team(team)
        await sync_settings_channel_permissions(interaction.guild, team)

        names = []
        for mid in team["admin_ids"]:
            m = interaction.guild.get_member(mid)
            names.append(m.display_name if m else str(mid))

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Admins de **{self.sigle}** : {', '.join(names) if names else 'aucun'}.",
            view=self,
        )

    async def _cancel(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Annulé.", view=self)


# ---------------------------------------------------------------------------
# Définir un Main (pour un membre choisi par le leader/admin)
# ---------------------------------------------------------------------------

class _MainPlayerPickView(discord.ui.View):
    def __init__(self, sigle: str, options: list):
        super().__init__(timeout=120)
        self.sigle = sigle
        select = discord.ui.Select(placeholder="Joueur...", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        from cogs.teams import load_team
        from cogs.crewbattle import is_team_authorized
        team = load_team(self.sigle)
        if not team or not is_team_authorized(interaction.user.id, team):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        target_id = int(interaction.data["values"][0])
        member = interaction.guild.get_member(target_id)
        name = member.display_name if member else str(target_id)
        await interaction.response.edit_message(
            content=f"🎮 Choisis le main de **{name}** :",
            view=_MainCharPickView(self.sigle, target_id, name, interaction.client),
        )


class _MainCharPickView(discord.ui.View):
    """Pages de boutons avec emotes (même style que la sélection de perso en
    CB / le bouton 'Définir un Main' individuel), suivi d'un récap à confirmer."""

    def __init__(self, sigle: str, target_id: int, target_name: str, bot):
        super().__init__(timeout=180)
        self.sigle = sigle
        self.target_id = target_id
        self.target_name = target_name
        self.bot = bot
        self.page = 0
        self.selected_char = None
        self._build()

    def _get_emoji(self, name: str):
        from cogs.crewbattle import EMOJI_SERVER_ID
        guild = self.bot.get_guild(EMOJI_SERVER_ID)
        return discord.utils.get(guild.emojis, name=name) if guild else None

    def _build(self):
        from cogs.crewbattle import CHARACTER_PAGES
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

        choose_btn = discord.ui.Button(
            label="✅ Choisir", style=discord.ButtonStyle.primary,
            disabled=self.selected_char is None, row=4,
        )
        choose_btn.callback = self._choose
        self.add_item(choose_btn)

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        from cogs.teams import load_team
        from cogs.crewbattle import is_team_authorized
        team = load_team(self.sigle)
        if not team or not is_team_authorized(interaction.user.id, team):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return False
        return True

    def _make_char_cb(self, char: str):
        async def cb(interaction: discord.Interaction):
            if not await self._authorized(interaction):
                return
            self.selected_char = char
            self._build()
            await interaction.response.edit_message(view=self)
        return cb

    async def _prev(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        self.page -= 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        self.page += 1
        self._build()
        await interaction.response.edit_message(view=self)

    async def _choose(self, interaction: discord.Interaction):
        if not await self._authorized(interaction):
            return
        emoji = self._get_emoji(self.selected_char)
        main_display = str(emoji) if emoji else self.selected_char
        await interaction.response.edit_message(
            content=f"⚠️ Confirmer : **{self.target_name}** → main {main_display} ?",
            view=_MainRecapConfirmView(self.sigle, self.target_id, self.target_name, self.selected_char, emoji),
        )


class _MainRecapConfirmView(discord.ui.View):
    def __init__(self, sigle: str, target_id: int, target_name: str, char: str, emoji):
        super().__init__(timeout=120)
        self.sigle = sigle
        self.target_id = target_id
        self.target_name = target_name
        self.char = char
        self.emoji = emoji

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        from cogs.teams import load_team
        from cogs.crewbattle import is_team_authorized
        team = load_team(self.sigle)
        if not team or not is_team_authorized(interaction.user.id, team):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorized(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        from cogs.teams import load_player, save_player, load_team
        from utils.players_stats import _refresh_player_thread_embed, refresh_team_stats_post, _get_thread
        from utils.teams_lu import refresh_team_lu

        main_value = str(self.emoji) if self.emoji else self.char
        player = load_player(self.target_id) or {"discord_id": self.target_id, "name": self.target_name}
        player.setdefault("stats", {})
        player["stats"]["main"] = main_value
        save_player(player)

        thread_id = player.get("stats_thread_id")
        if thread_id:
            thread = await _get_thread(interaction.guild, thread_id)
            if thread:
                await _refresh_player_thread_embed(thread, player)

        await refresh_team_stats_post(interaction.client, interaction.guild_id, self.sigle)

        team = load_team(self.sigle)
        if team:
            await refresh_team_lu(interaction.client, interaction.guild_id, team)

        await interaction.followup.send(
            f"✅ Main de **{self.target_name}** défini : {main_value}", ephemeral=True
        )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorized(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Annulé.", view=self)


# ---------------------------------------------------------------------------
# Ajouter Joueur (invitation en MP)
# ---------------------------------------------------------------------------

class _AddPlayerPickView(discord.ui.View):
    def __init__(self, sigle: str, options: list):
        super().__init__(timeout=120)
        self.sigle = sigle
        select = discord.ui.Select(placeholder="Joueur à inviter...", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        from cogs.teams import load_team
        from cogs.crewbattle import is_team_authorized
        team = load_team(self.sigle)
        if not team or not is_team_authorized(interaction.user.id, team):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return
        target_id = int(interaction.data["values"][0])
        member = interaction.guild.get_member(target_id)
        name = member.display_name if member else str(target_id)
        await interaction.response.edit_message(
            content=f"➕ Confirmer l'invitation de **{name}** à rejoindre **{self.sigle}** ?",
            view=_AddPlayerConfirmView(self.sigle, target_id, name),
        )


class _AddPlayerConfirmView(discord.ui.View):
    def __init__(self, sigle: str, target_id: int, target_name: str):
        super().__init__(timeout=120)
        self.sigle = sigle
        self.target_id = target_id
        self.target_name = target_name

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        from cogs.teams import load_team
        from cogs.crewbattle import is_team_authorized
        team = load_team(self.sigle)
        if not team or not is_team_authorized(interaction.user.id, team):
            await interaction.response.send_message("❌ Action non autorisée.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ Confirmer", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorized(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)

        member = interaction.guild.get_member(self.target_id)
        if not member:
            await interaction.followup.send("❌ Joueur introuvable sur le serveur.", ephemeral=True)
            return

        invite_view = TeamInviteView(self.sigle, self.target_id)
        try:
            dm_msg = await member.send(
                f"📨 L'équipe **{self.sigle}** souhaite que vous rejoigniez sa LineUp ! Accepter ?",
                view=invite_view,
            )
            invite_view.message = dm_msg
            await interaction.followup.send(f"✅ Invitation envoyée à **{self.target_name}**.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ Impossible d'envoyer un MP à **{self.target_name}** (messages privés fermés).", ephemeral=True
            )

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._authorized(interaction):
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Annulé.", view=self)


class TeamInviteView(discord.ui.View):
    """Envoyée en MP au joueur invité (limite connue : ne survit pas à un
    redémarrage du bot tant que l'invitation n'a pas été traitée, comme les
    autres messages de négociation intermédiaires de ce projet)."""

    def __init__(self, sigle: str, applicant_id: int):
        super().__init__(timeout=None)
        self.sigle = sigle
        self.applicant_id = applicant_id
        self.message: discord.Message | None = None

        self.accept_btn = discord.ui.Button(
            label="✅ Accepter", style=discord.ButtonStyle.success,
            custom_id=f"ts_invite_accept_{sigle}_{applicant_id}",
        )
        self.accept_btn.callback = self._accept
        self.add_item(self.accept_btn)

        self.refuse_btn = discord.ui.Button(
            label="❌ Refuser", style=discord.ButtonStyle.danger,
            custom_id=f"ts_invite_refuse_{sigle}_{applicant_id}",
        )
        self.refuse_btn.callback = self._refuse
        self.add_item(self.refuse_btn)

    async def _resolve(self, kept: discord.ui.Button):
        self.clear_items()
        kept.disabled = True
        self.add_item(kept)
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def _accept(self, interaction: discord.Interaction):
        if interaction.user.id != self.applicant_id:
            await interaction.response.send_message("❌ Cette invitation ne t'est pas destinée.", ephemeral=True)
            return

        from cogs.teams import load_team, find_team_of_player, add_member_to_team
        from utils.config import GUILD_ID

        team = load_team(self.sigle)
        if not team:
            await interaction.response.send_message("❌ Cette équipe n'existe plus.", ephemeral=True)
            return
        if find_team_of_player(self.applicant_id):
            await interaction.response.send_message("❌ Tu es déjà dans une équipe.", ephemeral=True)
            await self._resolve(self.accept_btn)
            return

        await interaction.response.defer()

        guild  = interaction.client.get_guild(GUILD_ID)
        member = guild.get_member(self.applicant_id) if guild else None
        if not guild or not member:
            await interaction.followup.send("❌ Erreur : impossible de te retrouver sur le serveur.")
            return

        await add_member_to_team(interaction.client, guild, team, member)
        await self._resolve(self.accept_btn)
        await interaction.followup.send(f"✅ Tu as rejoint **{self.sigle}** !")

        tasks_ch_id = team["channels"].get("tasks")
        tasks_ch = guild.get_channel(tasks_ch_id) if tasks_ch_id else None
        if tasks_ch:
            try:
                await tasks_ch.send(f"✅ **{member.display_name}** a accepté de rejoindre l'équipe.")
            except Exception:
                pass

    async def _refuse(self, interaction: discord.Interaction):
        if interaction.user.id != self.applicant_id:
            await interaction.response.send_message("❌ Cette invitation ne t'est pas destinée.", ephemeral=True)
            return

        from cogs.teams import load_team
        from utils.config import GUILD_ID

        team = load_team(self.sigle)
        await interaction.response.send_message("Invitation refusée.", ephemeral=True)
        await self._resolve(self.refuse_btn)

        if team:
            guild = interaction.client.get_guild(GUILD_ID)
            tasks_ch_id = team["channels"].get("tasks")
            tasks_ch = guild.get_channel(tasks_ch_id) if (guild and tasks_ch_id) else None
            if tasks_ch:
                try:
                    await tasks_ch.send(
                        f"❌ **{interaction.user.display_name}** a refusé de rejoindre l'équipe."
                    )
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class TeamSettings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        import os
        from cogs.teams import TEAMS_DIR
        if os.path.exists(TEAMS_DIR):
            for fn in os.listdir(TEAMS_DIR):
                if not fn.endswith(".json"):
                    continue
                import json
                with open(os.path.join(TEAMS_DIR, fn), encoding="utf-8") as f:
                    team = json.load(f)
                sigle = team.get("sigle", "")
                if sigle and team.get("channels", {}).get("settings"):
                    self.bot.add_view(TeamSettingsView(sigle))


async def setup(bot: commands.Bot):
    await bot.add_cog(TeamSettings(bot))
