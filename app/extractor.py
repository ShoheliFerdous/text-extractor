"""
Core extraction logic: PDF or image in -> plain text out.

Strategy
--------
PDF:   For each page, first try the PDF's own text layer (fast, exact, free).
       If a page has little or no real text (a scanned page / photo), render it
       to an image and OCR it.
Image: OCR it directly.

OCR engine
----------
- "ai"        : a vision AI model through any OpenAI-compatible API
                (Groq by default, or OpenAI / Azure OpenAI). Best on poor scans,
                tables and handwriting. Needs an API key.
- "tesseract" : free offline OCR. No key needed.
- "auto"      : AI when a key is configured, otherwise Tesseract. If an AI call
                fails for a page, that page falls back to Tesseract.
"""

from __future__ import annotations

import base64
import io
import logging
import os
from dataclasses import dataclass, field

import httpx
import pymupdf
import pytesseract
from PIL import Image, ImageOps

log = logging.getLogger("extractor")

# ---------------------------------------------------------------- settings ---

AI_API_KEY = os.getenv("AI_API_KEY") or os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT_SECONDS", "90"))

OCR_ENGINE = os.getenv("OCR_ENGINE", "auto").lower()        # auto | ai | tesseract
TESSERACT_LANG = os.getenv("TESSERACT_LANG", "eng")
MAX_PAGES = int(os.getenv("MAX_PAGES", "100"))
RENDER_DPI = int(os.getenv("RENDER_DPI", "200"))
# A PDF page with fewer real characters than this is treated as scanned.
MIN_TEXT_CHARS = int(os.getenv("MIN_TEXT_CHARS", "25"))
# Keep images sent to the AI under provider limits (Groq: ~4 MB base64).
AI_MAX_IMAGE_SIDE = int(os.getenv("AI_MAX_IMAGE_SIDE", "2000"))

# Windows: Tesseract is often installed but not on PATH. Use TESSERACT_CMD or the default install path.
_tess = os.getenv("TESSERACT_CMD")
if not _tess and os.name == "nt":
    for _p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if os.path.exists(_p):
            _tess = _p
            break
if _tess:
    pytesseract.pytesseract.tesseract_cmd = _tess


def tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXTS = {".pdf"}

AI_PROMPT = (
    "You are an OCR engine. Transcribe ALL text in this image exactly as written, "
    "in natural reading order. Keep line breaks and paragraph breaks. "
    "Render tables as rows with cells separated by ' | '. "
    "Do not summarise, translate, correct spelling, or add commentary. "
    "If a word is unreadable write [illegible]. "
    "If there is no text at all, reply with exactly: [no text]"
)


class ExtractionError(Exception):
    """Raised for problems the user should see (bad file, unsupported type...)."""


@dataclass
class PageResult:
    number: int
    text: str
    method: str  # "text-layer" | "ai" | "tesseract"


