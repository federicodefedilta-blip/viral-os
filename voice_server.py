#!/usr/bin/env python3
"""
Viral OS - Server locale (voce + render video)
- edge-tts: voce neurale + tempi per frase
- ffmpeg: monta il video finale in modo deterministico (no scatti, no browser)

Avvio:
  py voice_server.py

Lascia questa finestra aperta mentre usi ViralOS.

Endpoint:
  GET  /ping                          -> "ok"
  GET  /tts?text=...&voice=...         -> MP3
  GET  /tts_json?text=...&voice=...    -> {audio(b64), words[{t,d,w}]}
  POST /render  (JSON)                 -> MP4 montato
     body: { voice_mp3_b64, timings:[{t,d,w}], music_wav_b64?, clips:[url],
             total_ms, voice_vol?, music_vol? }
"""

import asyncio
import sys
import os
import json
import base64
import glob
import tempfile
import shutil
import subprocess
import urllib.request
from shutil import which
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# pip_system_certs si auto-attiva (certificati Windows per rete aziendale)
import edge_tts

PORT = 5555
DEFAULT_VOICE = "it-IT-DiegoNeural"
W, H = 1080, 1920

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
LAST_RENDER = os.path.join(OUTPUT_DIR, "last_render.mp4")
CLIENT_SECRET = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
             "https://www.googleapis.com/auth/youtube",
             "https://www.googleapis.com/auth/yt-analytics.readonly"]


# ----------------------------- TTS -----------------------------

def genera_mp3(testo, voce):
    """Genera l'MP3 della voce e restituisce (bytes_audio, lista_frasi)."""
    async def _run():
        chunks = bytearray()
        words = []
        communicate = edge_tts.Communicate(testo, voce)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.extend(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                words.append({
                    "t": chunk["offset"] / 10000.0,
                    "d": chunk["duration"] / 10000.0,
                    "w": chunk.get("text", ""),
                })
        return bytes(chunks), words
    return asyncio.run(_run())


# ----------------------------- RENDER -----------------------------

def find_ffmpeg():
    p = which("ffmpeg")
    if p:
        return p
    pattern = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                           "Microsoft", "WinGet", "Packages",
                           "Gyan.FFmpeg*", "**", "ffmpeg.exe")
    m = glob.glob(pattern, recursive=True)
    return m[0] if m else None


def download_file(url, path):
    # percorso locale esistente -> copia (utile per test e robustezza)
    if os.path.exists(url):
        shutil.copyfile(url, path)
        return
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)


def ms_to_ass(ms):
    cs = int(round(ms / 10.0))
    h = cs // 360000
    cs %= 360000
    m = cs // 6000
    cs %= 6000
    s = cs // 100
    cs %= 100
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(t):
    return t.replace("\n", " ").replace("\r", " ").replace("{", "(").replace("}", ")").strip()


def build_ass(timings, total_ms, path):
    """Crea un file ASS con sottotitoli sincronizzati, stile horror bianco con bordo."""
    fontsize = 52
    style = (
        "Style: Def,Arial,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,4,2,2,80,80,300,1" % fontsize
    )
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
        "MarginL,MarginR,MarginV,Encoding\n%s\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        % (W, H, style)
    )
    lines = [header]
    n = len(timings)
    for i, t in enumerate(timings):
        start = t["t"]
        end = timings[i + 1]["t"] if i + 1 < n else total_ms
        if end <= start:
            end = start + 800
        txt = ass_escape(t.get("w", ""))
        if not txt:
            continue
        lines.append("Dialogue: 0,%s,%s,Def,,0,0,0,,%s\n" %
                     (ms_to_ass(start), ms_to_ass(end), txt))
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(lines))


def run_ff(ffmpeg, args, cwd=None):
    p = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + args,
                       cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "ignore")[-800:])


def clip_index(i, n_sent, n_clips):
    return min(int(i * n_clips / max(n_sent, 1)), n_clips - 1)


