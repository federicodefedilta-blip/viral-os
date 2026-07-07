#!/usr/bin/env python3
"""
Viral OS Voice Automation - edge-tts edition
Genera voce neurale italiana (Microsoft Edge TTS) e la mixa con video + musica.

Uso:
  py audio_mixer.py <input.webm> "<script>" <output.mp4> [voce]

Esempio:
  py audio_mixer.py video.webm "Quella notte sentii dei passi..." finale.mp4
  py audio_mixer.py video.webm "..." finale.mp4 it-IT-IsabellaNeural

Voci italiane disponibili:
  it-IT-DiegoNeural      (maschile, default - ottimo per horror)
  it-IT-IsabellaNeural   (femminile)
  it-IT-ElsaNeural       (femminile)
  it-IT-GiuseppeMultilingualNeural (maschile)
"""

import sys
import os
import subprocess
import asyncio
import glob

# pip_system_certs si auto-attiva: fa usare a Python i certificati di Windows
# (necessario su reti aziendali con proxy SSL)
import edge_tts

DEFAULT_VOICE = "it-IT-DiegoNeural"


def find_ffmpeg():
    """Trova ffmpeg: prima nel PATH, poi nella cartella winget."""
    from shutil import which
    p = which("ffmpeg")
    if p:
        return p
    # Cerca nell'installazione winget
    pattern = os.path.join(
        os.environ.get("LOCALAPPDATA", ""),
        "Microsoft", "WinGet", "Packages",
        "Gyan.FFmpeg*", "**", "ffmpeg.exe"
    )
    matches = glob.glob(pattern, recursive=True)
    if matches:
        return matches[0]
    return None


async def genera_voce(testo, voce, out_mp3):
    """Genera la voce con edge-tts."""
    communicate = edge_tts.Communicate(testo, voce)
    await communicate.save(out_mp3)


def run_ffmpeg(ffmpeg, args, desc):
    """Esegue un comando ffmpeg, gestendo gli errori."""
    try:
        subprocess.run(
            [ffmpeg, "-y"] + args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ERRORE {desc}:")
        print("   " + e.stderr.decode("utf-8", errors="ignore")[-500:])
        return False


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    input_file = sys.argv[1]
    script_text = sys.argv[2].strip()
    output_file = sys.argv[3]
    voce = sys.argv[4] if len(sys.argv) > 4 else DEFAULT_VOICE

    if not os.path.exists(input_file):
        print(f"File input non trovato: {input_file}")
        sys.exit(1)
    if not script_text:
        print("Il testo dello script e' vuoto.")
        sys.exit(1)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg non trovato. Installa con: winget install Gyan.FFmpeg")
        sys.exit(1)

    print("=" * 55)
    print("  Viral OS Voice Generator (edge-tts)")
    print("=" * 55)
    print(f"  Input:  {input_file}")
    print(f"  Voce:   {voce}")
    print(f"  Testo:  {script_text[:55]}...")
    print()

    voice_mp3 = "_temp_voce.mp3"
    mixed_audio = "_temp_mix.m4a"

    # 1. Genera voce
    print("1/3  Genero la voce neurale...")
    try:
        asyncio.run(genera_voce(script_text, voce, voice_mp3))
        if not os.path.exists(voice_mp3) or os.path.getsize(voice_mp3) == 0:
            raise RuntimeError("file voce vuoto")
        print(f"     OK ({os.path.getsize(voice_mp3)//1024} KB)")
    except Exception as e:
        print(f"     ERRORE generazione voce: {e}")
        sys.exit(1)

    # 2. Mixa voce (100%) + audio originale del video / musica (25%)
    #    Se il video non ha audio, usa solo la voce.
    print("2/3  Mixo voce + musica di sottofondo...")
    has_audio = _video_has_audio(ffmpeg, input_file)
    if has_audio:
        ok = run_ffmpeg(ffmpeg, [
            "-i", voice_mp3,
            "-i", input_file,
            "-filter_complex",
            "[0:a]volume=1.6[v];[1:a]volume=0.25[m];[v][m]amix=inputs=2:duration=longest:dropout_transition=0[a]",
            "-map", "[a]", "-c:a", "aac", "-b:a", "192k",
            mixed_audio,
        ], "mixaggio")
    else:
        ok = run_ffmpeg(ffmpeg, [
            "-i", voice_mp3,
            "-filter:a", "volume=1.6",
            "-c:a", "aac", "-b:a", "192k",
            mixed_audio,
        ], "conversione voce")
    if not ok:
        _cleanup([voice_mp3, mixed_audio])
        sys.exit(1)
    print("     OK")

    # 3. Combina video + nuovo audio
    print("3/3  Combino video + audio...")
    ok = run_ffmpeg(ffmpeg, [
        "-i", input_file,
        "-i", mixed_audio,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_file,
    ], "combinazione")
    if not ok:
        _cleanup([voice_mp3, mixed_audio])
        sys.exit(1)

    _cleanup([voice_mp3, mixed_audio])

    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print()
    print("=" * 55)
    print(f"  COMPLETATO!  ->  {output_file}  ({size_mb:.1f} MB)")
    print("  Pronto per YouTube / TikTok")
    print("=" * 55)


def _video_has_audio(ffmpeg, path):
    """Verifica se il video ha una traccia audio."""
    ffprobe = ffmpeg.replace("ffmpeg.exe", "ffprobe.exe").replace("ffmpeg", "ffprobe")
    try:
        out = subprocess.run(
            [ffprobe, "-i", path, "-show_streams", "-select_streams", "a",
             "-loglevel", "error"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        return b"codec_type=audio" in out.stdout
    except Exception:
        return False


def _cleanup(files):
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass


if __name__ == "__main__":
    main()
