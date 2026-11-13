"""
Text Extractor API + web page.

Run:  uvicorn app.main:app --reload --port 8000
Open: http://localhost:8000
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from . import extractor
from .extractor import ExtractionError

logging.basicConfig(level=logging.INFO)

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Text Extractor", version="1.0.0")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "engine": extractor.active_engine(),
        "ai_configured": extractor.ai_available(),
        "tesseract_installed": extractor.tesseract_available(),
        "ai_model": extractor.AI_MODEL if extractor.ai_available() else None,
        "max_upload_mb": MAX_UPLOAD_MB,
    }


async def _run(file: UploadFile, engine: str | None):
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
    if engine not in (None, "", "auto", "ai", "tesseract"):
        raise HTTPException(400, "engine must be auto, ai or tesseract.")
    engine = None if engine in (None, "", "auto") else engine
    try:
        # OCR is CPU/network bound; keep the server responsive.
        return await asyncio.to_thread(extractor.extract, file.filename or "upload", data, engine)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/extract")
async def extract_json(file: UploadFile = File(...), engine: str | None = Form(None)) -> JSONResponse:
    """Returns the text plus per-page details (used by the web page)."""
    r = await _run(file, engine)
    return JSONResponse({
        "filename": r.filename,
        "kind": r.kind,
        "text": r.text,
        "pages": [{"number": p.number, "method": p.method, "chars": len(p.text.strip())} for p in r.pages],
        "methods": r.methods,
        "warnings": r.warnings,
        "download_name": _txt_name(r.filename),
    })


@app.post("/api/extract.txt")
async def extract_txt(file: UploadFile = File(...), engine: str | None = Form(None)) -> PlainTextResponse:
    """Returns the .txt file directly (handy for scripts / curl)."""
    r = await _run(file, engine)
    name = _txt_name(r.filename)
    return PlainTextResponse(
        r.text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _txt_name(filename: str) -> str:
    stem = Path(filename).stem or "extracted"
    safe = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in stem)
    return f"{safe}.txt"
