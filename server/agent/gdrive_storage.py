"""
Enterprise AI Hub - Google Drive Cloud Storage Module
Provides automated folder management and file uploading to Google Drive:
- Root Storage Folder
    └── <User_ID> - <User_Name>
          └── <Chat_Title> (<Session_ID_Short>)
                ├── <Uploaded_Files>
"""

import os
import io
import json
import time
import logging
from typing import Optional, Dict, Any
import requests

logger = logging.getLogger("gdrive_storage")

# Cache service client and folder IDs in memory to avoid redundant API round-trips
_drive_service = None
_folder_cache: Dict[str, str] = {}  # key: f"{parent_id}:{folder_name}", value: folder_id

# In-memory remote storage configuration cache from ORDS REST endpoint
_remote_storage_config: Dict[str, Any] = {}
_remote_config_fetched_at: float = 0.0

def fetch_storage_config_from_ords(force: bool = False) -> Dict[str, Any]:
    """
    Dynamically loads Google Drive configuration & credentials from ORDS REST API:
      GET {ORDS_BASE_URL}/storage/config
    This avoids keeping sensitive JSON secret files in local repository / Git.
    """
    global _remote_storage_config, _remote_config_fetched_at
    now = time.time()
    if not force and _remote_storage_config and (now - _remote_config_fetched_at) < 600:
        return _remote_storage_config

    ords_base = os.environ.get("ORDS_BASE_URL", "").strip()
    if not ords_base:
        try:
            from pathlib import Path
            server_dir = Path(__file__).resolve().parent.parent
            for env_cand in [server_dir / ".env", server_dir.parent / ".env"]:
                if env_cand.exists():
                    with open(env_cand, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith("ORDS_BASE_URL="):
                                ords_base = line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                if ords_base:
                    break
        except Exception:
            pass

    if not ords_base or "<your-db" in ords_base:
        return _remote_storage_config

    url = f"{ords_base.rstrip('/')}/storage/config"
    try:
        headers = {}
        client_id = os.environ.get("ORDS_CLIENT_ID", "").strip()
        client_sec = os.environ.get("ORDS_CLIENT_SECRET", "").strip()
        if client_id and client_sec:
            if "/ords/" in ords_base:
                token_url = f"{ords_base.split('/ords/')[0]}/ords/{ords_base.split('/ords/')[1].split('/')[0]}/oauth/token"
                try:
                    tok_r = requests.post(token_url, auth=(client_id, client_sec), data={"grant_type": "client_credentials"}, timeout=8)
                    if tok_r.ok:
                        headers["Authorization"] = f"Bearer {tok_r.json().get('access_token')}"
                except Exception:
                    pass

        r = requests.get(url, headers=headers, timeout=10)
        if r.ok:
            data = r.json()
            items = data.get("items", [])
            if items:
                _remote_storage_config = items[0]
                _remote_config_fetched_at = now
                logger.info("[GDrive] Loaded storage configuration dynamically from ORDS REST API.")
                return _remote_storage_config
    except Exception as e:
        logger.debug(f"[GDrive] Remote storage config fetch: {e}")

    return _remote_storage_config

def _load_app_config() -> dict:
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def get_credentials_path() -> Optional[str]:
    """Finds the Google Service Account JSON credentials path."""
    env_path = os.environ.get("GDRIVE_SERVICE_ACCOUNT_FILE", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path
    
    cfg = _load_app_config()
    cfg_path = cfg.get("gdrive_credentials_file", "").strip()
    if cfg_path:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        full_p = os.path.join(base_dir, cfg_path) if not os.path.isabs(cfg_path) else cfg_path
        if os.path.exists(full_p):
            return full_p

    # Common default locations
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for default_name in ["gdrive_service_account.json", "service_account.json", "google_drive_credentials.json"]:
        cand = os.path.join(base_dir, default_name)
        if os.path.exists(cand):
            return cand

    return None

def get_root_folder_id() -> Optional[str]:
    """Gets the target 5TB root folder ID in Google Drive."""
    fid = os.environ.get("GDRIVE_ROOT_FOLDER_ID", "").strip()
    if fid:
        return fid
    
    # Check cloud config from ORDS REST endpoint
    remote_cfg = fetch_storage_config_from_ords()
    remote_fid = (remote_cfg.get("root_folder_id") or "").strip()
    if remote_fid:
        return remote_fid

    cfg = _load_app_config()
    return cfg.get("gdrive_root_folder_id", "").strip() or None

def get_oauth_token_path() -> Optional[str]:
    """Finds the OAuth2 user token path for personal Google One storage."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for token_name in ["gdrive_oauth_token.json", "token.json"]:
        cand = os.path.join(base_dir, token_name)
        if os.path.exists(cand):
            return cand
    return None

def is_gdrive_configured() -> bool:
    """Returns True if Google Drive credentials (OAuth token or Service Account) and root folder ID are available."""
    remote_cfg = fetch_storage_config_from_ords()
    has_remote = bool(remote_cfg.get("service_account_json") or remote_cfg.get("oauth_token_json") or remote_cfg.get("oauth_refresh_token"))
    oauth_file = get_oauth_token_path()
    cred_file = get_credentials_path()
    raw_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    root_id = get_root_folder_id()
    return bool((has_remote or oauth_file or cred_file or raw_json) and root_id)

def get_auth_type() -> str:
    """Returns the active authentication type."""
    if get_oauth_token_path():
        return "OAuth2 User (Personal 5TB Google One - Local File)"
    remote_cfg = fetch_storage_config_from_ords()
    if remote_cfg.get("oauth_token_json"):
        return "OAuth2 User (Cloud ORDS REST)"
    if remote_cfg.get("service_account_json"):
        return "Service Account (Cloud ORDS REST)"
    if get_credentials_path() or os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON"):
        return "Service Account (Local File)"
    return "None"

def get_drive_service():
    """Initializes and caches the Google Drive v3 API client."""
    global _drive_service
    if _drive_service is not None:
        return _drive_service

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("[GDrive] google-api-python-client or google-auth not installed.")
        return None

    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = None

    # 1. First check for User OAuth2 credentials (personal 5TB Google One quota via local token file)
    token_path = get_oauth_token_path()
    if token_path:
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request as GRequest
            creds = Credentials.from_authorized_user_file(token_path, scopes=scopes)
            if creds:
                if creds.expired and creds.refresh_token:
                    creds.refresh(GRequest())
                logger.info(f"[GDrive] Using OAuth2 User credentials from {token_path}")
        except Exception as e:
            logger.warning(f"[GDrive] Failed to load OAuth token from {token_path}: {e}")

    # 2. Check for Cloud ORDS REST credentials (zero secret files on disk)
    if not creds:
        remote_cfg = fetch_storage_config_from_ords()
        
        # Check OAuth from ORDS
        raw_oauth = remote_cfg.get("oauth_token_json")
        if raw_oauth:
            try:
                from google.oauth2.credentials import Credentials
                from google.auth.transport.requests import Request as GRequest
                oauth_dict = json.loads(raw_oauth) if isinstance(raw_oauth, str) else raw_oauth
                creds = Credentials.from_authorized_user_info(oauth_dict, scopes=scopes)
                if creds:
                    if creds.expired and creds.refresh_token:
                        creds.refresh(GRequest())
                    logger.info("[GDrive] Using OAuth2 credentials dynamically loaded from ORDS REST API.")
            except Exception as e:
                logger.warning(f"[GDrive] Failed to load remote OAuth credentials from ORDS: {e}")

        # Check Service Account from ORDS
        if not creds:
            raw_sa = remote_cfg.get("service_account_json")
            if raw_sa:
                try:
                    sa_dict = json.loads(raw_sa) if isinstance(raw_sa, str) else raw_sa
                    creds = service_account.Credentials.from_service_account_info(sa_dict, scopes=scopes)
                    logger.info("[GDrive] Using Service Account credentials dynamically loaded from ORDS REST API.")
                except Exception as e:
                    logger.warning(f"[GDrive] Failed to load remote Service Account credentials from ORDS: {e}")

    # 3. Fall back to local Service Account file or environment variable
    if not creds:
        cred_path = get_credentials_path()
        raw_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()

        if cred_path and os.path.exists(cred_path):
            try:
                creds = service_account.Credentials.from_service_account_file(cred_path, scopes=scopes)
            except Exception as e:
                logger.error(f"[GDrive] Failed to load credentials from file {cred_path}: {e}")
                return None
        elif raw_json:
            try:
                cred_dict = json.loads(raw_json)
                creds = service_account.Credentials.from_service_account_info(cred_dict, scopes=scopes)
            except Exception as e:
                logger.error(f"[GDrive] Failed to parse GDRIVE_SERVICE_ACCOUNT_JSON: {e}")
                return None

    if not creds:
        return None

    try:
        _drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return _drive_service
    except Exception as e:
        logger.error(f"[GDrive] Failed to build Drive service: {e}")
        return None

def get_or_create_folder(folder_name: str, parent_id: Optional[str] = None) -> Optional[str]:
    """
    Finds an existing folder by name under parent_id, or creates a new one.
    Uses memory cache to avoid unnecessary Drive API requests.
    """
    service = get_drive_service()
    if not service:
        return None

    safe_name = folder_name.replace("'", "\\'").strip()
    cache_key = f"{parent_id or 'root'}:{safe_name}"
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    try:
        query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{safe_name}' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        results = service.files().list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=1,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        files = results.get("files", [])
        if files:
            folder_id = files[0]["id"]
            _folder_cache[cache_key] = folder_id
            return folder_id

        # Folder does not exist, create it
        metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder"
        }
        if parent_id:
            metadata["parents"] = [parent_id]

        new_folder = service.files().create(
            body=metadata,
            fields="id",
            supportsAllDrives=True
        ).execute()

        folder_id = new_folder.get("id")
        if folder_id:
            _folder_cache[cache_key] = folder_id
        return folder_id
    except Exception as e:
        logger.error(f"[GDrive] Failed to get/create folder '{folder_name}' under '{parent_id}': {e}")
        return None

def upload_file_to_drive(
    file_bytes: bytes,
    filename: str,
    mime_type: str = "application/octet-stream",
    user_id: int = 0,
    user_name: str = "User",
    session_id: str = "general",
    session_title: str = "Chat"
) -> Dict[str, Any]:
    """
    Uploads a file to Google Drive with automatic hierarchy:
      Root -> User (<user_id> - <user_name>) -> Chat (<session_title>) -> file
    """
    service = get_drive_service()
    if not service:
        return {
            "success": False,
            "error": "Google Drive service is not initialized. Please verify credentials."
        }

    root_id = get_root_folder_id()
    if not root_id:
        return {
            "success": False,
            "error": "GDRIVE_ROOT_FOLDER_ID is not configured in .env or app_config.json."
        }

    try:
        from googleapiclient.http import MediaIoBaseUpload

        # 1. User Folder: "101 - Muhammad Rizwan"
        clean_user = "".join(c for c in user_name if c.isalnum() or c in (" ", "_", "-")).strip() or "User"
        user_folder_name = f"{user_id} - {clean_user}"
        user_folder_id = get_or_create_folder(user_folder_name, parent_id=root_id)
        if not user_folder_id:
            user_folder_id = root_id

        # 2. Session Folder: "Oracle APEX responsive (2e9625d6)"
        clean_title = "".join(c for c in session_title if c.isalnum() or c in (" ", "_", "-")).strip()
        if not clean_title:
            clean_title = "Chat Session"
        short_sid = session_id[:8] if session_id else "chat"
        session_folder_name = f"{clean_title[:45]} ({short_sid})"
        session_folder_id = get_or_create_folder(session_folder_name, parent_id=user_folder_id)
        if not session_folder_id:
            session_folder_id = user_folder_id

        # 3. File metadata & upload
        file_metadata = {
            "name": filename,
            "parents": [session_folder_id]
        }
        media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)

        created_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, name, webViewLink, webContentLink, size",
            supportsAllDrives=True
        ).execute()

        file_id = created_file.get("id")

        # 4. Make file shareable (Anyone with link can view)
        try:
            service.permissions().create(
                fileId=file_id,
                body={"role": "reader", "type": "anyone"},
                supportsAllDrives=True
            ).execute()
        except Exception as perm_err:
            logger.warning(f"[GDrive] Could not set public view permission: {perm_err}")

        web_link = created_file.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
        download_link = created_file.get("webContentLink") or f"https://drive.google.com/uc?export=download&id={file_id}"

        return {
            "success": True,
            "file_id": file_id,
            "filename": filename,
            "web_link": web_link,
            "download_link": download_link,
            "size": len(file_bytes),
            "folder_id": session_folder_id,
            "user_folder": user_folder_name,
            "session_folder": session_folder_name
        }

    except Exception as e:
        err_msg = str(e)
        if "storageQuotaExceeded" in err_msg or "Service Accounts do not have storage quota" in err_msg:
            logger.warning("[GDrive Quota] Service accounts cannot own files in personal Google Drive ('My Drive'). To upload files to your 5TB drive, either: (1) Use a Google Workspace Shared Drive and add the service account as Manager, or (2) Run setup_gdrive_oauth.py to authorize your personal Google One account.")
        else:
            logger.error(f"[GDrive] Upload failed for '{filename}': {e}")
        return {
            "success": False,
            "error": err_msg,
            "is_quota_error": "storageQuotaExceeded" in err_msg
        }
