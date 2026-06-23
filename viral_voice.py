#!/usr/bin/env python3
"""
Viral OS Voice Automation
Genera voce con gTTS, mixerebbe con video/musica usando ffmpeg
Usage: python viral_voice.py input.webm "il tuo script" output.webm
"""

import sys
import subprocess
import os
from pathlib import Path
from gtts import gTTS

def main():
    if len(sys.argv) < 4:
        print("Usage: python viral_voice.py <input.webm> <script_text> <output.webm>")
        print("\nExample:")
        print('  python viral_voice.py video.webm "Una storia del terrore..." output.webm')
        sys.exit(1)

    input_file = sys.argv[1]
    script_text = sys.argv[2]
    output_file = sys.argv[3]

    # Verifica input
    if not os.path.exists(input_file):
        print(f"❌ File input non trovato: {input_file}")
        sys.exit(1)

    if len(script_text.strip()) == 0:
        print("❌ Il testo dello script è vuoto")
        sys.exit(1)

    print("🎙️ Viral OS Voice Generator")
    print(f"📹 Input: {input_file}")
    print(f"✍️ Testo: {script_text[:60]}...")
    print()

    # 1. Genera voce con gTTS
    print("1️⃣ Generando voce con gTTS...")
    try:
        tts = gTTS(script_text, lang='it', slow=False)
        voice_file = "temp_voice.mp3"
        tts.save(voice_file)
        print(f"   ✅ Voce generata: {voice_file}")
    except Exception as e:
        print(f"   ❌ Errore gTTS: {e}")
        sys.exit(1)

    # 2. Estrai audio dal video originale (musica di background)
    print("2️⃣ Estraendo audio dal video...")
    bg_audio_file = "temp_bg_audio.aac"
    try:
        subprocess.run([
            "ffmpeg", "-i", input_file, "-q:a", "9", "-n", bg_audio_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(f"   ✅ Audio estratto")
    except subprocess.CalledProcessError:
        print(f"   ❌ Errore estrazione audio (ffmpeg installato?)")
        cleanup([voice_file])
        sys.exit(1)

    # 3. Mixerebbe voce + musica di background
    print("3️⃣ Mixando voce + musica...")
    mixed_audio_file = "temp_mixed_audio.aac"
    try:
        # Voce al centro (1.0), musica ridotta (0.3) per non coprire la voce
        subprocess.run([
            "ffmpeg", "-i", voice_file, "-i", bg_audio_file,
            "-filter_complex", "[0]volume=1.0[v];[1]volume=0.3[m];[v][m]amix=inputs=2:duration=first:dropout_transition=0[out]",
            "-map", "[out]", "-c:a", "aac", "-q:a", "9", "-n", mixed_audio_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(f"   ✅ Audio mixato")
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Errore mixaggio: {e}")
        cleanup([voice_file, bg_audio_file])
        sys.exit(1)

    # 4. Ricombina video + audio mixato
    print("4️⃣ Ricombinando video + audio...")
    try:
        subprocess.run([
            "ffmpeg", "-i", input_file, "-i", mixed_audio_file,
            "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0",
            "-shortest", "-y", output_file
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(f"   ✅ Video finale creato: {output_file}")
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Errore ricombinazione: {e}")
        cleanup([voice_file, bg_audio_file, mixed_audio_file])
        sys.exit(1)

    # 5. Cleanup
    cleanup([voice_file, bg_audio_file, mixed_audio_file])

    # 6. Summary
    size_mb = os.path.getsize(output_file) / (1024 * 1024)
    print()
    print("=" * 50)
    print(f"✅ COMPLETATO!")
    print(f"📁 File: {output_file}")
    print(f"📊 Dimensione: {size_mb:.1f} MB")
    print("🚀 Pronto per YouTube/TikTok/Instagram!")
    print("=" * 50)

def cleanup(files):
    """Rimuove file temporanei"""
    for f in files:
        try:
            if os.path.exists(f):
                os.remove(f)
        except:
            pass

if __name__ == "__main__":
    main()