def render_job(data, work):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg non trovato")

    timings = data.get("timings") or []
    clips = data.get("clips") or []
    total_ms = float(data.get("total_ms") or 0)
    voice_vol = float(data.get("voice_vol", 1.8))
    music_vol = float(data.get("music_vol", 1.0))

    if not timings:
        raise RuntimeError("timings mancanti")
    if not clips:
        raise RuntimeError("nessuna clip")
    if total_ms <= 0:
        total_ms = timings[-1]["t"] + timings[-1]["d"] + 1200

    # 1. salva voce + musica
    voice_path = os.path.join(work, "voice.mp3")
    with open(voice_path, "wb") as f:
        f.write(base64.b64decode(data["voice_mp3_b64"]))
    music_path = None
    if data.get("music_wav_b64"):
        music_path = os.path.join(work, "music.wav")
        with open(music_path, "wb") as f:
            f.write(base64.b64decode(data["music_wav_b64"]))

    # 2. scarica clip
    clip_paths = []
    for idx, url in enumerate(clips):
        cp = os.path.join(work, f"clip{idx}.mp4")
        try:
            download_file(url, cp)
            if os.path.getsize(cp) > 0:
                clip_paths.append(cp)
        except Exception as e:
            print(f"     clip {idx} non scaricata: {e}")
    if not clip_paths:
        raise RuntimeError("nessuna clip scaricabile")
    n_clips = len(clip_paths)
    n_sent = len(timings)

    # 3. raggruppa le frasi per clip (clip in ordine, allineate alle frasi)
    segments = []  # (clip_path, dur_sec)
    i = 0
    while i < n_sent:
        k = clip_index(i, n_sent, n_clips)
        seg_start = 0.0 if i == 0 else timings[i]["t"]
        j = i
        while j < n_sent and clip_index(j, n_sent, n_clips) == k:
            j += 1
        seg_end = timings[j]["t"] if j < n_sent else total_ms
        dur = max(0.4, (seg_end - seg_start) / 1000.0)
        segments.append((clip_paths[k], dur))
        i = j

    # 4. crea un segmento normalizzato per gruppo
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},setsar=1,fps=30,format=yuv420p")
    seg_files = []
    for si, (cp, dur) in enumerate(segments):
        out = os.path.join(work, f"seg{si}.mp4")
        run_ff(ffmpeg, ["-stream_loop", "-1", "-i", cp, "-t", f"{dur:.3f}",
                        "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", out])
        seg_files.append(out)

    # 5. concat dei segmenti
    listf = os.path.join(work, "list.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for sf in seg_files:
            f.write(f"file '{os.path.basename(sf)}'\n")
    base = os.path.join(work, "base.mp4")
    run_ff(ffmpeg, ["-f", "concat", "-safe", "0", "-i", "list.txt",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "base.mp4"],
           cwd=work)

    # 6. sottotitoli ASS
    build_ass(timings, total_ms, os.path.join(work, "subs.ass"))

    # 7. mux finale: video + sottotitoli + vignette + voce (+ musica)
    total_sec = total_ms / 1000.0
    out = os.path.join(work, "final.mp4")
    vfilter = "[0:v]subtitles=subs.ass,vignette,eq=brightness=-0.04[v]"
    if music_path:
        inputs = ["-i", "base.mp4", "-i", "voice.mp3", "-i", "music.wav"]
        fc = (vfilter +
              f";[1:a]volume={voice_vol}[a1];[2:a]volume={music_vol}[a2];"
              "[a1][a2]amix=inputs=2:duration=longest:dropout_transition=0[a]")
    else:
        inputs = ["-i", "base.mp4", "-i", "voice.mp3"]
        fc = vfilter + f";[1:a]volume={voice_vol}[a]"
    run_ff(ffmpeg, inputs + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                             "-t", f"{total_sec:.3f}", "-c:v", "libx264", "-preset", "medium",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "final.mp4"],
           cwd=work)

    # salva una copia persistente per la pubblicazione
    try:
        shutil.copyfile(out, LAST_RENDER)
    except Exception:
        pass
    with open(out, "rb") as f:
        return f.read()


# ----------------------------- YOUTUBE -----------------------------

def yt_get_credentials():
    """Carica/aggiorna le credenziali OAuth salvate. None se non autorizzato."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    if not os.path.exists(TOKEN_FILE):
        return None
    # carica con gli scope già presenti nel token (evita errori di scope-mismatch al refresh)
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds if creds and creds.valid else None


def yt_authorize():
    """Avvia il flusso OAuth (apre il browser per il consenso). Salva token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not os.path.exists(CLIENT_SECRET):
        raise RuntimeError("client_secret.json mancante nella cartella viral-os")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, YT_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent",
                                  authorization_prompt_message="")
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    return creds


def yt_service(creds):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds)


def yt_channel_name(creds):
    try:
        yt = yt_service(creds)
        r = yt.channels().list(part="snippet", mine=True).execute()
        items = r.get("items", [])
        return items[0]["snippet"]["title"] if items else None
    except Exception:
        return None


