import asyncio
import os
import string
import discord
from discord.ext import commands
from discord import app_commands
from utils.sheets_log import log_command

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SPREADSHEET_ID   = "1mp7AP5tG3crtyY6_19nqa_HXsn0HIAbXDXwisZNt288"
CREDENTIALS_FILE = "credentials.json"
SHEETS_SCOPES    = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

BLOCK_SIZE = 13  # Colonnes A–M

LEAGUE_CATEGORIES: list[tuple[int, str]] = [
    (1476175030240149544, "〔🟨〕ligue-1〔{season}〕"),
    (1476175167163207781, "〔🟦〕ligue-2〔{season}〕"),
    (1476175247651901492, "〔🟥〕ligue-3〔{season}〕"),
    (1476175322855510158, "〔🟩〕ligue-4〔{season}〕"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def col_letter(col: int) -> str:
    """Index 1-based → lettre de colonne (1→A, 14→N, 27→AA, …)."""
    result = ""
    while col > 0:
        col, r = divmod(col - 1, 26)
        result = string.ascii_uppercase[r] + result
    return result


def get_sheets_client() -> "gspread.Client":
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SHEETS_SCOPES)
    return gspread.authorize(creds)


def find_next_paste_col(sheet: "gspread.Worksheet") -> int:
    """
    Retourne l'index 1-based de la première colonne disponible pour une nouvelle copie.
    Scanne par blocs de BLOCK_SIZE à partir de la colonne N (14), puis AA (27), etc.
    Si la colonne n'existe pas encore (hors limites), elle est forcément vide.
    """
    col = BLOCK_SIZE + 1  # N
    while True:
        try:
            values = sheet.col_values(col)
            if not any(v.strip() for v in values if v):
                return col
            col += BLOCK_SIZE
        except Exception:
            # La colonne n'existe pas encore → c'est là qu'on veut coller
            return col

# ---------------------------------------------------------------------------
# Google Sheets (bloquant — toujours exécuté dans un thread séparé via
# run_in_executor, jamais directement sur la boucle asyncio du bot)
# ---------------------------------------------------------------------------

def _setup_season_sheets(season: str) -> list[str]:
    report: list[str] = []

    if not GSPREAD_AVAILABLE:
        report.append("❌ Dépendances manquantes — installe : `pip install gspread google-auth`")
        return report
    if not os.path.exists(CREDENTIALS_FILE):
        report.append(f"❌ Fichier `{CREDENTIALS_FILE}` introuvable à la racine du projet")
        return report

    try:
        client      = get_sheets_client()
        spreadsheet = client.open_by_key(SPREADSHEET_ID)

        # 2a. Copier A–M vers le prochain bloc libre dans la feuille "Scores"
        scores      = spreadsheet.worksheet("Scores")
        dest_col    = find_next_paste_col(scores)
        dest_letter = col_letter(dest_col)

        # Ajouter 13 colonnes pour faire de la place au nouveau bloc
        spreadsheet.batch_update({"requests": [{
            "appendDimension": {
                "sheetId":   scores.id,
                "dimension": "COLUMNS",
                "length":    BLOCK_SIZE,
            }
        }]})

        # Copier A–M vers le bloc destination : contenu + largeurs de colonnes
        source_range = {
            "sheetId":          scores.id,
            "startRowIndex":    0,
            "endRowIndex":      scores.row_count,
            "startColumnIndex": 0,
            "endColumnIndex":   13,
        }
        dest_range = {
            "sheetId":          scores.id,
            "startRowIndex":    0,
            "endRowIndex":      scores.row_count,
            "startColumnIndex": dest_col - 1,
            "endColumnIndex":   dest_col - 1 + BLOCK_SIZE,
        }
        # Largeurs des colonnes A–M (offset, taille en pixels)
        # A=20, B-I=50, J=25, K=50, L-M=20
        col_widths = [
            (0,  1,  20),   # A
            (1,  9,  50),   # B–I
            (9,  10, 25),   # J
            (10, 11, 50),   # K
            (11, 13, 20),   # L–M
        ]
        width_requests = [
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId":    scores.id,
                        "dimension":  "COLUMNS",
                        "startIndex": dest_col - 1 + start,
                        "endIndex":   dest_col - 1 + end,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
            for start, end, px in col_widths
        ]

        spreadsheet.batch_update({"requests": [
            {
                "copyPaste": {
                    "source":           source_range,
                    "destination":      dest_range,
                    "pasteType":        "PASTE_NORMAL",
                    "pasteOrientation": "NORMAL",
                }
            },
            *width_requests,
        ]})
        report.append(
            f"✅ Colonnes A–M copiées → **{dest_letter}** "
            f"(bloc {dest_letter}–{col_letter(dest_col + BLOCK_SIZE - 1)}) dans **Scores**"
        )

        # 2b. Dupliquer T_SEASONS et renommer en [season], puis afficher la feuille
        t_seasons = spreadsheet.worksheet("T_SEASONS")
        spreadsheet.duplicate_sheet(
            source_sheet_id=t_seasons.id,
            new_sheet_name=season,
        )
        season_sheet = spreadsheet.worksheet(season)
        spreadsheet.batch_update({"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": season_sheet.id, "hidden": False},
                "fields": "hidden",
            }
        }]})
        report.append(f"✅ Feuille **T_SEASONS** dupliquée → **{season}**")

        # 2c. Dupliquer PlayersStocks et renommer en [season]_PStocks, puis afficher
        pstocks_tmpl = spreadsheet.worksheet("PlayersStocks")
        spreadsheet.duplicate_sheet(
            source_sheet_id=pstocks_tmpl.id,
            new_sheet_name=f"{season}_PStocks",
        )
        pstocks = spreadsheet.worksheet(f"{season}_PStocks")
        spreadsheet.batch_update({"requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": pstocks.id, "hidden": False},
                "fields": "hidden",
            }
        }]})
        # Remplacer '26_SPR' par la nouvelle saison dans toutes les cellules concernées
        # A-D, G-J, M-P, S-V (ligne 3)
        TMPL_REF = "26_SPR"
        formula_cells = (
            "A3", "B3", "C3", "D3",
            "G3", "H3", "I3", "J3",
            "M3", "N3", "O3", "P3",
            "S3", "T3", "U3", "V3",
        )
        for cell in formula_cells:
            formula = pstocks.acell(cell, value_render_option="FORMULA").value
            if formula and TMPL_REF in formula:
                updated = formula.replace(f"'{TMPL_REF}'", f"'{season}'")
                pstocks.update([[updated]], cell, value_input_option="USER_ENTERED")
        report.append(
            f"✅ Feuille **PlayersStocks** dupliquée → **{season}_PStocks** "
            f"(références `{TMPL_REF}` → `{season}` mises à jour)"
        )

    except gspread.exceptions.WorksheetNotFound as e:
        report.append(f"❌ Feuille introuvable dans le tableur : {e}")
    except Exception as e:
        report.append(f"❌ Erreur Google Sheets : {e}")

    return report

