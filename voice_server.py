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

# Filtro clip: zoom lento cinematografico (Ken Burns) + grana pellicola horror
ZW, ZH = int(1080 * 1.14), int(1920 * 1.14)
CLIP_VF = (f"scale={ZW}:{ZH}:force_original_aspect_ratio=increase,crop={ZW}:{ZH},"
           f"zoompan=z='min(1.0+0.0011*in,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
           f"d=1:s=1080x1920:fps=30,noise=c0s=9:allf=t,setsar=1,format=yuv420p")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
LAST_RENDER = os.path.join(OUTPUT_DIR, "last_render.mp4")
REGISTRY = os.path.join(OUTPUT_DIR, "registry.json")
CLIENT_SECRET = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
             "https://www.googleapis.com/auth/youtube",
             "https://www.googleapis.com/auth/yt-analytics.readonly"]


# ----------------------------- TTS -----------------------------

# Preset "narratore horror": più lento e più cupo = più espressivo/drammatico
# Prosodia per contesto: (rate, pitch)
PROSODY = {
    "classica":    ("-9%", "-13Hz"),   # lenta, cupa, sospesa (massima tensione)
    "interattivo": ("-1%", "-6Hz"),    # incalzante e presente (è un gioco)
    "classifica":  ("-4%", "-8Hz"),    # ritmata, in crescendo
}
# default (usato dal flusso classico via /tts_json)
TTS_RATE, TTS_PITCH = PROSODY["classica"]


def genera_mp3(testo, voce, rate=None, pitch=None):
    """Genera l'MP3 della voce (prosodia horror) e restituisce (bytes_audio, lista_frasi)."""
    rate = rate or TTS_RATE
    pitch = pitch or TTS_PITCH
    async def _run():
        chunks = bytearray()
        words = []
        communicate = edge_tts.Communicate(testo, voce, rate=rate, pitch=pitch)
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
    """Crea un file ASS con sottotitoli KARAOKE animati: ogni parola si illumina
    a tempo (stile virale Shorts/TikTok). Le parole già pronunciate sono gialle,
    quelle in arrivo bianche. Timing parola stimato per lunghezza nella frase."""
    fontsize = 58
    # Primary (parola attiva/passata) = giallo; Secondary (non ancora) = bianco
    style = (
        "Style: Def,Arial Black,%d,&H0000F0FF,&H00FFFFFF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,5,2,2,70,70,320,1" % fontsize
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
        txt = ass_escape(t.get("w", "")).strip()
        if not txt:
            continue
        words = txt.split()
        total_cs = max(1, int(round((end - start) / 10.0)))  # durata frase in centisec
        weights = [max(1, len(w)) for w in words]
        wsum = sum(weights)
        # distribuisce i centisecondi tra le parole, in proporzione alla lunghezza
        durs, acc = [], 0
        for j, w in enumerate(words):
            if j == len(words) - 1:
                durs.append(max(1, total_cs - acc))
            else:
                d = max(1, int(round(total_cs * weights[j] / wsum)))
                durs.append(d); acc += d
        # tag karaoke: {\kf<cs>}parola
        body = "".join("{\\kf%d}%s " % (durs[j], words[j]) for j in range(len(words))).strip()
        # piccolo pop iniziale (scala) per dare dinamismo
        body = "{\\fad(120,80)}" + body
        lines.append("Dialogue: 0,%s,%s,Def,,0,0,0,,%s\n" %
                     (ms_to_ass(start), ms_to_ass(end), body))
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

    # 4. crea un segmento normalizzato per gruppo (con zoom lento + grana)
    vf = CLIP_VF
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
        # musica in LOOP così non finisce prima del video
        inputs = ["-i", "base.mp4", "-i", "voice.mp3", "-stream_loop", "-1", "-i", "music.wav"]
        fc = (vfilter +
              f";[1:a]volume={voice_vol}[a1];[2:a]volume={music_vol}[a2];"
              "[a1][a2]amix=inputs=2:duration=first:dropout_transition=0[a]")
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


# ----------------------------- RENDER INTERATTIVO -----------------------------

def _seg_voice(text, voice, work, idx, rate=None, pitch=None):
    audio, words = genera_mp3(text, voice, rate=rate, pitch=pitch)
    p = os.path.join(work, f"v{idx}.mp3")
    with open(p, "wb") as f:
        f.write(audio)
    dur = (words[-1]["t"] + words[-1]["d"]) if words else 1500
    return p, dur, words


def _silence_mp3(ffmpeg, dur_ms, out):
    run_ff(ffmpeg, ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                    "-t", f"{dur_ms/1000.0:.3f}", "-c:a", "libmp3lame", "-q:a", "9", out])


