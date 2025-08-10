import json
import os
import re
import yt_dlp
import vlc
import time
from pathlib import Path
from urllib.parse import urlparse
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import threading
import InstagramBot
from dotenv import load_dotenv

# Configuration
QUEUE_FILE = "queue.json"
DOWNLOAD_DIR = "downloaded_music"
MAX_HISTORY = 3

# Spotify API credentials - replace with your own
load_dotenv()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Global player controls
player_instance = None
current_player = None
is_paused = True  # Start in paused state
should_play = False  # Flag to indicate if we should play after adding song


def sanitize_filename(filename):
    return re.sub(r'[<>:"/\\|?*]', '', filename)


def get_next_id():
    if not os.path.exists(QUEUE_FILE) or os.path.getsize(QUEUE_FILE) == 0:
        return 0

    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)
            return max(item['id'] for item in queue) + 1 if queue else 0
    except (json.JSONDecodeError, KeyError):
        return 0


def add_to_queue(url, filepath, filetype):
    new_id = get_next_id()
    new_item = {
        "id": new_id,
        "odkaz": url,
        "cesta_k_souboru": filepath,
        "format": filetype
    }

    queue = []
    if os.path.exists(QUEUE_FILE) and os.path.getsize(QUEUE_FILE) > 0:
        try:
            with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
                queue = json.load(f)
        except json.JSONDecodeError:
            queue = []

    queue.append(new_item)

    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)

    return new_id





def extract_info(url):
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if 'title' in info:
                return sanitize_filename(info['title'])
            return f"song_{get_next_id()}"
        except:
            return f"song_{get_next_id()}"


def convert_spotify_to_yt(spotify_url):
    try:
        # Initialize Spotify client
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        ))

        # Get track info from Spotify
        track_id = spotify_url.split('/')[-1].split('?')[0]
        track = sp.track(track_id)
        track_name = track['name']
        artist_name = track['artists'][0]['name']

        # Search on YouTube
        search_query = f"{artist_name} - {track_name}"
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(f"ytsearch:{search_query}", download=False)
            if info and 'entries' in info and info['entries']:
                return info['entries'][0]['webpage_url']

        return None
    except Exception as e:
        print(f"❌ Chyba při konverzi Spotify na YouTube: {str(e)}")
        return None


def download_audio(url, filename):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    parsed = urlparse(url)

    # Handle Spotify URLs
    if "spotify.com" in parsed.netloc.lower():
        print("🔍 Pokouším se stáhnout přímo ze Spotify...")
        return download_from_spotify(url, filename)

    # Handle YouTube/SoundCloud
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, f'{filename}.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        ext = info['ext']
        filepath = ydl.prepare_filename(info)
        return filepath, ext


def download_from_spotify(spotify_url, filename):
    try:
        # Initialize Spotify client
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        ))

        # Get track info
        track_id = spotify_url.split('/')[-1].split('?')[0]
        track = sp.track(track_id)
        track_name = track['name']
        artist_name = track['artists'][0]['name']

        print(f"🎵 Stahuji: {artist_name} - {track_name}")

        # Try to find on YouTube as fallback
        search_query = f"{artist_name} - {track_name}"
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(f"ytsearch:{search_query}", download=False)
            if info and 'entries' in info and info['entries']:
                yt_url = info['entries'][0]['webpage_url']
                print("🔍 Nalezeno na YouTube, stahuji odtud...")
                return download_audio(yt_url, filename)

        print("❌ Nelze stáhnout tuto skladbu - není dostupné na YouTube")
        return None, None

    except Exception as e:
        print(f"❌ Chyba při stahování ze Spotify: {str(e)}")
        return None, None


def get_current_song():
    if not os.path.exists(QUEUE_FILE) or os.path.getsize(QUEUE_FILE) == 0:
        return None

    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)
            for item in queue:
                if item['id'] == 0:
                    return item
    except (json.JSONDecodeError, KeyError):
        pass

    return None


def get_next_song():
    if not os.path.exists(QUEUE_FILE) or os.path.getsize(QUEUE_FILE) == 0:
        return None

    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)
            for item in queue:
                if item['id'] == 1:
                    return item
    except (json.JSONDecodeError, KeyError):
        pass

    return None


def get_previous_song():
    if not os.path.exists(QUEUE_FILE) or os.path.getsize(QUEUE_FILE) == 0:
        return None

    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)
            for item in queue:
                if item['id'] == -1:
                    return item
    except (json.JSONDecodeError, KeyError):
        pass

    return None


def pause_song():
    global current_player, is_paused
    if current_player and current_player.is_playing():
        current_player.pause()
        is_paused = True
        print("⏸️ Hudba pozastavena")
    elif is_paused:
        play_song()  # Will resume from pause
    else:
        print("❌ Nic se momentálně nehraje")