@dataclass
class ExtractionResult:
    filename: str
    kind: str  # "pdf" | "image"
    pages: list[PageResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        if not self.pages:
            return ""
        if len(self.pages) == 1:
            return self.pages[0].text.strip() + "\n"
        parts = [f"===== Page {p.number} =====\n{p.text.strip()}" for p in self.pages]
        return "\n\n".join(parts).strip() + "\n"

    @property
    def methods(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.pages:
            counts[p.method] = counts.get(p.method, 0) + 1
        return counts


# ------------------------------------------------------------- public API ---

def ai_available() -> bool:
    return bool(AI_API_KEY)


def active_engine() -> str:
    if OCR_ENGINE == "tesseract":
        return "tesseract"
    if OCR_ENGINE == "ai":
        return "ai"
    return "ai" if ai_available() else "tesseract"


def extract(filename: str, data: bytes, engine: str | None = None) -> ExtractionResult:
    """Extract text from a PDF or image given as raw bytes."""
    if not data:
        raise ExtractionError("The file is empty.")

    engine = (engine or active_engine()).lower()
    if engine == "ai" and not ai_available():
        raise ExtractionError("AI engine requested but no AI_API_KEY / GROQ_API_KEY is set.")

    ext = os.path.splitext(filename.lower())[1]
    if ext in PDF_EXTS or data[:5] == b"%PDF-":
        return _extract_pdf(filename, data, engine)
    if ext in IMAGE_EXTS or _looks_like_image(data):
        return _extract_image(filename, data, engine)
    raise ExtractionError("Unsupported file type. Upload a PDF or an image (PNG, JPG, WEBP, TIFF, BMP, GIF).")


# --------------------------------------------------------------- PDF path ---

def _extract_pdf(filename: str, data: bytes, engine: str) -> ExtractionResult:
    result = ExtractionResult(filename=filename, kind="pdf")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # corrupt / not really a PDF
        raise ExtractionError(f"Could not open the PDF: {exc}") from exc

    with doc:
        if doc.needs_pass:
            raise ExtractionError("This PDF is password-protected. Remove the password and try again.")
        total = doc.page_count
        if total == 0:
            raise ExtractionError("The PDF has no pages.")
        if total > MAX_PAGES:
            result.warnings.append(f"PDF has {total} pages; only the first {MAX_PAGES} were processed.")

        for i in range(min(total, MAX_PAGES)):
            page = doc[i]
            layer = page.get_text("text", sort=True) or ""
            if _real_chars(layer) >= MIN_TEXT_CHARS:
                result.pages.append(PageResult(i + 1, layer, "text-layer"))
                continue
            # Scanned page -> render and OCR
            pix = page.get_pixmap(dpi=RENDER_DPI)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text, method = _ocr(img, engine, result.warnings, page_no=i + 1)
            result.pages.append(PageResult(i + 1, text, method))
    return result


# ------------------------------------------------------------- image path ---

def _extract_image(filename: str, data: bytes, engine: str) -> ExtractionResult:
    result = ExtractionResult(filename=filename, kind="image")
    try:
        img = Image.open(io.BytesIO(data))
        frames = getattr(img, "n_frames", 1)
    except Exception as exc:
        raise ExtractionError(f"Could not read the image: {exc}") from exc

    if frames > 1 and img.format == "TIFF":
        # Multi-page TIFF (common from office scanners): treat like pages.
        for n in range(min(frames, MAX_PAGES)):
            img.seek(n)
            text, method = _ocr(img.copy(), engine, result.warnings, page_no=n + 1)
            result.pages.append(PageResult(n + 1, text, method))
        return result

    img = ImageOps.exif_transpose(img)  # respect phone-camera rotation
    text, method = _ocr(img, engine, result.warnings, page_no=1)
    result.pages.append(PageResult(1, text, method))
    return result


# -------------------------------------------------------------------- OCR ---

def _ocr(img: Image.Image, engine: str, warnings: list[str], page_no: int) -> tuple[str, str]:
    img = _normalise(img)
    if engine == "ai":
        try:
            return _ocr_ai(img), "ai"
        except Exception as exc:
            log.warning("AI OCR failed on page %s: %s", page_no, exc)
            warnings.append(f"Page {page_no}: AI reading failed ({_short(exc)}); used offline OCR instead.")
    return _ocr_tesseract(img), "tesseract"


def _ocr_tesseract(img: Image.Image) -> str:
    if not tesseract_available():
        raise ExtractionError(
            "Tesseract OCR is not installed (needed for scanned pages and images when no AI key is set). "
            "Install it from https://github.com/UB-Mannheim/tesseract/wiki, or set GROQ_API_KEY to use AI."
        )
    gray = ImageOps.grayscale(img)
    # Small images OCR badly; upscale them.
    if min(gray.size) < 1000:
        scale = 1000 / min(gray.size)
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.LANCZOS)
    return pytesseract.image_to_string(gray, lang=TESSERACT_LANG, config="--psm 3")


def _ocr_ai(img: Image.Image) -> str:
    img = img.copy()
    img.thumbnail((AI_MAX_IMAGE_SIDE, AI_MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode()

    payload = {
        "model": AI_MODEL,
        "temperature": 0,
        "max_tokens": 4096,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": AI_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    }
    resp = httpx.post(
        f"{AI_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {AI_API_KEY}"},
        json=payload,
        timeout=AI_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    text = resp.json()["choices"][0]["message"]["content"] or ""
    text = _strip_fences(text)
    return "" if text.strip() == "[no text]" else text


# ---------------------------------------------------------------- helpers ---

def _normalise(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB") if img.mode != "RGB" else img


def _real_chars(s: str) -> int:
    return sum(1 for c in s if c.isalnum())


def _looks_like_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s


def _short(exc: Exception) -> str:
    msg = str(exc)
    return msg if len(msg) < 80 else msg[:77] + "..."