def _ass_header_i(styles):
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
            "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
            "MarginL,MarginR,MarginV,Encoding\n%s\n\n"
            "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
            % (W, H, styles))


def build_interactive_ass(timeline, path):
    # N = narrazione karaoke (basso), I = overlay posizionati (scelte/countdown/reveal)
    styleN = "Style: N,Arial Black,54,&H0000F0FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,310,1"
    styleI = "Style: I,Arial Black,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,3,5,0,0,0,1"
    ev = [_ass_header_i(styleN + "\n" + styleI)]
    for item in timeline:
        if item["kind"] == "narr":
            words = item["words"]; base = item["start"]; seg_end = item["start"] + item["dur"]
            for i, t in enumerate(words):
                st = base + t["t"]
                en = base + (words[i+1]["t"] if i + 1 < len(words) else item["dur"])
                txt = ass_escape(t.get("w", "")).strip()
                if not txt:
                    continue
                ws = txt.split()
                total_cs = max(1, int(round((en - st) / 10.0)))
                weights = [max(1, len(w)) for w in ws]; wsum = sum(weights)
                durs, acc = [], 0
                for j, w in enumerate(ws):
                    if j == len(ws) - 1:
                        durs.append(max(1, total_cs - acc))
                    else:
                        d = max(1, int(round(total_cs * weights[j] / wsum))); durs.append(d); acc += d
                body = "".join("{\\kf%d}%s " % (durs[j], ws[j]) for j in range(len(ws))).strip()
                ev.append("Dialogue: 0,%s,%s,N,,0,0,0,,%s\n" % (ms_to_ass(st), ms_to_ass(en), body))
        else:  # choice
            r = item["round"]; cs = item["start"]; ce = cs + item["dur"]
            a = ass_escape(r.get("a", "")); b = ass_escape(r.get("b", ""))
            giusta = (r.get("giusta") or "a").lower().strip()
            # etichetta COSA FAI? in rosso sangue + scelte
            ev.append("Dialogue: 1,%s,%s,I,,0,0,0,,{\\pos(540,290)\\fs78\\c&H2020FF&\\bord7\\shad3}COSA FAI?\n"
                      % (ms_to_ass(cs), ms_to_ass(ce)))
            ev.append("Dialogue: 1,%s,%s,I,,0,0,0,,{\\pos(540,840)\\fs60\\bord8\\3c&H0010A0&\\c&HFFFFFF&}A)  %s\n"
                      % (ms_to_ass(cs), ms_to_ass(ce), a))
            ev.append("Dialogue: 1,%s,%s,I,,0,0,0,,{\\pos(540,1100)\\fs60\\bord8\\3c&H0010A0&\\c&HFFFFFF&}B)  %s\n"
                      % (ms_to_ass(cs), ms_to_ass(ce), b))
            n = max(1, item["dur"] // 1000)
            for k in range(n):
                ns = cs + k * 1000; ne = ns + 1000; num = n - k
                ev.append("Dialogue: 2,%s,%s,I,,0,0,0,,{\\pos(540,560)\\fs170\\c&H2020FF&\\bord12\\shad4}%d\n"
                          % (ms_to_ass(ns), ms_to_ass(ne), num))
            # reveal: scelta giusta verde, sbagliata rossa (1.8s, sopra l'inizio dell'esito)
            re_end = ce + 1800
            green, red = "&H00FF00&", "&H0000FF&"
            ca, cb = (green, red) if giusta == "a" else (red, green)
            ma, mb = ("✓", "✗") if giusta == "a" else ("✗", "✓")
            ev.append("Dialogue: 3,%s,%s,I,,0,0,0,,{\\pos(540,840)\\fs60\\bord8\\c%s}%s A)  %s\n"
                      % (ms_to_ass(ce), ms_to_ass(re_end), ca, ma, a))
            ev.append("Dialogue: 3,%s,%s,I,,0,0,0,,{\\pos(540,1100)\\fs60\\bord8\\c%s}%s B)  %s\n"
                      % (ms_to_ass(ce), ms_to_ass(re_end), cb, mb, b))
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(ev))