def skip_song():
    global current_player, should_play, is_paused
    # když skipuju, určitě nechci zůstat ve 'paused' režimu
    is_paused = False

    if current_player:
        current_player.stop()

    if not os.path.exists(QUEUE_FILE) or os.path.getsize(QUEUE_FILE) == 0:
        print("❌ Fronta je prázdná")
        return

    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)
    except json.JSONDecodeError:
        print("❌ Chyba při čtení fronty")
        return

    if len(queue) == 0:
        print("❌ Žádná skladba k přeskočení")
        return

    # Najdi aktuální skladbu (id=0)
    current_song = next((item for item in queue if item['id'] == 0), None)
    if not current_song:
        print("❌ Nenalezena aktuální skladba")
        return

    # Postav novou frontu: current -> -1, >0 posuň o -1, historie posuň dolů
    new_queue = []
    history_items = [item for item in queue if item['id'] < 0]
    for item in sorted(history_items, key=lambda x: x['id']):
        item['id'] -= 1
        if item['id'] >= -MAX_HISTORY:
            new_queue.append(item)
        else:
            if item['cesta_k_souboru'] and os.path.exists(item['cesta_k_souboru']):
                try:
                    os.remove(item['cesta_k_souboru'])
                    print(f"🗑️ Smazáno: {Path(item['cesta_k_souboru']).name}")
                except:
                    pass

    current_song['id'] = -1
    new_queue.append(current_song)

    for item in [item for item in queue if item['id'] > 0]:
        item['id'] -= 1
        new_queue.append(item)

    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(new_queue, f, indent=2, ensure_ascii=False)

    print("⏭️ Přeskočeno na další skladbu")
    if should_play:
        next_song = get_current_song()
        if next_song:
            play_song(next_song['cesta_k_souboru'])
        else:
            print("❌ Žádná další skladba k přehrání")


def update_queue():
    if not os.path.exists(QUEUE_FILE) or os.path.getsize(QUEUE_FILE) == 0:
        return

    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)
    except json.JSONDecodeError:
        return

    new_queue = []
    files_to_delete = []

    # Move current song to history (id=-1)
    for item in queue:
        if item['id'] == 0:  # Current song
            item['id'] = -1
        elif item['id'] > 0:  # Upcoming songs
            item['id'] -= 1
        elif item['id'] < 0:  # History items
            item['id'] -= 1
            if item['id'] < -MAX_HISTORY:
                if item['cesta_k_souboru'] and os.path.exists(item['cesta_k_souboru']):
                    files_to_delete.append(item['cesta_k_souboru'])
                continue

        new_queue.append(item)

    with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(new_queue, f, indent=2, ensure_ascii=False)

    for filepath in files_to_delete:
        try:
            os.remove(filepath)
            print(f"🗑️ Smazáno: {Path(filepath).name}")
        except:
            pass


def play_previous_song():
    global current_player, should_play
    if current_player:
        current_player.stop()

    previous = get_previous_song()
    if not previous:
        print("❌ Žádná předchozí skladba v historii")
        return

    if not os.path.exists(QUEUE_FILE) or os.path.getsize(QUEUE_FILE) == 0:
        return

    try:
        with open(QUEUE_FILE, 'r', encoding='utf-8') as f:
            queue = json.load(f)

        # Update IDs to move previous song to current position
        new_queue = []
        for item in queue:
            if item['id'] == -1:  # The previous song we want to play
                item['id'] = 0    # Make it current
            elif item['id'] == 0:  # Current song
                item['id'] = 1    # Move to next position
            elif item['id'] > 0:  # Other upcoming songs
                item['id'] += 1
            elif item['id'] < -1:  # Older history items
                item['id'] += 1
                if item['id'] < -MAX_HISTORY:
                    continue  # Remove from queue

            new_queue.append(item)

        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_queue, f, indent=2, ensure_ascii=False)

        print("⏮️ Vráceno k předchozí skladbě")
        if should_play:
            play_song(previous['cesta_k_souboru'])
    except (json.JSONDecodeError, KeyError):
        print("❌ Chyba při zpracování fronty skladeb")


def play_song(filepath=None):
    global player_instance, current_player, is_paused, should_play

    if filepath is None and current_player:
        # Resume playback if paused
        if is_paused:
            current_player.play()
            is_paused = False
            print("▶️ Pokračování v přehrávání")
        return

    if current_player:
        current_player.stop()

    if filepath is None:
        current = get_current_song()
        if current and current['cesta_k_souboru']:
            filepath = current['cesta_k_souboru']
        else:
            print("❌ Žádná skladba k přehrání")
            return

    try:
        player_instance = vlc.Instance()
        current_player = player_instance.media_player_new()
        media = player_instance.media_new(filepath)
        current_player.set_media(media)
        current_player.play()
        is_paused = False
        should_play = True

        # Místo jednorázového čekání jemně čekej až 3 s na rozběhnutí přehrávání
        start = time.time()
        started = False
        while time.time() - start < 3.0:
            if current_player.is_playing():
                started = True
                break
            time.sleep(0.1)

        if not started:
            print("❌ Nepodařilo se spustit přehrávání (timeout)")
            # DŮLEŽITÉ: neshazuj should_play; smyčka pak může zkusit další skladbu
    except Exception as e:
        print(f"❌ Chyba při přehrávání: {str(e)}")
        # DŮLEŽITÉ: neshazuj should_play; ponecháme logiku na smyčce přehrávače



