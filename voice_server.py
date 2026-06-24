#!/usr/bin/env python3
"""
Viral OS - Server vocale locale
Genera voce neurale italiana (edge-tts) e la serve al tool nel browser.

Avvio:
  py voice_server.py

Lascia questa finestra aperta mentre usi ViralOS.
Per fermarlo: chiudi la finestra o premi Ctrl+C.

Endpoint:
  GET  http://127.0.0.1:5555/tts?text=...&voice=it-IT-DiegoNeural  -> MP3
  GET  http://127.0.0.1:5555/ping                                  -> "ok"
"""

import asyncio
import sys
import json
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# pip_system_certs si auto-attiva (certificati Windows per rete aziendale)
import edge_tts

PORT = 5555
DEFAULT_VOICE = "it-IT-DiegoNeural"


def genera_mp3(testo, voce):
    """Genera l'MP3 della voce e restituisce (bytes_audio, lista_parole)."""
    async def _run():
        chunks = bytearray()
        words = []
        communicate = edge_tts.Communicate(testo, voce)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.extend(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                # offset e duration sono in unità da 100 nanosecondi
                words.append({
                    "t": chunk["offset"] / 10000.0,   # ms di inizio
                    "d": chunk["duration"] / 10000.0,  # ms di durata
                    "w": chunk.get("text", ""),
                })
        return bytes(chunks), words
    return asyncio.run(_run())


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/ping":
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if parsed.path in ("/tts", "/tts_json"):
            testo = (qs.get("text", [""])[0]).strip()
            voce = qs.get("voice", [DEFAULT_VOICE])[0] or DEFAULT_VOICE
            if not testo:
                self.send_response(400)
                self._cors()
                self.end_headers()
                self.wfile.write(b"manca il parametro text")
                return
            try:
                print(f"  -> genero voce ({voce}): {testo[:50]}...")
                audio, words = genera_mp3(testo, voce)
                print(f"     OK {len(audio)//1024} KB, {len(words)} parole")
                if parsed.path == "/tts_json":
                    payload = json.dumps({
                        "audio": base64.b64encode(audio).decode("ascii"),
                        "words": words,
                    }).encode("utf-8")
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(200)
                    self._cors()
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Content-Length", str(len(audio)))
                    self.end_headers()
                    self.wfile.write(audio)
            except Exception as e:
                print(f"     ERRORE: {e}")
                self.send_response(500)
                self._cors()
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return

        self.send_response(404)
        self._cors()
        self.end_headers()

    def log_message(self, *args):
        pass  # silenzia il log HTTP di default


def main():
    print("=" * 55)
    print("  Viral OS - Server vocale locale")
    print("=" * 55)
    print(f"  In ascolto su http://127.0.0.1:{PORT}")
    print("  Lascia questa finestra aperta mentre usi il tool.")
    print("  Per fermare: chiudi la finestra o premi Ctrl+C")
    print("=" * 55)
    print()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer fermato.")
        sys.exit(0)
    except OSError as e:
        print(f"\nErrore avvio server: {e}")
        print(f"La porta {PORT} potrebbe essere gia' in uso.")
        sys.exit(1)


if __name__ == "__main__":
    main()
