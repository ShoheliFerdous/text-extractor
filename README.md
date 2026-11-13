# Text Extractor

You upload a PDF or an image, AI reads it, and you get the text back as a `.txt` file.

---

## Run it in VS Code (Windows), step by step

### One-time installs
1. **Python 3.11+**: https://www.python.org/downloads/. In the installer, **tick "Add python.exe to PATH"**.
2. **Tesseract OCR** (reads scanned pages when no AI key is set): https://github.com/UB-Mannheim/tesseract/wiki. Use the default install folder.
3. **VS Code** plus the **Python** extension (VS Code will suggest it when you open the folder).

### Open and run
1. Unzip `text-extractor.zip` somewhere simple, e.g. `C:\Projects\text-extractor`.
2. In VS Code: **File → Open Folder…** and pick the `text-extractor` folder (the one with `README.md` in it).
3. Open a terminal: **Terminal → New Terminal** (it opens in PowerShell). Run:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   If PowerShell says *"running scripts is disabled"*, run this once and then try again:
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
4. Start the app:
   ```powershell
   uvicorn app.main:app --reload --port 8000
   ```
   Or press **F5** and choose "Run Text Extractor".
5. Open **http://localhost:8000** in your browser.
6. Drag in a file from the `samples` folder:
   - `1-normal.pdf`: a PDF with real text
   - `2-scanned.pdf`: a scanned PDF (needs OCR)
   - `3-photo.png`: an image
7. Click **Extract text**, then **Download .txt**.

To stop the app, press `Ctrl+C` in the terminal.

### Turn on the AI (optional)
Without a key it uses free offline OCR. To use the AI vision model, get a key from https://console.groq.com/keys, then start the app like this:
```powershell
$env:GROQ_API_KEY = "gsk_your_key_here"
uvicorn app.main:app --reload --port 8000
```
The top-right badge on the page shows which engine is active.

---

## How it works

| Input | What happens |
|---|---|
| PDF with real text (exported from Word, emailed policy docs) | Text is read directly from the PDF. It's instant, exact and free. |
| Scanned PDF / photo pages | Each page is turned into an image and read by OCR. |
| Mixed PDF | The program decides **page by page**, so only scanned pages go to OCR. |
| Image (PNG, JPG, WEBP, TIFF, BMP, GIF) | OCR. Phone-photo rotation is fixed automatically, and multi-page TIFFs are split into pages. |

There are two OCR engines:
- **AI vision** (used when `GROQ_API_KEY` is set): Groq's `meta-llama/llama-4-scout-17b-16e-instruct`, or any OpenAI-compatible vision model. It does best on poor scans, tables and handwriting.
- **Offline OCR** (Tesseract): free, needs no key and nothing leaves the machine.

If the AI fails on a page, that page is read with offline OCR instead and a warning is shown.

Multi-page results are separated with `===== Page N =====`.

## Project files
```
text-extractor/
  app/
    extractor.py      <- the extraction logic (PDF text layer, OCR, AI)
    main.py           <- the web API (FastAPI)
    static/index.html <- the upload page
  samples/            <- demo files
  requirements.txt
  run.cmd             <- alternative: double-click to install + run
  .vscode/            <- F5 run config
```

## API
| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | Which engine is active |
| POST | `/api/extract` | JSON: text plus per-page details |
| POST | `/api/extract.txt` | The `.txt` file itself |

Interactive API docs are at **http://localhost:8000/docs**.

## Settings (environment variables)
See `.env.example`. The main ones are `GROQ_API_KEY`, `AI_MODEL`, `OCR_ENGINE` (`auto` / `ai` / `tesseract`), `MAX_UPLOAD_MB` (default 25), `MAX_PAGES` (default 100) and `TESSERACT_CMD` (only if Tesseract is installed somewhere unusual).

## Troubleshooting
| Problem | Fix |
|---|---|
| `'python' is not recognized` | Reinstall Python with "Add to PATH" ticked, then restart VS Code. |
| "Tesseract OCR is not installed" | Install Tesseract (step 2 above), or set `GROQ_API_KEY`. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Port 8000 already in use | Use `--port 8010` and open http://localhost:8010 |

## Notes
- **Privacy:** with AI on, scanned pages are sent to Groq. Set `OCR_ENGINE=tesseract` for documents that must stay on the machine.
- Password-protected PDFs are rejected with a clear message.
- AI can misread text, so check important figures against the original.
