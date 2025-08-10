# InstagramBot.py
# -*- coding: utf-8 -*-
"""
Instagram DM integrace pro UniversalMusicPlayer.

Funkce:
- Každé 2 vteřiny kontroluje poslední 3 zprávy ve zvoleném GROUP threadu.
- Při prvním spuštění si poslední 3 zprávy jen "načte" a nepracuje s nimi.
- Přidává odkazy (YouTube / SoundCloud / Spotify) do fronty přehrávače.
- Spotify speciál: pokud IG zprávu označí jako 'music', vrátí uživateli instrukci poslat textový odkaz.
- Cooldown (výchozí 20 min) pro ne-admin uživatele přes SQLite (soubor cooldown.db).
- Příkazy: play, pause (pro všechny), next, previous, set cooldown X (jen admin).
- Odpovídá do chatu potvrzením / chybovou hláškou.
- Udržuje session v session.json, aby se zbytečně znovu nepřihlašovalo.

Pozn.: Pro integraci do přehrávače importuje modul UniversalMusicPlayer pod aliasem `ump`
a snaží se použít existující funkce. Má i "inteligentní" fallbacky, pokud se názvy ve tvém projektu mírně liší.
"""

import os
import re
import time
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional, Tuple

# --- Závislosti třetích stran ---
# pip install instagrapi python-dotenv
from instagrapi import Client
from instagrapi.exceptions import LoginRequired
from dotenv import load_dotenv

# --- Import hlavního přehrávače ---
# Uprav případně název, pokud se hlavní modul jmenuje jinak.
import UniversalMusicPlayer as ump


# -----------------------------
# Konfigurace a konstanty
# -----------------------------
load_dotenv()

IG_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
IG_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")
THREAD_ID = os.getenv("GROUP_THREAD_ID", "")  # ID skupinového vlákna (DM group)
ADMIN_IG_USER_ID = os.getenv("ADMIN_IG_USER_ID")  # tvé ID, lze přepsat v .env

SESSION_FILE = os.getenv("IG_SESSION_FILE", "session.json")
SQLITE_FILE = os.getenv("IG_COOLDOWN_DB", "cooldown.db")

# Výchozí cooldown v minutách (lze měnit příkazem "set cooldown X" od admina)
cooldown_minutes = int(os.getenv("IG_DEFAULT_COOLDOWN_MINUTES", "20"))

# Interval kontroly zpráv (sekundy)
POLL_INTERVAL_SEC = 2

# Kolik posledních zpráv načítat při každé iteraci
LAST_N_MSG = 3

# Regexy pro detekci URL a příkazů
URL_REGEX = re.compile(
    r"(?P<url>(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be|soundcloud\.com|on\.soundcloud\.com|open\.spotify\.com)/[^\s]+)",
    re.IGNORECASE,
)
SET_COOLDOWN_REGEX = re.compile(r"^\s*set\s+cooldown\s+(\d+)\s*$", re.IGNORECASE)


