import json
import os

LOCALES_DIR = os.path.join(os.path.dirname(__file__), "..", "locales")
SERVER_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "server_configs.json")

SUPPORTED_LANGUAGES = {"fr": "Français", "en": "English"}
DEFAULT_LANG = "fr"

_locales: dict[str, dict] = {}
_server_configs: dict[str, dict] = {}


def _load_locales():
    for lang in SUPPORTED_LANGUAGES:
        path = os.path.join(LOCALES_DIR, f"{lang}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                _locales[lang] = json.load(f)


def _load_server_configs():
    global _server_configs
    if os.path.exists(SERVER_CONFIG_FILE):
        with open(SERVER_CONFIG_FILE, encoding="utf-8") as f:
            _server_configs = json.load(f)
    else:
        _server_configs = {}


def _save_server_configs():
    os.makedirs(os.path.dirname(SERVER_CONFIG_FILE), exist_ok=True)
    with open(SERVER_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_server_configs, f, ensure_ascii=False, indent=2)


def get_lang(guild_id: int) -> str:
    if not _locales:
        _load_locales()
    if not _server_configs:
        _load_server_configs()
    return _server_configs.get(str(guild_id), {}).get("language", DEFAULT_LANG)


def set_lang(guild_id: int, lang: str):
    if not _server_configs and os.path.exists(SERVER_CONFIG_FILE):
        _load_server_configs()
    key = str(guild_id)
    if key not in _server_configs:
        _server_configs[key] = {}
    _server_configs[key]["language"] = lang
    _save_server_configs()


def t(guild_id: int, key: str, **kwargs) -> str:
    if not _locales:
        _load_locales()
    lang = get_lang(guild_id)
    locale = _locales.get(lang, _locales.get(DEFAULT_LANG, {}))
    text = locale.get(key) or _locales.get(DEFAULT_LANG, {}).get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text
