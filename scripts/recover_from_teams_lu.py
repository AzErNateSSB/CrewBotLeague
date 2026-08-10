"""
Script de récupération des données équipes depuis les embeds du salon teams-lu.
Lance-le UNE SEULE FOIS pour reconstruire data/teams/ et data/players/.

Usage : python scripts/recover_from_teams_lu.py
"""

import asyncio
import discord
import json
import os
import re
from datetime import date
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# IDs fournis manuellement
# ---------------------------------------------------------------------------

CHANNEL_TEAMS_LU = 1532892553786036496

# { sigle: { role_id, category_id, channels: {...}, teams_lu_msg_id } }
TEAMS_META = {
    "OXVI": {
        "role_id":     1532107397475074229,
        "category_id": 1532107399979208704,
        "channels": {
            "general":      1532107400994226316,
            "historique":   1532107402160111647,
            "tasks":        1533811893469188106,
            "players_stats":1533874102417489922,
            "vocal":        1532107403435180077,
        },
        "teams_lu_msg_id": 1532895265877655625,
    },
    "3QI": {
        "role_id":     1532427011283353771,
        "category_id": 1532427013866918081,
        "channels": {
            "general":      1532427015053901997,
            "historique":   1532427016303804496,
            "tasks":        1533811878093127780,
            "players_stats":1533874094607695995,
            "vocal":        1532427018845556888,
        },
        "teams_lu_msg_id": 1532895259418169525,
    },
    "RC": {
        "role_id":     1532818386038296697,
        "category_id": 1532818388739690718,
        "channels": {
            "general":      1532818390425665677,
            "historique":   1532818392170365099,
            "tasks":        1533811896040296578,
            "players_stats":1533874104002805761,
            "vocal":        1532818393378459659,
        },
        "teams_lu_msg_id": 1532895267303461098,
    },
    "LT": {
        "role_id":     1532891327774330940,
        "category_id": 1532891330387247228,
        "channels": {
            "general":      1532891332241129492,
            "historique":   1532891333864325251,
            "tasks":        1533811886565625946,
            "players_stats":1533874101083570376,
            "vocal":        1532897425092771976,
        },
        "teams_lu_msg_id": 1532895263604347112,
    },
    "MST": {
        "role_id":     1532897417538703361,
        "category_id": 1532897421707837600,
        "channels": {
            "general":      1532897422848823297,
            "historique":   1532897424211705866,
            "tasks":        1533811889799172146,
            "players_stats":1533874101083570376,
            "vocal":        1532897425092771976,
        },
        "teams_lu_msg_id": 1532897426367582209,
    },
    "SSL": {
        "role_id":     1533168921476206682,
        "category_id": 1533168924957474816,
        "channels": {
            "general":      1533168926249189598,
            "historique":   1533168927868321922,
            "tasks":        1533811898385039463,
            "players_stats":1533874105328078959,
            "vocal":        1533168928992395304,
        },
        "teams_lu_msg_id": 1533168930493829224,
    },
    "ARK": {
        "role_id":     1533241991239041197,
        "category_id": 1533241994485432371,
        "channels": {
            "general":      1533241996460949564,
            "historique":   1533241998000259092,
            "tasks":        1533811880668430398,
            "players_stats":1533874096520040588,
            "vocal":        1533241999170207754,
        },
        "teams_lu_msg_id": 1533242000055210015,
    },
    "HoJ": {
        "role_id":     1533383800975790120,
        "category_id": 1533383803769327659,
        "channels": {
            "general":      1533383804927082578,
            "historique":   1533383806025863178,
            "tasks":        1533811883331813446,
            "players_stats":1533874097849896979,
            "vocal":        1533383807414173767,
        },
        "teams_lu_msg_id": 1533383808571801733,
    },
    "JRJ": {
        "role_id":     1534216802211594270,
        "category_id": 1534216805281829099,
        "channels": {
            "general":      1534216807185907866,
            "historique":   1534216808217710817,
            "tasks":        1534216810747138139,
            "players_stats":1534216812353294386,
            "vocal":        1534216809581121748,
        },
        "teams_lu_msg_id": 1534216814911950858,
    },
    "SU": {
        "role_id":     1534668301093830868,
        "category_id": 1534668304394752160,
        "channels": {
            "general":      1534668315065057371,
            "historique":   1534668316566749305,
            "tasks":        1534668319356092537,
            "players_stats":1534668320840876192,
            "vocal":        1534668317975904256,
        },
        "teams_lu_msg_id": 1534668322052903123,
    },
    "ADN": {
        "role_id":     1535719017891893298,
        "category_id": 1535719020781903882,
        "channels": {
            "general":      1535719021998112891,
            "historique":   1535719023277379736,
            "tasks":        1535719026267783228,
            "players_stats":1535719027882852380,
            "vocal":        1535719024804110458,
        },
        "teams_lu_msg_id": 1535719028944015424,
    },
    "POM": {
        "role_id":     1535719768621842452,
        "category_id": 1535719771205804064,
        "channels": {
            "general":      1535719772866613358,
            "historique":   1535719774271832095,
            "tasks":        1535719777119641831,
            "players_stats":1535719778272940093,
            "vocal":        1535719775714545664,
        },
        "teams_lu_msg_id": 1535719780135469127,
    },
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEAMS_DIR   = os.path.join("data", "teams")
PLAYERS_DIR = os.path.join("data", "players")
TEAMS_LU_FILE = os.path.join("data", "teams_lu.json")

def extract_ids(text: str) -> list[int]:
    """Extrait tous les Discord IDs d'une chaîne <@123456789>."""
    return [int(x) for x in re.findall(r"<@(\d+)>", text)]

def save_team(data: dict):
    os.makedirs(TEAMS_DIR, exist_ok=True)
    path = os.path.join(TEAMS_DIR, f"{data['sigle']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_player(data: dict):
    os.makedirs(PLAYERS_DIR, exist_ok=True)
    path = os.path.join(PLAYERS_DIR, f"{data['discord_id']}.json")
    # Ne pas écraser un joueur qui serait dans plusieurs équipes (ne devrait pas arriver)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.members = True

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"Connecté en tant que {client.user}")
    channel = client.get_channel(CHANNEL_TEAMS_LU)
    if not channel:
        print("❌ Salon teams-lu introuvable.")
        await client.close()
        return

    teams_lu_index = {}
    recovered_teams  = []
    skipped_teams    = []

    for sigle, meta in TEAMS_META.items():
        msg_id = meta["teams_lu_msg_id"]
        try:
            msg = await channel.fetch_message(msg_id)
        except discord.NotFound:
            print(f"⚠️  Message introuvable pour {sigle} (ID {msg_id})")
            skipped_teams.append(sigle)
            continue

        if not msg.embeds:
            print(f"⚠️  Aucun embed dans le message de {sigle}")
            skipped_teams.append(sigle)
            continue

        embed = msg.embeds[0]

        # Extraire leader
        leader_id = None
        members   = []
        for field in embed.fields:
            ids = extract_ids(field.value or "")
            if field.name == "Leader" and ids:
                leader_id = ids[0]
            elif "Effectif" in field.name:
                members = ids

        if not leader_id:
            print(f"⚠️  Impossible de trouver le leader pour {sigle}")
            skipped_teams.append(sigle)
            continue

        # S'assurer que le leader est dans les membres
        if leader_id not in members:
            members.insert(0, leader_id)

        # Construire le JSON équipe
        team_data = {
            "sigle":       sigle,
            "leader_id":   leader_id,
            "role_id":     meta["role_id"],
            "category_id": meta["category_id"],
            "channels": {
                "general":       meta["channels"]["general"],
                "historique":    meta["channels"]["historique"],
                "tasks":         meta["channels"]["tasks"],
                "players_stats": meta["channels"]["players_stats"],
                "vocal":         meta["channels"]["vocal"],
            },
            "members":    members,
            "league":     None,
            "created_at": date.today().isoformat(),
        }
        save_team(team_data)

        # Construire les JSONs joueurs
        guild = msg.guild or client.guilds[0] if client.guilds else None
        for member_id in members:
            member = guild.get_member(member_id) if guild else None
            name   = member.display_name if member else str(member_id)
            save_player({
                "discord_id": member_id,
                "name":       name,
                "team":       sigle,
            })

        teams_lu_index[sigle] = msg_id
        recovered_teams.append(sigle)
        print(f"✅ {sigle} — leader: {leader_id} — {len(members)} membre(s)")

    # Mettre à jour teams_lu.json
    os.makedirs("data", exist_ok=True)
    with open(TEAMS_LU_FILE, "w", encoding="utf-8") as f:
        json.dump(teams_lu_index, f, ensure_ascii=False, indent=2)

    print("\n─────────────────────────────────────")
    print(f"✅ Récupérées  : {', '.join(recovered_teams) or 'aucune'}")
    print(f"⚠️  Ignorées   : {', '.join(skipped_teams)  or 'aucune'}")
    print("─────────────────────────────────────")
    await client.close()


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN manquant dans .env")
        return
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