# ---------------------------------------------------------------------------
# Commande
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_setup_season", description="Configure une nouvelle saison sur Discord et Google Sheets")
@app_commands.describe(season="Identifiant de la saison (ex: aut26)")
async def cbl_setupseason(interaction: discord.Interaction, season: str):
    await interaction.response.defer()

    guild  = interaction.guild
    report: list[str] = []

    # ── 1. Salons forums Discord ─────────────────────────────────────────────
    for cat_id, name_tpl in LEAGUE_CATEGORIES:
        channel_name = name_tpl.format(season=season)
        category = guild.get_channel(cat_id)

        if not isinstance(category, discord.CategoryChannel):
            report.append(f"❌ Catégorie `{cat_id}` introuvable ou invalide")
            continue

        try:
            forum = await guild.create_forum(name=channel_name, category=category)
            report.append(f"✅ Forum créé : **{forum.name}**")
        except discord.Forbidden:
            report.append(f"❌ Permission refusée pour **{channel_name}**")
        except Exception as e:
            report.append(f"❌ Erreur création **{channel_name}** : {e}")

    # ── 2. Google Sheets (dans un thread séparé pour ne pas geler le bot) ────
    loop = asyncio.get_running_loop()
    report.extend(await loop.run_in_executor(None, _setup_season_sheets, season))

    embed = discord.Embed(
        title=f"🔧 SetupSeason — {season}",
        description="\n".join(report),
        color=discord.Color.blurple(),
    )
    await interaction.followup.send(embed=embed)

    failed = any(line.startswith("❌") for line in report)
    status  = "Failed" if failed else "Completed"
    details = " | ".join(report)
    await log_command(interaction.user.display_name,
                      f"cbl_setup_season **{season}**", status, details)

# ---------------------------------------------------------------------------
# Commande shutdown
# ---------------------------------------------------------------------------

@app_commands.command(name="cbl_shutdown", description="Éteint le bot proprement")
async def cbl_shutdown(interaction: discord.Interaction):
    # Réservé aux administrateurs du serveur
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Réservé aux administrateurs.", ephemeral=True)
        return
    await interaction.response.send_message("🔌 Bot en cours d'arrêt...")
    await interaction.client.close()

# ---------------------------------------------------------------------------
# Cog (vide, sert juste au chargement)
# ---------------------------------------------------------------------------

class Season(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.tree.add_command(cbl_setupseason)
        self.bot.tree.add_command(cbl_shutdown)


async def setup(bot: commands.Bot):
    await bot.add_cog(Season(bot))
