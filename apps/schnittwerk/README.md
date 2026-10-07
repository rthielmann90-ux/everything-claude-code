# Schnittwerk

Schnittwerk macht aus langen Videos kurze Clips im Hochformat, ähnlich wie Opus Clip oder Ssemble. Es läuft als eigenständiger Webserver: Python/FastAPI im Backend, eine einzelne HTML-Seite als Frontend.

## Ablauf

1. **Import:** Link (über yt-dlp: YouTube, Vimeo, TikTok, X und viele mehr) oder Datei-Upload.
2. **Transkription:** faster-whisper läuft lokal und liefert Wort-Zeitstempel für die Untertitel.
3. **Auswahl:**
   - Mit `ANTHROPIC_API_KEY` liest Claude das Transkript und liefert Clips mit Titel, Hook, Score, Begründung und Hashtags.
   - Ohne Key, oder falls Claude scheitert, wählt Schnittwerk nach Lautstärke, Dynamik und Sprechanteil der Tonspur.
4. **Editor im Browser:**
   - Clips trimmen; Format 9:16, 1:1 oder 16:9.
   - Bild zuschneiden oder ganzes Bild auf unscharfem Hintergrund.
   - Auto-Fokus, der den Ausschnitt nach Bewegung im Bild setzt.
   - Hook-Titel und drei Untertitel-Stile.
5. **Export:** ffmpeg rendert auf dem Server ein MP4 (H.264/AAC) mit eingebrannten Untertiteln (libass).

## Starten

### Docker

```bash
docker build -t schnittwerk apps/schnittwerk
docker run -p 8000:8000 -v schnittwerk-data:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e SCHNITTWERK_TOKEN=ein-langer-zugangscode \
  schnittwerk
```

Danach `http://localhost:8000` öffnen. Beim ersten Job lädt Whisper sein Modell von Hugging Face (beim Modell `small` mehrere hundert MB) in das Volume.

### Lokal

Voraussetzung: Python 3.10+ und ffmpeg mit libass im `PATH`.

```bash
cd apps/schnittwerk
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn schnittwerk.app:app --reload
pytest
```

## Konfiguration

| Variable | Standard | Bedeutung |
|---|---|---|
| `ANTHROPIC_API_KEY` | leer | Aktiviert die Clip-Auswahl durch Claude |
| `SCHNITTWERK_CLAUDE_MODEL` | `claude-opus-5-5` | Modell für die Auswahl |
| `SCHNITTWERK_TOKEN` | leer | Zugangscode für alle API-Aufrufe. Für jeden öffentlich erreichbaren Server setzen |
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3`. Größer = genauer, aber langsamer |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE` | `cpu` / `int8` | Mit NVIDIA-GPU: `cuda` / `float16` |
| `SCHNITTWERK_DATA_DIR` | `./data` | Uploads, Downloads, Exporte, Whisper-Modelle (Docker: `/data`) |
| `SCHNITTWERK_MAX_UPLOAD_MB` | `2048` | Maximale Dateigröße für Upload und Download |
| `SCHNITTWERK_MAX_DURATION_MIN` | `180` | Maximale Videolänge |
| `SCHNITTWERK_JOB_TTL_HOURS` | `24` | Danach werden Jobs und Dateien gelöscht |

## Grenzen

- **Speicher:** Jobs liegen im Arbeitsspeicher. Ein Neustart verwirft laufende und fertige Jobs, die Dateien löscht die Aufräumroutine später.
- **Verarbeitung:** Es läuft immer nur eine Analyse gleichzeitig, weitere warten in der Schlange. Exporte laufen zu zweit parallel. Für mehrere Nutzer bräuchte es eine echte Queue (z. B. Redis + Worker).
- **Rechenzeit:** Die Transkription ist der langsamste Schritt. Auf CPU hängt sie stark von Hardware und Modellgröße ab; für lange Videos lohnt `WHISPER_MODEL=base` oder eine GPU.
- **Untertitel:** Die Vorschau zeichnet mit Figtree, der Export mit DejaVu Sans. Position, Zeiten und Farben stimmen überein, die Schrift unterscheidet sich leicht.
- **Auto-Fokus:** richtet sich nach Bewegung im Bild, es gibt keine Gesichtserkennung.
- **Plattformen:** yt-dlp hängt von deren Änderungen ab. Wenn Downloads scheitern, yt-dlp aktualisieren. Lade nur Videos, an denen du die Rechte hast; die Nutzungsbedingungen von YouTube und anderen Plattformen beschränken Downloads.
- **Link-Prüfung:** Links auf interne Adressen (localhost, private Netze) werden abgelehnt. Weiterleitungen innerhalb von yt-dlp sind damit nicht vollständig abgesichert, deshalb öffentlich nur mit `SCHNITTWERK_TOKEN` betreiben.