def yt_make_thumbnail(title):
    """Genera una miniatura dall'ultimo render: frame + titolo in sovrimpressione."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not os.path.exists(LAST_RENDER):
        return None
    thumb = os.path.join(OUTPUT_DIR, "thumb.jpg")
    safe = title.replace("'", "").replace(":", " ").replace("\\", " ")[:60]
    # estrae un frame al 25% e scrive il titolo grande in basso
    vf = (f"scale=720:1280,drawtext=text='{safe}':fontcolor=white:fontsize=54:"
          f"borderw=4:bordercolor=black:x=(w-text_w)/2:y=h*0.72:line_spacing=8")
    try:
        run_ff(ffmpeg, ["-ss", "3", "-i", LAST_RENDER, "-frames:v", "1", "-vf", vf, thumb])
        return thumb if os.path.exists(thumb) else None
    except Exception as e:
        print(f"     thumbnail saltata: {e}")
        return None


def yt_upload(data):
    from googleapiclient.http import MediaFileUpload
    creds = yt_get_credentials()
    if not creds:
        raise RuntimeError("non autorizzato - clicca prima 'Collega YouTube'")
    if not os.path.exists(LAST_RENDER):
        raise RuntimeError("nessun video montato - assembla prima il video")

    title = (data.get("title") or "Storia horror").strip()[:100]
    desc = (data.get("description") or "").strip()[:4900]
    tags = [t.lstrip("#") for t in (data.get("tags") or []) if t][:25]
    privacy = data.get("privacy") or "private"
    publish_at = data.get("publishAt")  # ISO8601 UTC, opzionale

    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}
    if publish_at:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at

    body = {
        "snippet": {"title": title, "description": desc, "tags": tags,
                    "categoryId": "24"},
        "status": status,
    }
    yt = yt_service(creds)
    media = MediaFileUpload(LAST_RENDER, mimetype="video/mp4", resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    vid = resp["id"]

    thumb_set = False
    if data.get("thumbnail"):
        try:
            tp = yt_make_thumbnail(title)
            if tp:
                yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(tp)).execute()
                thumb_set = True
        except Exception as e:
            print(f"     thumbnail non impostata: {e}")
    return {"id": vid, "url": f"https://youtu.be/{vid}", "thumbnail": thumb_set,
            "scheduled": bool(publish_at)}


def yt_analytics(max_videos=25):
    """Restituisce i video recenti del canale con statistiche e retention."""
    from datetime import date, timedelta
    from googleapiclient.discovery import build
    creds = yt_get_credentials()
    if not creds:
        raise RuntimeError("non autorizzato")
    yt = yt_service(creds)

    ch = yt.channels().list(part="contentDetails,statistics", mine=True).execute()
    if not ch.get("items"):
        return {"videos": [], "subs": 0}
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    subs = int(ch["items"][0].get("statistics", {}).get("subscriberCount", 0))

    pl = yt.playlistItems().list(part="snippet,contentDetails",
                                 playlistId=uploads, maxResults=max_videos).execute()
    vids = []
    for it in pl.get("items", []):
        vids.append((it["contentDetails"]["videoId"],
                     it["snippet"]["title"],
                     it["contentDetails"].get("videoPublishedAt", "")))
    ids = [v[0] for v in vids]

    stats = {}
    if ids:
        vr = yt.videos().list(part="statistics", id=",".join(ids)).execute()
        for it in vr.get("items", []):
            s = it.get("statistics", {})
            stats[it["id"]] = {
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
            }

    retention = {}
    analytics_ok = True
    try:
        ya = build("youtubeAnalytics", "v2", credentials=creds)
        start = (date.today() - timedelta(days=90)).isoformat()
        end = date.today().isoformat()
        rep = ya.reports().query(
            ids="channel==MINE", startDate=start, endDate=end,
            metrics="views,averageViewPercentage,averageViewDuration",
            dimensions="video", sort="-views", maxResults=200).execute()
        cols = [c["name"] for c in rep.get("columnHeaders", [])]
        for row in rep.get("rows", []):
            d = dict(zip(cols, row))
            retention[d["video"]] = {
                "retention": d.get("averageViewPercentage"),
                "avgDur": d.get("averageViewDuration"),
            }
    except Exception as e:
        analytics_ok = False
        print(f"     analytics API non disponibile: {e}")

    out = []
    for vid, title, pub in vids:
        st = stats.get(vid, {})
        rt = retention.get(vid, {})
        out.append({
            "id": vid, "title": title, "published": pub,
            "views": st.get("views", 0), "likes": st.get("likes", 0),
            "comments": st.get("comments", 0),
            "retention": rt.get("retention"), "avgDur": rt.get("avgDur"),
        })
    return {"videos": out, "subs": subs, "analytics_ok": analytics_ok}


# ----------------------------- HTTP -----------------------------

class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _send(self, code, ctype, body):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/ping":
            self._send(200, "text/plain", b"ok")
            return

        if parsed.path == "/youtube_status":
            try:
                creds = yt_get_credentials()
                name = yt_channel_name(creds) if creds else None
                payload = json.dumps({
                    "configured": os.path.exists(CLIENT_SECRET),
                    "authorized": bool(creds),
                    "channel": name,
                }).encode("utf-8")
                self._send(200, "application/json", payload)
            except Exception as e:
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        if parsed.path == "/youtube_analytics":
            try:
                print("  -> analytics YouTube...")
                res = yt_analytics()
                print(f"     {len(res['videos'])} video, {res['subs']} iscritti")
                self._send(200, "application/json", json.dumps(res).encode("utf-8"))
            except Exception as e:
                print(f"     ERRORE analytics: {e}")
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        if parsed.path == "/youtube_auth":
            try:
                print("  -> avvio OAuth YouTube (apro il browser per il consenso)...")
                yt_authorize()
                name = yt_channel_name(yt_get_credentials())
                print(f"     autorizzato: {name}")
                self._send(200, "application/json",
                           json.dumps({"authorized": True, "channel": name}).encode("utf-8"))
            except Exception as e:
                print(f"     ERRORE auth: {e}")
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        if parsed.path in ("/tts", "/tts_json"):
            testo = (qs.get("text", [""])[0]).strip()
            voce = qs.get("voice", [DEFAULT_VOICE])[0] or DEFAULT_VOICE
            if not testo:
                self._send(400, "text/plain", b"manca text")
                return
            try:
                print(f"  -> voce ({voce}): {testo[:50]}...")
                audio, words = genera_mp3(testo, voce)
                print(f"     OK {len(audio)//1024} KB, {len(words)} frasi")
                if parsed.path == "/tts_json":
                    payload = json.dumps({
                        "audio": base64.b64encode(audio).decode("ascii"),
                        "words": words,
                    }).encode("utf-8")
                    self._send(200, "application/json", payload)
                else:
                    self._send(200, "audio/mpeg", audio)
            except Exception as e:
                print(f"     ERRORE: {e}")
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        self._send(404, "text/plain", b"not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""

        if parsed.path == "/render":
            try:
                data = json.loads(raw.decode("utf-8"))
                print(f"  -> RENDER: {len(data.get('clips', []))} clip, "
                      f"{len(data.get('timings', []))} frasi, "
                      f"{round(float(data.get('total_ms', 0))/1000)}s")
                work = tempfile.mkdtemp(prefix="viralos_")
                try:
                    mp4 = render_job(data, work)
                    print(f"     OK video {len(mp4)//1024} KB")
                    self._send(200, "video/mp4", mp4)
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            except Exception as e:
                print(f"     ERRORE render: {e}")
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        if parsed.path == "/save_pack":
            try:
                data = json.loads(raw.decode("utf-8"))
                day = (data.get("date") or "video").replace("/", "-")
                folder = os.path.join(OUTPUT_DIR, day)
                os.makedirs(folder, exist_ok=True)
                if os.path.exists(LAST_RENDER):
                    shutil.copyfile(LAST_RENDER, os.path.join(folder, "video.mp4"))
                with open(os.path.join(folder, "caption.txt"), "w", encoding="utf-8") as f:
                    f.write((data.get("caption") or "").strip())
                print(f"  -> pacchetto salvato: {folder}")
                self._send(200, "application/json", json.dumps({"folder": folder}).encode("utf-8"))
            except Exception as e:
                print(f"     ERRORE save_pack: {e}")
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        if parsed.path == "/youtube_upload":
            try:
                data = json.loads(raw.decode("utf-8"))
                print(f"  -> UPLOAD YouTube: {data.get('title', '')[:50]}...")
                res = yt_upload(data)
                print(f"     OK {res['url']}")
                self._send(200, "application/json", json.dumps(res).encode("utf-8"))
            except Exception as e:
                print(f"     ERRORE upload: {e}")
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        self._send(404, "text/plain", b"not found")

    def log_message(self, *args):
        pass


def main():
    print("=" * 55)
    print("  Viral OS - Server locale (voce + render)")
    print("=" * 55)
    print(f"  In ascolto su http://127.0.0.1:{PORT}")
    ff = find_ffmpeg()
    print(f"  ffmpeg: {'OK' if ff else 'NON TROVATO - installa Gyan.FFmpeg'}")
    print("  Lascia questa finestra aperta mentre usi il tool.")
    print("=" * 55)
    print()
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nServer fermato.")
        sys.exit(0)
    except OSError as e:
        print(f"\nErrore avvio: {e} (porta {PORT} occupata?)")
        sys.exit(1)


if __name__ == "__main__":
    main()
