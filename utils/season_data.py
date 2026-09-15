import json
import os
from datetime import date, timedelta
from typing import Optional

SEASON_FILE          = os.path.join("data", "season.json")
OFFICIAL_MATCHES_DIR = os.path.join("data", "official_matches")

LEAGUE_NAMES = ["Ligue-1", "Ligue-2", "Ligue-3", "Ligue-4"]

# IDs des catégories Discord parentes pour chaque ligue (configurées une fois)
LEAGUE_CATEGORY_IDS: dict[str, int] = {
    "Ligue-1": 1476175030240149544,
    "Ligue-2": 1476175167163207781,
    "Ligue-3": 1476175247651901492,
    "Ligue-4": 1476175322855510158,
}

LEAGUE_EMOJIS: dict[str, str] = {
    "Ligue-1": "🟨",
    "Ligue-2": "🟦",
    "Ligue-3": "🟥",
    "Ligue-4": "🟩",
}

LEAGUE_SHORT: dict[str, str] = {
    "Ligue-1": "L1",
    "Ligue-2": "L2",
    "Ligue-3": "L3",
    "Ligue-4": "L4",
}

# ---------------------------------------------------------------------------
# Season load / save
# ---------------------------------------------------------------------------

def load_season() -> Optional[dict]:
    if not os.path.exists(SEASON_FILE):
        return None
    with open(SEASON_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_season(data: dict):
    os.makedirs("data", exist_ok=True)
    with open(SEASON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def new_season(name: str, start_date: str) -> dict:
    """start_date : date ISO (AAAA-MM-JJ) de début de la journée 1."""
    return {
        "name":       name,
        "start_date": start_date,
        "status":     "registration",   # registration | active | finished
        "leagues":    {lg: [] for lg in LEAGUE_NAMES},
        "calendar":   {lg: [] for lg in LEAGUE_NAMES},
        "standings":  {lg: {} for lg in LEAGUE_NAMES},
        "barrages":   [],
        "forums":     {lg: None for lg in LEAGUE_NAMES},
    }

# ---------------------------------------------------------------------------
# Fenêtres de dates par journée
# ---------------------------------------------------------------------------

def round_window(start_date_str: str, round_idx: int) -> tuple[date, date]:
    """Fenêtre de sélection de dates pour la journée round_idx (0-indexée) :
    2 semaines de période normale, + 1 semaine de grâce sur la période suivante.
    Retourne (début de période, date limite)."""
    start = date.fromisoformat(start_date_str)
    period_start = start + timedelta(days=round_idx * 14)
    deadline     = period_start + timedelta(days=20)
    return period_start, deadline

# ---------------------------------------------------------------------------
# Round-robin calendar
# ---------------------------------------------------------------------------

def generate_round_robin(teams: list[str]) -> list[list[dict]]:
    """
    Génère un calendrier round-robin complet.
    Retourne une liste de journées, chaque journée étant une liste de matchs
    {"home": str, "away": str, "result": None, "channel_id": None}.
    """
    t = list(teams)
    if len(t) % 2 == 1:
        t.append("BYE")
    n = len(t)
    rounds = []
    for _ in range(n - 1):
        round_matches = []
        for i in range(n // 2):
            home = t[i]
            away = t[n - 1 - i]
            if home != "BYE" and away != "BYE":
                round_matches.append({"home": home, "away": away, "result": None, "channel_id": None})
        rounds.append(round_matches)
        # Rotation : premier fixe, on tourne le reste
        t = [t[0]] + [t[-1]] + t[1:-1]
    return rounds

# ---------------------------------------------------------------------------
# Points
# ---------------------------------------------------------------------------

def compute_points(winner_lives: int) -> tuple[int, int]:
    """
    Retourne (pts_winner, pts_loser).
    Système : base 15 ± vies restantes du vainqueur.
    """
    return 15 + winner_lives, 15 - winner_lives

def update_standings(season: dict, league: str, winner: str, loser: str, winner_lives: int):
    """Met à jour le classement après un match officiel."""
    st = season["standings"][league]
    for team in (winner, loser):
        if team not in st:
            st[team] = {"pts": 0, "wins": 0, "losses": 0, "lives_scored": 0, "lives_conceded": 0}

    pts_w, pts_l = compute_points(winner_lives)
    st[winner]["pts"]           += pts_w
    st[winner]["wins"]          += 1
    st[winner]["lives_scored"]  += winner_lives

    st[loser]["pts"]            += pts_l
    st[loser]["losses"]         += 1
    st[loser]["lives_conceded"] += winner_lives

def sorted_standings(season: dict, league: str) -> list[tuple[str, dict]]:
    """Retourne le classement trié (pts desc, diff vies desc)."""
    st = season["standings"].get(league, {})
    return sorted(
        st.items(),
        key=lambda x: (x[1]["pts"], x[1]["lives_scored"] - x[1]["lives_conceded"]),
        reverse=True,
    )

# ---------------------------------------------------------------------------
# Barrages
# ---------------------------------------------------------------------------

def compute_barrages(season: dict) -> list[dict]:
    """
    Calcule les matchs de barrage et les mouvements directs en fin de saison.
    Retourne la liste des barrages [{league_high, team_high, league_low, team_low, result}].
    Applique directement les promotions/relégations directes dans season["leagues"].
    """
    barrages = []
    leagues  = LEAGUE_NAMES

    for i in range(len(leagues) - 1):
        lg_high = leagues[i]
        lg_low  = leagues[i + 1]

        high_sorted = sorted_standings(season, lg_high)
        low_sorted  = sorted_standings(season, lg_low)

        if not high_sorted or not low_sorted:
            continue

        # Promotion directe : 1er de lg_low monte dans lg_high
        promoted = low_sorted[0][0]
        season["leagues"][lg_high].append(promoted)
        season["leagues"][lg_low].remove(promoted)

        # Relégation directe : dernier de lg_high descend dans lg_low
        relegated = high_sorted[-1][0]
        season["leagues"][lg_low].append(relegated)
        season["leagues"][lg_high].remove(relegated)

        # Re-trier après modifications
        high_sorted = sorted_standings(season, lg_high)
        low_sorted  = sorted_standings(season, lg_low)

        # Barrage : 2e de lg_low vs avant-dernier de lg_high
        if len(high_sorted) >= 2 and len(low_sorted) >= 2:
            team_high = high_sorted[-1][0]   # avant-dernier (après retrait du dernier)
            team_low  = low_sorted[1][0]      # 2e de la ligue basse
            barrages.append({
                "league_high": lg_high,
                "team_high":   team_high,
                "league_low":  lg_low,
                "team_low":    team_low,
                "result":      None,
                "channel_id":  None,
            })

    season["barrages"] = barrages
    return barrages

# ---------------------------------------------------------------------------
# Official matches
# ---------------------------------------------------------------------------

def save_official_match(channel_id: int, data: dict):
    os.makedirs(OFFICIAL_MATCHES_DIR, exist_ok=True)
    path = os.path.join(OFFICIAL_MATCHES_DIR, f"{channel_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_official_match(channel_id: int) -> Optional[dict]:
    path = os.path.join(OFFICIAL_MATCHES_DIR, f"{channel_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def delete_official_match(channel_id: int):
    path = os.path.join(OFFICIAL_MATCHES_DIR, f"{channel_id}.json")
    if os.path.exists(path):
        os.remove(path)
