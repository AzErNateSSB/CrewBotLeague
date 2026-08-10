"""
Récupère les IDs des threads players-stats existants sur Discord
et les stocke dans les fichiers JSON équipes/joueurs.

Usage : python scripts/recover_stats_threads.py
"""

import asyncio
import discord
import os
import json
import re
from dotenv import load_dotenv

load_dotenv()

TEAMS_DIR   = os.path.join("data", "teams")
PLAYERS_DIR = os.path.join("data", "players")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

GUILD_ID = 1472927998301835356


def load_all_teams() -> dict[str, dict]:
    teams = {}
    for f in os.listdir(TEAMS_DIR):
        if f.endswith(".json"):
            with open(os.path.join(TEAMS_DIR, f), encoding="utf-8") as fp:
                t = json.load(fp)
            teams[t["sigle"]] = t
    return teams


def load_all_players() -> dict[int, dict]:
    players = {}
    for f in os.listdir(PLAYERS_DIR):
        if f.endswith(".json"):
            with open(os.path.join(PLAYERS_DIR, f), encoding="utf-8") as fp:
                p = json.load(fp)
            players[p["discord_id"]] = p
    return players


def save_team(data: dict):
    with open(os.path.join(TEAMS_DIR, f"{data['sigle']}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_player(data: dict):
    with open(os.path.join(PLAYERS_DIR, f"{data['discord_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@client.event
async def on_ready():
    print(f"Connecté : {client.user}\n")
    guild   = client.get_guild(GUILD_ID)
    teams   = load_all_teams()
    players = load_all_players()

    # Index joueurs par nom normalisé pour les correspondances
    # {nom_normalisé: [player, ...]}
    players_by_name: dict[str, list] = {}
    for p in players.values():
        key = p.get("name", "").lower().strip()
        players_by_name.setdefault(key, []).append(p)

    # Index joueurs par membre Discord (member_id → player)
    # On construit aussi un index "équipe → membres"
    team_members: dict[str, set] = {
        sigle: set(t.get("members", [])) for sigle, t in teams.items()
    }

    stats_matched  = 0
    player_matched = 0
    unmatched      = []

    for sigle, team in teams.items():
        forum_id = team.get("channels", {}).get("players_stats")
        if not forum_id:
            print(f"⚠️  {sigle} — pas de players_stats dans les channels")
            continue

        forum = guild.get_channel(forum_id)
        if not isinstance(forum, discord.ForumChannel):
            print(f"⚠️  {sigle} — forum {forum_id} introuvable")
            continue

        threads = list(forum.threads)
        archived = [t async for t in forum.archived_threads(limit=100)]
        all_threads = threads + archived

        print(f"\n{'='*50}")
        print(f"📁 {sigle} — {len(all_threads)} thread(s)")

        for thread in all_threads:
            # Lire le premier message pour trouver l'embed
            first_embed = None
            async for msg in thread.history(limit=5, oldest_first=True):
                if msg.author == guild.me and msg.embeds:
                    first_embed = msg.embeds[0]
                    break

            if not first_embed or not first_embed.title:
                print(f"  ❓ {thread.name} (ID {thread.id}) — pas d'embed bot")
                continue

            title = first_embed.title

            # Post d'équipe
            if title.startswith("📈"):
                team["stats_thread_id"] = thread.id
                save_team(team)
                print(f"  ✅ Post équipe → thread {thread.id}")
                stats_matched += 1
                continue

            # Post joueur
            if title.startswith("📊"):
                display_name = title.removeprefix("📊 ").strip()
                key = display_name.lower().strip()

                # Chercher le joueur par nom dans les membres de cette équipe
                matched_player = None

                # Essai 1 : correspondance exacte sur le nom
                candidates = players_by_name.get(key, [])
                for cand in candidates:
                    if cand["discord_id"] in team_members.get(sigle, set()):
                        matched_player = cand
                        break

                # Essai 2 : correspondance partielle (nom contenu dans le nom du joueur)
                if not matched_player:
                    for member_id in team_members.get(sigle, set()):
                        p = players.get(member_id)
                        if p and key in p.get("name", "").lower():
                            matched_player = p
                            break

                if matched_player:
                    matched_player["stats_thread_id"] = thread.id
                    # Récupérer le main depuis l'embed si défini
                    for field in first_embed.fields:
                        if field.name == "Main" and field.value != "*Non défini*":
                            matched_player.setdefault("stats", {})["main"] = field.value
                    save_player(matched_player)
                    print(f"  ✅ {display_name} → player {matched_player['discord_id']} (thread {thread.id})")
                    player_matched += 1
                else:
                    print(f"  ❌ {display_name} — aucun joueur trouvé dans {sigle}")
                    unmatched.append(f"{sigle} : {display_name} (thread {thread.id})")

    print(f"\n{'='*50}")
    print(f"✅ Posts équipe récupérés : {stats_matched}")
    print(f"✅ Posts joueur récupérés : {player_matched}")
    if unmatched:
        print(f"❌ Non matchés ({len(unmatched)}) :")
        for u in unmatched:
            print(f"   - {u}")

    await client.close()


async def main():
    await client.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    asyncio.run(main())