def player_loop():
    global should_play, is_paused, current_player
    print("\n🎵 Přehrávač spuštěn - čekám na skladby.")
    while True:
        try:
            current = get_current_song()
            if not current or not current['cesta_k_souboru']:
                time.sleep(2)
                continue

            song_path = current['cesta_k_souboru']
            song_name = Path(song_path).stem

            if should_play:
                # 🔧 OPRAVA: nespouštěj znovu, pokud už hraje (nebo se právě resumlo)
                already_playing = current_player is not None and current_player.is_playing()
                if not already_playing and not is_paused:
                    print(f"\n🎵 Nyní hraje: {song_name} [{current['format'].upper()}]")
                    try:
                        play_song(song_path)
                    except Exception as e:
                        print(f"❌ Chyba při spuštění přehrávání: {str(e)}")
                        should_play = False
                        continue

                # Čekej, dokud skladba neskončí (pauza = jen čekej, neposouvej frontu)
                while True:
                    if current_player is None:
                        break
                    if is_paused:
                        time.sleep(0.2)
                        continue
                    if current_player.is_playing():
                        time.sleep(0.2)
                        continue
                    # nehraje a není pauza -> skladba dohrála
                    break

                if not is_paused:
                    update_queue()
                    next_song = get_current_song()
                    if next_song and next_song['cesta_k_souboru']:
                        print("\n🔜 Automaticky spouštím další skladbu.")
                        # spuštění další skladby, ale jen když se opravdu nehraje nic
                        already_playing = current_player is not None and current_player.is_playing()
                        if not already_playing:
                            play_song(next_song['cesta_k_souboru'])
                    else:
                        print("\n⏹️ Konec fronty - žádné další skladby k přehrání")
                        should_play = False
            else:
                time.sleep(2)

        except Exception as e:
            print(f"❌ Chyba v player_loop: {str(e)}")
            time.sleep(2)



def add_song_process():
    global should_play
    print("\n🎵 Hudební stahovač v2.4")
    print("Podporované služby: YouTube, Spotify, SoundCloud")
    print("Příkazy: next (přeskočit), previous (zpět), pause (pozastavit), play (pokračovat)")
    print("Pro ukončení napište 'q'\n")

    while True:
        try:
            user_input = input("Zadejte odkaz nebo příkaz: ").strip()
            if user_input.lower() == 'q':
                break
            elif user_input.lower() == 'next' or user_input.lower() == 'skip':
                skip_song()
                continue
            elif user_input.lower() == 'previous':
                play_previous_song()
                continue
            elif user_input.lower() == 'pause':
                pause_song()
                continue
            elif user_input.lower() == 'play':
                should_play = True
                play_song()
                continue

            # If not a command, treat as URL
            url = user_input
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                print("Neplatný URL formát!")
                continue

            netloc = parsed.netloc.lower()
            if "spotify.com" in netloc:
                print("🔍 Spotify odkaz - hledám na YouTube...")
                yt_url = convert_spotify_to_yt(url)
                if yt_url:
                    url = yt_url
                    print("✅ Nalezeno na YouTube")
                else:
                    print("⚠️ Nenalezeno na YouTube - pokusím se stáhnout přímo ze Spotify")
            elif "soundcloud.com" in netloc:
                print("🔍 SoundCloud odkaz - stahuji...")
            elif "youtube.com" in netloc or "youtu.be" in netloc:
                print("🔍 YouTube odkaz - stahuji...")
            else:
                print("❌ Nepodporovaná služba!")
                continue

            filename = extract_info(url)
            try:
                filepath, filetype = download_audio(url, filename)
                if filepath and filetype:
                    add_to_queue(url, filepath, filetype)
                    print(f"✅ Úspěšně staženo: {Path(filepath).name}")
                    print(f"📁 Formát: {filetype.upper()}, Velikost: {os.path.getsize(filepath) / 1024:.1f} KB")
                    print("ℹ️ Napište 'play' pro spuštění přehrávání (pokud ještě nehraje)")
                    # DŮLEŽITÉ: odstraněno `should_play = False` – neblokuj autoplay
                else:
                    print("❌ Nepodařilo se stáhnout skladbu")
            except Exception as e:
                print(f"❌ Chyba při stahování: {str(e)}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Neočekávaná chyba: {str(e)}")


if __name__ == "__main__":
    ig_thread = threading.Thread(target=InstagramBot.run, daemon=True)
    ig_thread.start()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Initialize empty queue file if it doesn't exist
    if not os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)

    import threading

    player_thread = threading.Thread(target=player_loop, daemon=True)
    player_thread.start()

    add_song_process()