def build_base_even(ffmpeg, clip_paths, total_ms, work):
    """Crea base.mp4 di durata total_ms distribuendo le clip equamente."""
    n = len(clip_paths)
    seg_dur = (total_ms / 1000.0) / n
    vf = CLIP_VF
    seg_files = []
    for i, cp in enumerate(clip_paths):
        out = os.path.join(work, f"bseg{i}.mp4")
        run_ff(ffmpeg, ["-stream_loop", "-1", "-i", cp, "-t", f"{seg_dur:.3f}",
                        "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", out])
        seg_files.append(out)
    listf = os.path.join(work, "blist.txt")
    with open(listf, "w", encoding="utf-8") as f:
        for sf in seg_files:
            f.write(f"file '{os.path.basename(sf)}'\n")
    run_ff(ffmpeg, ["-f", "concat", "-safe", "0", "-i", "blist.txt",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "base.mp4"],
           cwd=work)


def render_interactive_job(data, work):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg non trovato")
    voice = data.get("voice") or DEFAULT_VOICE
    clips = data.get("clips") or []
    intro = (data.get("intro") or "").strip()
    rounds = data.get("rounds") or []
    finale = (data.get("finale") or "").strip()
    music_vol = float(data.get("music_vol", 1.0))
    voice_vol = float(data.get("voice_vol", 1.8))
    choice_ms = int(data.get("choice_ms", 5000))
    if not rounds:
        raise RuntimeError("nessun round")

    # clip
    clip_paths = []
    for idx, url in enumerate(clips):
        cp = os.path.join(work, f"c{idx}.mp4")
        try:
            download_file(url, cp)
            if os.path.getsize(cp) > 0:
                clip_paths.append(cp)
        except Exception as e:
            print(f"     clip {idx} ko: {e}")
    if not clip_paths:
        raise RuntimeError("nessuna clip scaricabile")

    # voce + musica salvate
    music_path = None
    if data.get("music_wav_b64"):
        music_path = os.path.join(work, "music.wav")
        with open(music_path, "wb") as f:
            f.write(base64.b64decode(data["music_wav_b64"]))

    # SFX: ticchettio clessidra (durante la scelta) + stinger spaventoso (sulla morte)
    tick_path = os.path.join(work, "tick.mp3")
    run_ff(ffmpeg, ["-f", "lavfi", "-i",
                    f"aevalsrc=0.28*sin(2*PI*1900*t)*exp(-28*(t-floor(t))):d={choice_ms/1000.0:.3f}:s=24000",
                    "-c:a", "libmp3lame", "-q:a", "9", tick_path])
    sting_ms = 1300
    sting_path = os.path.join(work, "sting.mp3")
    run_ff(ffmpeg, ["-f", "lavfi", "-i",
                    "aevalsrc='0.8*(random(0)*2-1)*exp(-6*t)+0.6*sin(2*PI*55*t)*exp(-2.2*t)':d=1.3:s=24000",
                    "-c:a", "libmp3lame", "-q:a", "9", sting_path])

    # timeline + segmenti audio
    timeline, audio_files = [], []
    t = 0

    _r, _p = PROSODY["interattivo"]

    def add_narr(text):
        nonlocal t
        text = (text or "").strip()
        if not text:
            return
        p, dur, words = _seg_voice(text, voice, work, len(audio_files), rate=_r, pitch=_p)
        audio_files.append(p)
        timeline.append({"kind": "narr", "start": t, "dur": dur, "words": words})
        t += dur

    def add_audio(path, dur_ms):
        nonlocal t
        audio_files.append(path)
        t += dur_ms

    add_narr(intro)
    for r in rounds:
        add_narr(r.get("situazione", ""))
        # finestra scelta col ticchettio
        audio_files.append(tick_path)
        timeline.append({"kind": "choice", "start": t, "dur": choice_ms, "round": r})
        t += choice_ms
        # stinger di rivelazione (la morte) — il reveal visivo è allineato qui
        add_audio(sting_path, sting_ms)
        add_narr(r.get("esito", ""))
    add_narr(finale)
    total_ms = t + 600

    # concat audio (mp3 mono 24k) -> aac
    alist = os.path.join(work, "alist.txt")
    with open(alist, "w", encoding="utf-8") as f:
        for af in audio_files:
            f.write(f"file '{os.path.basename(af)}'\n")
    run_ff(ffmpeg, ["-f", "concat", "-safe", "0", "-i", "alist.txt",
                    "-c:a", "aac", "-b:a", "192k", "voice.m4a"], cwd=work)

    # base video + sottotitoli interattivi
    build_base_even(ffmpeg, clip_paths, total_ms, work)
    build_interactive_ass(timeline, os.path.join(work, "subs.ass"))

    total_sec = total_ms / 1000.0
    out = os.path.join(work, "final.mp4")
    # grading horror: desaturato, contrasto, scuro, tinta rossa, vignette forte
    vfilter = ("[0:v]subtitles=subs.ass,eq=brightness=-0.07:contrast=1.28:saturation=0.55,"
               "colorbalance=rs=0.10:rm=0.06:rh=0.04,vignette=PI/3[v]")
    if music_path:
        # musica in LOOP per coprire tutta la durata
        inputs = ["-i", "base.mp4", "-i", "voice.m4a", "-stream_loop", "-1", "-i", "music.wav"]
        fc = (vfilter + f";[1:a]volume={voice_vol}[a1];[2:a]volume={music_vol}[a2];"
              "[a1][a2]amix=inputs=2:duration=first:dropout_transition=0[a]")
    else:
        inputs = ["-i", "base.mp4", "-i", "voice.m4a"]
        fc = vfilter + f";[1:a]volume={voice_vol}[a]"
    run_ff(ffmpeg, inputs + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                             "-t", f"{total_sec:.3f}", "-c:v", "libx264", "-preset", "medium",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "final.mp4"],
           cwd=work)
    try:
        shutil.copyfile(out, LAST_RENDER)
    except Exception:
        pass
    with open(out, "rb") as f:
        return f.read()