# -----------------------------
# Pomocné: databáze cooldown
# -----------------------------
def _init_sqlite():
    conn = sqlite3.connect(SQLITE_FILE, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cooldown (
            user_id TEXT PRIMARY KEY,
            last_added INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


_sql_conn = _init_sqlite()
_sql_lock = threading.Lock()


def _now_ts() -> int:
    return int(time.time())


def is_on_cooldown(user_id: str) -> Tuple[bool, int]:
    """
    Vrátí (is_on_cooldown, seconds_left).
    Admina nikdy neomezuje.
    """
    if str(user_id) == str(ADMIN_IG_USER_ID):
        return (False, 0)
    with _sql_lock:
        cur = _sql_conn.execute(
            "SELECT last_added FROM cooldown WHERE user_id = ?", (str(user_id),)
        )
        row = cur.fetchone()
    if not row:
        return (False, 0)

    last_added = int(row[0])
    delta = _now_ts() - last_added
    cooldown_sec = cooldown_minutes * 60
    if delta >= cooldown_sec:
        return (False, 0)
    return (True, cooldown_sec - delta)


def set_cooldown_time(user_id: str):
    with _sql_lock:
        _sql_conn.execute(
            "REPLACE INTO cooldown (user_id, last_added) VALUES (?, ?)",
            (str(user_id), _now_ts()),
        )
        _sql_conn.commit()


# -----------------------------
# Instagram klient (instagrapi)
# -----------------------------
_cl = Client()
_cl_lock = threading.Lock()  # ochrana volání klienta z 1 vlákna (pro jistotu)


def _login_with_session():
    """
    Přihlášení s využitím session.json pokud existuje.
    """
    if IG_USERNAME == "" or IG_PASSWORD == "" or THREAD_ID == "":
        raise RuntimeError(
            "IG_USERNAME, IG_PASSWORD a IG_THREAD_ID musí být nastaveny v .env"
        )

    # Načti existující session (pokud je)
    if os.path.exists(SESSION_FILE):
        try:
            _cl.load_settings(SESSION_FILE)
        except Exception:
            # pokud se nepodaří načíst, budeme pokračovat čistým loginem
            pass

    # Login (pokud jsou session cookies platné, instagrapi je použije)
    _cl.login(IG_USERNAME, IG_PASSWORD)

    # Dumpni session pro budoucí použití (po úspěšném loginu)
    try:
        _cl.dump_settings(SESSION_FILE)
    except Exception:
        # nevadí, běžíme dál
        pass


def _ig_send_text(text: str):
    """
    Pošli textovou zprávu do skupinového threadu.
    """
    with _cl_lock:
        _cl.direct_send(text, thread_ids=[THREAD_ID])


def _ig_fetch_last_messages(n: int = LAST_N_MSG):
    """
    Načti posledních N zpráv z threadu.
    """
    with _cl_lock:
        msgs = _cl.direct_messages(THREAD_ID, amount=n)
    return msgs


# -----------------------------
# Přehrávač: adapter vrstvička
# -----------------------------
def _safe_hasattr(obj, name: str) -> bool:
    return getattr(obj, name, None) is not None


def player_play() -> bool:
    """
    Spustí/obnoví přehrávání.
    """
    try:
        if _safe_hasattr(ump, "play_song"):
            ump.play_song()
            return True
        # Fallback: některé projekty používají toggle v pause_song
        if _safe_hasattr(ump, "pause_song"):
            # Zkusíme 'odpauznout' – některé implementace pause_song samy resume
            ump.pause_song()
            return True
    except Exception:
        pass
    return False


def player_pause() -> bool:
    """
    Pauzne / toggluje pauzu.
    """
    try:
        if _safe_hasattr(ump, "pause_song"):
            ump.pause_song()
            return True
    except Exception:
        pass
    return False


def player_next() -> bool:
    """
    Přeskočí na další skladbu.
    """
    try:
        if _safe_hasattr(ump, "skip_song"):
            ump.skip_song()
            return True
    except Exception:
        pass
    return False


def player_previous() -> bool:
    """
    Přeskočí na předchozí skladbu.
    """
    try:
        if _safe_hasattr(ump, "play_previous_song"):
            ump.play_previous_song()
            return True
    except Exception:
        pass
    return False


def add_track_from_url(url: str) -> Tuple[bool, Optional[str]]:
    """
    Přidá skladbu do fronty podle URL.
    Vrací (success, human_name_or_none).
    Snaží se adaptovat na různé názvy funkcí v UniversalMusicPlayer.
    """
    # 1) Přímý adapter, pokud ho projekt má
    for fname in ("add_link_to_queue", "add_to_queue_from_url", "enqueue_url"):
        func = getattr(ump, fname, None)
        if callable(func):
            try:
                human = func(url)
                return True, str(human) if human else None
            except Exception:
                pass  # zkusíme další variantu

    # 2) "Manuální" cesta používaná v UMP: extract_info -> download_audio -> add_to_queue
    extract_info = getattr(ump, "extract_info", None)
    download_audio = getattr(ump, "download_audio", None)
    add_to_queue = getattr(ump, "add_to_queue", None)

    if callable(extract_info) and callable(download_audio) and callable(add_to_queue):
        try:
            filename = extract_info(url)  # např. název podle metadat
            filepath, filetype = download_audio(url, filename)
            if filepath:
                add_to_queue(url, filepath, filetype)
                # Vrátíme název souboru bez cesty
                try:
                    import os as _os

                    base = _os.path.basename(filepath)
                except Exception:
                    base = filename
                return True, base
        except Exception:
            pass

    # 3) Poslední šance – některé projekty mají "download_from_spotify" atd.
    # ale bez detailní znalosti to nebudeme víc komplikovat.
    return False, None


# -----------------------------
# Zpracování zpráv
# -----------------------------
def _is_admin(user_id: str) -> bool:
    return str(user_id) == str(ADMIN_IG_USER_ID)


def _normalize_spotify_text_url(text: str) -> Optional[str]:
    """
    Pokud text obsahuje 'open.spotify...' bez http, doplní https://
    Jinak vrátí None pokud nenašla nic vhodného.
    """
    text = text.strip()
    if "open.spotify.com/" in text and not text.lower().startswith(("http://", "https://")):
        return "https://" + text
    return None


def _extract_supported_urls(text: str) -> list:
    """
    Najde všechny podporované odkazy v textu (yt / sc / spotify).
    Vrací list URL (se schématem https:// pokud chybí).
    """
    urls = []
    for m in URL_REGEX.finditer(text or ""):
        raw = m.group("url")
        # Doplň schéma, pokud chybí
        if not raw.lower().startswith(("http://", "https://")):
            raw = "https://" + raw
        urls.append(raw)
    return urls


def _process_command(msg_text: str, from_user_id: str) -> bool:
    """
    Vrátí True, pokud šlo o příkaz a byl zpracován (a tedy nemáme dál zpracovávat jako odkaz).
    """
    if not msg_text:
        return False
    t = msg_text.strip().lower()

    # set cooldown X (jen admin)
    m = SET_COOLDOWN_REGEX.match(msg_text)
    if m:
        if not _is_admin(from_user_id):
            _ig_send_text("❌ Nemáš oprávnění měnit cooldown.")
            return True
        try:
            minutes = int(m.group(1))
            if minutes < 0:
                raise ValueError
            global cooldown_minutes
            cooldown_minutes = minutes
            _ig_send_text(f"⏱️ Cooldown nastaven na {minutes} min.")
        except Exception:
            _ig_send_text("❌ Neplatná hodnota pro cooldown. Použij třeba: set cooldown 1")
        return True

    # play (pro všechny)
    if t == "play":
        if player_play():
            _ig_send_text("▶️ Přehrávání spuštěno / pokračuje.")
        else:
            _ig_send_text("❌ Nepodařilo se spustit přehrávání.")
        return True

    # pause (pro všechny)
    if t == "pause":
        if player_pause():
            _ig_send_text("⏸️ Přehrávání pozastaveno / togglováno.")
        else:
            _ig_send_text("❌ Nepodařilo se pozastavit / togglovat přehrávání.")
        return True

    # next (jen admin)
    if t == "next":
        if not _is_admin(from_user_id):
            _ig_send_text("❌ Tento příkaz může použít jen admin.")
            return True
        if player_next():
            _ig_send_text("⏭️ Přeskočeno na další skladbu.")
        else:
            _ig_send_text("❌ Nelze přeskočit na další skladbu.")
        return True

    # previous (jen admin)
    if t == "previous":
        if not _is_admin(from_user_id):
            _ig_send_text("❌ Tento příkaz může použít jen admin.")
            return True
        if player_previous():
            _ig_send_text("⏮️ Vráceno na předchozí skladbu.")
        else:
            _ig_send_text("❌ Nelze přejít na předchozí skladbu.")
        return True

    return False  # nebyl to příkaz


def _process_message(msg) -> None:
    """
    Zpracuje jednu zprávu z IG.
    msg má typ DirectMessage z instagrapi, očekávané atributy:
      - id
      - user_id
      - item_type ('text', 'link', 'media_share', 'story_share', 'raven_media', 'animated_media', 'music' apod.)
      - text (u textových zpráv)
    """
    from_user_id = str(getattr(msg, "user_id", ""))  # číslo -> string
    item_type = getattr(msg, "item_type", None)
    text = getattr(msg, "text", None) or ""

    # 1) nejdřív příkazy (play/pause/next/previous/set cooldown)
    if _process_command(text, from_user_id):
        return

    # 2) Spotify sdílení přes IG jako "music" (bez dostupné URL)
    if item_type and str(item_type).lower() == "music":
        _ig_send_text(
            "⚠️ Tento typ Spotify sdílení neumím zpracovat. "
            "Pošli prosím odkaz jako text ve tvaru:\n"
            "`open.spotify.com/track/...` (bez https) – já si `https://` doplním."
        )
        return

    # 3) Text bez http, ale obsahuje 'open.spotify...' -> doplníme https://
    normalized_spotify = _normalize_spotify_text_url(text)
    candidate_urls = []
    if normalized_spotify:
        candidate_urls.append(normalized_spotify)

    # 4) Najdi běžné URL v textu (yt / sc / spotify)
    candidate_urls.extend(_extract_supported_urls(text))

    if not candidate_urls:
        # Nebyl to příkaz ani odkaz, ignorujeme
        return

    # 5) Cooldown kontrola (pro přidávání skladeb)
    on_cd, left = is_on_cooldown(from_user_id)
    if on_cd:
        minutes_left = max(1, int((left + 59) // 60))
        _ig_send_text(
            f"⌛ Už jsi nedávno přidal(a) skladbu. Zkus to znovu za ~{minutes_left} min."
        )
        return

    # 6) Projdi nalezené URL a první úspěšné přidej do fronty
    # (Pokud by někdo poslal více odkazů v 1 zprávě, přidáme jen první validní.)
    for url in candidate_urls:
        # Převod Spotify -> YouTube necháváme na implementaci v UMP,
        # případně UMP už obsahuje logiku uvnitř downloadu.
        ok, human = add_track_from_url(url)
        if ok:
            set_cooldown_time(from_user_id)
            if human:
                _ig_send_text(f"✅ Přidáno do fronty: {human}")
            else:
                _ig_send_text("✅ Skladba přidána do fronty.")
            return

    # 7) Pokud žádný odkaz se nepovedl zpracovat:
    _ig_send_text("❌ Nepodařilo se zpracovat odkaz. Podporuji YouTube, SoundCloud a Spotify.")


# -----------------------------
# Hlavní smyčka
# -----------------------------
def run():
    """
    Spusť IG bota: přihlášení + smyčka pro kontrolu zpráv.
    Tuto funkci spusť v samostatném vlákně z UniversalMusicPlayer.py.
    """
    # Přihlášení
    try:
        _login_with_session()
    except Exception as e:
        print(f"[InstagramBot] Chyba přihlášení: {e}")
        raise

    print("[InstagramBot] Přihlášeno k Instagramu.")
    print(f"[InstagramBot] Sleduji thread: {THREAD_ID}")
    print(f"[InstagramBot] Admin ID: {ADMIN_IG_USER_ID}")
    print(f"[InstagramBot] Cooldown: {cooldown_minutes} min")

    # Na první iteraci jen načteme posledních LAST_N_MSG a uložíme si jejich ID
    last_seen_ids = []
    try:
        initial_msgs = _ig_fetch_last_messages(LAST_N_MSG)
        last_seen_ids = [getattr(m, "id", None) for m in initial_msgs if getattr(m, "id", None)]
        print(f"[InstagramBot] Inicializace: pamatuji si {len(last_seen_ids)} posledních zpráv (bez zpracování).")
    except LoginRequired:
        # pokud session expirovala, zkusíme znovu login a pokračujeme
        _login_with_session()
        initial_msgs = _ig_fetch_last_messages(LAST_N_MSG)
        last_seen_ids = [getattr(m, "id", None) for m in initial_msgs if getattr(m, "id", None)]

    # Hlavní smyčka
    while True:
        try:
            msgs = _ig_fetch_last_messages(LAST_N_MSG)
        except LoginRequired:
            # Občas IG vyžaduje re-login
            try:
                _login_with_session()
                msgs = _ig_fetch_last_messages(LAST_N_MSG)
            except Exception as e:
                print(f"[InstagramBot] LoginRequired -> chyba: {e}")
                time.sleep(POLL_INTERVAL_SEC)
                continue
        except Exception as e:
            print(f"[InstagramBot] Chyba při načítání zpráv: {e}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # Pokud nemáme nic, pauza
        if not msgs:
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # Zprávy zpracujeme od nejstarší po nejnovější
        new_msgs = [m for m in reversed(msgs) if getattr(m, "id", None) not in last_seen_ids]

        for m in new_msgs:
            try:
                _process_message(m)
            except Exception as e:
                print(f"[InstagramBot] Chyba při zpracování zprávy: {e}")

        # Uložíme aktuální okno posledních zpráv (abychom věděli, co už je zkontrolováno)
        last_seen_ids = [getattr(m, "id", None) for m in msgs if getattr(m, "id", None)]

        time.sleep(POLL_INTERVAL_SEC)


# Pro samostatné ladicí spuštění (nepovinné)
if __name__ == "__main__":
    run()
