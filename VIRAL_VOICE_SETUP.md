# 🎙️ Viral OS Voice Automation (edge-tts)

Aggiunge automaticamente una **voce neurale italiana naturale** ai video generati da ViralOS, mixandola con la musica di sottofondo.

Usa **edge-tts** (le voci neurali di Microsoft Edge): gratuito, illimitato, voce di qualità professionale.

---

## Setup (già fatto su questo PC ✅)

Questi comandi sono già stati eseguiti. Servono solo se reinstalli su un altro computer:

```powershell
py -m pip install edge-tts pip-system-certs
winget install Gyan.FFmpeg
```

> `pip-system-certs` serve perché sei su rete aziendale (proxy SSL): fa usare a Python i certificati di Windows.

---

## Uso quotidiano

### 1. Genera il video in ViralOS
- Completa tutti gli step fino allo step 6
- Scarica il video (file `.webm` o `.mp4`)
- Copia anche lo **script** generato (lo stesso testo della voce)

### 2. Esegui lo script

Apri PowerShell nella cartella `viral-os` e lancia:

```powershell
py viral_voice.py "percorso\al\video.webm" "il testo dello script qui" finale.mp4
```

**Esempio reale:**
```powershell
py viral_voice.py "$env:USERPROFILE\Downloads\viral-os-123.webm" "Quella notte sentii dei passi nella soffitta. Ma in casa ero solo. Mi alzai, e quello che vidi mi gela ancora il sangue." finale.mp4
```

### 3. Carica `finale.mp4` su YouTube / TikTok / Instagram 🚀

---

## Scegliere la voce

Aggiungi il nome della voce come **quarto parametro**:

```powershell
py viral_voice.py video.webm "testo" finale.mp4 it-IT-IsabellaNeural
```

| Voce | Tipo | Note |
|------|------|------|
| `it-IT-DiegoNeural` | Maschile | **default** — ottimo per horror |
| `it-IT-IsabellaNeural` | Femminile | calda, narrativa |
| `it-IT-ElsaNeural` | Femminile | chiara |
| `it-IT-GiuseppeMultilingualNeural` | Maschile | versatile |

---

## Come funziona

1. **edge-tts** genera la voce italiana dal testo (MP3)
2. **ffmpeg** mixa: voce al 160%, musica del video al 25% (così la voce è sempre chiara)
3. **ffmpeg** ricombina video + nuovo audio in un MP4 finale

Voce + musica + sottotitoli (già nel video) = video completo pronto da pubblicare.

---

## Problemi comuni

**"ffmpeg non trovato"** → riavvia PowerShell (winget ha aggiunto ffmpeg al PATH), oppure lo script lo cerca automaticamente nella cartella winget.

**Errore SSL / certificato** → verifica che `pip-system-certs` sia installato: `py -m pip show pip-system-certs`

**La voce copre troppo la musica (o viceversa)** → modifica i valori `volume=1.6` (voce) e `volume=0.25` (musica) in `viral_voice.py`, riga ~120.

---

100% gratis, illimitato, nessuna carta di credito. 🎬