# ----------------------------- RENDER CLASSIFICA (Top N) -----------------------------

def _karaoke_dialogue(ev, base, seg_dur, words):
    """Aggiunge righe Dialogue karaoke (stile N) per un segmento di narrazione."""
    for i, t in enumerate(words):
        st = base + t["t"]
        en = base + (words[i + 1]["t"] if i + 1 < len(words) else seg_dur)
        txt = ass_escape(t.get("w", "")).strip()
        if not txt:
            continue
        ws = txt.split()
        total_cs = max(1, int(round((en - st) / 10.0)))
        weights = [max(1, len(w)) for w in ws]; wsum = sum(weights)
        durs, acc = [], 0
        for j, w in enumerate(ws):
            if j == len(ws) - 1:
                durs.append(max(1, total_cs - acc))
            else:
                d = max(1, int(round(total_cs * weights[j] / wsum))); durs.append(d); acc += d
        body = "".join("{\\kf%d}%s " % (durs[j], ws[j]) for j in range(len(ws))).strip()
        ev.append("Dialogue: 0,%s,%s,N,,0,0,0,,%s\n" % (ms_to_ass(st), ms_to_ass(en), body))


def build_ranking_ass(timeline, path):
    styleN = "Style: N,Arial Black,54,&H0000F0FF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,300,1"
    styleB = "Style: B,Arial Black,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,3,5,0,0,0,1"
    ev = [_ass_header_i(styleN + "\n" + styleB)]
    for item in timeline:
        base = item["start"]; dur = item["dur"]
        _karaoke_dialogue(ev, base, dur, item.get("words", []))
        rank = item.get("rank")
        if rank is not None:
            st = ms_to_ass(base); en = ms_to_ass(base + dur)
            # numero gigante in alto (rosso sangue) con pop iniziale
            ev.append("Dialogue: 2,%s,%s,B,,0,0,0,,{\\pos(540,430)\\fs300\\c&H2020FF&\\bord14\\shad6\\fad(180,0)}#%s\n"
                      % (st, en, rank))
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(ev))


