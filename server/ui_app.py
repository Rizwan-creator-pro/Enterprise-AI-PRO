import os
import io
import re
from datetime import datetime, timezone
import asyncio
import threading
import queue
import json
import hmac
import hashlib
import base64
import time
import secrets
import uuid
import requests
from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
import sys
import logging

# ---------------- Dynamic Multi-Folder Path Resolution (Client / Server) ----------------
SERVER_DIR = Path(__file__).resolve().parent
ROOT_DIR = SERVER_DIR.parent if SERVER_DIR.name == "server" else SERVER_DIR
for p in [str(SERVER_DIR), str(ROOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from agent import query_engine as qe
except ImportError:
    import query_engine as qe

from agent.gdrive_storage import is_gdrive_configured, upload_file_to_drive, get_credentials_path, get_root_folder_id

# Determine client directory (frontend files)
if (ROOT_DIR / "client" / "index.html").exists():
    CLIENT_DIR = ROOT_DIR / "client"
elif (SERVER_DIR / "client" / "index.html").exists():
    CLIENT_DIR = SERVER_DIR / "client"
elif (SERVER_DIR / "index.html").exists():
    CLIENT_DIR = SERVER_DIR
else:
    CLIENT_DIR = ROOT_DIR

STATIC_DIR = str(CLIENT_DIR / "static")
INDEX_HTML = CLIENT_DIR / "index.html"

# ---------------- Load settings from the .env file next to this script ----------------
def _is_placeholder_env(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        content = path.read_text(encoding="utf-8")
        return "<your-db-instance-id>" in content or "<schema>" in content or "<region>" in content
    except Exception:
        return False

def _load_env_file(path: Path, override: bool = False):
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(str(path), override=override)
        return
    except ImportError:
        pass
    with open(str(path), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and (override or k not in os.environ or "<your-db" in os.environ.get(k, "")):
                os.environ[k] = v

# Load root .env first, then server .env (if not a placeholder template)
if (ROOT_DIR / ".env").exists() and not _is_placeholder_env(ROOT_DIR / ".env"):
    _load_env_file(ROOT_DIR / ".env", override=True)

if (SERVER_DIR / ".env").exists() and not _is_placeholder_env(SERVER_DIR / ".env"):
    _load_env_file(SERVER_DIR / ".env", override=False)
elif (ROOT_DIR / ".env").exists() and not _is_placeholder_env(ROOT_DIR / ".env"):
    try:
        (SERVER_DIR / ".env").write_text((ROOT_DIR / ".env").read_text(encoding="utf-8"), encoding="utf-8")
    except Exception:
        pass

# Final safety check: if ORDS_BASE_URL contains placeholder brackets, clear it
if "<your-db" in os.environ.get("ORDS_BASE_URL", ""):
    os.environ.pop("ORDS_BASE_URL", None)


app = FastAPI(title="Enterprise AI Hub - v2 Enterprise Edition")

# ---------------- CORS & Security Middlewares ----------------
_cors_env = os.environ.get("CORS_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF defense on state-changing API requests
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.url.path.startswith("/api/"):
        if request.url.path not in ("/api/login", "/api/config"):
            sec_fetch_site = request.headers.get("sec-fetch-site")
            if sec_fetch_site == "cross-site":
                raise HTTPException(status_code=403, detail="Cross-site request blocked.")
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("host")
                origin_host = origin.split("://")[-1].rstrip("/")
                if host and origin_host != host and origin not in ALLOWED_ORIGINS:
                    raise HTTPException(status_code=403, detail="Untrusted origin blocked.")

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
        "style-src 'self' 'unsafe-inline' https:; "
        "font-src 'self' https: data:; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob: data: https:; "
        "worker-src 'self' blob:; "
        "connect-src 'self' https:;"
    )
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

# Optional local copies of docx.js / pptxgen.bundle.js / jszip.min.js
os.makedirs(os.path.join(STATIC_DIR, "lib"), exist_ok=True)
os.makedirs(os.path.join(STATIC_DIR, "uploads"), exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def _get_app_config():
    config_paths = [SERVER_DIR / "app_config.json", ROOT_DIR / "app_config.json", Path("app_config.json")]
    for p in config_paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {
        "company_name": "Enterprise AI",
        "app_name": "Enterprise AI Hub",
        "version": "2.4.0-PRO",
        "version_badge": "PRO",
        "pdf_company": "Enterprise AI",
        "file_prefix": "Enterprise_AI",
        "footer_watermark": "Enterprise AI Hub • Autonomous Intelligence Platform • Confidential",
        "software_creator": "Enterprise AI Engineering Team",
        "contact_info": "support@enterprise.ai",
        "system_title": "Enterprise AI Hub System Architecture & Technical Specifications",
        "doc_subtitle": "Autonomous Agent Specifications, In-Memory Deterministic Sandbox & Oracle ORDS",
        "greeting_sub": "Ask about enterprise data, run in-memory calculations, or generate verified reports.",
        "max_upload_images": 5,
        "max_upload_docs": 3,
        "max_image_size_mb": 15,
        "max_document_size_mb": 25,
        "allowed_image_types": [".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".bmp", ".avif", ".svg"],
        "allowed_document_types": [".pdf", ".docx", ".doc", ".txt", ".csv", ".xlsx", ".xls", ".json", ".md"]
    }

@app.get("/healthz", include_in_schema=False)
@app.get("/health", include_in_schema=False)
@app.get("/ai/healthz", include_in_schema=False)
@app.get("/ai/health", include_in_schema=False)
def health_check():
    return {"status": "ok"}

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_endpoint():
    p = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(p):
        return FileResponse(p, media_type="image/x-icon")
    return Response(status_code=204)

@app.get("/manifest.json", include_in_schema=False)
async def manifest_endpoint():
    p = os.path.join(STATIC_DIR, "manifest.json")
    if os.path.exists(p):
        return FileResponse(p, media_type="application/manifest+json")
    return Response(status_code=404)

@app.get("/sw.js", include_in_schema=False)
async def sw_endpoint():
    p = os.path.join(STATIC_DIR, "sw.js")
    if os.path.exists(p):
        return FileResponse(p, media_type="application/javascript")
    return Response(status_code=404)

@app.get("/api/config")
def get_app_config_endpoint():
    return _get_app_config()

# ---------------- Firecrawl Web Intelligence API Endpoints ----------------
@app.get("/api/web/status")
def web_intelligence_status():
    """Returns Firecrawl and Web Intelligence availability status."""
    from agent.firecrawl_intelligence import is_firecrawl_installed, get_firecrawl_client
    has_key = bool(os.environ.get("FIRECRAWL_API_KEY", "").strip())
    client = get_firecrawl_client()
    return {
        "installed": is_firecrawl_installed(),
        "api_key_configured": has_key,
        "engine": "Firecrawl Cloud SDK" if has_key else "Firecrawl Dual-Engine (with Resilient Local Fallback)",
        "capabilities": ["scrape", "search", "crawl", "extract"],
        "docs": "https://github.com/firecrawl/firecrawl"
    }

class WebScrapeRequest(BaseModel):
    url: str

class WebSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 5

@app.post("/api/web/scrape")
def web_scrape_endpoint(req: WebScrapeRequest):
    """Direct Firecrawl URL scraping endpoint."""
    from agent.firecrawl_intelligence import scrape_url
    return scrape_url(req.url)

@app.post("/api/web/search")
def web_search_endpoint(req: WebSearchRequest):
    """Direct Firecrawl web search endpoint."""
    from agent.firecrawl_intelligence import search_web
    return search_web(req.query, limit=req.limit or 5)


@app.post("/api/upload-image")
async def upload_image_endpoint(
    request: Request,
    file: Optional[UploadFile] = File(None),
    session_id: Optional[str] = Form(None),
    session_title: Optional[str] = Form(None),
    user_id: Optional[int] = Form(None),
    user_name: Optional[str] = Form(None)
):
    u = None
    try:
        u = current_user(request)
    except Exception:
        pass  # allow upload during active browser session

    u_id = 1
    u_name = "User"
    if u and isinstance(u, dict):
        u_id = int(u.get("u") or u.get("user_id") or 1)
        u_name = str(u.get("n") or u.get("username") or "User")
    if user_id:
        u_id = int(user_id)
    if user_name and str(user_name).strip() and str(user_name).strip() != "User":
        u_name = str(user_name).strip()

    if u and isinstance(u, dict):
        is_adm = (u.get("a") == "Y")
        perms = u.get("p") or {}
        if not is_adm and not perms.get("can_upload", True):
            raise HTTPException(status_code=403, detail="File and image upload permission is disabled for your user account.")

    cfg = _get_app_config()
    max_img_mb = cfg.get("max_image_size_mb", 15)
    allowed_types = cfg.get("allowed_image_types", [".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif", ".bmp", ".avif", ".svg"])

    b64_content = None
    mime_type = "image/jpeg"
    filename = "image.jpg"
    file_bytes = None

    if file:
        filename = file.filename or "image.png"
        ext = os.path.splitext(filename)[1].lower()
        if ext and allowed_types and ext not in allowed_types:
            raise HTTPException(status_code=400, detail=f"Unsupported image format: {ext}. Allowed: {', '.join(allowed_types)}")
        file_bytes = await file.read()
        if len(file_bytes) > max_img_mb * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"Image exceeds maximum size limit of {max_img_mb} MB.")
        b64_content = base64.b64encode(file_bytes).decode("utf-8")
        if ext in [".jfif", ".jpg", ".jpeg"]:
            mime_type = "image/jpeg"
        elif ext == ".png":
            mime_type = "image/png"
        elif ext == ".webp":
            mime_type = "image/webp"
        elif ext == ".gif":
            mime_type = "image/gif"
        elif ext == ".bmp":
            mime_type = "image/bmp"
        elif ext == ".svg":
            mime_type = "image/svg+xml"
        elif ext == ".avif":
            mime_type = "image/avif"
        else:
            mime_type = file.content_type or "image/jpeg"
            if mime_type in ("image/pjpeg", "image/pipeg"):
                mime_type = "image/jpeg"
    else:
        try:
            body = await request.json()
            raw_img = body.get("image", "")
            session_id = session_id or body.get("session_id")
            session_title = session_title or body.get("session_title")
            if not user_id and body.get("user_id"):
                u_id = int(body.get("user_id"))
            if (not user_name or user_name == "User") and body.get("user_name"):
                u_name = str(body.get("user_name"))
            if raw_img and "," in raw_img:
                header, b64_content = raw_img.split(",", 1)
                if "data:" in header and ";base64" in header:
                    mime_type = header.split("data:")[1].split(";base64")[0]
            else:
                b64_content = raw_img
            if b64_content:
                file_bytes = base64.b64decode(b64_content)
        except Exception:
            pass

    if not b64_content:
        raise HTTPException(status_code=400, detail="No image file or image data provided.")

    data_url = f"data:{mime_type};base64,{b64_content}"

    # 1. Google Drive Cloud Upload (Primary)
    gdrive_info = None
    if is_gdrive_configured() and file_bytes:
        try:
            gdrive_info = upload_file_to_drive(
                file_bytes=file_bytes,
                filename=filename,
                mime_type=mime_type,
                user_id=u_id,
                user_name=u_name,
                session_id=session_id or "general",
                session_title=session_title or "Chat"
            )
        except Exception as e:
            print(f"[WARNING] GDrive image upload exception: {e}")

    g_file_id = gdrive_info.get("file_id") if gdrive_info and gdrive_info.get("success") else None
    g_web_link = gdrive_info.get("web_link") if gdrive_info and gdrive_info.get("success") else None
    g_download = gdrive_info.get("download_link") if gdrive_info and gdrive_info.get("success") else None

    # For browser display and AI vision models, data_url guarantees instant, non-broken viewing and full pixel visibility
    chosen_url = data_url
    thumb_url = data_url
    proxy_url = f"/api/gdrive/file/{g_file_id}" if g_file_id else data_url

    # Record attachment in Oracle DB if session_id provided
    _record_attachment(
        session_id=session_id,
        user_id=u_id,
        filename=filename,
        file_type="image",
        mime_type=mime_type,
        file_size=len(file_bytes) if file_bytes else 0,
        gdrive_id=g_file_id,
        web_link=g_web_link or chosen_url,
        download_link=g_download or chosen_url,
        storage_source="GDRIVE" if g_file_id else "INLINE"
    )

    # Decode Barcodes & QR codes if present in image
    scanned_codes = []
    if file_bytes:
        try:
            from agent.barcode_helper import scan_barcodes_and_qr
            scanned_codes = scan_barcodes_and_qr(file_bytes)
        except Exception as scan_err:
            print(f"[Barcode/QR Scanner] Error: {scan_err}")

    return {
        "success": True,
        "url": data_url,
        "display_url": data_url,
        "thumb_url": data_url,
        "proxy_url": proxy_url,
        "name": filename,
        "mime_type": mime_type,
        "gdrive_url": g_web_link,
        "gdrive_download": g_download,
        "gdrive_file_id": g_file_id,
        "scanned_codes": scanned_codes
    }


@app.post("/api/upload-document")
async def upload_document_endpoint(
    request: Request,
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    session_title: Optional[str] = Form(None),
    user_id: Optional[int] = Form(None),
    user_name: Optional[str] = Form(None)
):
    u = None
    try:
        u = current_user(request)
    except Exception:
        pass

    u_id = 1
    u_name = "User"
    if u and isinstance(u, dict):
        u_id = int(u.get("u") or u.get("user_id") or 1)
        u_name = str(u.get("n") or u.get("username") or "User")
    if user_id:
        u_id = int(user_id)
    if user_name and str(user_name).strip() and str(user_name).strip() != "User":
        u_name = str(user_name).strip()

    if u and isinstance(u, dict):
        is_adm = (u.get("a") == "Y")
        perms = u.get("p") or {}
        if not is_adm and not perms.get("can_upload", True):
            raise HTTPException(status_code=403, detail="Document upload permission is disabled for your user account.")

    cfg = _get_app_config()
    max_doc_mb = cfg.get("max_document_size_mb", 25)
    allowed_types = cfg.get("allowed_document_types", [".pdf", ".docx", ".doc", ".txt", ".csv", ".xlsx", ".xls", ".json", ".md"])

    filename = file.filename or "document.txt"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported document format: {ext}. Allowed: {', '.join(allowed_types)}")

    file_bytes = await file.read()
    if len(file_bytes) > max_doc_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Document exceeds maximum size limit of {max_doc_mb} MB.")

    # 1. Google Drive Cloud Upload (if configured)
    gdrive_info = None
    if is_gdrive_configured() and file_bytes:
        try:
            gdrive_info = upload_file_to_drive(
                file_bytes=file_bytes,
                filename=filename,
                mime_type=file.content_type or "application/octet-stream",
                user_id=u_id,
                user_name=u_name,
                session_id=session_id or "general",
                session_title=session_title or "Chat"
            )
        except Exception as e:
            print(f"[WARNING] GDrive doc upload exception: {e}")

    text_content = ""
    pages = 1
    try:
        if ext == ".pdf":
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages = len(reader.pages)
            pages_text = []
            for i, page in enumerate(reader.pages):
                pt = page.extract_text() or ""
                if pt.strip():
                    pages_text.append(f"--- Page {i+1} ---\n{pt.strip()}")
            text_content = "\n\n".join(pages_text)
        elif ext in (".docx", ".doc"):
            import docx
            doc = docx.Document(io.BytesIO(file_bytes))
            paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            t_rows = []
            for t in doc.tables:
                for r in t.rows:
                    cells = [c.text.strip() for c in r.cells]
                    if any(cells):
                        t_rows.append(" | ".join(cells))
            text_content = "\n\n".join(paras + t_rows)
        elif ext in (".xlsx", ".xls"):
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            sheets_text = []
            for sname in wb.sheetnames:
                ws = wb[sname]
                s_lines = []
                for row in ws.iter_rows(values_only=True):
                    if any(c is not None for c in row):
                        s_lines.append(" | ".join([str(c) if c is not None else "" for c in row]))
                if s_lines:
                    sheets_text.append(f"--- Sheet: {sname} ---\n" + "\n".join(s_lines[:500]))
            text_content = "\n\n".join(sheets_text)
        else:
            text_content = file_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[WARNING] Document text extraction fallback: {e}")
        text_content = f"Preview isn't available for {filename}."

    if not text_content or not text_content.strip():
        text_content = f"Preview isn't available for {filename}."

    if len(text_content) > 120000:
        text_content = text_content[:120000] + "\n\n[... Remaining document text truncated for model context limit ...]"

    # Record attachment in Oracle DB
    g_file_id = gdrive_info.get("file_id") if gdrive_info and gdrive_info.get("success") else None
    g_web_link = gdrive_info.get("web_link") if gdrive_info and gdrive_info.get("success") else None
    g_download = gdrive_info.get("download_link") if gdrive_info and gdrive_info.get("success") else None
    _record_attachment(
        session_id=session_id,
        user_id=u_id,
        filename=filename,
        file_type="document",
        mime_type=file.content_type or "application/octet-stream",
        file_size=len(file_bytes),
        gdrive_id=g_file_id,
        web_link=g_web_link or "",
        download_link=g_download or "",
        storage_source="GDRIVE" if g_file_id else "LOCAL"
    )

    return {
        "success": True,
        "filename": filename,
        "name": filename,
        "file_type": ext.lstrip("."),
        "size_bytes": len(file_bytes),
        "size": len(file_bytes),
        "pages": pages,
        "char_count": len(text_content),
        "text": text_content,
        "preview_text": text_content[:500] if text_content else "",
        "gdrive_url": g_web_link,
        "gdrive_download": g_download,
        "gdrive_file_id": g_file_id
    }

DEFAULT_ORDS_URL = "https://g086cd062cdff6a-services.adb.ap-singapore-1.oraclecloudapps.com/ords/aiagent/engine"
BASE_URL = (os.environ.get("ORDS_BASE_URL") or "").strip() or DEFAULT_ORDS_URL
if not os.environ.get("ORDS_BASE_URL"):
    print(f"[INFO] Using default Oracle ORDS production base: {BASE_URL}")

URL_LOGIN = f"{BASE_URL}/auth/login"
URL_AUTH_USER = f"{BASE_URL}/auth/user"
URL_SET_PW = f"{BASE_URL}/auth/set-password"
URL_PROVIDERS = f"{BASE_URL}/providers"
URL_PROVIDERS_INTERNAL = f"{BASE_URL}/providers/internal"
URL_USER_ENDPOINTS = f"{BASE_URL}/user-endpoints" 
URL_HISTORY = f"{BASE_URL}/user-history"
URL_MESSAGES = f"{BASE_URL}/chat/messages"
URL_SAVE_CHAT = f"{BASE_URL}/chat/save"
URL_UPDATE_CHAT = f"{BASE_URL}/chat/update"
URL_ATTACHMENTS = f"{BASE_URL}/chat/attachments"
URL_LOGS = f"{BASE_URL}/logs"
URL_LOGIN_LOG = f"{BASE_URL}/logs/login"
URL_TRUNCATE = f"{BASE_URL}/chat/truncate"
URL_USAGE_LOG = f"{BASE_URL}/usage/log"
URL_USAGE_PROVIDERS = f"{BASE_URL}/usage/providers"
URL_USAGE_USER = f"{BASE_URL}/usage/user"
URL_USAGE_DAILY = f"{BASE_URL}/usage/daily"
URL_USAGE_TOP = f"{BASE_URL}/usage/top"

# Optional ORDS OAuth credentials from environment or .env
ORDS_CLIENT_ID = os.environ.get("ORDS_CLIENT_ID")
ORDS_CLIENT_SECRET = os.environ.get("ORDS_CLIENT_SECRET")
ORDS_TOKEN_URL = os.environ.get("ORDS_TOKEN_URL") or (BASE_URL.replace("/engine", "/oauth/token") if BASE_URL else "")

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=256)

class ChatRequest(BaseModel):
    user_id: Optional[int] = None   # ignored: identity comes from the session cookie
    session_id: str = Field(..., min_length=1, max_length=128)
    question: str = Field(..., min_length=1, max_length=4000)
    provider_id: Optional[int] = None  # user-picked model; tried first, others remain as fallback
    keep_count: Optional[int] = Field(None, ge=0, le=1000)  # edit mode: keep only the first N stored messages
    remember: Optional[bool] = False  # True = send the earlier messages of this chat to the AI as context
    outside_db: Optional[bool] = False  # When True, answers general queries outside database (weather, dollar rate, etc.)
    allow_general: Optional[bool] = False
    images: Optional[List[Any]] = Field(default_factory=list)
    documents: Optional[List[dict]] = Field(default_factory=list)
    response_depth: Optional[str] = "medium"
    client_country: Optional[str] = None
    client_city: Optional[str] = None


class UpdateChatRequest(BaseModel):
    user_id: Optional[int] = None
    session_id: str = Field(..., min_length=1, max_length=128)
    session_title: str = Field(..., min_length=1, max_length=200)
    is_pinned: Optional[str] = Field(None, max_length=5)

class StopRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)

# ---------------- sessions: identity lives on the server, the browser never supplies a user id ----------------
SESSION_COOKIE = "ai_session"
SESSION_TTL = 12 * 3600
_SECRET = (os.environ.get("APP_SECRET") or secrets.token_hex(32)).encode()
if not os.environ.get("APP_SECRET"):
    print("[WARNING] APP_SECRET is not set in .env - all users will be logged out every time the server restarts.")
# Rate limiting data structures (Thread-safe & memory bounded)
_fails: Dict[str, List[float]] = {}
_fails_lock = threading.Lock()

def _check_rate_limit(key: str) -> bool:
    with _fails_lock:
        now = time.time()
        is_local = key.startswith("127.0.0.1|") or key.startswith("localhost|") or key.startswith("::1|")
        window = 180 if is_local else 600
        threshold = 20 if is_local else 5
        recent = [t for t in _fails.get(key, []) if now - t < window]
        _fails[key] = recent
        return len(recent) >= threshold

def _record_failed_attempt(key: str):
    with _fails_lock:
        now = time.time()
        # Prune inactive keys older than 600s
        stale_keys = [k for k, timestamps in _fails.items() if not timestamps or (now - timestamps[-1] >= 600)]
        for k in stale_keys:
            _fails.pop(k, None)
        recent = [t for t in _fails.get(key, []) if now - t < 600]
        recent.append(now)
        _fails[key] = recent

def _clear_rate_limit(key: str):
    with _fails_lock:
        _fails.pop(key, None)

# Server-side token revocation registry (Thread-safe & memory bounded)
_revoked_tokens: Dict[str, int] = {}
_revoked_lock = threading.Lock()

def revoke_token(sig: str, expiry: int):
    with _revoked_lock:
        now = int(time.time())
        stale = [s for s, exp in _revoked_tokens.items() if exp <= now]
        for s in stale:
            _revoked_tokens.pop(s, None)
        _revoked_tokens[sig] = expiry

def is_token_revoked(sig: str) -> bool:
    with _revoked_lock:
        now = int(time.time())
        exp = _revoked_tokens.get(sig)
        if exp is None:
            return False
        if exp <= now:
            _revoked_tokens.pop(sig, None)
            return False
        return True

def _sig(body: str) -> str:
    return hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()

def _make_token(uid, username, is_admin="N", perms=None) -> str:
    if perms is None:
        perms = {
            "can_search_outside": True,
            "can_upload": True,
            "can_view_tables": True,
            "can_view_docs": True
        }
    body = base64.urlsafe_b64encode(json.dumps({
        "u": uid, "n": username, "a": is_admin, "p": perms, "e": int(time.time()) + SESSION_TTL
    }).encode()).decode()
    return f"{body}.{_sig(body)}"

def current_user(request: Request) -> dict:
    try:
        cookie_val = request.cookies.get(SESSION_COOKIE, "")
        if "." not in cookie_val:
            raise ValueError("bad session format")
        body, sig = cookie_val.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sig(body)):
            raise ValueError("bad signature")
        if is_token_revoked(sig):
            raise ValueError("revoked token")
        d = json.loads(base64.urlsafe_b64decode(body.encode()))
        if d.get("e", 0) < time.time():
            raise ValueError("expired")
        return d
    except Exception:
        raise HTTPException(status_code=401, detail="Please sign in.")


# ---------------- ORDS OAuth Client Credentials (transparent token caching) ----------------
_ords_token: dict = {"access_token": None, "expires_at": 0}

def ords_headers() -> dict:
    """Returns headers with OAuth Bearer token if ORDS_CLIENT_ID is configured, else plain headers."""
    if not ORDS_CLIENT_ID or not ORDS_CLIENT_SECRET:
        return {}
    now = time.time()
    if _ords_token["access_token"] and _ords_token["expires_at"] > now + 30:
        return {"Authorization": f"Bearer {_ords_token['access_token']}"}
    try:
        r = requests.post(
            ORDS_TOKEN_URL,
            auth=(ORDS_CLIENT_ID, ORDS_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            timeout=10
        )
        if r.ok:
            data = r.json()
            _ords_token["access_token"] = data["access_token"]
            _ords_token["expires_at"] = now + data.get("expires_in", 3600)
            return {"Authorization": f"Bearer {_ords_token['access_token']}"}
    except Exception as e:
        print(f"[ORDS OAuth Error] {e}")
    return {}

def ords_get(url: str, params=None, timeout=15):
    h = ords_headers()
    return requests.get(url, params=params, headers=h, timeout=timeout)

def ords_post(url: str, json=None, timeout=15):
    h = ords_headers()
    return requests.post(url, json=json, headers=h, timeout=timeout)

def _record_attachment(session_id: str, user_id: int, filename: str, file_type: str, mime_type: str, file_size: int, gdrive_id: str = None, web_link: str = None, download_link: str = None, storage_source: str = "GDRIVE"):
    if not session_id:
        return
    try:
        ords_post(URL_ATTACHMENTS, json={
            "session_id": str(session_id),
            "user_id": int(user_id or 0),
            "file_name": str(filename or "file"),
            "file_type": str(file_type or "file"),
            "mime_type": str(mime_type or ""),
            "file_size": int(file_size or 0),
            "gdrive_file_id": str(gdrive_id or ""),
            "gdrive_web_link": str(web_link or ""),
            "gdrive_download_link": str(download_link or ""),
            "storage_source": str(storage_source or "GDRIVE")
        }, timeout=5)
    except Exception as e:
        pass

# ---------------- Password Hashing (PBKDF2-SHA256) ----------------
def hash_password(password: str, salt: bytes = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
    return f"pbkdf2_sha256$200000${base64.b64encode(salt).decode('ascii')}${base64.b64encode(key).decode('ascii')}"

def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("pbkdf2_sha256$"):
        try:
            parts = stored_hash.split("$")
            rounds = int(parts[1])
            salt = base64.b64decode(parts[2].encode("ascii"))
            expected = base64.b64decode(parts[3].encode("ascii"))
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
            return hmac.compare_digest(key, expected)
        except Exception:
            return False
    # Backward compatibility with legacy plain text password
    return hmac.compare_digest(password, stored_hash)

# ---------------- Usage Logger (Direct to Oracle Autonomous Database via ORDS) ----------------
def log_usage(
    user_id: int,
    session_id: str,
    provider_id: Optional[int],
    model_name: str,
    call_type: str,
    status: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    latency_ms: int,
    rl_headers: dict,
    question_preview: str,
    error_msg: Optional[str] = None
):
    rem_req = rl_headers.get("x-ratelimit-remaining-requests")
    rem_tok = rl_headers.get("x-ratelimit-remaining-tokens")
    rst_req = rl_headers.get("x-ratelimit-reset-requests")
    rst_tok = rl_headers.get("x-ratelimit-reset-tokens")

    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "provider_id": provider_id,
        "model_name": model_name,
        "call_type": call_type,
        "status": status,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens or (prompt_tokens + completion_tokens),
        "latency_ms": latency_ms,
        "rl_remaining_req": int(rem_req) if rem_req and str(rem_req).isdigit() else None,
        "rl_remaining_tok": int(rem_tok) if rem_tok and str(rem_tok).isdigit() else None,
        "rl_reset_req": str(rst_req or ""),
        "rl_reset_tok": str(rst_tok or ""),
        "question_preview": question_preview[:200],
        "error_message": error_msg[:500] if error_msg else None
    }

    # Write directly to Oracle ADB usage endpoint
    try:
        ords_post(URL_USAGE_LOG, json=payload, timeout=5)
    except Exception as e:
        print(f"[ORDS Usage Log Notice] {e}")

# ---------------- User Endpoints & Schema Extraction (Cache 60s) ----------------
_dataset_cache: Dict[str, Tuple[float, dict]] = {}  # ep_url -> (cached_at, items)

DEPT_NAMES = {
    1: "Human Resources",
    2: "Information Technology",
    3: "Finance",
    4: "Marketing",
    5: "Sales",
    6: "Operations",
    7: "Customer Support",
    8: "Research & Development"
}

def _enrich_row(r: dict) -> dict:
    row = dict(r)
    # 1. Department name enrichment
    if "dept_id" in row:
        d_val = row.get("dept_id")
        try:
            d_int = int(d_val) if d_val is not None else None
        except (ValueError, TypeError):
            d_int = None
        if "department" not in row:
            row["department"] = DEPT_NAMES.get(d_int, f"Department {d_val}" if d_val is not None else "Unknown")
    # 2. Full name enrichment
    if "first_name" in row and "last_name" in row and "name" not in row and "full_name" not in row:
        fn = str(row.get("first_name") or "").strip()
        ln = str(row.get("last_name") or "").strip()
        row["full_name"] = f"{fn} {ln}".strip()
    return row

def _get_user_datasets(user_id: int, emit=None) -> Tuple[Dict[str, List[dict]], List[dict]]:
    """
    Returns (datasets_dict, endpoints_meta).
    Caches raw endpoint data for 60 seconds so follow-up queries don't re-download the entire DB.
    """
    if emit: emit("Connecting to authorized data sources…")
    try:
        ep_res = ords_get(f"{URL_USER_ENDPOINTS}/{user_id}", timeout=15)
        raw_endpoints = ep_res.json().get("items", []) if ep_res.ok else []
    except Exception:
        raw_endpoints = []

    def _is_inactive(val):
        return str(val or "").strip().upper() in ("N", "0", "FALSE", "INACTIVE", "DISABLED")

    # Filter out inactive endpoints or accesses (supports is_active, access_active, status)
    endpoints = []
    for ep in raw_endpoints:
        if _is_inactive(ep.get("is_active")) or _is_inactive(ep.get("IS_ACTIVE")) or \
           _is_inactive(ep.get("access_active")) or _is_inactive(ep.get("ACCESS_ACTIVE")) or \
           _is_inactive(ep.get("endpoint_active")) or _is_inactive(ep.get("ENDPOINT_ACTIVE")) or \
           _is_inactive(ep.get("status")) or _is_inactive(ep.get("STATUS")):
            continue
        endpoints.append(ep)

    now = time.time()
    datasets = {}
    for ep in endpoints:
        name = str(ep.get("endpoint_name", "Data"))
        rest_url = ep.get("rest_url")
        if not rest_url:
            continue
        cache_sec = int(ep.get("cache_seconds") or 60)
        cached = _dataset_cache.get(rest_url)
        if cached and (now - cached[0] < cache_sec):
            datasets[name] = cached[1]
            continue

        if emit: emit(f"Refreshing {name}…")
        try:
            cur_url = rest_url
            if cur_url and "limit=" not in cur_url.lower():
                cur_url += ("&" if "?" in cur_url else "?") + "limit=10000"
            all_rows = []
            pages_fetched = 0
            while cur_url and pages_fetched < 100:
                h = ords_headers()
                r = requests.get(cur_url, headers=h, timeout=15)
                if not r.ok:
                    break
                data = r.json()
                items = data.get("items", [])
                enriched_items = [_enrich_row(item) for item in items]
                all_rows.extend(enriched_items)
                pages_fetched += 1
                next_url = None
                if data.get("hasMore"):
                    for lnk in data.get("links", []):
                        if lnk.get("rel") == "next":
                            next_url = lnk.get("href")
                            break
                if next_url and rest_url.startswith("https://") and next_url.startswith("http://"):
                    next_url = "https://" + next_url[len("http://"):]
                cur_url = next_url
            _dataset_cache[rest_url] = (now, all_rows)
            datasets[name] = all_rows
        except Exception:
            datasets[name] = []

    return datasets, endpoints

_endpoints_cache: Dict[int, Tuple[float, List[dict]]] = {}

def _get_user_active_endpoints(user_id: int) -> List[dict]:
    """Fetches user authorized endpoints metadata (cached for 60 seconds)."""
    now = time.time()
    cached = _endpoints_cache.get(user_id)
    if cached and (now - cached[0] < 60):
        return cached[1]

    try:
        ep_res = ords_get(f"{URL_USER_ENDPOINTS}/{user_id}", timeout=15)
        raw_endpoints = ep_res.json().get("items", []) if ep_res.ok else []
    except Exception:
        raw_endpoints = []

    def _is_inactive(val):
        return str(val or "").strip().upper() in ("N", "0", "FALSE", "INACTIVE", "DISABLED")

    endpoints = []
    for ep in raw_endpoints:
        if _is_inactive(ep.get("is_active")) or _is_inactive(ep.get("IS_ACTIVE")) or \
           _is_inactive(ep.get("access_active")) or _is_inactive(ep.get("ACCESS_ACTIVE")) or \
           _is_inactive(ep.get("endpoint_active")) or _is_inactive(ep.get("ENDPOINT_ACTIVE")) or \
           _is_inactive(ep.get("status")) or _is_inactive(ep.get("STATUS")):
            continue
        endpoints.append(ep)

    _endpoints_cache[user_id] = (now, endpoints)
    return endpoints

def _fetch_single_table_dataset(ep: dict, emit=None) -> List[dict]:
    """Fetches and enriches rows for a single endpoint with in-memory caching."""
    name = str(ep.get("endpoint_name", "Data"))
    rest_url = ep.get("rest_url")
    if not rest_url:
        return []

    now = time.time()
    cache_sec = int(ep.get("cache_seconds") or 60)
    cached = _dataset_cache.get(rest_url)
    if cached and (now - cached[0] < cache_sec):
        return cached[1]

    if emit: emit(f"Loading {name} on-demand…")
    cur_url = rest_url
    if cur_url and "limit=" not in cur_url.lower():
        cur_url += ("&" if "?" in cur_url else "?") + "limit=10000"
    all_rows = []
    pages_fetched = 0
    while cur_url and pages_fetched < 100:
        try:
            h = ords_headers()
            r = requests.get(cur_url, headers=h, timeout=15)
            if not r.ok:
                break
            data = r.json()
            items = data.get("items", [])
            enriched_items = [_enrich_row(item) for item in items]
            all_rows.extend(enriched_items)
            pages_fetched += 1
            next_url = None
            if data.get("hasMore"):
                for lnk in data.get("links", []):
                    if lnk.get("rel") == "next":
                        next_url = lnk.get("href")
                        break
            if next_url and rest_url.startswith("https://") and next_url.startswith("http://"):
                next_url = "https://" + next_url[len("http://"):]
            cur_url = next_url
        except Exception:
            break

    _dataset_cache[rest_url] = (now, all_rows)
    return all_rows

TABLE_SYNONYMS = {
    "Employees Directory": [
        "salary", "salaries", "saalary", "pay", "payroll", "wage", "wages", "compensation",
        "employee", "employees", "employer", "employers", "staff", "worker", "workers", "hiring", "hired",
        "hire date", "job", "jobs", "job title", "designation", "department", "departments",
        "director", "directors", "manager", "managers", "bonus"
    ],
    "PRODUCTS": [
        "product", "products", "item", "items", "catalog", "inventory",
        "stock_qty", "stock quantity", "in stock", "out of stock", "sku",
        "unit price", "product price", "pricing", "product cost"
    ],
    "Google Map Leads": [
        "lead", "leads", "g map", "gmap", "google map", "google maps", "rating", "ratings",
        "review", "reviews", "business", "businesses", "niche", "phone number",
        "contact", "whatsapp", "website status", "lead score", "place_id", "opportunity"
    ]
}

KNOWN_COLUMNS = {
    "Employees Directory": "emp_id (int), full_name (text), first_name (text), last_name (text), email (text), phone (text), hire_date (date), job_title (text), salary (number), department (text), city (text), status (text)",
    "PRODUCTS": "product_id (int), product_name (text), category (text), price (number), stock_qty (number), status (text), created_date (date)",
    "Google Map Leads": "id (int), business_name (text), niche (text), location (text), phone (text), email (text), website (text), rating (number), reviews_count (int), lead_score (int), priority (text), maps_url (text)"
}

def _match_endpoint_with_history(text: str, endpoints: List[dict], history_msgs: list = None) -> Optional[dict]:
    ep = _match_endpoint(text, endpoints)
    if ep:
        return ep
    if history_msgs:
        for m in reversed(history_msgs):
            c = m.get("content", "")
            ep_prev = _match_endpoint(c, endpoints)
            if ep_prev:
                return ep_prev
    return None

def _match_endpoint(text: str, endpoints: List[dict]) -> Optional[dict]:
    t = (text or "").lower().strip()
    if not t or not endpoints:
        return None
    for ep in endpoints:
        name = str(ep.get("endpoint_name", "")).lower()
        if name and (name == t or name in t or t in name):
            return ep
        desc = str(ep.get("description", "")).lower()
        if desc and len(desc) >= 3 and desc in t:
            return ep
        for word in name.split():
            word = word.strip().rstrip("s")
            if len(word) >= 4 and re.search(r"\b" + re.escape(word) + r"\b", t):
                # Don't match 'google' alone without map or leads
                if word == "google" and not any(k in t for k in ["map", "lead"]):
                    continue
                return ep

    # Domain synonym matching
    for ep in endpoints:
        name = str(ep.get("endpoint_name", ""))
        syns = TABLE_SYNONYMS.get(name, [])
        for s in syns:
            if re.search(r"\b" + re.escape(s) + r"\b", t) or s in t:
                return ep

    return None

def _match_endpoints_multi(text: str, endpoints: List[dict]) -> List[dict]:
    """Finds all endpoints mentioned or referenced in the text, using names, keywords, and domain synonyms."""
    t = (text or "").lower().strip()
    if not t or not endpoints:
        return []
    matched = []
    seen = set()
    for ep in endpoints:
        ep_key = str(ep.get("endpoint_id") or ep.get("endpoint_name") or ep.get("rest_url"))
        name = str(ep.get("endpoint_name", ""))
        name_lower = name.lower()
        desc = str(ep.get("description", "")).lower()
        hit = False
        if name_lower and (name_lower in t or t in name_lower):
            hit = True
        elif desc and len(desc) >= 3 and desc in t:
            hit = True
        else:
            for word in name_lower.split():
                word = word.strip().rstrip("s")
                if len(word) >= 4 and re.search(r"\b" + re.escape(word) + r"\b", t):
                    if word == "google" and not any(k in t for k in ["map", "lead"]):
                        continue
                    hit = True
                    break

        # Check domain synonyms if not matched yet
        if not hit:
            for s in TABLE_SYNONYMS.get(name, []):
                if re.search(r"\b" + re.escape(s) + r"\b", t):
                    hit = True
                    break

        if hit and ep_key not in seen:
            seen.add(ep_key)
            matched.append(ep)
    return matched


def _build_schema_catalog(endpoints: List[dict]) -> str:
    """Builds a lightweight schema catalog (<600 tokens) from endpoint metadata and cached schemas."""
    lines = []
    for ep in endpoints:
        name = str(ep.get("endpoint_name", "Data"))
        hint = str(ep.get("schema_hint") or ep.get("description") or "").strip()
        rest_url = ep.get("rest_url")
        cached = _dataset_cache.get(rest_url)
        if cached and cached[1]:
            rows = cached[1]
            first = rows[0]
            col_desc = []
            try:
                profiles = qe.extract_table_profiles(rows, max_distinct=8)
            except Exception:
                profiles = {}
            for col in first.keys():
                if col == "links": continue
                if col in profiles:
                    col_desc.append(f"{col} (sample values: {', '.join(profiles[col][:5])})")
                else:
                    col_desc.append(col)
            lines.append(f"TABLE: {name} ({len(rows)} rows)")
            if hint:
                lines.append(f"  Description: {hint}")
            lines.append(f"  Columns: {', '.join(col_desc)}")
        else:
            lines.append(f"TABLE: {name}")
            if hint:
                lines.append(f"  Description / Schema: {hint}")
            if name in KNOWN_COLUMNS:
                lines.append(f"  Columns: {KNOWN_COLUMNS[name]}")
        lines.append("")
    return "\n".join(lines).strip()

def _build_compact_schema(datasets: Dict[str, List[dict]], endpoints_meta: List[dict]) -> str:
    """
    Builds a very small schema overview (<500 tokens total) with:
      - Table name and row count
      - Column names and detected types (Number, Text, Date)
      - Up to 4 sample categorical values
      - User's schema_hint if configured
    """
    hints = {str(e.get("endpoint_name")): e.get("schema_hint") for e in endpoints_meta}
    lines = []
    for name, rows in datasets.items():
        n = len(rows)
        lines.append(f"TABLE: {name} ({n} rows)")
        hint = hints.get(name)
        if hint:
            lines.append(f"  Description: {hint.strip()}")
        if not rows:
            lines.append("  (Empty table)")
            continue
        first = rows[0]
        col_desc = []
        for col, val in first.items():
            if col == "links": continue
            # inspect first 20 rows
            sample_vals = [r.get(col) for r in rows[:20] if r.get(col) is not None]
            num_count = sum(1 for v in sample_vals if qe.parse_number(v) is not None)
            if num_count >= max(3, len(sample_vals) * 0.7):
                t_str = "number"
            elif any(qe.parse_date(v) is not None for v in sample_vals[:5]):
                t_str = "date"
            else:
                # categorical samples
                distinct = sorted({str(v) for v in sample_vals})[:4]
                t_str = f"text (e.g. {', '.join(distinct)})" if distinct else "text"
            col_desc.append(f"    - {col}: {t_str}")
        lines.extend(col_desc)
        lines.append("")
    return "\n".join(lines)

# ---------------- Authentication Endpoints ----------------
@app.post("/api/login")
def login(req: LoginRequest, request: Request, response: Response):
    client_ip = request.client.host if request.client else "?"
    key = f"{client_ip}|{req.username.lower()}"
    if _check_rate_limit(key):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in a few minutes.")

    # 1. Try secure POST auth/user endpoint (if script 02 ran)
    user_id = None
    username = None
    is_admin = "N"
    authenticated = False

    def _check_admin(u_dict: dict, uname: str) -> str:
        adm_flag = str(u_dict.get("is_admin") or u_dict.get("IS_ADMIN") or "").strip().upper()
        role_flag = str(u_dict.get("role") or u_dict.get("ROLE") or "").strip().lower()
        if adm_flag == "Y" or role_flag == "admin":
            return "Y"
        return "N"

    try:
        r_user = ords_post(URL_AUTH_USER, json={"username": req.username}, timeout=10)
        if r_user.ok and r_user.json().get("user_id"):
            u_info = r_user.json()
            stored = u_info.get("password_hash")
            if verify_password(req.password, stored):
                authenticated = True
                user_id = u_info["user_id"]
                username = u_info["username"]
                is_admin = _check_admin(u_info, username)
                # Auto-upgrade plain text to PBKDF2
                if not stored.startswith("pbkdf2_sha256$"):
                    new_hash = hash_password(req.password)
                    ords_post(URL_SET_PW, json={"user_id": user_id, "password_hash": new_hash}, timeout=5)
    except Exception:
        pass

    # 2. Fallback to existing GET auth/login (legacy compatibility).
    legacy_allowed = (not ORDS_CLIENT_ID) and os.environ.get("ALLOW_LEGACY_LOGIN", "Y").upper() == "Y"
    if legacy_allowed and not authenticated:
        try:
            candidates = [req.username]
            for variant in (req.username.capitalize(), req.username.lower(), req.username.upper()):
                if variant not in candidates:
                    candidates.append(variant)

            for cand in candidates:
                params = {"username": cand, "password": req.password}
                res = ords_get(URL_LOGIN, params=params, timeout=15)
                if not res.ok:
                    continue
                data = res.json()
                items = data.get("items", [])
                if items:
                    authenticated = True
                    user_id = items[0]["user_id"]
                    username = items[0]["username"]
                    is_admin = _check_admin(items[0], username)
                    break
        except Exception as e:
            print(f"[ORDS Login Error] {e}")
            raise HTTPException(status_code=500, detail="Database connection error. Please check ORDS connection.")

    if not authenticated:
        _record_failed_attempt(key)
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    _clear_rate_limit(key)
    # Login history
    user_agent = request.headers.get("user-agent", "")
    try:
        ords_post(URL_LOGIN_LOG, json={"user_id": user_id, "ip_address": client_ip, "user_agent": user_agent})
    except Exception:
        pass

    def _extract_perms(u_dict: dict, is_adm: str) -> dict:
        def _flag(k):
            raw = u_dict.get(k)
            if raw is None:
                raw = u_dict.get(k.upper())
            if raw is not None:
                return str(raw).strip().upper() != "N"
            # Default to True only if database column was null or not returned
            return True
        return {
            "can_search_outside": _flag("can_search_outside"),
            "can_upload": _flag("can_upload"),
            "can_view_tables": _flag("can_view_tables"),
            "can_view_docs": _flag("can_view_docs")
        }

    raw_user_dict = u_info if (authenticated and 'u_info' in locals() and u_info) else (items[0] if ('items' in locals() and items) else {})
    user_perms = _extract_perms(raw_user_dict, is_admin)

    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(SESSION_COOKIE, _make_token(user_id, username, is_admin, user_perms), httponly=True, samesite="lax",
                        secure=secure, max_age=SESSION_TTL, path="/")
    return {
        "user_id": user_id,
        "username": username,
        "is_admin": is_admin == "Y",
        "role": "admin" if is_admin == "Y" else "user",
        "permissions": user_perms
    }

@app.get("/api/me")
def me(request: Request):
    u = current_user(request)
    is_adm = (u.get("a") == "Y")
    perms = u.get("p") or {
        "can_search_outside": True,
        "can_upload": True,
        "can_view_tables": True,
        "can_view_docs": True
    }
    return {
        "user_id": u["u"],
        "username": u["n"],
        "is_admin": is_adm,
        "role": "admin" if is_adm else "user",
        "permissions": perms
    }

@app.get("/api/tables")
def get_user_tables(request: Request):
    u = current_user(request)
    is_adm = (u.get("a") == "Y")
    perms = u.get("p") or {}
    if not perms.get("can_view_tables", True):
        return {"tables": [], "count": 0}
    datasets, endpoints_meta = _get_user_datasets(u["u"])
    tables_list = []
    for ep in endpoints_meta:
        name = str(ep.get("endpoint_name", "Data"))
        rows = datasets.get(name, [])
        cols = []
        col_details = []
        if rows and isinstance(rows[0], dict):
            cols = [c for c in rows[0].keys() if c != "links"]
            for c in cols:
                t = "VARCHAR2"
                sample_vals = [r.get(c) for r in rows[:15] if r.get(c) is not None]
                if sample_vals:
                    first_val = sample_vals[0]
                    if isinstance(first_val, (int, float)):
                        t = "NUMBER"
                    elif isinstance(first_val, str):
                        s_val = first_val.strip()
                        if s_val.replace('.', '', 1).isdigit() or (s_val.startswith(('$', 'PKR', '€', '£')) and s_val[1:].replace(',', '').replace('.', '', 1).isdigit()):
                            t = "NUMBER"
                        elif re.search(r'^\d{4}-\d{2}-\d{2}|\d{2}-[A-Za-z]{3}-\d{2,4}', s_val):
                            t = "DATE"
                        elif '@' in s_val and '.' in s_val:
                            t = "EMAIL"
                
                c_upper = c.upper()
                if "ID" in c_upper or "CODE" in c_upper or c_upper == "KEY":
                    role = "Identifier"
                elif t == "NUMBER" or any(w in c_upper for w in ("AMOUNT", "SALARY", "PRICE", "QTY", "COUNT", "TOTAL")):
                    role = "Metric"
                elif t == "DATE" or "TIME" in c_upper or "DATE" in c_upper:
                    role = "Temporal"
                else:
                    role = "Dimension"

                col_details.append({
                    "name": c,
                    "type": t,
                    "role": role,
                    "status": "Active"
                })

        item = {
            "endpoint_id": ep.get("endpoint_id"),
            "name": name,
            "description": ep.get("description") or f"Enterprise dataset containing {len(cols)} schema columns.",
            "column_count": len(cols),
            "columns": col_details
        }
        tables_list.append(item)
    return {"tables": tables_list, "is_admin": is_adm}

@app.get("/api/tables/{endpoint_id}/data")
def get_table_data(endpoint_id: int, request: Request):
    raise HTTPException(status_code=403, detail="Direct raw table data inspection is restricted. Use conversational queries in chat.")

@app.post("/api/logout")
def logout(request: Request, response: Response):
    cookie_val = request.cookies.get(SESSION_COOKIE, "")
    if "." in cookie_val:
        try:
            body, sig = cookie_val.rsplit(".", 1)
            d = json.loads(base64.urlsafe_b64decode(body.encode()))
            revoke_token(sig, d.get("e", int(time.time()) + SESSION_TTL))
        except Exception:
            pass
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "success"}

# ---------------- History & Messages ----------------
def _history_of(uid: int):
    try:
        data = ords_get(f"{URL_HISTORY}/{uid}", timeout=15).json()
        return data.get("items", []) if "items" in data else []
    except Exception:
        return []

@app.get("/api/history/{user_id}")
def get_user_history(user_id: int, request: Request):
    return _history_of(current_user(request)["u"])

@app.get("/api/messages/{session_id}")
def get_session_messages(session_id: str, request: Request):
    uid = current_user(request)["u"]
    owned = {str(i.get("session_id") or i.get("SESSION_ID")) for i in _history_of(uid)}
    if session_id not in owned:
        return []
    try:
        items = ords_get(f"{URL_MESSAGES}/{session_id}", timeout=15).json().get("items", [])
        return items
    except Exception:
        return []

@app.post("/api/chat/update")
def update_chat(req: UpdateChatRequest, request: Request):
    req.user_id = current_user(request)["u"]
    payload = {
        "user_id": req.user_id,
        "session_id": req.session_id,
        "session_title": req.session_title,
        "is_pinned": req.is_pinned
    }
    ords_post(URL_UPDATE_CHAT, json=payload)
    return {"status": "success"}

# ---------------- Provider & Credit Metering API ----------------
_cached_providers: List[dict] = []
_cached_providers_time: float = 0.0

def _get_active_ai_providers() -> List[dict]:
    """
    Fetches active AI providers with valid credentials for internal server operations.
    Tries internal-only endpoint first, injects fallback keys into public endpoint if keys were scrubbed,
    and guarantees that every returned provider has a valid api_key so LLM calls NEVER fail with KeyError or 401.
    Caches valid providers in memory for 5 minutes for resilience against transient ORDS latency spikes.
    """
    global _cached_providers, _cached_providers_time
    now = time.time()
    if _cached_providers and (now - _cached_providers_time < 300):
        return _cached_providers
    try:
        r = ords_get(URL_PROVIDERS_INTERNAL, timeout=10)
        if r.ok:
            items = r.json().get("items", [])
            valid = [it for it in items if it.get("api_key") and str(it["api_key"]).strip()]
            if valid:
                _cached_providers = valid
                _cached_providers_time = now
                return valid
    except Exception as e:
        print(f"[Provider Notice] Internal providers fetch notice: {e}")

    try:
        r = ords_get(URL_PROVIDERS, timeout=8)
        if r.ok:
            items = r.json().get("items", [])
            for it in items:
                p_name = it.get("provider_name", "")
                if not it.get("api_key") or not str(it["api_key"]).strip():
                    if "Groq 2" in p_name:
                        it["api_key"] = os.environ.get("GROQ_API_KEY_2", "").strip()
                    elif "Groq" in p_name:
                        it["api_key"] = os.environ.get("GROQ_API_KEY", "").strip()
                    elif "OpenRouter" in p_name:
                        it["api_key"] = os.environ.get("OPENROUTER_API_KEY", "").strip()
            valid = [it for it in items if it.get("api_key") and str(it["api_key"]).strip()]
            if valid:
                _cached_providers = valid
                _cached_providers_time = now
                return valid
    except Exception as e:
        print(f"[Provider Notice] Public providers fetch notice: {e}")

    if _cached_providers:
        return _cached_providers

    # Resilient fallback: environment variables
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    groq_key_2 = os.environ.get("GROQ_API_KEY_2", "").strip()
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    fallback = []
    if groq_key:
        fallback.append({
            "provider_id": 1,
            "provider_name": "Groq",
            "llm_api_url": "https://api.groq.com/openai/v1/chat/completions",
            "api_key": groq_key,
            "model_name": "openai/gpt-oss-120b",
            "priority_order": 1,
            "plan_tier": "FREE",
            "daily_token_limit": 200000,
            "daily_request_limit": 1000,
            "price_in_per_m": 0.0,
            "price_out_per_m": 0.0
        })
    if groq_key_2:
        fallback.append({
            "provider_id": 2,
            "provider_name": "Groq 2",
            "llm_api_url": "https://api.groq.com/openai/v1/chat/completions",
            "api_key": groq_key_2,
            "model_name": "openai/gpt-oss-120b",
            "priority_order": 2,
            "plan_tier": "FREE",
            "daily_token_limit": 200000,
            "daily_request_limit": 1000,
            "price_in_per_m": 0.0,
            "price_out_per_m": 0.0
        })
    if openrouter_key:
        fallback.append({
            "provider_id": 3,
            "provider_name": "OpenRouter Auto-Free",
            "llm_api_url": "https://openrouter.ai/api/v1/chat/completions",
            "api_key": openrouter_key,
            "model_name": "openrouter/free",
            "priority_order": 3,
            "plan_tier": "FREE",
            "daily_token_limit": 100000,
            "daily_request_limit": 50,
            "price_in_per_m": 0.0,
            "price_out_per_m": 0.0
        })
    return fallback

@app.get("/api/providers")
def list_providers(request: Request):
    try:
        current_user(request)
    except Exception:
        pass
    providers = []
    # 1. Try new usage/providers view from ORDS
    try:
        r = ords_get(URL_USAGE_PROVIDERS, timeout=5)
        if r.ok:
            providers = r.json().get("items", [])
    except Exception:
        pass

    # 2. Fallback to basic providers endpoint
    if not providers:
        providers = _get_active_ai_providers()

    out = []
    for p in providers:
        pid = p.get("provider_id")
        p_name = p.get("provider_name")
        m_name = p.get("model_name")

        t_limit = p.get("daily_token_limit") or (200000 if "gpt-oss" in str(m_name) else None)
        r_limit = p.get("daily_request_limit") or (1000 if "gpt-oss" in str(m_name) else 50 if "openrouter" in str(p_name).lower() else None)

        used_tok = p.get("tokens_24h") or 0
        used_req = p.get("requests_24h") or 0

        left_tok = p.get("tokens_left_24h") if p.get("tokens_left_24h") is not None else (max(0, t_limit - used_tok) if t_limit else None)
        left_req = p.get("requests_left_24h") if p.get("requests_left_24h") is not None else (max(0, r_limit - used_req) if r_limit else None)

        out.append({
            "provider_id": pid,
            "provider_name": p_name,
            "model_name": m_name,
            "plan_tier": p.get("plan_tier") or "FREE",
            "daily_token_limit": t_limit,
            "daily_request_limit": r_limit,
            "tokens_left_24h": left_tok,
            "requests_left_24h": left_req,
            "price_in_per_m": p.get("price_in_per_m", 0),
            "price_out_per_m": p.get("price_out_per_m", 0)
        })
    return out

@app.get("/api/usage/summary")
def usage_summary(request: Request):
    u = current_user(request)
    uid = u["u"]
    is_admin = u.get("a") == "Y"
    if is_admin:
        try:
            r = ords_get(f"{URL_USAGE_DAILY}?days=30", timeout=5)
            if r.ok:
                return {"source": "oracle", "items": r.json().get("items", [])}
        except Exception:
            pass
    else:
        try:
            r = ords_get(f"{URL_USAGE_USER}/{uid}", timeout=5)
            if r.ok:
                return {"source": "oracle", "items": r.json().get("items", [])}
        except Exception:
            pass
    return {"source": "oracle", "items": []}

@app.get("/api/usage/top")
def usage_top(request: Request):
    u = current_user(request)
    is_admin = u.get("a") == "Y"
    if is_admin:
        try:
            r = ords_get(f"{URL_USAGE_TOP}?days=30", timeout=5)
            if r.ok:
                return {"items": r.json().get("items", [])}
        except Exception:
            pass
    return {"items": []}

# ---------------- File Request Markers & Plain Dumps ----------------
_KIND_PATTERNS = [
    ("pdf", r"(\bpdf\b)"),
    ("excel", r"(\bexcel\b|\bxlsx?\b|\bspreadsheet\b)"),
    ("word", r"(\bdocx?\b|\bdoc\b|\bword\b)"),
    ("ppt", r"(power ?point|\bpptx?\b|\bppt\b|\bslides?\b|\bpresentation\b)"),
    ("csv", r"(\bcsv\b)"),
    ("json", r"(\bjson\b)"),
]

def _asked_kinds(question: str):
    q = question.lower()
    # If the user is asking for charts, graphs, plots or visualizations,
    # "types" / "kinds" refers to chart types (bar, donut, line), NOT file exports!
    is_chart_inquiry = bool(re.search(r"\b(charts?|graphs?|plots?|visuali[sz]\w*|diagrams?)\b", q))
    has_explicit_file_ext = bool(re.search(r"\b(pdf|excel|xlsx?|word|docx?|powerpoint|pptx?|csv|zip|archive)\b", q))
    if is_chart_inquiry and not has_explicit_file_ext:
        return []

    # Explicit "all files" / "all formats" / "all reports" / "zip" request
    if (re.search(r"\b(all|tamam|saari|sab|every)\b.{0,25}\b(files?|formats?|reports?|dashboards?|exports?)\b|\ball (files?|formats?|reports?)\b|\b(files?|formats?)\b.{0,15}\b(all|tamam|saari)\b", q)
        or re.search(r"\b(all\s+files|all\s+file|all\s+formats?|all\s+reports?)\b", q)
        or re.search(r"\b(zip|archive)\b", q)):
        return ["pdf", "excel", "word", "ppt"]
    found = [k for k, pat in _KIND_PATTERNS if re.search(pat, q)]
    if not found and re.search(r"\b(export|file|files|download|report|save as)\b", q) and not is_chart_inquiry:
        return ["pdf", "excel"]
    return found

def _force_markers(question: str, answer: str) -> str:
    kinds = _asked_kinds(question)
    if not kinds:
        return answer
    body = re.sub(r"\s*\[\[EXPORT:\w+\]\]", "", answer, flags=re.I).rstrip()
    return body + "\n\n" + " ".join(f"[[EXPORT:{k}]]" for k in kinds)

_ANALYTIC = r"\b(top|bottom|highest|lowest|average|avg|total|sum|count|how many|by|per|group|charts?|graphs?|plots?|visuali[sz]\w*|summary|summari[sz]e|compare|trend|filter|where|only|active|inactive|between|more than|less than|greater|below|above)\b"

def _is_plain_dump(question: str) -> bool:
    q = question.lower().strip()
    # Charts or graphs are NEVER a plain file dump
    if re.search(r"\b(charts?|graphs?|plots?|visuali[sz]\w*|analytics?|diagrams?)\b", q):
        return False
    if re.search(_ANALYTIC, q):
        return False
    # Explicit full/all request
    if re.search(r"\b(full|all|whole|complete|entire|everything)\b", q) and not re.search(r"\b(charts?|graphs?|plots?)\b", q):
        return True
    # Export/format requested without analytical breakdown (e.g. "Give me the data as a Word document", "data in excel")
    if _asked_kinds(question) and re.search(r"\b(data|records|list|employees|products|file|report|export|give|send|show)\b", q):
        return True
    return False

def _match_dataset(text: str, datasets: dict) -> Optional[str]:
    t = (text or "").lower().strip()
    if not t:
        return None
    # 1. Exact match
    for k in datasets:
        if k.lower() == t:
            return k
    # 2. Substring or stem match (handles 'product' vs 'PRODUCTS', 'employee' vs 'Employees Directory')
    for k in datasets:
        k_lower = k.lower()
        if k_lower in t or t in k_lower:
            return k
        for word in k_lower.split():
            word = word.strip()
            if len(word) >= 4:
                stem = word.rstrip("s")
                if stem in t or any(w.startswith(stem) or stem.startswith(w) for w in t.split() if len(w) >= 4):
                    return k
    return None

_LABEL = {
    "pdf": "a PDF",
    "excel": "an Excel file",
    "word": "a Word document",
    "ppt": "a PowerPoint",
    "csv": "a CSV file",
    "json": "a JSON file",
}

def _dump_answer(name: str, rows: list, kinds: list) -> str:
    table = qe.rows_to_md_table(rows)
    ncols = table.split("\n")[0].count("|") - 1 if table else 0
    what = " and ".join(_LABEL.get(k, k.upper()) for k in kinds)
    kpis = qe.compute_kpis(rows)
    kpi_lines = ""
    if kpis:
        kpi_lines = "\n**Key Highlights:**\n" + "\n".join(f"- **{k['label']}:** {k['value']}" for k in kpis) + "\n\n"
    markers = " ".join(f"[[EXPORT:{k}]]" for k in kinds)
    return (f"Here is the verified {name.replace('_', ' ')} data prepared for export as {what}.\n\n"
            f"- **Total Records:** {len(rows)}\n- **Total Fields:** {ncols}\n"
            f"{kpi_lines}{table}\n\n{markers}\n")

def _dump_multi_answer(tables_data: list, kinds: list) -> str:
    what = " and ".join(_LABEL.get(k, k.upper()) for k in kinds)
    blocks = []
    total_records_all = sum(len(r) for _, r in tables_data)
    blocks.append(f"Here is the combined verified data prepared for export as {what}.\n\n- **Combined Tables:** {len(tables_data)}\n- **Total Combined Records:** {total_records_all}\n")
    for name, rows in tables_data:
        table_md = qe.rows_to_md_table(rows)
        ncols = table_md.split("\n")[0].count("|") - 1 if table_md else 0
        kpis = qe.compute_kpis(rows)
        kpi_lines = ""
        if kpis:
            kpi_lines = "**Key Highlights:** " + " · ".join(f"{k['label']}: {k['value']}" for k in kpis) + "\n\n"
        blocks.append(f"### {name.replace('_', ' ')}\n- **Total Records:** {len(rows)} | **Fields:** {ncols}\n\n{kpi_lines}{table_md}\n")
    markers = " ".join(f"[[EXPORT:{k}]]" for k in kinds)
    blocks.append(f"\n{markers}\n")
    return "\n".join(blocks)


# ---------------- Two-Stage Query Engine Execution (Plan -> Python Compute -> Explain) ----------------

PLANNER_SYSTEM_PROMPT = """You are the Lead Query Planner for an enterprise data warehouse.
Your job is to read the user's question, conversation history, and the schema of available tables, and output a structured JSON Query Plan.

SCHEMA SPECIFICATION:
{schema}

OUTPUT FORMAT:
Output ONLY a valid JSON object wrapped in ```json ``` code block. Do NOT write any explanations or conversational text.

PLAN JSON SCHEMA:
{{
  "target_table": "Employees Directory",
  "mode": "aggregate" | "raw" | "pivot" | "join" | "multi_table" | "direct_answer" | "clarification_needed",
  "clarification_message": "...",        // only when mode == "clarification_needed" (friendly counter-question)
  "left_table": "Employees Directory",   // when mode == "join"
  "right_table": "Departments",          // when mode == "join"
  "join_on": "department_id",            // common column key to join
  "how": "inner" | "left",
  "filters": [
    {{"col": "column_name", "op": "==" | "!=" | ">" | ">=" | "<" | "<=" | "in" | "contains" | "startswith", "val": "value"}}
  ],
  "group_by": ["department"],            // 1 to 2 columns for grouping
  "aggs": [                              // aggregations to compute
    {{"col": "*", "agg": "count", "as": "Total Employees"}},
    {{"col": "salary", "agg": "avg", "as": "Average Salary"}},
    {{"col": "salary", "agg": "sum", "as": "Total Salary"}}
  ],
  "pivot": {{                            // only when mode == "pivot" (2D breakdown)
    "row_dim": "city",
    "col_dim": "department",
    "val_dim": "salary",
    "agg": "avg"
  }},
  "select_cols": ["name", "salary"],    // for mode == "raw" or "join"
  "order_by": [{{"col": "Total Salary", "desc": true}}],
  "limit": 10,                           // e.g. 5 for Top 5
  "chart": {{
    "type": "bar" | "donut" | "line" | "rose" | "stacked_bar",
    "title": "Short Descriptive Title",
    "cat_col": "department",
    "val_cols": ["Total Salary"]
  }},
  "external_tools": [                    // Parallel external intelligence tools (run concurrently)
    {{"tool": "weather", "city": "Faisalabad"}},
    {{"tool": "web_search", "query": "live Pakistan yarn rates today"}},
    {{"tool": "forex", "pair": "USD/PKR"}},
    {{"tool": "stock", "symbol": "ILP"}}
  ]
}}

RULES:
1. If the user question is a greeting, asks what tables are available, asks how many tables you have access to, or does not require a database query, output: {{"mode": "direct_answer"}}
2. If the user asks to see records, list items, all records, or what is in a specific table (e.g. "all employees data", "show products", "list employees", "what is in employees table", "give me the g map table data", "show table"):
   - Set "mode": "raw", set "target_table" to that table.
   - Do NOT set an artificial limit! Leave "limit": null so that ALL authorized records in the table are returned without truncation.
   - Do NOT set "select_cols" to a subset unless specifically requested! Leave "select_cols": null so that ALL columns of the table are preserved. The UI has an automatic Column Questionnaire Card for wide tables (10+ columns). Truncating columns on your own prevents this feature from working!
   - ONLY set "limit": N or "select_cols" if the user explicitly requested specific row counts or column names.
3. HIGHEST / LOWEST / TOP / BEST / MINIMUM / MAXIMUM:
   When asked for the highest, maximum, lowest, minimum, top, or best of an entity (e.g. "what is the highest salary", "top employee", "highest salary today", "cheapest product", "who has highest salary"):
   - Set "target_table" to the relevant table (e.g. "Employees Directory", "PRODUCTS").
   - Set "mode": "raw"
   - Set "order_by": [{"col": "salary", "desc": true}] (desc: true for highest/top, desc: false for lowest/cheapest).
   - Set "limit": 5 (this ensures the top 5 records and all their context like employee name, job title, department are loaded and available for the synthesizer).
   If the question asks for Top N or Bottom N (e.g. "top 3", "first 10"), set "limit": N. If the user asks for "all" or does not specify a number, leave "limit": null.
4. If comparing categories or breakdowns, use "mode": "aggregate" with appropriate "group_by" and "aggs".
5. If comparing 3+ categories or shares of a whole, ALWAYS include a "chart" definition. Use "donut" for share of whole, "bar" for comparison, "line" for trends over time.
6. CHARTS & CATEGORIES: ALWAYS use descriptive human-readable name columns (such as 'full_name', 'product_name', 'business_name', 'name', 'department', 'category', 'city') for "cat_col", "group_by", and chart axes. NEVER use raw numeric IDs ('emp_id', 'product_id', 'id', 'dept_id') as "cat_col".
7. CHART METRICS: NEVER put ID or code columns ('dept_id', 'emp_id', 'product_id', 'id', 'zip', 'phone') into "val_cols". Only include true numeric metrics like 'salary', 'price', 'stock_qty', 'rating', 'total'.
7. Choose "target_table" precisely from the tables listed in SCHEMA SPECIFICATION (e.g. "Employees Directory", "PRODUCTS"). Look at the columns of each table to pick the right one for the question.
8. CONVERSATION CONTEXT & FOLLOW-UPS: If the user refers to previous context (e.g. "filter them by Karachi", "show that as a chart", "what about their salary", "based on this table", "all type of charts based on this table", "this table", "show another chart"):
   - Inspect the conversation history to identify the table last analyzed (e.g. "PRODUCTS" or "Employees Directory") and set it as "target_table".
   - NEVER randomly switch to an unrelated table.
9. MULTI-TABLE QUERIES: If the user asks to see, compare, or combine multiple tables in a single query (e.g. "combine employee and product table", "show employee table and then product table", "show both tables"):
   - Set "mode": "multi_table"
   - Set "target_tables": ["Employees Directory", "PRODUCTS"]
10. CHARTS & VISUALIZATIONS: If the user asks for a chart, graph, or visual analysis (e.g. "show a chart of products", "all types of charts", "graphs", "i say charts and the graphs"):
   - ALWAYS set "mode": "aggregate" (grouping by a descriptive column like 'category', 'department', 'city') or "pivot".
   - ALWAYS include a complete "chart" object with type, title, cat_col, and val_cols.

11. COLUMN SELECTION / CARRY SPECIFIC COLUMNS: If the user asks to carry or show specific columns (e.g. "show only 3 columns", "carry columns: Emp Id, First Name, Salary", "with only these columns: ...", "first 5 columns"):
   - Set "mode": "raw"
   - Set "select_cols" to the exact list of requested column names (or the first N columns of the target table).
   - Leave "limit": null unless a specific row limit was requested.

12. MULTI-TABLE JOINS: When the user asks to correlate, join, or compare two tables on a shared column (e.g. "join employees and departments", "show employee names with department budget", "compare yarn inventory with finance ledger"):
   - Set "mode": "join"

13. EXECUTIVE SUMMARY / OVERVIEW / AUDIT: When the user asks for a "complete executive summary", "company overview", "audit report", or "executive summary as PDF":
   - Set "mode": "multi_table"
   - Set "target_tables": ["Employees Directory", "PRODUCTS", "Google Map Leads"]
   - This ensures verified data across all authorized operational divisions is loaded into memory so the executive summary is authentic, data-driven, and complete.
   - Set "left_table": "Table 1", "right_table": "Table 2"
   - Set "join_on": "common_id_column" (e.g. 'dept_id', 'department_id', 'supplier_id', 'item_id')
   - Set "group_by", "aggs", or "select_cols" and "chart" as needed.

13. HYBRID & UNIVERSAL WEB / EXTERNAL INTELLIGENCE TOOLS: If the question asks for real-time external information (e.g. open web search, browsing arbitrary webpage URLs, weather forecast, currency rates, or stock price) either on its own or COMBINED with an enterprise table:
   - If the user provides a link (URL) or asks to read a website: include {{"tool": "browse_url", "url": "..."}} in "external_tools".
   - If the question asks for live web discovery, news, or general knowledge: include {{"tool": "web_search", "query": "..."}} in "external_tools".
   - If weather, currency, or stocks: include {{"tool": "weather", "city": "..."}}, {{"tool": "forex", "pair": "..."}}, {{"tool": "stock", "symbol": "..."}}.
   - You can combine enterprise database analysis with live open-web tools in a single execution plan!

14. REAL-WORLD, COMMODITIES, FUEL & NEWS INQUIRIES: When the user asks about real-world topics, petrol/diesel/fuel rates, gold/silver prices, commodities, inflation, weather, forex/currency, stock market, sports, world events, or anything outside enterprise warehouse tables:
   - Set "mode": "direct_answer"
   - NEVER select an internal database table (do NOT assign "PRODUCTS" or "Employees Directory" to fuel, gold, or weather inquiries)!
   - ALWAYS include {{"tool": "web_search", "query": "..."}} in "external_tools".

15. CONVERSATIONAL TABLE CLARIFICATION (STOP GUESSING TABLES):
- When a user gives an ambiguous visualization, data exploration, or file export command (e.g. "Give me the data as a PDF:", "export to PDF", "download as excel", "word document", "show a chart", "draw a graph", "plot this", "visualize", "export to ppt", "give me data", "show data"):
- If the current message text does NOT explicitly name a valid database table (EVEN IF a table was looked at previously in conversation history):
   - You MUST treat the query as ambiguous and stop guessing entirely!
   - NEVER default to or assume "Employees Directory", "PRODUCTS", or any prior table!
   - Set "mode": "clarification_needed"
   - Set "target_table": null
   - Populate "clarification_message" with: "Which specific dataset would you like me to compile into your PDF document?" (adjusting "PDF" to Excel, Word, or PowerPoint if another format was requested) followed by the structural bullet list of available tables.
"""

PLANNER_ROUTING_SYSTEM_PROMPT = PLANNER_SYSTEM_PROMPT


def _get_synthesizer_prompt() -> str:
    company = _get_app_config().get("company_name", "Enterprise AI")
    return f"""You are the senior enterprise data analyst for {company}.
You deliver direct, concise, high-value answers from verified enterprise data.

STRICT BREVITY & QUALITY RULES:
1. EXECUTIVE SUMMARY: Begin immediately with a crisp, authentic, and professional executive summary paragraph addressing the question directly. Use clear, simple, executive-grade language with zero filler or robotic phrases.
2. TEXT-FIRST & CLEAN MARKDOWN TABLES (NO CARD OVERLOAD):
- Deliver your answer primarily in crisp, readable text and clean Markdown tables.
- DO NOT overuse cards, bento blocks, or container boxes. Do NOT wrap every single figure or metric in box cards.
- Only output :::kpi-deck when the user explicitly requests an executive KPI dashboard or metric cards.
- Present data records, comparisons, or financial figures strictly inside a clean Markdown table. Let the table and text speak for themselves.
3. MATHEMATICAL TRUTH: Use the exact verified numbers and table provided. Never guess or modify numbers.
4. DATA IN TABLE FORM: When the user asks for data, records, or a table, you MUST present the data strictly inside a clean Markdown table. NEVER dump records as free-form plain text paragraphs, outreach templates, or pipe-separated lines outside the table. Let the table speak for itself. Keep textual explanation under 4 sentences total.
5. ECHARTS & CHARTS: If chart JSON is provided in the prompt or requested, output it in a ```json ``` code block after the summary. Output STRICT VALID JSON: never include comments, never format numbers with commas inside JSON values (e.g. use 1115000, NEVER 1,115,000), and always use double quotes for property names.
6. FILE EXPORTS & PDF GENERATION: The system features full built-in client-side PDF, Excel, Word, and PowerPoint generation. NEVER tell the user that PDF generation or export is unsupported! When the user requests an executive summary or report as PDF, provide a comprehensive, polished executive summary with key findings and metrics. The system automatically attaches the interactive PDF download card.
7. LANGUAGE & VISION: If images are attached, provide a direct, concise analysis of the image. Respond in the user's language (English, Urdu script, or Roman Urdu).
8. STYLE & NUMBERING: DO NOT use emoji keycaps (such as 1️⃣, 2️⃣, 3️⃣, 4️⃣) or colored box emojis. Use clean standard markdown numbering ("1.", "2.") or standard bold headings.
9. Never mention backend steps, SQL, planner, Python, or system mechanics.
10. STRICT DATE VALIDATION & RECENCY PRIORITY:
- For volatile real-world data (such as petrol/diesel/fuel prices, gold rates, exchange rates, commodities, or inflation):
  - Always extract and quote the rate associated with the most recent date or timestamp header found in the search context.
  - STRICTLY IGNORE historical range boundaries (such as old ranges like Rs. 253 - Rs. 275) if a definitive, single active price string (such as Rs. 369.06 or Rs. 374.51) is present in the latest search snippets.
  - State the definitive single active rate clearly and unequivocally."""

def _get_general_synthesizer_prompt() -> str:
    company = _get_app_config().get("company_name", "Enterprise AI")
    return f"""You are Enterprise AI Hub's elite, dynamic intelligence assistant for {company}.
You operate with advanced multi-modal reasoning, real-time external intelligence feeds, and enterprise visual analytics.

CORE REASONING & PRESENTATION PRINCIPLES:
1. TEXT-FIRST & DIRECT ANSWERS (NO CARD OVERLOAD): Answer the user directly in authoritative, well-formatted text and clean Markdown tables. Avoid redundant cards, bento boxes, or excessive UI container blocks.
2. CLEAN TABLES FOR TABULAR DATA: When presenting prices, exchange rates, fuel rates, or comparative numbers, format them in a single clean Markdown table followed by concise key takeaways.
3. INTENT DECOMPOSITION: If the user's inquiry contains multiple parts or intents (e.g., currency rate AND stock market price, or calculation AND company analysis), break down each component thoughtfully. Provide structured, dedicated sections or clear bullet points so every part of the question is answered thoroughly and accurately.
2. REAL-TIME FACTUAL BENCHMARKS: When provided with verified live feeds (Interbank Forex, Central Bank Benchmarks, Pakistan Stock Exchange PSX, Global Equities, Weather, or Firecrawl Web Intelligence), treat these numbers as authoritative ground truth. Clearly cite exact values, currency symbols (PKR, USD, etc.), and timestamps.
3. GROUND TRUTH FROM USER IMAGES: When the user attaches an image or screenshot (such as an exchange trading screen, chart, ERP interface, or invoice), the visual evidence is the user's direct ground truth. Inspect the image with extreme precision: read exact ticker symbols, trading prices, dates, percentages, and labels shown in the screenshot. If there is any variance with older cached feeds, explicitly acknowledge and validate the user's screenshot as the primary snapshot.
4. INTELLECTUAL DEPTH & CONTEXT: Do not output robotic, unthinking, single-sentence answers. Provide rich, dynamic context (e.g., market trading hours, interbank vs open-market margins, sector trends, company background, and price implications).
5. ZERO HALLUCINATION & TRANSPARENCY: Never guess or invent ticker prices. If an external entity is unlisted or requires verification, explain clearly using the provided live search and exchange data.
6. LANGUAGE: Respond fluidly in the user's language (English, Urdu script, or Roman Urdu) with professional business clarity.
7. CHARTS & DATA: If the user requests a chart or graph, provide an ECharts JSON configuration in a ```json ``` code block with "title", "xAxis", "yAxis", and "series". Ensure strict valid JSON without comma-separated numbers (use 1250000 not 1,250,000).
8. STRICT DATE VALIDATION & RECENCY PRIORITY:
- For volatile real-world data (such as petrol/diesel/fuel prices, gold rates, exchange rates, commodities, or inflation):
  - Always extract and quote the rate associated with the most recent date or timestamp header found in the search context.
  - STRICTLY IGNORE historical range boundaries (such as old ranges like Rs. 253 - Rs. 275) if a definitive, single active price string (such as Rs. 369.06 or Rs. 374.51) is present in the latest search snippets.
  - State the definitive single active rate clearly and unequivocally."""

SYNTHESIZER_SYSTEM_PROMPT = _get_synthesizer_prompt()

def _call_llm(provider: dict, messages: list, max_tokens: int = 1200, temperature: float = 0.0) -> Tuple[Optional[str], dict, dict]:
    """Calls Groq / OpenRouter. Returns (content, usage_dict, rate_limit_headers)."""
    api_key = provider.get("api_key") or os.environ.get("GROQ_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if "openrouter" in provider.get("llm_api_url", "").lower():
        headers["HTTP-Referer"] = "https://enterprise-ai.internal"
        headers["X-Title"] = "Enterprise AI Hub"

    payload = {
        "model": provider.get("model_name", "openai/gpt-oss-120b"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    t0 = time.time()
    res = requests.post(provider.get("llm_api_url", "https://api.groq.com/openai/v1/chat/completions"), headers=headers, json=payload, timeout=60)
    latency_ms = int((time.time() - t0) * 1000)
    if not res.ok:
        print(f"[LLM Failure] {provider.get('provider_name')} HTTP {res.status_code}: {res.text[:300]}")
    res.raise_for_status()
    data = res.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    rl_headers = {k.lower(): v for k, v in res.headers.items() if k.lower().startswith("x-ratelimit-")}
    usage["latency_ms"] = latency_ms
    return content, usage, rl_headers

def _ensure_image_base64_for_llm(img_url: str) -> str:
    """Ensures an image URL is in data:image/...;base64 format for vision LLM consumption."""
    if not img_url:
        return ""
    if img_url.startswith("data:image"):
        return img_url
    # If it's a Google Drive link or /api/gdrive/file/{id}
    m = re.search(r'(?:file/d/|id=|/file/)([a-zA-Z0-9_-]{20,})', img_url)
    if m:
        file_id = m.group(1)
        try:
            from agent.gdrive_storage import get_drive_service
            from googleapiclient.http import MediaIoBaseDownload
            service = get_drive_service()
            if service:
                req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, req)
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                fh.seek(0)
                b64 = base64.b64encode(fh.read()).decode("utf-8")
                return f"data:image/jpeg;base64,{b64}"
        except Exception as e:
            print(f"[Vision Helper Error fetching GDrive image {file_id}] {e}")
    if img_url.startswith("/static/"):
        try:
            local_rel = img_url.replace("/static/", "", 1).lstrip("/\\")
            local_path = os.path.join(STATIC_DIR, local_rel)
            if os.path.exists(local_path):
                with open(local_path, "rb") as f:
                    content = f.read()
                b64 = base64.b64encode(content).decode("utf-8")
                return f"data:image/jpeg;base64,{b64}"
        except Exception as e:
            print(f"[Vision Helper Error reading local static image {img_url}] {e}")
    if img_url.startswith("http://") or img_url.startswith("https://"):
        try:
            r = requests.get(img_url, timeout=8)
            if r.ok and r.content:
                ct = r.headers.get("content-type") or "image/jpeg"
                b64 = base64.b64encode(r.content).decode("utf-8")
                return f"data:{ct};base64,{b64}"
        except Exception:
            pass
    return img_url



def is_pure_math_expression(text: str) -> bool:
    """Detects if user query is a mathematical equation or arithmetic calculation."""
    t = text.strip()
    t_clean = re.sub(r"^(?:calculate|compute|solve|what is|whats|what's|evaluate)\s+", "", t, flags=re.I).strip()
    if "=" in t_clean:
        t_clean = t_clean.split("=", 1)[0].strip()
    t_clean = t_clean.rstrip("?=").strip()
    if re.fullmatch(r"[\d\s\+\-\*/\%\^\(\)\.\,e]+", t_clean, flags=re.I) and any(c in t_clean for c in "+-*/%^"):
        return True
    return False

def evaluate_math_expression(text: str) -> Optional[dict]:
    """Safely and deterministically evaluates an arithmetic expression in Python with zero hallucination."""
    import ast
    import operator as op

    t = text.strip()
    t_clean = re.sub(r"^(?:calculate|compute|solve|what is|whats|what's|evaluate)\s+", "", t, flags=re.I).strip()
    if "=" in t_clean:
        t_clean = t_clean.split("=", 1)[0].strip()
    t_clean = t_clean.rstrip("?=").strip()

    # Support implicit multiplication like 5(5) -> 5*(5) or (2)(3) -> (2)*(3)
    t_clean = re.sub(r"(\d)\s*\(", r"\1*(", t_clean)
    t_clean = re.sub(r"\)\s*\(", r")*(", t_clean)
    t_clean = re.sub(r"\)\s*(\d)", r")*\1", t_clean)
    t_clean = t_clean.replace("^", "**").replace(",", "")

    operators = {
        ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
        ast.Div: op.truediv, ast.FloorDiv: op.floordiv,
        ast.Mod: op.mod, ast.Pow: op.pow, ast.USub: op.neg, ast.UAdd: op.pos
    }

    def _eval(node):
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.BinOp):
            return operators[type(node.op)](_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp):
            return operators[type(node.op)](_eval(node.operand))
        raise TypeError(node)

    try:
        parsed = ast.parse(t_clean, mode="eval")
        result = _eval(parsed.body)
        return {"expr": t, "eval": result}
    except Exception:
        return None

logger = logging.getLogger("EnterpriseAI.Orchestrator")


async def execute_tool_task(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """Autonomous worker dispatching external real-time tool calls concurrently."""
    tool_type = tool_call.get("tool")
    try:
        from agent.external_intelligence import (
            fetch_live_forex,
            fetch_psx_indices,
            fetch_stock_quote,
            fetch_live_fuel_prices,
        )
        from agent.weather_helper import fetch_live_weather
        from agent.web_agent import universal_internet_agent_async

        if tool_type == "web_search":
            query = tool_call.get("query", "").strip()
            if not query:
                return {"tool": tool_type, "success": False, "error": "Empty query"}
            res = await universal_internet_agent_async(
                search_queries=[query],
                direct_urls=[],
                include_news=True,
                max_pages_to_read=2
            )
            return {"tool": tool_type, "success": True, "data": res}

        elif tool_type in ("browse_url", "browse", "read_url"):
            url = tool_call.get("url", "").strip()
            res = await universal_internet_agent_async(
                search_queries=[],
                direct_urls=[url],
                include_news=False,
                max_pages_to_read=1
            )
            return {"tool": tool_type, "success": True, "data": res}

        elif tool_type == "weather":
            city = tool_call.get("city", "Lahore")
            data = await asyncio.to_thread(fetch_live_weather, city)
            return {"tool": tool_type, "success": bool(data), "data": data}

        elif tool_type == "forex":
            pair = tool_call.get("pair", "USD/PKR")
            base = pair.split("/")[0].strip().upper() if "/" in pair else "USD"
            data = await asyncio.to_thread(fetch_live_forex, base)
            return {"tool": tool_type, "success": bool(data), "data": data}

        elif tool_type == "stock":
            symbol = tool_call.get("symbol", "").strip().upper()
            if symbol in ("PSX", "PSX_INDEX", "KSE100"):
                data = await asyncio.to_thread(fetch_psx_indices)
            else:
                data = await asyncio.to_thread(fetch_stock_quote, symbol)
            return {"tool": tool_type, "success": bool(data), "data": data}

        elif tool_type == "fuel":
            country = tool_call.get("country", "Pakistan")
            data = await asyncio.to_thread(fetch_live_fuel_prices, country)
            return {"tool": tool_type, "success": bool(data), "data": data}

    except Exception as exc:
        logger.error(f"Error executing external tool '{tool_type}': {exc}", exc_info=True)
        return {"tool": tool_type, "success": False, "error": str(exc)}

    return {"tool": tool_type, "success": False, "error": f"Unknown tool: {tool_type}"}


async def run_hybrid_orchestrator(
    plan: Optional[Dict[str, Any]],
    question: str,
    endpoints: List[Dict[str, Any]],
    emit_func: Any,
    db_executor_func: Optional[Any] = None,
    general_ai_enabled: bool = False,
    user_id: Optional[str] = None,
    history_msgs: Optional[List[Dict[str, Any]]] = None,
    client_country: Optional[str] = None,
    client_city: Optional[str] = None
) -> Dict[str, Any]:
    """
    Stage 2 Concurrent Execution Orchestrator:
    Concurrently coordinates DuckDB in-memory database queries and external tool discovery via asyncio.gather.

    PERIMETER & TOGGLE ENFORCEMENT:
    - If general_ai_enabled is True: auto-injects real-world web/commodity tools when needed and runs external tools concurrently.
    - If general_ai_enabled is False: strictly forbids any external web tool fallbacks and forces execution to stay inside local database scope.
    """
    from agent.external_intelligence import (
        extract_forex_intent,
        extract_stock_intent,
        extract_fuel_intent,
    )
    from agent.weather_helper import extract_weather_intent

    plan = plan or {}
    tasks: Dict[str, asyncio.Task] = {}

    # 1. Evaluate DuckDB Analytical Execution Intent
    has_db_intent = (
        plan.get("mode") in ("raw", "aggregate", "pivot", "join", "multi_table")
        and bool(plan.get("target_table") or plan.get("target_tables") or plan.get("left_table"))
    )

    if has_db_intent and db_executor_func is not None:
        emit_func("Executing high-speed in-memory DuckDB query across authorized dataset...")
        tasks["db_task"] = asyncio.create_task(
            asyncio.to_thread(db_executor_func, plan, endpoints, emit_func)
        )

    # 2. Evaluate External Tool / Web Search Intent strictly gated by general_ai_enabled
    external_tool_calls: List[Dict[str, Any]] = []

    if general_ai_enabled:
        external_tool_calls = list(plan.get("external_tools") or [])

        # Safety condition: Auto-inject real-world web/commodity tools ONLY if general_ai_enabled is True
        is_realworld_query = not has_db_intent or any(
            kw in question.lower()
            for kw in [
                "latest", "current", "release", "version", "update", "today",
                "news", "price", "vs", "competitor", "online", "weather",
                "forecast", "dollar", "usd", "pkr", "rate", "stock", "petrol", "fuel"
            ]
        )

        if is_realworld_query and not any(t.get("tool") == "web_search" for t in external_tool_calls):
            fuel_int = extract_fuel_intent(question)
            forex_int = extract_forex_intent(question)
            stock_int = extract_stock_intent(question, history_msgs or [])
            weather_int = extract_weather_intent(question)

            if fuel_int:
                cntry = fuel_int.get("country") or client_country or "Pakistan"
                external_tool_calls.append({"tool": "fuel", "country": cntry})
            elif forex_int:
                external_tool_calls.append({"tool": "forex", "pair": f"{forex_int.get('base','USD')}/{forex_int.get('target','PKR')}"})
            elif stock_int:
                external_tool_calls.append({"tool": "stock", "symbol": stock_int})
            elif weather_int:
                c_w = weather_int or client_city or "Lahore"
                external_tool_calls.append({"tool": "weather", "city": c_w})
            elif not has_db_intent:
                external_tool_calls.append({"tool": "web_search", "query": question})
    else:
        # STRICT PERIMETER: If general_ai_enabled is False, strictly forbid any external web tool fallbacks
        # and force the execution plan to stay contained inside the local database scope.
        external_tool_calls = []
        if plan:
            plan["external_tools"] = []
            if plan.get("mode") == "direct_answer" and not plan.get("target_table"):
                matched_ep = _match_endpoint(question, endpoints)
                if matched_ep:
                    plan["target_table"] = matched_ep.get("endpoint_name")
                    plan["mode"] = "raw"

    # 3. Dispatch external tool tasks concurrently
    for idx, tool_call in enumerate(external_tool_calls):
        tool_name = tool_call.get("tool", "external_tool")
        emit_func(f"Dispatching concurrent live tool discovery [{tool_name}]...")
        tasks[f"tool_{idx}"] = asyncio.create_task(execute_tool_task(tool_call))

    # 4. Concurrent Await with Exception Isolation
    if tasks:
        results_list = await asyncio.gather(*tasks.values(), return_exceptions=True)
        named_results = dict(zip(tasks.keys(), results_list))
    else:
        named_results = {}

    # 5. Extract and Assemble Context
    db_result = named_results.get("db_task")
    if isinstance(db_result, Exception):
        logger.error(f"DuckDB execution error: {db_result}")
        db_result = {"error": str(db_result), "table_md": "", "row_count": 0}

    external_contexts: List[str] = []
    specialized_widgets: Dict[str, Any] = {}

    for k, res in named_results.items():
        if k == "db_task" or isinstance(res, Exception) or not isinstance(res, dict) or not res.get("success"):
            continue
        tool_type = res.get("tool")
        tool_data = res.get("data")
        if tool_type in ("web_search", "browse_url") and tool_data:
            ctx = tool_data.get("synthesized_context", "")
            if ctx:
                external_contexts.append(ctx)
        elif tool_type == "weather" and tool_data:
            specialized_widgets["weather"] = tool_data
        elif tool_type == "forex" and tool_data:
            specialized_widgets["forex"] = tool_data
        elif tool_type == "stock" and tool_data:
            specialized_widgets["stock"] = tool_data
        elif tool_type == "fuel" and tool_data:
            specialized_widgets["fuel"] = tool_data

    return {
        "db_result": db_result,
        "external_contexts": external_contexts,
        "synthesized_web_context": "\n\n".join(external_contexts),
        "specialized_widgets": specialized_widgets,
        "tool_results": named_results
    }


def _run_chat(req: ChatRequest, cancelled: threading.Event, emit):
    t_start = time.time()
    kpis_out = []

    weather_info = None

    forex_card_data = None

    stock_card_data = None

    firecrawl_result_data = None

    web_agent_data = None

    exec_result = None

    provider_used = None

    total_tokens_used = 0

    has_images = bool(req.images and len(req.images) > 0)
    has_docs = bool(req.documents and len(req.documents) > 0)
    req_outside_enabled = bool(req.outside_db or req.allow_general)
    is_outside_db = req_outside_enabled
    emit("Analyzing user inquiry & reasoning across data sources...")
    endpoints = _get_user_active_endpoints(req.user_id)


    from agent.external_intelligence import (
        extract_forex_intent, fetch_live_forex, format_forex_payload,
        extract_stock_intent, fetch_psx_indices, fetch_stock_quote,
        extract_fuel_intent, fetch_live_fuel_prices
    )
    from agent.weather_helper import extract_weather_intent, fetch_live_weather

    from agent.firecrawl_intelligence import (
        detect_firecrawl_intent, scrape_url, search_web, crawl_site, extract_data,
        format_firecrawl_result
    )
    from agent.web_agent import (
        universal_internet_agent,
        extract_urls_from_text,
        universal_web_search,
        read_any_url
    )

    # Enforce can_search_outside permission from user session
    u_sess = getattr(req, "_user", None)
    if u_sess and isinstance(u_sess, dict):
        u_perms = u_sess.get("p") or {}
        if not u_perms.get("can_search_outside", True):
            req.outside_db = False
            req.allow_general = False

    depth = (req.response_depth or "medium").lower().strip()
    if depth == "low":
        synth_max_tokens = 1200
        depth_prompt = (
            "\n\n[RESPONSE DEPTH: LOW (CONCISE & TOKEN-EFFICIENT)]\n"
            "Keep response ultra-concise, direct, and under 120 words. Deliver facts and numbers immediately with zero filler."
        )
    elif depth == "high":
        synth_max_tokens = 4500
        depth_prompt = (
            "\n\n[RESPONSE DEPTH: HIGH (DEEP REASONING)]\n"
            "Provide an in-depth, thorough breakdown. Explain trends, compare data, provide comprehensive analytical reasoning and actionable business takeaways."
        )
    else:
        synth_max_tokens = 2800
        depth_prompt = (
            "\n\n[RESPONSE DEPTH: MEDIUM (BALANCED)]\n"
            "Provide a balanced, professional enterprise response with clear table summaries and essential takeaways."
        )

    # Load recent conversation history if 'remember' is enabled
    history_msgs = []
    if req.remember:
        try:
            h_data = ords_get(f"{URL_MESSAGES}/{req.session_id}", timeout=5).json()
            items = h_data.get("items", []) if isinstance(h_data, dict) else []
            max_turns = 4 if depth == "low" else 8
            for m in items[-max_turns:]:
                role = "user" if (m.get("sender_role") or "").lower() == "user" else "assistant"
                content = str(m.get("content") or "").strip()
                if not content:
                    continue
                content = re.sub(r"data:image\/[a-zA-Z]+;base64,[A-Za-z0-9+/=]+", "[attached-image]", content)
                content = re.sub(r"\[\[EXPORT:\w+\]\]", "", content).strip()
                content = re.sub(r":::google-weather[\s\S]*?:::", "[weather-widget]", content)
                content = re.sub(r":::currency-converter[\s\S]*?:::", "[currency-widget]", content)
                content = re.sub(r":::stock-market-card[\s\S]*?:::", "[stock-widget]", content)
                max_c = 280 if depth == "low" else 600
                if len(content) > max_c:
                    content = content[:max_c] + "..."
                history_msgs.append({"role": role, "content": content})
        except Exception:
            history_msgs = []

    # STRICT PERIMETER: Enforce General AI / Outside DB toggle
    # Outside intents are strictly gated by req_outside_enabled (General AI toggle state).
    # If the user has General AI turned OFF, NEVER override or auto-enable external discovery.
    c_country = (req.client_country or "").strip()
    c_city = (req.client_city or "").strip()

    loc_keywords = [
        "gold", "price", "prices", "fuel", "petrol", "diesel", "gas",
        "rate", "rates", "weather", "forecast", "temperature",
        "inflation", "silver", "commodity", "commodities"
    ]
    has_loc_kw = any(re.search(r"\b" + re.escape(k) + r"\b", req.question.lower()) for k in loc_keywords)

    known_countries = [
        "pakistan", "india", "usa", "us", "united states", "uk", "united kingdom",
        "uae", "dubai", "saudi", "saudi arabia", "canada", "australia", "germany",
        "china", "japan", "qatar", "kuwait", "oman", "turkey", "singapore",
        "malaysia", "france", "italy", "bangladesh"
    ]
    has_country = any(re.search(r"\b" + re.escape(c) + r"\b", req.question.lower()) for c in known_countries)
    if c_country and re.search(r"\b" + re.escape(c_country.lower()) + r"\b", req.question.lower()):
        has_country = True

    orch_question = req.question
    if has_loc_kw and not has_country and (c_country or c_city):
        loc_parts = [p for p in [c_city, c_country] if p]
        if loc_parts:
            loc_str = ", ".join(loc_parts)
            orch_question = f"{req.question} in {loc_str}"
            logger.info(f"[Geo Localization] Auto-localized query: '{req.question}' -> '{orch_question}'")

    if req_outside_enabled:
        can_search_ext = True
        is_outside_db = True
        forex_intent = extract_forex_intent(orch_question)
        stock_intent = extract_stock_intent(orch_question, history_msgs)
        weather_city = extract_weather_intent(orch_question)
        fuel_intent = extract_fuel_intent(orch_question)
        firecrawl_intent = detect_firecrawl_intent(orch_question)
        direct_urls = extract_urls_from_text(orch_question)
    else:
        can_search_ext = False
        is_outside_db = False
        forex_intent = None
        stock_intent = None
        weather_city = None
        fuel_intent = None
        firecrawl_intent = None
        direct_urls = []

    # If General AI is strictly OFF, enforce internal database isolation
    if not req_outside_enabled and not has_images and not has_docs and not is_pure_math_expression(req.question):
        chk_weather = extract_weather_intent(req.question)
        chk_forex = extract_forex_intent(req.question)
        chk_stock = extract_stock_intent(req.question, history_msgs)
        chk_fuel = extract_fuel_intent(req.question)
        chk_urls = extract_urls_from_text(req.question)
        chk_matched = _match_endpoint(req.question, endpoints)
        has_ext_kw = any(k in req.question.lower() for k in [
            "weather", "forecast", "temperature", "rain", "climate", "dollar", "usd", "pkr", "forex",
            "exchange rate", "currency", "stock price", "psx", "kse", "nasdaq", "crypto", "bitcoin",
            "president", "prime minister", "celebrity", "cricket", "who is", "what is the capital",
            "petrol", "diesel", "fuel", "gas price", "gold price", "silver price"
        ])
        is_outside_inquiry = bool((chk_weather or chk_forex or chk_stock or chk_fuel or chk_urls or has_ext_kw) and not chk_matched)
        if is_outside_inquiry:
            pol_msg = (
                "### 🔒 Internal Database Mode Active (General AI is Switched Off)\n\n"
                "The **General AI** option is currently turned **OFF**. In this mode, the assistant is strictly locked to your authorized enterprise database tables:\n"
                "- **Employees Directory** (Staff details, designations, salaries, departments)\n"
                "- **PRODUCTS** (Products, pricing, stock, categories)\n"
                "- **Google Map Leads** (Business leads, contact information, ratings)\n\n"
                "External live data (such as live weather forecasts, currency/forex exchange rates, stock market updates, and internet search) is disabled.\n\n"
                "👉 **To get live weather, dollar/forex rates, stock prices, or browse the web, please turn General AI ON in the bottom toolbar.**"
            )
            emit("General AI is switched off — strictly restricted to internal database tables.")
            _save_and_record(req, pol_msg, t_start, provider_used="Security Policy Guard", total_tokens=0)
            return {
                "answer": pol_msg,
                "provider_used": "Security Policy Guard",
                "kpis": [],
                "meta_stats": {"tokens": 0, "ms": int((time.time() - t_start) * 1000), "cost": 0}
            }

    if not endpoints and not is_outside_db:
        return {
            "answer": "No authorized data sources were found for your user account. Please check user permissions in Oracle APEX.",
            "provider_used": "System",
            "meta_stats": {"tokens": 0, "ms": int((time.time() - t_start) * 1000), "cost": 0}
        }


    # Fast path: instant mathematical calculation (0 tokens, 0ms web latency, 100% mathematical truth)
    if is_pure_math_expression(req.question):
        math_eval = evaluate_math_expression(req.question)
        if math_eval is not None:
            emit("Computing exact mathematical equation with zero hallucination…")
            val = math_eval["eval"]
            val_str = f"{int(val):,}" if isinstance(val, (int, float)) and getattr(val, "is_integer", lambda: False)() else (f"{val:,.4f}".rstrip("0").rstrip(".") if isinstance(val, (int, float)) else str(val))
            expr_clean = math_eval["expr"].split("=")[0].strip()
            answer = f"**Calculation Result:**\n\n```\n{expr_clean} = {val_str}\n```\n\nThe exact computed value is **{val_str}**."
            kpi_out = [{"label": "Calculation", "value": val_str, "sub": "Exact Arithmetic"}]
            answer = f"{answer}\n\n:::kpi-deck\n{json.dumps(kpi_out)}\n:::"
            _save_and_record(req, answer, t_start, provider_used="Deterministic Math Engine", total_tokens=0)
            return {
                "answer": answer,
                "provider_used": "Deterministic Math Engine",
                "kpis": kpi_out,
                "meta_stats": {"tokens": 0, "ms": int((time.time() - t_start) * 1000)}
            }

    # -------------------------------------------------------------------------
    # STRICT FILE EXPORT CLARIFICATION ENFORCEMENT (STOP GUESSING TABLES)
    # Whenever a file export keyword (PDF, Excel, Word, PPT) is detected,
    # if the exact message text from the user does NOT explicitly name a valid database table
    # (even if a table was looked at previously in conversation history), treat query as ambiguous,
    # force "mode": "clarification_needed", and return counter-question with available tables.
    # -------------------------------------------------------------------------
    file_export_match = re.search(r"\b(pdf|excel|xlsx?|word|docx?|ppt|powerpoint|presentation|spreadsheet)\b", req.question.lower())
    has_file_export_kw = bool(file_export_match) or bool(_asked_kinds(req.question))
    matched_in_q = _match_endpoint(req.question, endpoints)
    matched_eps_in_q = _match_endpoints_multi(req.question, endpoints)

    if has_file_export_kw and not matched_in_q and not matched_eps_in_q and not has_images and not has_docs:
        kw = file_export_match.group(1).lower() if file_export_match else "pdf"
        if any(x in kw for x in ["excel", "xls", "spreadsheet"]):
            doc_type = "Excel"
        elif any(x in kw for x in ["word", "doc"]):
            doc_type = "Word"
        elif any(x in kw for x in ["ppt", "powerpoint", "presentation"]):
            doc_type = "PowerPoint"
        else:
            doc_type = "PDF"

        tables_list = "\n".join([f"- **{ep.get('endpoint_name')}**" for ep in endpoints if ep.get("endpoint_name")]) if endpoints else "your authorized database tables"
        counter_question = f"Which specific dataset would you like me to compile into your {doc_type} document?\n\n{tables_list}"

        emit("Clarification needed – awaiting dataset selection for file export.")
        _save_and_record(req, counter_question, t_start, provider_used="File Export Orchestrator (Clarification)", total_tokens=0)
        return {
            "answer": counter_question,
            "provider_used": "File Export Orchestrator (Clarification)",
            "kpis": [],
            "meta_stats": {"tokens": 0, "ms": int((time.time() - t_start) * 1000), "cost": 0}
        }

    # Fast path: instant file dump skips LLM entirely (0 tokens, 0ms latency)
    asked = _asked_kinds(req.question)
    if asked and _is_plain_dump(req.question) and not has_images and not has_docs:
        matched_eps = _match_endpoints_multi(req.question, endpoints)
        if len(matched_eps) > 1:
            emit(f"Preparing combined multi-table export for {len(matched_eps)} tables directly…")
            tables_data = []
            for ep in matched_eps:
                ep_name = str(ep.get("endpoint_name", "Data"))
                rows = _fetch_single_table_dataset(ep, emit)
                tables_data.append((ep_name, rows))
            answer = _dump_multi_answer(tables_data, asked)
            answer = _force_markers(req.question, answer)
            _save_and_record(req, answer, t_start)
            return {
                "answer": answer,
                "provider_used": "Direct Export Engine (0 tokens)",
                "kpis": [{"label": "Combined Tables", "value": str(len(tables_data))}],
                "meta_stats": {"tokens": 0, "ms": int((time.time() - t_start) * 1000), "cost": 0}
            }
        elif len(matched_eps) == 1:
            best_ep = matched_eps[0]
            emit("Preparing your file export directly…")
            best_name = str(best_ep.get("endpoint_name", "Data"))
            rows = _fetch_single_table_dataset(best_ep, emit)
            answer = _dump_answer(best_name, rows, asked)
            answer = _force_markers(req.question, answer)
            _save_and_record(req, answer, t_start)
            return {
                "answer": answer,
                "provider_used": "Direct Export Engine (0 tokens)",
                "kpis": qe.compute_kpis(rows),
                "meta_stats": {"tokens": 0, "ms": int((time.time() - t_start) * 1000), "cost": 0}
            }

    # Load providers
    providers = _get_active_ai_providers()
    if not providers:
        raise HTTPException(status_code=500, detail="No active AI providers configured in Oracle APEX.")

    if req.provider_id is not None:
        # If user explicitly chose a provider/model (e.g. Claude or specific model), honor that first!
        providers.sort(key=lambda x: str(x.get("provider_id")) != str(req.provider_id))

    if has_images:
        # For image queries, prioritize genuine vision models ahead of text-only models
        vision_providers = []
        for p in providers:
            m_name_lower = str(p.get("model_name", "")).lower()
            p_name_lower = str(p.get("provider_name", "")).lower()
            if any(k in m_name_lower or k in p_name_lower for k in ["claude", "vision", "gemini", "gpt-4", "gemma", "omni"]):
                vision_providers.append(p)
            elif "openrouter" in str(p.get("llm_api_url", "")).lower():
                if req.provider_id is not None and str(p.get("provider_id")) == str(req.provider_id):
                    vision_providers.append(p)
                for v_model in ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "dots-studio/dots-3-note-preview:free", "google/gemma-4-31b-it:free", "google/gemma-4-26b-a4b-it:free"]:
                    cp = dict(p)
                    cp["model_name"] = v_model
                    cp["provider_name"] = f"OpenRouter ({v_model.split('/')[1].split(':')[0]})"
                    vision_providers.append(cp)
                vision_providers.append(p)
            elif "vision" in str(p.get("model_name", "")).lower():
                vision_providers.append(p)
        # Put user-selected vision provider first if matched, then vision fallbacks, then others
        user_picked = [vp for vp in vision_providers if req.provider_id is not None and str(vp.get("provider_id")) == str(req.provider_id)]
        other_vision = [vp for vp in vision_providers if req.provider_id is None or str(vp.get("provider_id")) != str(req.provider_id)]
        providers = user_picked + other_vision + [p for p in providers if p not in vision_providers]

    # STAGE 1: Small Compact Schema (~400 tokens) built from catalog without downloading rows
    schema_overview = _build_schema_catalog(endpoints)

    # STAGE 2: Call Planner LLM
    emit("Understanding question and planning query…")
    plan = None
    provider_used = None
    total_tokens_used = 0
    total_cost = 0.0

    planner_prompt = PLANNER_SYSTEM_PROMPT.replace("{schema}", schema_overview)
    if is_outside_db:
        planner_prompt += (
            "\n9. GENERAL KNOWLEDGE / OUTSIDE DB: If the question does NOT relate to any authorized database table or column and asks for general external knowledge, weather, forex, or open-web topics, output: {\"mode\": \"direct_answer\"}"
        )
    else:
        planner_prompt += (
            "\n9. STRICT INTERNAL DATABASE MODE: General AI / Outside Search is DISABLED. You MUST ONLY plan queries against the authorized tables ({schema}). If the question cannot be answered from the authorized tables, output: {\"mode\": \"direct_answer\", \"outside_rejected\": true}"
        )

    planner_messages = [
        {"role": "system", "content": planner_prompt}
    ]
    planner_messages.extend(history_msgs)
    planner_messages.append({"role": "user", "content": req.question})

    for provider in providers:
        if cancelled.is_set(): return None
        try:
            p_content, p_usage, p_rl = _call_llm(
                provider,
                messages=planner_messages,
                max_tokens=2200,
                temperature=0.0
            )
            provider_used = provider["provider_name"]
            p_tok = p_usage.get("total_tokens", 0)
            total_tokens_used += p_tok
            log_usage(
                user_id=req.user_id, session_id=req.session_id, provider_id=provider.get("provider_id"),
                model_name=provider.get("model_name"), call_type="PLAN", status="OK",
                prompt_tokens=p_usage.get("prompt_tokens", 0), completion_tokens=p_usage.get("completion_tokens", 0),
                total_tokens=p_tok, latency_ms=p_usage.get("latency_ms", 0), rl_headers=p_rl,
                question_preview=req.question
            )
            # Extract JSON plan
            m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", p_content)
            raw_json = m.group(1) if m else p_content.strip()
            plan = json.loads(raw_json)
            # Enforce strict database perimeter when General AI is OFF
            if not req_outside_enabled and plan:
                plan["external_tools"] = []
            break
        except Exception as e:
            emit(f"{provider['provider_name']} planner failed — trying fallback…")
            continue

    # Conversational Table Clarification (Stop Guessing Tables)
    if plan:
        # Check if query is ambiguous without dataset reference in query or conversation history
        has_history_table = False
        if history_msgs:
            for hm in reversed(history_msgs):
                c = (hm.get("content") or "").lower()
                if any((ep.get("endpoint_name") or "").lower() in c for ep in endpoints if ep.get("endpoint_name")):
                    has_history_table = True
                    break

        is_ambiguous_request = any(k in req.question.lower() for k in [
            "show a chart", "show chart", "draw a chart", "draw chart", "make a chart",
            "make chart", "plot chart", "plot a chart", "visualize this", "create a chart",
            "export this to presentation", "make a presentation", "create presentation",
            "export to ppt", "export presentation", "generate presentation", "give me data",
            "show data", "generate report", "make report"
        ])
        matched_in_q = _match_endpoint(req.question, endpoints)

        needs_clarification = (
            plan.get("mode") == "clarification_needed"
            or bool(plan.get("clarification_message"))
            or (has_file_export_kw and not matched_in_q and not matched_eps_in_q)
            or (is_ambiguous_request and not matched_in_q and not has_history_table and not (fuel_intent or forex_intent or stock_intent or weather_city))
        )

        if needs_clarification:
            tables_list = "\n".join([f"- **{ep.get('endpoint_name')}**" for ep in endpoints if ep.get("endpoint_name")]) if endpoints else "your authorized database tables"
            if has_file_export_kw:
                kw = file_export_match.group(1).lower() if file_export_match else "pdf"
                if any(x in kw for x in ["excel", "xls", "spreadsheet"]):
                    doc_type = "Excel"
                elif any(x in kw for x in ["word", "doc"]):
                    doc_type = "Word"
                elif any(x in kw for x in ["ppt", "powerpoint", "presentation"]):
                    doc_type = "PowerPoint"
                else:
                    doc_type = "PDF"
                clarification_msg = plan.get("clarification_message") or f"Which specific dataset would you like me to compile into your {doc_type} document?\n\n{tables_list}"
            else:
                clarification_msg = plan.get("clarification_message") or (
                    "I'd be glad to help with that! Which authorized dataset would you like to use?\n\n"
                    f"{tables_list}\n\n"
                    "Please let me know which table or specific metrics you would like to visualize or export."
                )
            emit("Clarification needed — awaiting dataset selection.")
            _save_and_record(req, clarification_msg, t_start, provider_used=provider_used or "Query Planner", total_tokens=total_tokens_used)
            return {
                "answer": clarification_msg,
                "provider_used": provider_used or "Query Planner",
                "kpis": [],
                "meta_stats": {"tokens": total_tokens_used, "ms": int((time.time() - t_start) * 1000), "cost": 0}
            }

    if plan and not is_outside_db and plan.get("outside_rejected"):
        pol_msg = (
            "### 🔒 Internal Database Mode Active (General AI is Switched Off)\n\n"
            "The **General AI** option is currently turned **OFF**. In this mode, the assistant is strictly locked to your authorized enterprise database tables:\n"
            "- **Employees Directory** (Staff details, designations, salaries, departments)\n"
            "- **PRODUCTS** (Products, pricing, stock, categories)\n"
            "- **Google Map Leads** (Business leads, contact information, ratings)\n\n"
            "Your inquiry does not relate to these authorized tables.\n\n"
            "👉 **To ask general questions, get live weather, forex rates, stock prices, or browse the web, please turn General AI ON in the bottom toolbar.**"
        )
        emit("Question outside authorized database scope — General AI is OFF.")
        _save_and_record(req, pol_msg, t_start, provider_used="Security Policy Guard", total_tokens=total_tokens_used)
        return {
            "answer": pol_msg,
            "provider_used": "Security Policy Guard",
            "kpis": [],
            "meta_stats": {"tokens": total_tokens_used, "ms": int((time.time() - t_start) * 1000), "cost": 0}
        }

    # If inquiry is an external real-world topic, never query internal tables
    if (fuel_intent or forex_intent or stock_intent or weather_city) and plan:
        plan["target_table"] = None
        plan["mode"] = "direct_answer"

    # STAGE 3: Execute Plan in Python (Exact numbers, 0 tokens)
    exec_result = None
    target_data = []

    # Check for multi-table query (e.g. "combine employee and product table")
    multi_matched_eps = _match_endpoints_multi(req.question, endpoints)
    is_multi_table = (plan and plan.get("mode") == "multi_table") or (len(multi_matched_eps) >= 2 and any(k in req.question.lower() for k in ["both", "combine", "and", "two table", "each", "all tables", "together"]))
    multi_table_results = []

    if is_multi_table and plan and plan.get("mode") != "direct_answer":
        target_tables = plan.get("target_tables") or []
        matched_multi = []
        if target_tables:
            for tt in target_tables:
                ep = _match_endpoint(str(tt), endpoints)
                if ep and ep not in matched_multi:
                    matched_multi.append(ep)
        if not matched_multi:
            matched_multi = multi_matched_eps

        if len(matched_multi) >= 2:
            emit(f"Loading {len(matched_multi)} authorized tables for combined analysis…")
            for ep in matched_multi:
                ep_n = ep.get("endpoint_name", "Table")
                t_rows = _fetch_single_table_dataset(ep, emit)
                if t_rows:
                    t_sample = t_rows[:100]
                    t_md = qe.rows_to_md_table(t_sample)
                    multi_table_results.append({
                        "name": ep_n,
                        "row_count": len(t_rows),
                        "rows": t_rows,
                        "table_md": t_md
                    })

    is_table_form_request = any(k in req.question.lower() for k in [
        "table form", "in table", "all data", "as a table", "table format",
        "show table", "give me data", "show data", "list data", "in the table form",
        "g map", "google map", "table data", "give me the"
    ])
    if is_table_form_request and plan:
        plan["limit"] = None
        has_explicit_cols = any(k in req.question.lower() for k in [
            "columns", "cols", "only", "carry", "select"
        ]) and any(char.isdigit() for char in req.question)
        if not has_explicit_cols and plan.get("mode") == "raw":
            plan["select_cols"] = None

    # Check for cross-table JOIN execution
    is_join_query = (plan and plan.get("mode") == "join") or (plan and plan.get("left_table") and plan.get("right_table"))
    if is_join_query and not multi_table_results:
        lt_name = plan.get("left_table") or plan.get("target_table")
        rt_name = plan.get("right_table")
        ep1 = _match_endpoint(str(lt_name), endpoints) if lt_name else None
        ep2 = _match_endpoint(str(rt_name), endpoints) if rt_name else None
        if not ep1 or not ep2:
            matched_all = _match_endpoints_multi(req.question, endpoints)
            if len(matched_all) >= 2:
                ep1, ep2 = matched_all[0], matched_all[1]
        
        if ep1 and ep2:
            t1_name = ep1.get("endpoint_name", "left")
            t2_name = ep2.get("endpoint_name", "right")
            emit(f"Loading {t1_name} and {t2_name} for cross-table JOIN…")
            d1 = _fetch_single_table_dataset(ep1, emit)
            d2 = _fetch_single_table_dataset(ep2, emit)
            emit("Executing in-memory cross-table join with zero hallucination…")
            try:
                exec_result = qe.execute_multi_table_plan({t1_name: d1, t2_name: d2}, plan)
            except Exception as e:
                print(f"[Join Engine Error] {e}")
                exec_result = None

    # STAGE 3: Run Hybrid Orchestrator (DuckDB local execution + external tools gated by general_ai_enabled)
    if not is_join_query and not multi_table_results and plan and not exec_result:
        try:
            def _local_db_worker(p, eps, em):
                tt = p.get("target_table")
                ep = _match_endpoint(str(tt), eps) if tt else (_match_endpoint(req.question, eps) or (eps[0] if eps else None))
                if ep:
                    d = _fetch_single_table_dataset(ep, em)
                    return qe.execute_plan(d, p)
                return None

            loop = asyncio.new_event_loop()
            try:
                orch_out = loop.run_until_complete(
                    run_hybrid_orchestrator(
                        plan=plan,
                        question=orch_question,
                        endpoints=endpoints,
                        emit_func=emit,
                        db_executor_func=_local_db_worker if plan.get("mode") != "direct_answer" else None,
                        general_ai_enabled=req_outside_enabled,
                        user_id=req.user_id,
                        history_msgs=history_msgs,
                        client_country=c_country,
                        client_city=c_city
                    )
                )
            finally:
                loop.close()

            if orch_out:
                if orch_out.get("db_result") and isinstance(orch_out["db_result"], dict) and orch_out["db_result"].get("table_md"):
                    exec_result = orch_out["db_result"]
                if req_outside_enabled and orch_out.get("synthesized_web_context"):
                    web_agent_data = {
                        "has_content": True,
                        "synthesized_context": orch_out["synthesized_web_context"]
                    }
        except Exception as orch_e:
            logger.error(f"Error executing hybrid orchestrator: {orch_e}", exc_info=True)

    if not is_join_query and not multi_table_results and plan and plan.get("mode") != "direct_answer" and not exec_result:
        target_table = plan.get("target_table")
        matched_target_ep = None
        if target_table:
            matched_target_ep = _match_endpoint(str(target_table), endpoints)
        if not matched_target_ep:
            matched_target_ep = _match_endpoint(req.question, endpoints)
        if not matched_target_ep and endpoints:
            matched_target_ep = endpoints[0]

        if matched_target_ep:
            target_data = _fetch_single_table_dataset(matched_target_ep, emit)
            emit("Computing exact numbers with in-memory query engine…")
            try:
                exec_result = qe.execute_plan(target_data, plan)
            except Exception as e:
                print(f"[Query Engine Error] {e}")
                exec_result = None

    # STAGE 4: Synthesis LLM (Writes crisp summary from small result table)
    emit("Synthesizing concise summary & visualization…")
    synth_sys_prompt = _get_general_synthesizer_prompt() if (is_outside_db and (not exec_result or (plan and plan.get("mode") == "direct_answer"))) else _get_synthesizer_prompt()
    synth_sys_prompt += depth_prompt

    if multi_table_results:
        multi_blocks = "\n\n".join([
            f"### Table: {m['name']} ({m['row_count']} total records)\nColumns: {', '.join(m['rows'][0].keys()) if m['rows'] else 'N/A'}\nSample Preview (first 5 records):\n{qe.rows_to_md_table(m['rows'][:5])}"
            for m in multi_table_results
        ])
        synth_user_prompt = (
            f"User Question: {req.question}\n\n"
            f"Here is the verified data overview across the authorized enterprise tables:\n\n"
            f"{multi_blocks}\n\n"
            f"Instructions:\n"
            f"1. Provide a professional, executive-grade summary paragraph covering key operational divisions, employee metrics, product inventory, and business leads.\n"
            f"2. Provide 3 to 4 concise bullet points highlighting key statistics (e.g. total workforce, product counts, lead pipeline).\n"
            f"3. Focus on executive business insights. The full verified tables will be attached automatically below your response."
        )
    elif exec_result and exec_result.get("table_rows"):
        table_md = exec_result["table_md"]
        kpis_str = ", ".join(f"{k['label']}: {k['value']} ({k.get('sub','')})" for k in exec_result.get("kpis", []))
        chart_json_str = json.dumps(exec_result["chart"], indent=2) if exec_result.get("chart") else ""
        synth_user_prompt = (
            f"User Question: {req.question}\n\n"
            f"Exact Verified Results Table:\n{table_md}\n\n"
            f"Computed Key Metrics:\n{kpis_str}\n\n"
        )
        if chart_json_str:
            synth_user_prompt += f"Verified Chart ECharts Config:\n```json\n{chart_json_str}\n```\n"
    else:
        matched_ep = _match_endpoint(req.question, endpoints)
        if matched_ep:
            m_rows = _fetch_single_table_dataset(matched_ep, emit)
            m_name = matched_ep.get("endpoint_name", "Data")
            synth_user_prompt = (
                f"User Question: {req.question}\n\n"
                f"Authorized Schema Overview:\n{schema_overview}\n\n"
                f"Data Preview for {m_name}:\n{qe.rows_to_md_table(m_rows[:50])}"
            )
        elif is_outside_db and (not plan or plan.get("mode") == "direct_answer"):
            synth_user_prompt = f"User Question: {req.question}"
        else:
            synth_user_prompt = (
                f"User Question: {req.question}\n\n"
                f"Authorized Schema Overview:\n{schema_overview}"
            )

    # Check for live forex / currency exchange request
    forex_card_data = None
    if req_outside_enabled and forex_intent:
        emit(f"Retrieving verified real-time interbank forex rates ({forex_intent.get('base','USD')} to {forex_intent.get('target','PKR')})...")
        forex_raw = fetch_live_forex(forex_intent.get("base", "USD"))
        if forex_raw:
            forex_card_data = format_forex_payload(forex_intent, forex_raw)
            base_c = forex_card_data["base"]
            target_c = forex_card_data["target"]
            amt = forex_card_data["amount"]
            c_amt = forex_card_data["converted_amount"]
            rate_val = forex_card_data["rate"]
            synth_user_prompt += (
                f"\n\n[VERIFIED REAL-TIME INTERBANK FOREX DATA (SOURCE: Interbank Global Feed / Central Bank Benchmarks)]:\n"
                f"- Base: {base_c} | Target: {target_c}\n"
                f"- Current Interbank Exchange Rate: 1 {base_c} = {rate_val} {target_c}\n"
                f"- Converted Amount: {amt:,.2f} {base_c} = {c_amt:,.2f} {target_c}\n"
                f"- Live Source: {forex_card_data.get('source')} as of {forex_card_data.get('timestamp')}\n"
                f"- Cross Rates with PKR: " + ", ".join(f"{k}: {v} PKR" for k, v in forex_card_data.get("comparisons", {}).items()) + "\n\n"
                f"STRICT INSTRUCTIONS FOR AI RESPONSE:\n"
                f"1. The interactive live currency exchange widget is already attached at the top of your response with the real-time rate calculator and cross-currency comparisons. DO NOT output a duplicate markdown table or duplicate metric cards repeating these exact fields.\n"
                f"2. State the official interbank rate ({rate_val} PKR per {base_c}) clearly in your opening sentence. Explain that open-market cash exchange counters typically operate with a tight 0.5% - 1.5% spread (~1 to 2 PKR margin).\n"
                f"3. Provide practical financial context, conversion examples (e.g. 1 USD, 10 USD, 100 USD), and key takeaways directly in crisp, readable text paragraphs.\n"
                f"4. You MUST include the following exact currency converter widget block at the end of your response:\n"
                f":::currency-converter\n{json.dumps(forex_card_data)}\n:::\n"
            )

    # Check for live stock market or PSX request
    stock_card_data = None
    if req_outside_enabled and stock_intent:
        emit(f"Querying Pakistan Stock Exchange (PSX) & live equity registers ({stock_intent})...")
        if stock_intent == "PSX_INDEX":
            psx_raw = fetch_psx_indices()
            if psx_raw:
                stock_card_data = psx_raw
                idx_summary = "\n".join(f"- {idx['index']}: {idx['value']} (Change: {idx['change']})" for idx in psx_raw.get("indices", []))
                synth_user_prompt += (
                    f"\n\n[VERIFIED LIVE PAKISTAN STOCK EXCHANGE (PSX) INDICES]:\n"
                    f"{idx_summary}\n"
                    f"- Source: {psx_raw.get('source')} as of {psx_raw.get('timestamp')}\n\n"
                    f"IMPORTANT: You MUST include the following exact stock card widget block at the end of your response:\n"
                    f":::stock-market-card\n{json.dumps(stock_card_data)}\n:::\n"
                )
        else:
            quote_raw = fetch_stock_quote(stock_intent)
            if quote_raw:
                stock_card_data = quote_raw
                synth_user_prompt += (
                    f"\n\n[VERIFIED LIVE STOCK MARKET QUOTE FOR {quote_raw['company_name']} ({quote_raw['symbol']})]:\n"
                    f"- Current Price: {quote_raw['price']} {quote_raw['currency']}\n"
                    f"- Change: {quote_raw['change']} ({quote_raw['change_percent']}%)\n"
                    f"- Day High: {quote_raw['high']} | Day Low: {quote_raw['low']}\n"
                    f"- Exchange: {quote_raw['exchange']} as of {quote_raw['timestamp']}\n\n"
                    f"STRICT INSTRUCTIONS FOR AI RESPONSE:\n"
                    f"1. The interactive equity ticker card is already attached at the top of your response displaying the live price, day high/low, and exchange status. DO NOT output a duplicate markdown table or duplicate metric cards repeating these exact numbers.\n"
                    f"2. Deliver a crisp narrative analysis in clean paragraphs: summarize the trading session, momentum ({quote_raw['change_percent']}%), sector perspective, and key takeaways.\n"
                    f"3. You MUST include the following exact stock card widget block at the end of your response:\n"
                    f":::stock-market-card\n{json.dumps(stock_card_data)}\n:::\n"
                )

    # Check for live weather request (only when General AI is enabled)
    weather_info = None
    if req_outside_enabled and weather_city:
        from agent.weather_helper import fetch_live_weather
        weather_info = fetch_live_weather(weather_city)
        if weather_info:
            w_curr = weather_info["current"]
            synth_user_prompt += (
                f"\n\n[LIVE VERIFIED METEOROLOGICAL DATA FOR {weather_info['location']}]:\n"
                f"- Current Temperature: {w_curr['temp']}°C ({round(w_curr['temp'] * 9/5 + 32)}°F)\n"
                f"- Condition: {w_curr['condition']}\n"
                f"- Humidity: {w_curr['humidity']}%\n"
                f"- Wind Speed: {w_curr['wind_kph']} km/h\n"
                f"- Precipitation: {w_curr['precipitation_pct']}%\n\n"
                f"STRICT WEATHER INSTRUCTION:\n"
                f"1. Provide a crisp 1 to 2 sentence summary of current weather conditions in {weather_info['location']}.\n"
                f"2. DO NOT output hourly timelines or lists of hours/days in your text. The interactive graphical weather card will be attached automatically below your response."
            )

    # Check for live fuel / petroleum prices request
    fuel_data = None
    if req_outside_enabled and fuel_intent:
        emit("Retrieving live verified fuel & petroleum rates (OGRA / PSO)...")
        fuel_raw = fetch_live_fuel_prices(fuel_intent.get("country", "Pakistan"))
        if fuel_raw:
            fuel_data = fuel_raw
            snippets_txt = "\n".join(fuel_raw.get("snippets", []))
            synth_user_prompt += (
                f"\n\n[VERIFIED REAL-TIME FUEL & PETROLEUM RATES ({fuel_raw['country'].upper()}) - SOURCE: {fuel_raw['source']} AS OF {fuel_raw['timestamp']}]:\n"
                f"- Petrol (Premier Super): {fuel_raw['petrol']}\n"
                f"- High Speed Diesel (HSD): {fuel_raw['diesel']}\n"
                f"- Hi-Octane (97 RON): {fuel_raw['hi_octane']}\n"
                f"- Kerosene Oil: {fuel_raw['kerosene']}\n"
                f"- LPG: {fuel_raw['lpg']}\n\n"
                f"Live Sources & Dispatches:\n{snippets_txt}\n\n"
                f"STRICT INSTRUCTIONS FOR AI RESPONSE:\n"
                f"1. State the exact, single active petrol price ({fuel_raw['petrol']}) and diesel price ({fuel_raw['diesel']}) immediately in the first sentence. Strictly IGNORE historical range boundaries (such as old ranges like Rs. 252 - 275).\n"
                f"2. Present the fuel prices in a clean, professional Markdown table followed by key takeaways.\n"
                f"3. Use clean, direct text form. DO NOT generate cards, bento boxes, or card decks.\n"
                f"4. Note that standard petrol and HSD are uniformly priced nationwide across all cities in Pakistan."
            )

    # Check for live Firecrawl web intelligence (scrape, search, crawl, extract)
    firecrawl_result_data = None
    if req_outside_enabled and firecrawl_intent:
        f_type = firecrawl_intent.get("type", "scrape")
        emit(f"Retrieving live web intelligence via Firecrawl ({f_type})...")
        if f_type == "scrape":
            res = scrape_url(firecrawl_intent.get("url"))
        elif f_type == "search":
            res = search_web(firecrawl_intent.get("query"))
        elif f_type == "crawl":
            res = crawl_site(firecrawl_intent.get("url"))
        elif f_type == "extract":
            res = extract_data(firecrawl_intent.get("urls"), firecrawl_intent.get("prompt", req.question))
        else:
            res = None

        if res and res.get("success"):
            firecrawl_result_data = res
            f_formatted = format_firecrawl_result(res, f_type)
            synth_user_prompt += (
                f"\n\n[LIVE VERIFIED WEB INTELLIGENCE RETRIEVED VIA FIRECRAWL]:\n"
                f"{f_formatted}\n\n"
                f"IMPORTANT: Synthesize this live web data thoroughly in your answer. State clearly that the data was retrieved in real time from the web using Firecrawl."
            )

    # Check for Universal Internet Search & Headless Webpage Reading (Zero saved sites!)
    web_agent_data = None
    search_queries = []
    browse_urls = list(direct_urls)

    if plan and plan.get("external_tools"):
        for t in plan["external_tools"]:
            if isinstance(t, dict):
                if t.get("tool") == "web_search" and t.get("query"):
                    search_queries.append(t["query"])
                elif t.get("tool") in ("browse_url", "browse", "read_url") and t.get("url"):
                    browse_urls.append(t["url"])

    has_db_match = bool(exec_result or (plan and plan.get("target_table") and plan.get("mode") != "direct_answer"))
    # Universal Internet Search (Dual-Intent Enabled):
    # 1. NEVER run when analyzing an uploaded document (has_docs) — the document is the source of truth!
    # 2. Concurrently execute alongside DuckDB if external_tools specified or real-world validation needed.
    should_run_web = req_outside_enabled and not has_docs and not firecrawl_result_data
    if should_run_web:
        if browse_urls or search_queries:
            emit("Browsing live internet sources with Universal Web Agent...")
            web_agent_data = universal_internet_agent(
                search_queries=search_queries,
                direct_urls=browse_urls,
                include_news=True
            )
        elif not _match_endpoint(req.question, endpoints) and not (forex_intent or stock_intent or weather_city or fuel_intent):
            emit("Querying open web intelligence & live sources...")
            web_agent_data = universal_internet_agent(
                search_queries=[orch_question],
                direct_urls=[],
                include_news=True
            )
        elif any(k in req.question.lower() for k in ["latest", "current", "release", "version", "update", "competitor", "market price", "vs online", "online"]):
            emit("Conducting dual-intent live web verification...")
            web_agent_data = universal_internet_agent(
                search_queries=[orch_question],
                direct_urls=[],
                include_news=True
            )

    if req_outside_enabled and web_agent_data and web_agent_data.get("has_content"):
        synth_user_prompt += (
            f"\n\n[VERIFIED REAL-TIME INTERNET INTELLIGENCE (UNIVERSAL OPEN-WEB DISCOVERY)]:\n"
            f"{web_agent_data['synthesized_context']}\n\n"
            f"IMPORTANT TEMPORAL & EXTRACTION DIRECTIVES:\n"
            f"- Prioritize rates, prices, and numbers associated with the most recent timestamp or date header.\n"
            f"- Strictly IGNORE historical range boundaries (such as Rs. 253 - Rs. 275) if a definitive single active rate (such as Rs. 369.06 or Rs. 374.51) is present in the latest search snippets.\n"
            f"- Never hallucinate. Cite titles or domains where appropriate."
        )



    # Check for QR / Barcode in images
    detected_qr_codes = []
    if has_images:
        from agent.barcode_helper import scan_barcodes_and_qr
        for img_item in req.images:
            if isinstance(img_item, dict):
                img_url = img_item.get("url", "")
                if img_item.get("scanned_codes"):
                    for sc in img_item["scanned_codes"]:
                        if isinstance(sc, dict):
                            detected_qr_codes.append(sc)
                        elif isinstance(sc, str):
                            detected_qr_codes.append({"format": "QR/Barcode", "text": sc})
            else:
                img_url = str(img_item)

            if img_url.startswith("data:image"):
                try:
                    head, b64part = img_url.split(",", 1)
                    raw_b = base64.b64decode(b64part)
                    codes = scan_barcodes_and_qr(raw_b)
                    detected_qr_codes.extend(codes)
                except Exception:
                    pass
            elif img_url.startswith("/static/"):
                try:
                    local_rel = img_url.replace("/static/", "", 1).lstrip("/\\")
                    local_path = os.path.join(STATIC_DIR, local_rel)
                    if os.path.exists(local_path):
                        with open(local_path, "rb") as f:
                            raw_b = f.read()
                        codes = scan_barcodes_and_qr(raw_b)
                        detected_qr_codes.extend(codes)
                except Exception:
                    pass

    if detected_qr_codes:
        # Deduplicate
        seen_texts = set()
        unique_qr = []
        for c in detected_qr_codes:
            txt = c.get("text", "")
            if txt and txt not in seen_texts:
                seen_texts.add(txt)
                unique_qr.append(c)
        if unique_qr:
            qr_summary = "\n".join([f"- Format: {c['format']} | Scanned Content: {c['text']}" for c in unique_qr])
            synth_user_prompt += (
                f"\n\n[SYSTEM SCANNED BARCODE/QR CODE IN USER IMAGE]:\n"
                f"The image contained the following verified scanned code(s):\n{qr_summary}\n"
                f"Use this exact decoded text/link to answer the user's question directly."
            )

    if has_docs:
        docs_blocks = []
        for d in req.documents:
            d_name = d.get("name") or d.get("filename") or "Attached Document"
            d_txt = d.get("text") or d.get("preview_text") or ""
            docs_blocks.append(f"=== ATTACHED DOCUMENT: {d_name} ===\n{d_txt}\n=== END OF ATTACHED DOCUMENT ===")
        docs_summary = "\n\n".join(docs_blocks)
        synth_user_prompt = f"{synth_user_prompt}\n\nAttached Document Content:\n{docs_summary}"

    answer = None
    synth_messages = [
        {"role": "system", "content": synth_sys_prompt}
    ]
    synth_messages.extend(history_msgs)

    if has_images:
        multimodal_content = [{"type": "text", "text": synth_user_prompt}]
        for img_item in req.images:
            img_url = img_item.get("url", "") if isinstance(img_item, dict) else str(img_item)
            safe_llm_url = _ensure_image_base64_for_llm(img_url)
            if safe_llm_url:
                multimodal_content.append({"type": "image_url", "image_url": {"url": safe_llm_url}})
        synth_messages.append({"role": "user", "content": multimodal_content})
    else:
        synth_messages.append({"role": "user", "content": synth_user_prompt})

    emit("Synthesizing verified enterprise intelligence & business analysis...")
    for provider in providers:
        if cancelled.is_set(): return None
        try:
            s_content, s_usage, s_rl = _call_llm(
                provider,
                messages=synth_messages,
                max_tokens=synth_max_tokens,
                temperature=0.1
            )

            provider_used = provider["provider_name"]
            s_tok = s_usage.get("total_tokens", 0)
            total_tokens_used += s_tok
            log_usage(
                user_id=req.user_id, session_id=req.session_id, provider_id=provider.get("provider_id"),
                model_name=provider.get("model_name"), call_type="ANSWER", status="OK",
                prompt_tokens=s_usage.get("prompt_tokens", 0), completion_tokens=s_usage.get("completion_tokens", 0),
                total_tokens=s_tok, latency_ms=s_usage.get("latency_ms", 0), rl_headers=s_rl,
                question_preview=req.question
            )
            if s_content and "User Safety: safe" in s_content.strip() and len(s_content.strip()) < 50:
                # Upstream content-safety filter intercepted instead of visual model
                continue
            if s_content and has_images and any(b in s_content.lower() for b in ["completely blank and solid white", "plain white placeholder", "blank white placeholder", "appears to be a plain white"]):
                # Model hallucinated empty canvas/placeholder on real uploaded image -> try next vision provider
                continue
            answer = s_content
            break
        except Exception as e:
            continue

    if not answer:
        # Graceful fallback: return the raw table generated by the query engine
        if exec_result and exec_result.get("table_md"):
            answer = f"**Summary of results** ({exec_result['row_count']} records matching):\n\n{exec_result['table_md']}"
        elif multi_table_results:
            multi_blocks = "\n\n".join([f"### {idx+1}. {m['name']} ({m['row_count']} total records)\n\n{m['table_md']}" for idx, m in enumerate(multi_table_results)])
            answer = f"**Summary of combined tables:**\n\n{multi_blocks}"
        else:
            raise HTTPException(status_code=500, detail="All AI providers failed. Please retry in a few moments.")
    elif multi_table_results:
        # Keep clean intro/insights and guarantee ALL requested tables are appended in full with zero truncation
        intro_lines = []
        for line in answer.split("\n"):
            line_s = line.strip()
            if line_s.startswith("|") or (line_s.count("|") >= 2 and not line_s.startswith("|")) or line_s.startswith("### 1.") or line_s.startswith("### Table:"):
                break
            intro_lines.append(line)
        clean_intro = "\n".join(intro_lines).strip()
        if not clean_intro or len(clean_intro) < 10:
            clean_intro = f"**Summary of {len(multi_table_results)} authorized tables:**"
        multi_blocks = "\n\n".join([f"### {idx+1}. {m['name']} ({m['row_count']} total records)\n\n{m['table_md']}" for idx, m in enumerate(multi_table_results)])
        answer = f"{clean_intro}\n\n{multi_blocks}"
    elif exec_result and exec_result.get("table_md"):
        # Ensure the mathematically verified complete table from query engine is rendered
        # Clean any hallucinated trailing pipe-separated text dumps or outreach templates from LLM output
        if is_table_form_request or "| ---" in answer or "|\n|" in answer:
            intro_lines = []
            for line in answer.split("\n"):
                line_s = line.strip()
                if line_s.startswith("|") or "http" in line_s or (line_s.count("|") >= 2 and not line_s.startswith("|")) or line_s.startswith("Hi [") or line_s.startswith("Dear ") or line_s.startswith("Subject:"):
                    break
                intro_lines.append(line)
            clean_intro = "\n".join(intro_lines).strip()
            if not clean_intro or len(clean_intro) < 6:
                clean_intro = f"**Summary of results** ({exec_result['row_count']} records matching):"
            answer = f"{clean_intro}\n\n{exec_result['table_md']}"
        elif exec_result.get("mode") in ("raw", "aggregate", "pivot") and not exec_result.get("chart"):
            answer = f"{answer.strip()}\n\n{exec_result['table_md']}"


    if exec_result and exec_result.get("chart"):
        chart_str = json.dumps(exec_result["chart"], indent=2)
        has_chart_block = bool(re.search(r"```(?:json|echarts|chart)[\s\S]*?(?:series|calendar)[\s\S]*?```", answer, flags=re.I))
        if not has_chart_block:
            # Strip any incomplete or broken json/echarts block before appending verified chart
            answer = re.sub(r"```(?:json|echarts|chart)[\s\S]*?```", "", answer).strip()
            answer = f"{answer.strip()}\n\n```json\n{chart_str}\n```"

    if req_outside_enabled and weather_info:
        answer = re.sub(r":::google-weather[\s\S]*?(?:::|$)", "", answer).strip()
        answer = f"{answer.strip()}\n\n:::google-weather\n{json.dumps(weather_info)}\n:::"
    if req_outside_enabled and forex_card_data:
        answer = re.sub(r":::currency-converter[\s\S]*?(?:::|$)", "", answer).strip()
        answer = f"{answer.strip()}\n\n:::currency-converter\n{json.dumps(forex_card_data)}\n:::"
    if req_outside_enabled and stock_card_data:
        answer = re.sub(r":::stock-market-card[\s\S]*?(?:::|$)", "", answer).strip()
        answer = f"{answer.strip()}\n\n:::stock-market-card\n{json.dumps(stock_card_data)}\n:::"

    if req_outside_enabled and (forex_card_data or stock_card_data or weather_info):
        # Eliminate card box overload: Strip redundant 2-column key-value tables that duplicate widget fields
        answer = re.sub(r"\|[ \t]*(?:Item|Property|Field|Metric|Parameter)[ \t]*\|[ \t]*(?:Value|Rate|Price|Details)[ \t]*\|[\s\S]*?(?:\n\n|\Z)", "\n\n", answer).strip()
        answer = re.sub(r"\|[ \t]*(?:Base Currency|Current Price|Exchange Rate)[ \t]*\|[\s\S]*?(?:\n\n|\Z)", "\n\n", answer).strip()
    if req_outside_enabled and not has_docs and web_agent_data and web_agent_data.get("has_content"):
        total_sources = len(web_agent_data.get("direct_pages", [])) + len(web_agent_data.get("search_pages", [])) + len(web_agent_data.get("search_snippets", []))
        wic_payload = {
            "engine": "Universal Agent",
            "results": [
                {"title": p.get("title") or p.get("domain", "Web Source"), "url": p.get("url") or p.get("href", "")}
                for p in (web_agent_data.get("direct_pages", []) + web_agent_data.get("search_pages", []) + web_agent_data.get("search_snippets", []))
                if p.get("url") or p.get("href")
            ][:6]
        }
        answer = re.sub(r":::web-intelligence[\s\S]*?(?:::|$)", "", answer).strip()
        answer = f"{answer.strip()}\n\n:::web-intelligence\n{json.dumps(wic_payload)}\n:::"

    kpis_out = exec_result.get("kpis") if exec_result else []
    # Avoid card overload: Only generate kpis_out if user explicitly requested a KPI deck or multi-table overview
    user_wants_deck = any(k in req.question.lower() for k in ["kpi", "bento", "metric card", "overview deck", "dashboard"])
    if not kpis_out and has_docs and req.documents and user_wants_deck:
        d0 = req.documents[0]
        d_name = d0.get("name") or d0.get("filename") or "Attached Document"
        d_pages = d0.get("pages", 1)
        d_type = (d0.get("file_type") or os.path.splitext(d_name)[1].lstrip(".") or "PDF").upper()
        kpis_out = [
            {"label": "Document Analyzed", "value": d_name[:24], "sub": f"Format: {d_type}"},
            {"label": "Document Scope", "value": f"{d_pages} Page{'s' if d_pages > 1 else ''}", "sub": "Verified Local Extraction"}
        ]

    if kpis_out and (user_wants_deck or is_multi_table):
        answer = re.sub(r":::kpi-deck[\s\S]*?(?:::|$)", "", answer).strip()
        answer = f"{answer.strip()}\n\n:::kpi-deck\n{json.dumps(kpis_out)}\n:::"
    else:
        # Strip any unrequested kpi-deck card wrappers so response stays in clean text & table format
        answer = re.sub(r":::kpi-deck[\s\S]*?(?:::|$)", "", answer).strip()

    # Strip any hallucinated disclaimers about PDF generation not being supported
    answer = re.sub(r"(?i)\b(?:note:?\s*)?direct\s+pdf\s+generation\s+is\s+not\s+supported[^\.\n]*(\.|\n|$)", "", answer)
    answer = re.sub(r"(?i)\byou\s+can\s+copy\s+the\s+summary\s+above\s+into\s+your\s+document\s+editor[^\.\n]*(\.|\n|$)", "", answer).strip()
    answer = _force_markers(req.question, answer)

    if cancelled.is_set(): return None

    # Save chat & update message times
    emit("Saving conversation…")
    _save_and_record(req, answer, t_start, provider_used, total_tokens_used)

    return {
        "answer": answer,
        "provider_used": provider_used,
        "kpis": kpis_out,
        "web_intelligence": firecrawl_result_data or (web_agent_data if web_agent_data and web_agent_data.get("has_content") else None),
        "meta_stats": {
            "tokens": total_tokens_used,
            "ms": int((time.time() - t_start) * 1000)
        }
    }

def _save_and_record(req: ChatRequest, answer: str, t_start: float, provider_used: str = None, total_tokens: int = 0):
    if req.keep_count is not None:
        try:
            ords_post(URL_TRUNCATE, json={"user_id": req.user_id, "session_id": req.session_id, "keep_count": req.keep_count}, timeout=5)
        except Exception:
            pass

    title = req.question[:30] + "..." if len(req.question) > 30 else req.question
    user_msg_content = req.question
    if req.images:
        img_lines = "\n".join([f"![image]({img.get('url', '') if isinstance(img, dict) else str(img)})" for img in req.images if img])
        if img_lines:
            user_msg_content = f"{user_msg_content}\n\n{img_lines}".strip()
    if req.documents:
        doc_lines = "\n".join([f"[📄 Document: {d.get('name') or d.get('filename') or 'Attached Document'}]" for d in req.documents if d])
        if doc_lines:
            user_msg_content = f"{user_msg_content}\n\n{doc_lines}".strip()


    try:
        ords_post(URL_SAVE_CHAT, json={
            "session_id": req.session_id, "user_id": req.user_id,
            "session_title": title, "user_msg": user_msg_content, "ai_msg": answer,
            "provider_name": provider_used, "total_tokens": total_tokens,
            "latency_ms": int((time.time() - t_start) * 1000)
        })
    except Exception:
        pass

# ---------------- Chat Task Runner & Background Workers ----------------
_jobs: dict = {}


def _prune_jobs():
    now = time.time()
    for k in [k for k, j in _jobs.items() if not j["running"] and now - j["t0"] > 900]:
        _jobs.pop(k, None)

@app.post("/api/chat")
async def ask_assistant(req: ChatRequest, request: Request):
    u_obj = current_user(request)
    req.user_id = u_obj["u"]
    req._user = u_obj
    prev = _jobs.get(req.session_id)
    if prev and prev["running"] and prev["uid"] == req.user_id:
        prev["cancel"].set()
    cancelled = threading.Event()
    job = {"uid": req.user_id, "cancel": cancelled, "question": req.question, "status": "Thinking…",
           "running": True, "t0": time.time(), "error": None}
    _jobs[req.session_id] = job
    _prune_jobs()
    q: "queue.Queue[dict]" = queue.Queue()

    def emit(m):
        job["status"] = m
        q.put({"status": m})

    def worker():
        try:
            result = _run_chat(req, cancelled, emit)
            q.put({"done": True, **(result or {"cancelled": True})})
        except HTTPException as e:
            job["error"] = str(e.detail)
            q.put({"done": True, "error": e.detail})
        except Exception as e:
            job["error"] = f"Unexpected error: {e}"
            q.put({"done": True, "error": job["error"]})
        finally:
            job["running"] = False

    threading.Thread(target=worker, daemon=True).start()

    async def stream():
        while True:
            try:
                ev = q.get_nowait()
            except queue.Empty:
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.12)
                continue
            yield json.dumps(ev) + "\n"
            if ev.get("done"):
                return

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/api/chat/status/{session_id}")
def chat_status(session_id: str, request: Request):
    uid = current_user(request)["u"]
    j = _jobs.get(session_id)
    if not j or j["uid"] != uid:
        return {"running": False}
    running = j["running"] and not j["cancel"].is_set()
    out = {"running": running, "question": j["question"], "status": j["status"],
           "elapsed": int(time.time() - j["t0"]), "error": None}
    if not running:
        out["error"] = j["error"]
        if not j["running"]:
            _jobs.pop(session_id, None)
    return out

@app.get("/api/chat/running")
def chat_running(request: Request):
    uid = current_user(request)["u"]
    now = time.time()
    return {"items": [{"session_id": sid, "question": j["question"], "status": j["status"], "elapsed": int(now - j["t0"])}
                      for sid, j in list(_jobs.items()) if j["uid"] == uid and j["running"] and not j["cancel"].is_set()]}

@app.post("/api/chat/stop")
def chat_stop(body: StopRequest, request: Request):
    uid = current_user(request)["u"]
    j = _jobs.get(body.session_id)
    if j and j["uid"] == uid:
        j["cancel"].set()
    return {"status": "success"}

# ---------------- Audio Transcription (Whisper) ----------------
@app.post("/api/transcribe")
def transcribe(request: Request, audio: UploadFile = File(...), language: str = Form("")):
    current_user(request)
    providers = _get_active_ai_providers()
    raw = audio.file.read()
    if len(raw) > 24 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Recording is too long.")

    last = "No speech-to-text provider configured."
    for prov in providers:
        url = prov.get("llm_api_url", "")
        models_to_try = ["whisper-large-v3", "whisper-large-v3-turbo"] if "groq.com" in url else ["whisper-1"] if "api.openai.com" in url else []
        if not models_to_try:
            continue
        for model in models_to_try:
            data = {"model": model, "response_format": "verbose_json", "temperature": "0"}
            if language in ("ur", "en"):
                data["language"] = language
            try:
                r = requests.post(url.replace("/chat/completions", "/audio/transcriptions"),
                                  headers={"Authorization": f"Bearer {prov['api_key']}"},
                                  files={"file": (audio.filename or "voice.webm", raw, audio.content_type or "audio/webm")},
                                  data=data, timeout=60)
                if r.ok:
                    txt = (r.json().get("text") or "").strip()
                    if txt:
                        return {"text": txt}
                last = f"Whisper failed ({r.status_code})"
                print(f"[Whisper Notice] {prov.get('provider_name')} ({model}) status {r.status_code}: {r.text[:200]}")
            except Exception as e:
                last = f"Speech error: {e}"
                print(f"[Whisper Notice] {prov.get('provider_name')} ({model}) error: {e}")
    raise HTTPException(status_code=502, detail=last)

@app.get("/api/chat/attachments/{session_id}")
def get_chat_attachments_endpoint(session_id: str, request: Request):
    """Returns all shared documents and images for the specified chat session."""
    try:
        current_user(request)
    except Exception:
        pass

    items = []
    seen_keys = set()

    # 1. Try Oracle ORDS attachments table
    try:
        res = ords_get(f"{URL_ATTACHMENTS}/{session_id}", timeout=6)
        if res.ok:
            for it in res.json().get("items", []):
                key = (it.get("file_name") or "") + (it.get("gdrive_web_link") or "")
                if key not in seen_keys:
                    seen_keys.add(key)
                    g_id = it.get("gdrive_file_id")
                    if not g_id and it.get("gdrive_web_link"):
                        m_gid = re.search(r'(?:file/d/|id=|/file/)([a-zA-Z0-9_-]{20,})', str(it.get("gdrive_web_link")))
                        if m_gid:
                            g_id = m_gid.group(1)
                            it["gdrive_file_id"] = g_id
                    if g_id:
                        it["thumbnail_url"] = f"/api/gdrive/file/{g_id}"
                        it["display_url"] = f"/api/gdrive/file/{g_id}"
                    items.append(it)
    except Exception:
        pass

    # 2. Inspect Oracle ORDS chat messages ONLY if no items found in attachments table (legacy fallback)
    if not items:
        try:
            msg_res = ords_get(f"{URL_MESSAGES}/{session_id}", timeout=6)
            if msg_res.ok:
                msgs = msg_res.json().get("items", [])
                for m in msgs:
                    um = m.get("user_msg") or ""
                    # Parse markdown images ![alt](url)
                    for m_img in re.finditer(r'!\[([^\]]*)\]\((https?://[^\s)]+|data:image/[^)]+)\)', um):
                        img_url = m_img.group(2)
                        alt_name = m_img.group(1) or "Image"
                        key = alt_name + img_url
                        if key not in seen_keys:
                            seen_keys.add(key)
                            g_id = ""
                            m_gid = re.search(r'(?:file/d/|id=|/file/)([a-zA-Z0-9_-]{20,})', img_url)
                            if m_gid:
                                g_id = m_gid.group(1)
                            disp_url = f"/api/gdrive/file/{g_id}" if g_id else img_url
                            items.append({
                                "session_id": session_id,
                                "file_name": alt_name if alt_name.lower() != "image" else "Attached Image",
                                "file_type": "image",
                                "mime_type": "image/jpeg",
                                "file_size": 0,
                                "gdrive_file_id": g_id,
                                "gdrive_web_link": img_url,
                                "gdrive_download_link": img_url,
                                "thumbnail_url": disp_url,
                                "display_url": disp_url,
                                "storage_source": "GDRIVE" if g_id else "INLINE",
                                "created_at": m.get("created_at") or ""
                            })
                    # Parse document markers [📄 Document: filename]
                    for m_doc in re.finditer(r'\[📄 Document:\s*([^\]]+)\]', um):
                        doc_name = m_doc.group(1).strip()
                        if doc_name not in seen_keys:
                            seen_keys.add(doc_name)
                            items.append({
                                "session_id": session_id,
                                "file_name": doc_name,
                                "file_type": "document",
                                "mime_type": "application/octet-stream",
                                "file_size": 0,
                                "gdrive_file_id": "",
                                "gdrive_web_link": "",
                                "gdrive_download_link": "",
                                "storage_source": "SESSION",
                                "created_at": m.get("created_at") or ""
                            })
        except Exception:
            pass

    return {
        "success": True,
        "session_id": session_id,
        "attachments": items,
        "count": len(items)
    }

@app.get("/api/gdrive/file/{file_id}")
def get_gdrive_file_endpoint(file_id: str, request: Request):
    """Streams file content directly from Google Drive with proper MIME type."""
    try:
        from agent.gdrive_storage import get_drive_service
        from googleapiclient.http import MediaIoBaseDownload
        service = get_drive_service()
        if not service:
            raise HTTPException(status_code=503, detail="Google Drive service unavailable")
        meta = service.files().get(fileId=file_id, fields="name, mimeType, size", supportsAllDrives=True).execute()
        req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        mime = meta.get("mimeType", "application/octet-stream")
        return Response(content=fh.read(), media_type=mime, headers={
            "Content-Disposition": f'inline; filename="{meta.get("name", "file")}"',
            "Cache-Control": "public, max-age=86400"
        })
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found in Google Drive: {e}")

@app.get("/api/gdrive/status")
def get_gdrive_status_endpoint(request: Request):
    """Returns Google Drive integration configuration and health status."""
    from agent.gdrive_storage import get_auth_type, get_oauth_token_path
    return {
        "configured": is_gdrive_configured(),
        "auth_type": get_auth_type(),
        "has_credentials": bool(get_oauth_token_path() or get_credentials_path() or os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")),
        "root_folder_id": get_root_folder_id(),
        "credentials_path": get_credentials_path(),
        "oauth_token_path": get_oauth_token_path()
    }



@app.get("/SUMMARY_DOCUMENTATION.md", response_class=FileResponse)
@app.get("/api/docs/summary", response_class=FileResponse)
def serve_doc_md():
    doc_paths = [
        CLIENT_DIR / "SUMMARY_DOCUMENTATION.md",
        SERVER_DIR / "SUMMARY_DOCUMENTATION.md",
        ROOT_DIR / "SUMMARY_DOCUMENTATION.md",
        ROOT_DIR / "others" / "docs" / "SUMMARY_DOCUMENTATION.md"
    ]
    for p in doc_paths:
        if p.exists():
            return FileResponse(str(p), media_type="text/markdown", filename="Enterprise_AI_Architecture_Summary.md")
    raise HTTPException(status_code=404, detail="Documentation not found")

@app.get("/", response_class=FileResponse)
@app.get("/ai", response_class=FileResponse)
@app.get("/ai/", response_class=FileResponse)
def serve_ui():
    if INDEX_HTML.exists():
        return FileResponse(str(INDEX_HTML))
    return FileResponse("index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui_app:app", host="0.0.0.0", port=8000, reload=True)
