"""Uzak Panel - web sunucusu."""
import hmac
import ipaddress
import json
import os
import secrets
import stat
import threading
import time
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

import bcrypt
import psutil
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from epic import EpicWatcher
from metrics import Sampler

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
CONFIG = json.loads((ROOT / "config.json").read_text("utf-8"))

SESSION_COOKIE = "up_session"
SESSION_IDLE = CONFIG.get("session_idle_minutes", 120) * 60
SESSION_MAX = CONFIG.get("session_max_hours", 12) * 3600
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 15 * 60
TREE_LIMIT = CONFIG.get("tree_max_entries", 300_000)
CHUNK = 1024 * 1024

sampler = Sampler()
sampler.start()
epic_watcher = EpicWatcher()  # Epic Games Launcher / Fab indirmeleri (günlükten)
epic_watcher.start()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# ------------------------------------------------------------------ oturum
_sessions = {}
_sessions_lock = threading.Lock()
_fails = defaultdict(deque)


# https://www.cloudflare.com/ips/
CF_NETS = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
    *CONFIG.get("extra_allowed_networks", []),
)]


def peer_allowed(host: str) -> bool:
    """Cloudflare'i atlayıp doğrudan IP'ye gelen bağlantıları reddetmek için."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_private:  # yerel ağdan erişim serbest
        return True
    return any(ip in n for n in CF_NETS)


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else "?"
    cf = request.headers.get("cf-connecting-ip")
    if cf and CONFIG.get("trust_cf_header") and peer_allowed(host):
        return cf
    return host


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def get_session(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    now = time.time()
    with _sessions_lock:
        s = _sessions.get(token)
        if not s:
            return None
        if now - s["last"] > SESSION_IDLE or now - s["created"] > SESSION_MAX:
            _sessions.pop(token, None)
            return None
        s["last"] = now
        return s


def require_auth(request: Request):
    s = get_session(request)
    if not s:
        raise HTTPException(401, "Oturum gerekli")
    return s


def require_csrf(request: Request):
    # SameSite=Strict çerezine ek olarak, form ile taklit edilemeyen bir başlık iste
    if request.headers.get("x-requested-with") != "uzakpanel":
        raise HTTPException(403, "Geçersiz istek")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if CONFIG.get("cloudflare_only") and not peer_allowed(request.client.host if request.client else ""):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    response = await call_next(request)
    h = response.headers
    h["X-Frame-Options"] = "DENY"
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "no-referrer"
    h["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
        "img-src 'self' data: https://media.fab.com; connect-src 'self'; frame-ancestors 'none'"
    )
    if is_https(request):
        h["Strict-Transport-Security"] = "max-age=31536000"
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    return response


@app.post("/api/login", dependencies=[Depends(require_csrf)])
async def login(request: Request):
    ip = client_ip(request)
    now = time.time()
    q = _fails[ip]
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    if len(q) >= LOGIN_MAX_FAILS:
        wait = int(LOGIN_WINDOW - (now - q[0]))
        raise HTTPException(429, f"Çok fazla hatalı deneme. {wait // 60 + 1} dakika sonra tekrar deneyin.")

    body = await request.json()
    user = str(body.get("username", "")).strip().lower()
    pw = str(body.get("password", "")).encode("utf-8")
    user_ok = hmac.compare_digest(user.encode(), CONFIG["username"].lower().encode())
    pw_ok = bcrypt.checkpw(pw, CONFIG["password_hash"].encode())
    if not (user_ok and pw_ok):
        q.append(now)
        raise HTTPException(401, "E-posta veya şifre hatalı")

    q.clear()
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = {"created": now, "last": now, "ip": ip, "user": CONFIG["username"]}
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict",
                    secure=is_https(request), max_age=SESSION_MAX, path="/")
    return resp


@app.post("/api/logout", dependencies=[Depends(require_csrf)])
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    with _sessions_lock:
        _sessions.pop(token, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/api/me")
async def me(s=Depends(require_auth)):
    return {"user": s["user"]}


# ------------------------------------------------------------------ metrikler
@app.get("/api/system", dependencies=[Depends(require_auth)])
async def system():
    return sampler.system


@app.get("/api/metrics", dependencies=[Depends(require_auth)])
async def metrics():
    return sampler.get_snapshot()


@app.get("/api/history", dependencies=[Depends(require_auth)])
async def history():
    return sampler.get_history()


@app.get("/api/processes", dependencies=[Depends(require_auth)])
async def processes():
    return sampler.get_processes()


@app.get("/api/epic", dependencies=[Depends(require_auth)])
async def epic_downloads():
    return epic_watcher.snapshot()


# ------------------------------------------------------------------ dosyalar
def resolve(path: str) -> Path:
    if not path:
        raise HTTPException(400, "Yol gerekli")
    p = Path(os.path.abspath(path))
    if not p.exists():
        raise HTTPException(404, "Bulunamadı")
    return p


def _is_link(entry: os.DirEntry) -> bool:
    try:
        return entry.is_symlink() or entry.is_junction()
    except OSError:
        return True


@app.get("/api/fs/roots", dependencies=[Depends(require_auth)])
def fs_roots():
    drives = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
            drives.append({"path": part.mountpoint, "total": u.total, "free": u.free})
        except OSError:
            pass
    home = Path.home()
    quick = []
    for name, sub in (("Masaüstü", "Desktop"), ("Belgeler", "Documents"), ("İndirilenler", "Downloads"),
                      ("Resimler", "Pictures"), ("Ev", "")):
        # OneDrive yedeklemesi açıksa Masaüstü/Belgeler/Resimler OneDrive altına taşınır
        for cand in (home / sub, home / "OneDrive" / sub) if sub else (home,):
            if cand.exists():
                quick.append({"name": name, "path": str(cand)})
                break
    return {"drives": drives, "quick": quick}


@app.get("/api/fs/list", dependencies=[Depends(require_auth)])
def fs_list(path: str):
    p = resolve(path)
    if not p.is_dir():
        raise HTTPException(400, "Klasör değil")
    entries = []
    try:
        it = list(os.scandir(p))
    except PermissionError:
        raise HTTPException(403, "Bu klasöre erişim izni yok")
    for e in it:
        try:
            st = e.stat(follow_symlinks=False)
            is_dir = e.is_dir(follow_symlinks=False) or _is_link(e) and e.is_dir()
            hidden = bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_HIDDEN) or e.name.startswith(".")
            entries.append({
                "name": e.name, "dir": is_dir, "size": 0 if is_dir else st.st_size,
                "mtime": st.st_mtime, "hidden": hidden, "link": _is_link(e),
            })
        except OSError:
            entries.append({"name": e.name, "dir": False, "size": 0, "mtime": 0, "hidden": False, "error": True})
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    parent = str(p.parent) if p.parent != p else None
    return {"path": str(p), "parent": parent, "entries": entries}


@app.get("/api/fs/tree", dependencies=[Depends(require_auth)])
def fs_tree(path: str):
    """Klasörün tüm alt içeriğini düz bir liste olarak döndürür (kopyalama için)."""
    root = resolve(path)
    if not root.is_dir():
        raise HTTPException(400, "Klasör değil")
    files, dirs, errors = [], [], []
    total = 0
    truncated = False
    stack = [""]
    while stack:
        rel = stack.pop()
        full = root / rel if rel else root
        try:
            it = list(os.scandir(full))
        except OSError as ex:
            errors.append({"p": rel, "e": ex.strerror or str(ex)})
            continue
        for e in it:
            if len(files) + len(dirs) >= TREE_LIMIT:
                truncated = True
                break
            r = f"{rel}/{e.name}" if rel else e.name
            if _is_link(e):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    dirs.append(r)
                    stack.append(r)
                else:
                    s = e.stat(follow_symlinks=False).st_size
                    files.append({"p": r, "s": s})
                    total += s
            except OSError as ex:
                errors.append({"p": r, "e": ex.strerror or str(ex)})
        if truncated:
            break
    return {"root": str(root), "name": root.name or str(root).rstrip(":\\"), "dirs": dirs,
            "files": files, "total": total, "errors": errors, "truncated": truncated}


def _attachment(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@app.get("/api/fs/download", dependencies=[Depends(require_auth)])
def fs_download(path: str):
    p = resolve(path)
    if not p.is_file():
        raise HTTPException(400, "Dosya değil")
    try:
        with open(p, "rb"):
            pass
    except OSError as ex:
        raise HTTPException(403, ex.strerror or "Dosya okunamıyor")
    return FileResponse(p, media_type="application/octet-stream",
                        headers={"Content-Disposition": _attachment(p.name)})


class _ZipSink:
    """zipfile'ın yazdığı baytları biriktirip akış halinde dışarı verir."""

    def __init__(self):
        self.buf = bytearray()
        self.pos = 0

    def write(self, b):
        self.buf += b
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def flush(self):
        pass

    def take(self):
        out = bytes(self.buf)
        self.buf.clear()
        return out


