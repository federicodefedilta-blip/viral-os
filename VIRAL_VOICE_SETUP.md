# 🎙️ Viral OS Voice Automation

Script Python per aggiungere automaticamente voce italiana ai video generati da ViralOS.

## Requisiti

- **Python 3.8+**
- **ffmpeg** (per mixaggio audio/video)
- Librerie Python: `gtts`, `pydub`

## Setup (una volta)

### 1. Installa Python
- Windows: Scarica da [python.org](https://www.python.org/downloads/)
- macOS: `brew install python3`
- Linux: `sudo apt install python3 python3-pip`

### 2. Installa ffmpeg

**Windows:**
```bash
# Via Chocolatey (consigliato)
choco install ffmpeg

# O scarica manualmente da ffmpeg.org
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt install ffmpeg
```

### 3. Installa librerie Python

Apri Terminal/PowerShell e esegui:

```bash
pip install gtts pydub
```

## Uso

### Flusso veloce:

1. **Genera il video in ViralOS** → Scarica il WebM
2. **Apri Terminal/PowerShell** nella stessa cartella del video
3. **Esegui:**

```bash
python viral_voice.py input.webm "Il tuo script qui" output.webm
```

### Esempio completo:

```bash
python viral_voice.py video.webm "Una notte buia, mi svegliai e sentii strani rumori nella casa. Non sapevo che quella sarebbe stata l'ultima notte della mia vita." output.webm
```

### Output:
- ✅ File: `output.webm` (video pronto con voce + musica)
- ✅ Dimensione: ~20-30 MB
- ✅ Durata: 61 secondi (o come generato in ViralOS)

## Dettagli tecnici

| Elemento | Impostazione |
|----------|-------------|
| **Voce** | gTTS (Google Translate TTS) |
| **Lingua** | Italiano (it-IT) |
| **Musica background** | Volume 30% (per non coprire la voce) |
| **Voce** | Volume 100% |
| **Formato output** | WebM VP9 + AAC |
| **Costo** | 100% GRATIS |

## Troubleshooting

### "ffmpeg not found"
- Verifica che ffmpeg sia installato: `ffmpeg -version`
- Se non è installato, rifai il setup di ffmpeg

### "ModuleNotFoundError: gtts"
- Reinstalla le librerie:
```bash
pip install --upgrade gtts pydub
```

### "Error estrazione audio"
- Verifica che il file WebM sia valido
- Prova a rigenerare il video in ViralOS

### Lo script è lento
- È normale, gTTS genera la voce in tempo reale (~1-2 minuti per uno script medio)
- ffmpeg impiega altri 1-2 minuti per mixare

## Prossimi passi

1. **Carica su YouTube/TikTok/Instagram**
2. **Personalizza i tag/hashtag** per il tuo canale
3. **Monitora le visualizzazioni**

## Supporto

Se hai problemi:
1. Verifica che ffmpeg sia installato correttamente
2. Assicurati che il WebM sia generato correttamente da ViralOS
3. Prova con uno script più breve prima

---

**Nota:** La prima volta che usi lo script, gTTS potrebbe impiegare più tempo (1-2 minuti). Le volte successive saranno più veloci (30-60 secondi).

Buona fortuna! 🚀