def wiki_image(subject, lang):
    """Cerca su Wikipedia la foto del soggetto (lato server, rete affidabile)."""
    if not subject:
        return None
    import urllib.parse
    wl = "en" if lang == "inglese" else "es" if lang == "spagnolo" else "it"
    def try_host(host):
        u = (f"https://{host}/w/api.php?action=query&generator=search&"
             f"gsrsearch={urllib.parse.quote(subject)}&gsrlimit=1&prop=pageimages&"
             f"piprop=thumbnail&pithumbsize=1280&format=json&origin=*")
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 ViralOS"})
            j = json.load(urllib.request.urlopen(req, timeout=15))
            pages = j.get("query", {}).get("pages", {})
            for k in pages:
                th = pages[k].get("thumbnail")
                if th and th.get("source"):
                    return th["source"]
        except Exception:
            return None
        return None
    return try_host(wl + ".wikipedia.org") or try_host("en.wikipedia.org")


def render_ranking_job(data, work):
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg non trovato")
    voice = data.get("voice") or DEFAULT_VOICE
    intro = (data.get("intro") or "").strip()
    items = data.get("items") or []
    outro = (data.get("outro") or "").strip()
    lang = data.get("lang") or "italiano"
    # clip di fallback (una per elemento, stesso ordine) — usate se manca la foto Wikipedia
    fallback = data.get("fallback") or data.get("clips") or []
    music_vol = float(data.get("music_vol", 1.0))
    voice_vol = float(data.get("voice_vol", 1.8))
    if not items:
        raise RuntimeError("nessun elemento in classifica")

    # una sorgente per elemento: PRIMA la foto Wikipedia del soggetto, poi la clip di fallback
    sources = []  # (path, kind)
    for i, it in enumerate(items):
        got = False
        img = wiki_image(it.get("titolo"), lang)
        if img:
            cp = os.path.join(work, f"m{i}.jpg")
            try:
                download_file(img, cp)
                if os.path.getsize(cp) > 0:
                    sources.append((cp, "image")); got = True
                    print(f"     foto Wikipedia: {it.get('titolo')}")
            except Exception as e:
                print(f"     wiki img ko ({it.get('titolo')}): {e}")
        if not got:
            url = fallback[i] if i < len(fallback) else (fallback[-1] if fallback else None)
            if url:
                cp = os.path.join(work, f"m{i}.mp4")
                try:
                    download_file(url, cp)
                    if os.path.getsize(cp) > 0:
                        sources.append((cp, "video")); got = True
                except Exception as e:
                    print(f"     clip fallback ko: {e}")
        if not got and sources:
            sources.append(sources[-1])  # non lasciare buchi
    if not sources:
        raise RuntimeError("nessun media disponibile")
    clip_paths = sources  # lista di (path, kind)

    music_path = None
    if data.get("music_wav_b64"):
        music_path = os.path.join(work, "music.wav")
        with open(music_path, "wb") as f:
            f.write(base64.b64decode(data["music_wav_b64"]))

    timeline, audio_files = [], []
    t = 0
    nc = len(clip_paths)
    _r, _p = PROSODY["classifica"]

    def add_narr(text, rank=None, clip_idx=0):
        nonlocal t
        text = (text or "").strip()
        if not text:
            return
        p, dur, words = _seg_voice(text, voice, work, len(audio_files), rate=_r, pitch=_p)
        audio_files.append(p)
        timeline.append({"kind": "narr", "start": t, "dur": dur, "words": words,
                         "rank": rank, "clip_idx": min(clip_idx, nc - 1)})
        t += dur

    add_narr(intro, clip_idx=0)
    for pos, it in enumerate(items):
        rank = it.get("rank")
        text = f"Numero {rank}. {it.get('titolo','')}. {it.get('descrizione','')}"
        add_narr(text, rank=rank, clip_idx=pos)
    add_narr(outro, clip_idx=nc - 1)
    total_ms = t + 600

    alist = os.path.join(work, "alist.txt")
    with open(alist, "w", encoding="utf-8") as f:
        for af in audio_files:
            f.write(f"file '{os.path.basename(af)}'\n")
    run_ff(ffmpeg, ["-f", "concat", "-safe", "0", "-i", "alist.txt",
                    "-c:a", "aac", "-b:a", "192k", "voice.m4a"], cwd=work)

    # base video: una foto/clip a tema per ogni segmento (allineata all'elemento)
    seg_files = []
    for si, item in enumerate(timeline):
        cp, kind = clip_paths[item.get("clip_idx", 0)]
        dur = max(0.4, item["dur"] / 1000.0)
        outc = os.path.join(work, f"rseg{si}.mp4")
        loop_args = ["-loop", "1"] if kind == "image" else ["-stream_loop", "-1"]
        run_ff(ffmpeg, loop_args + ["-i", cp, "-t", f"{dur:.3f}",
                        "-an", "-vf", CLIP_VF, "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", outc])
        seg_files.append(outc)
    with open(os.path.join(work, "rlist.txt"), "w", encoding="utf-8") as f:
        for sf in seg_files:
            f.write(f"file '{os.path.basename(sf)}'\n")
    run_ff(ffmpeg, ["-f", "concat", "-safe", "0", "-i", "rlist.txt",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "base.mp4"],
           cwd=work)
    build_ranking_ass(timeline, os.path.join(work, "subs.ass"))

    total_sec = total_ms / 1000.0
    out = os.path.join(work, "final.mp4")
    vfilter = ("[0:v]subtitles=subs.ass,eq=brightness=-0.07:contrast=1.28:saturation=0.55,"
               "colorbalance=rs=0.10:rm=0.06:rh=0.04,vignette=PI/3[v]")
    if music_path:
        inputs = ["-i", "base.mp4", "-i", "voice.m4a", "-stream_loop", "-1", "-i", "music.wav"]
        fc = (vfilter + f";[1:a]volume={voice_vol}[a1];[2:a]volume={music_vol}[a2];"
              "[a1][a2]amix=inputs=2:duration=first:dropout_transition=0[a]")
    else:
        inputs = ["-i", "base.mp4", "-i", "voice.m4a"]
        fc = vfilter + f";[1:a]volume={voice_vol}[a]"
    run_ff(ffmpeg, inputs + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                             "-t", f"{total_sec:.3f}", "-c:v", "libx264", "-preset", "medium",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "final.mp4"],
           cwd=work)
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


