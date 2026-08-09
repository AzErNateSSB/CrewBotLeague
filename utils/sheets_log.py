import asyncio
import re
from datetime import datetime
from typing import Optional

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

SPREADSHEET_ID   = "1mp7AP5tG3crtyY6_19nqa_HXsn0HIAbXDXwisZNt288"
CREDENTIALS_FILE = "credentials.json"
LOG_SHEET_NAME   = "Historique"
SHEETS_SCOPES    = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Cache du client pour éviter une réauthentification à chaque appel
_client: Optional["gspread.Client"] = None
_sheet:  Optional["gspread.Worksheet"] = None


def _get_sheet() -> "gspread.Worksheet":
    global _client, _sheet
    if _sheet is None:
        creds   = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SHEETS_SCOPES)
        _client = gspread.authorize(creds)
        _sheet  = _client.open_by_key(SPREADSHEET_ID).worksheet(LOG_SHEET_NAME)
    return _sheet


def _append_row_sync(values: list) -> int:
    """Ajoute une ligne et retourne son numéro (1-based)."""
    sheet  = _get_sheet()
    result = sheet.append_row(values, value_input_option="USER_ENTERED")
    updated_range = result.get("updates", {}).get("updatedRange", "")
    m = re.search(r":?[A-Z]+(\d+)$", updated_range)
    return int(m.group(1)) if m else -1


def _update_row_sync(row: int, status: str, details: str):
    sheet = _get_sheet()
    sheet.update(f"D{row}:E{row}", [[status, details]])


async def log_command(
    user: str,
    command: str,
    status: str,
    details: str,
) -> int:
    """
    Ajoute une ligne dans la feuille Historique.
    Retourne le numéro de la ligne créée (utile pour la mettre à jour plus tard).
    Retourne -1 si gspread n'est pas disponible ou si une erreur survient.
    """
    if not GSPREAD_AVAILABLE:
        return -1
    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    values    = [timestamp, user, command, status, details]
    loop      = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _append_row_sync, values)
    except Exception as e:
        print(f"[sheets_log] log_command failed: {e}")
        global _sheet
        _sheet = None  # reset cache si la session a expiré
        return -1


async def update_log(row: int, status: str, details: str):
    """Met à jour le statut et les détails d'une ligne existante."""
    if not GSPREAD_AVAILABLE or row < 1:
        return
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _update_row_sync, row, status, details)
    except Exception as e:
        print(f"[sheets_log] update_log failed: {e}")
        global _sheet
        _sheet = None