def _zip_stream(root: Path):
    sink = _ZipSink()
    base = root.name or "disk"
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
                           and not os.path.isjunction(os.path.join(dirpath, d))]
            rel_dir = os.path.relpath(dirpath, root)
            arc_dir = base if rel_dir == "." else f"{base}/{rel_dir.replace(os.sep, '/')}"
            if not filenames and not dirnames:
                zf.writestr(arc_dir + "/", b"")
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                try:
                    st = os.stat(full)
                    info = zipfile.ZipInfo.from_file(full, f"{arc_dir}/{fn}")
                    info.compress_type = zipfile.ZIP_STORED
                    with open(full, "rb") as src, zf.open(info, "w", force_zip64=st.st_size > 2**31) as dst:
                        while chunk := src.read(CHUNK):
                            dst.write(chunk)
                            if len(sink.buf) >= CHUNK:
                                yield sink.take()
                except OSError:
                    continue
                if sink.buf:
                    yield sink.take()
    if sink.buf:
        yield sink.take()


@app.get("/api/fs/zip", dependencies=[Depends(require_auth)])
def fs_zip(path: str):
    p = resolve(path)
    if not p.is_dir():
        raise HTTPException(400, "Klasör değil")
    name = (p.name or "disk") + ".zip"
    return StreamingResponse(_zip_stream(p), media_type="application/zip",
                             headers={"Content-Disposition": _attachment(name)})


# ------------------------------------------------------------------ sayfalar
@app.get("/")
async def index(request: Request):
    if not get_session(request):
        return RedirectResponse("/login", 303)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
async def login_page(request: Request):
    if get_session(request):
        return RedirectResponse("/", 303)
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