def _wrap_title(title, per_line=18):
    """Spezza il titolo in righe da ~per_line caratteri (per la miniatura)."""
    words = title.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= per_line:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def yt_make_thumbnail(title):
    """Miniatura d'impatto: frame dinamico + banda scura + titolo grande su più righe."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not os.path.exists(LAST_RENDER):
        return None
    thumb = os.path.join(OUTPUT_DIR, "thumb.jpg")
    safe = title.replace("\\", " ").replace("'", "").replace(":", " ").replace("%", " ")
    lines = _wrap_title(safe)
    # banda scura in basso per leggibilità + righe di testo grandi gialle/bianche
    filters = ["scale=720:1280",
               "drawbox=x=0:y=ih*0.60:w=iw:h=ih*0.40:color=black@0.55:t=fill"]
    n = len(lines)
    for i, ln in enumerate(lines):
        y = f"h*0.66+{i}*86"
        col = "white" if i else "#FFD400"  # prima riga gialla (accento)
        filters.append(
            f"drawtext=text='{ln}':fontfile='C\\:/Windows/Fonts/ariblk.ttf':"
            f"fontcolor={col}:fontsize=64:borderw=6:bordercolor=black:"
            f"x=(w-text_w)/2:y={y}")
    vf = ",".join(filters)
    try:
        # frame al ~20% del video (più in azione del primissimo)
        run_ff(ffmpeg, ["-ss", "4", "-i", LAST_RENDER, "-frames:v", "1", "-vf", vf, thumb])
        return thumb if os.path.exists(thumb) else None
    except Exception as e:
        print(f"     thumbnail saltata: {e}")
        return None


def registry_load():
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def registry_add(record):
    data = registry_load()
    data.append(record)
    try:
        with open(REGISTRY, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"     registro non salvato: {e}")


def yt_upload(data):
    from googleapiclient.http import MediaFileUpload
    import time as _time
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

    # registra le scelte creative per la futura ottimizzazione
    meta = data.get("meta") or {}
    registry_add({
        "id": vid, "title": title, "ts": _time.strftime("%Y-%m-%d %H:%M"),
        "publishAt": publish_at, "privacy": status.get("privacyStatus"),
        "nicchia": meta.get("nicchia"), "voice": meta.get("voice"),
        "music": meta.get("music"), "duration": meta.get("duration"),
        "hook": meta.get("hook"), "lang": meta.get("lang"),
    })
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


def yt_optimize():
    """Incrocia il registro creativo con la retention reale e trova i pattern vincenti."""
    reg = registry_load()
    an = yt_analytics()
    perf = {v["id"]: v for v in an.get("videos", [])}
    rows = []
    for r in reg:
        p = perf.get(r.get("id"))
        if p and p.get("retention") is not None:
            rows.append({**r, "retention": float(p["retention"]), "views": p.get("views", 0)})

    MIN = 5  # video con dati minimi per insight affidabili
    if len(rows) < 1:
        return {"count": 0, "enough": False, "dims": {}, "best": {},
                "insight": "", "analytics_ok": an.get("analytics_ok", True)}

    def agg(dim):
        groups = {}
        for x in rows:
            key = x.get(dim) or "?"
            groups.setdefault(str(key), []).append(x["retention"])
        out = [{"value": k, "avg": round(sum(v)/len(v), 1), "n": len(v)} for k, v in groups.items()]
        out.sort(key=lambda o: -o["avg"])
        return out

    dims = {d: agg(d) for d in ["nicchia", "format", "voice", "music", "duration"]}
    best = {d: (dims[d][0]["value"] if dims[d] else None) for d in dims}
    # top hook per retention (esempi che funzionano)
    top = sorted(rows, key=lambda x: -x["retention"])[:3]
    top_hooks = [x.get("hook") for x in top if x.get("hook")]
    avg_ret = round(sum(x["retention"] for x in rows)/len(rows), 1)

    # stringa insight da iniettare nel prompt di generazione
    parts = []
    if best.get("nicchia"): parts.append(f"sottogenere '{best['nicchia']}'")
    if best.get("format"): parts.append(f"format '{best['format']}'")
    if best.get("duration"): parts.append(f"durata {best['duration']}s")
    insight = ""
    if len(rows) >= MIN and parts:
        insight = ("Dai dati REALI del canale, i contenuti che trattengono di più hanno: "
                   + ", ".join(parts) + ". Privilegia questo stile.")
        if top_hooks:
            insight += " Esempi di hook che hanno funzionato: " + " / ".join(top_hooks[:2])

    return {"count": len(rows), "enough": len(rows) >= MIN, "dims": dims,
            "best": best, "insight": insight, "avgRet": avg_ret,
            "analytics_ok": an.get("analytics_ok", True)}


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

        if parsed.path == "/youtube_optimize":
            try:
                print("  -> ottimizzazione (registro x retention)...")
                res = yt_optimize()
                print(f"     {res['count']} video con dati, enough={res['enough']}")
                self._send(200, "application/json", json.dumps(res).encode("utf-8"))
            except Exception as e:
                print(f"     ERRORE optimize: {e}")
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

        if parsed.path == "/render_interactive":
            try:
                data = json.loads(raw.decode("utf-8"))
                print(f"  -> RENDER INTERATTIVO: {len(data.get('rounds', []))} scelte, "
                      f"{len(data.get('clips', []))} clip")
                work = tempfile.mkdtemp(prefix="viralosi_")
                try:
                    mp4 = render_interactive_job(data, work)
                    print(f"     OK video {len(mp4)//1024} KB")
                    self._send(200, "video/mp4", mp4)
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            except Exception as e:
                print(f"     ERRORE interattivo: {e}")
                self._send(500, "text/plain", str(e).encode("utf-8"))
            return

        if parsed.path == "/render_ranking":
            try:
                data = json.loads(raw.decode("utf-8"))
                print(f"  -> RENDER CLASSIFICA: {len(data.get('items', []))} elementi, "
                      f"{len(data.get('clips', []))} clip")
                work = tempfile.mkdtemp(prefix="viralosr_")
                try:
                    mp4 = render_ranking_job(data, work)
                    print(f"     OK video {len(mp4)//1024} KB")
                    self._send(200, "video/mp4", mp4)
                finally:
                    shutil.rmtree(work, ignore_errors=True)
            except Exception as e:
                print(f"     ERRORE classifica: {e}")
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
