"""Aile sitesinin sosyal katmanı (kaderoglu.com): hesaplar, giriş zorunluluğu, Duvar, yorum/beğeni,
bildirimler ve yüklemeler.

Bağlama (app/main.py):   family_app = social.wrap(family.app)
wrap() aile sitesinin önüne bir giriş kapısı koyar: oturumu olmayan hiçbir sayfa, API veya /media isteği
geçemez. /giris, /duvar ve /social/... adresleri bu modülün kendi uygulamasına gider.

Hesaplar data/family.json'daki her kişi için kendiliğinden açılır (kullanıcı adı isim.soyisim, İngilizce harflerle).
Başlangıç şifresi DEFAULT_PASSWORD'dur; ilk girişte değiştirmek zorunludur.
Veri: data/social.db (SQLite), yüklemeler data/uploads/ (ham dosyalar raw/, sitede gösterilenler media/).
Yüklenen fotoğraf/videolar arka planda tools/ingest_uploads.py ile işlenir (küçültme, üst veri silme, yüz tanıma).
"""
import hashlib
import json
import os
import queue
import random
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import unicodedata
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote, unquote

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

import server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
DATA = Path(os.environ.get("KADEROGLU_DATA", ROOT / "data"))  # test için ayrı veri klasörü verilebilir
DB_PATH = DATA / "social.db"
UPLOADS = DATA / "uploads"
STATIC = Path(__file__).resolve().parent / "social_static"

# başlangıç şifresi kodda değil config.json'da (depoya girmez); ilk girişte değiştirmek zorunlu
DEFAULT_PASSWORD = server.CONFIG.get("social_default_password") or secrets.token_urlsafe(12)
ADMIN_PEOPLE = {"muhammed"}
# aynı ada sahip kişiler: kullanıcı adları elle verilir
USERNAME_OVERRIDES = {"elmas-anne": "elmas.kaderoglu", "elmas-kiz": "elmas.kaderoglu.kucuk"}
COOKIE = "kf_session"
SESSION_IDLE = 30 * 86400
SESSION_MAX = 90 * 86400
LOGIN_WINDOW = 15 * 60
LOGIN_MAX_IP = 10
LOGIN_MAX_USER = 8
PASSWORD_MIN = 8
UPLOAD_MAX = {"photo": 40 * 1024 * 1024, "video": 600 * 1024 * 1024}
UPLOADS_PER_HOUR = 40
CAPTION_MAX = 2200
COMMENT_MAX = 1000
MEDIA_RE = re.compile(r"^[0-9a-f]{20}$")
MENTION_RE = re.compile(r"@([a-z0-9]+(?:\.[a-z0-9]+)*)")
MOSAIC = UPLOADS / "mosaic.jpg"
MOSAIC_TILE, MOSAIC_GAP, MOSAIC_COLS, MOSAIC_ROWS = 52, 3, 44, 32  # 1408 kare, 2288x1664 piksel
MOSAIC_MAX_AGE = 24 * 3600

# ------------------------------------------------------------------ kullanıcı adı
_TR = str.maketrans({"ç": "c", "ğ": "g", "ı": "i", "İ": "i", "ö": "o", "ş": "s", "ü": "u",
                     "Ç": "c", "Ğ": "g", "Ö": "o", "Ş": "s", "Ü": "u", "â": "a", "î": "i", "û": "u"})


def slug(name: str) -> str:
    """'Ömer Faruk Bolay' -> 'omer.faruk.bolay'"""
    s = unicodedata.normalize("NFKD", name.translate(_TR)).encode("ascii", "ignore").decode().lower()
    return ".".join(re.findall(r"[a-z0-9]+", s))


# ------------------------------------------------------------------ veritabanı
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, person TEXT UNIQUE NOT NULL, pw TEXT NOT NULL,
  must_change INTEGER NOT NULL DEFAULT 1, admin INTEGER NOT NULL DEFAULT 0,
  created REAL, last_login REAL, pw_changed REAL);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL NOT NULL, last REAL NOT NULL, ip TEXT);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, media TEXT NOT NULL, kind TEXT NOT NULL,
  caption TEXT NOT NULL DEFAULT '', created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'processing',
  raw TEXT, w INTEGER, h INTEGER, dur REAL, taken REAL,
  people TEXT NOT NULL DEFAULT '[]', faces TEXT NOT NULL DEFAULT '[]', nsfw TEXT NOT NULL DEFAULT '[]', error TEXT,
  archived INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS posts_media ON posts(media);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY, item TEXT NOT NULL, user_id INTEGER NOT NULL, body TEXT NOT NULL,
  created REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS comments_item ON comments(item, id);
CREATE TABLE IF NOT EXISTS likes (
  item TEXT NOT NULL, user_id INTEGER NOT NULL, created REAL NOT NULL, PRIMARY KEY (item, user_id));
CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, actor_id INTEGER, type TEXT NOT NULL,
  item TEXT, post_id INTEGER, body TEXT, created REAL NOT NULL, read INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS notif_user ON notifications(user_id, id);
"""

_local = threading.local()
_init_lock = threading.Lock()
_ready = False


def db() -> sqlite3.Connection:
    c = getattr(_local, "db", None)
    if c is None:
        DATA.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.db = c
    return c


def init(start_worker=True):
    """Tabloları oluşturur, eksik hesapları açar, yarım kalmış yüklemeleri kuyruğa alır (bir kez)."""
    global _ready
    if _ready:
        return
    with _init_lock:
        if _ready:
            return
        for sub in ("raw", "media/full", "media/thumb", "media/video", "media/vframes"):
            (UPLOADS / sub).mkdir(parents=True, exist_ok=True)
        db().executescript(SCHEMA)
        sync_users()
        for r in db().execute("SELECT id FROM posts WHERE status='processing' AND deleted=0"):
            _queue.put(r["id"])
        _ready = True
    if start_worker:
        _start_worker()


# ------------------------------------------------------------------ aile verisi
_fam_cache = {"key": None, "data": None}


def family_data():
    p = DATA / "family.json"
    key = p.stat().st_mtime
    if _fam_cache["key"] != key:
        _fam_cache["data"] = json.loads(p.read_text("utf-8"))
        _fam_cache["key"] = key
        if _ready:
            sync_users()
    return _fam_cache["data"]


_idx_cache = {"key": None, "data": None}


def index_data():
    """Fotoğraf dizini. Aile sitesiyle aynı süreçte family._load()'un önbelleği kullanılır (bellek iki kez dolmasın)."""
    fam_mod = sys.modules.get("family")
    if fam_mod is not None and hasattr(fam_mod, "_load"):
        return fam_mod._load()[1]
    p = DATA / "index.json"
    if not p.exists():
        return {"photos": {}, "people": {}}
    key = p.stat().st_mtime
    if _idx_cache["key"] != key:
        _idx_cache["data"] = json.loads(p.read_text("utf-8"))
        _idx_cache["key"] = key
    return _idx_cache["data"]


def sync_users():
    """family.json'daki her kişi için hesap açar (var olanlara dokunmaz)."""
    fam = json.loads((DATA / "family.json").read_text("utf-8"))
    c = db()
    have = {r["person"] for r in c.execute("SELECT person FROM users")}
    taken = {r["username"] for r in c.execute("SELECT username FROM users")}
    for pid, p in fam["people"].items():
        if pid in have:
            continue
        name = USERNAME_OVERRIDES.get(pid) or slug(p["name"]) or slug(pid)
        base, n = name, 2
        while name in taken:
            name, n = f"{base}{n}", n + 1
        pw = bcrypt.hashpw(DEFAULT_PASSWORD.encode(), bcrypt.gensalt()).decode()
        with c:
            c.execute("INSERT INTO users (username, person, pw, must_change, admin, created) VALUES (?,?,?,?,?,?)",
                      (name, pid, pw, 1, int(pid in ADMIN_PEOPLE), time.time()))
        taken.add(name)
        print(f"[social] hesap açıldı: {name} ({p['name']})", flush=True)


_cards = {"t": 0, "key": None, "by_id": {}}


def users():
    """kullanıcı kimliği -> herkese gösterilen bilgiler (ad, profil fotoğrafı). 5 sn önbellekli."""
    fam = family_data()
    idx = index_data()
    now = time.time()
    if now - _cards["t"] < 5 and _cards["key"] == (id(fam), id(idx)):
        return _cards["by_id"]
    av_v = idx.get("avatar_v", {})
    by_id = {}
    for r in db().execute("SELECT id, username, person, admin FROM users"):
        p = fam["people"].get(r["person"], {})
        v = av_v.get(r["person"]) or idx.get("avatars", {}).get(r["person"])
        by_id[r["id"]] = {"id": r["id"], "username": r["username"], "person": r["person"],
                          "name": p.get("name", r["username"]), "role": p.get("role", ""),
                          "avatar": f"/media/avatar/{r['person']}.jpg?v={v}" if v else None}
    _cards.update(t=now, key=(id(fam), id(idx)), by_id=by_id)
    return by_id


def by_person():
    return {c["person"]: uid for uid, c in users().items()}


# ------------------------------------------------------------------ oturum
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_user(token):
    if not token or len(token) > 100:
        return None
    init()
    now = time.time()
    c = db()
    r = c.execute("SELECT s.token, s.created AS s_created, s.last AS s_last, u.id, u.username, u.person, "
                  "u.must_change, u.admin FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
                  (_hash(token),)).fetchone()
    if not r:
        return None
    if now - r["s_last"] > SESSION_IDLE or now - r["s_created"] > SESSION_MAX:
        with c:
            c.execute("DELETE FROM sessions WHERE token = ?", (r["token"],))
        return None
    if now - r["s_last"] > 300:
        with c:
            c.execute("UPDATE sessions SET last = ? WHERE token = ?", (now, r["token"]))
    return {"id": r["id"], "username": r["username"], "person": r["person"],
            "must_change": bool(r["must_change"]), "admin": bool(r["admin"])}


_MISSING = object()


def get_user(request: Request):
    u = request.scope.get("social_user", _MISSING)
    if u is _MISSING:
        u = session_user(request.cookies.get(COOKIE))
        request.scope["social_user"] = u
    return u


def require_login(request: Request):
    """Oturum yeterli (şifresini henüz değiştirmemiş olabilir)."""
    u = get_user(request)
    if not u:
        raise HTTPException(401, "Oturum gerekli")
    return u


def require_user(request: Request):
    u = require_login(request)
    if u["must_change"]:
        raise HTTPException(403, "Önce şifrenizi değiştirin")
    return u


def is_admin(request: Request) -> bool:
    """Aile hesabı yönetici mi, ya da panel hesabıyla (config.json) giriş yapılmış mı."""
    u = get_user(request)
    return bool(u and u["admin"] and not u["must_change"]) or bool(server.get_session(request))


def require_admin(request: Request):
    if not is_admin(request):
        raise HTTPException(401, "Yönetici oturumu gerekli")
    return True


require_csrf = server.require_csrf  # X-Requested-With: uzakpanel


# ------------------------------------------------------------------ uygulama
sub = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@sub.middleware("http")
async def headers(request: Request, call_next):
    host = request.client.host if request.client else ""
    if server.CONFIG.get("cloudflare_only") and not server.peer_allowed(host):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    response = await call_next(request)
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "same-origin"
    h["X-Frame-Options"] = "DENY"
    h["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    h["X-Robots-Tag"] = "noindex, noimageindex"
    if server.is_https(request):
        h["Strict-Transport-Security"] = "max-age=31536000"
    path = request.url.path
    if path.startswith("/social/api/"):
        h["Cache-Control"] = "no-store"
    elif path.startswith("/social/assets/"):
        h["Cache-Control"] = "no-cache"
    return response


async def _body(request: Request) -> dict:
    try:
        d = await request.json()
    except Exception:
        raise HTTPException(400, "Geçersiz istek")
    if not isinstance(d, dict):
        raise HTTPException(400, "Geçersiz istek")
    return d


def _page(name: str):
    html = (STATIC / name).read_text("utf-8")
    for asset in re.findall(r'/social/assets/([\w.-]+\.(?:css|js))', html):
        v = int((STATIC / asset).stat().st_mtime)
        html = html.replace(f"/social/assets/{asset}\"", f"/social/assets/{asset}?v={v}\"")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _safe_next(n: str | None) -> str:
    if not n or not n.startswith("/") or n.startswith("//") or "\\" in n or n.startswith("/giris"):
        return "/"
    return n


@sub.get("/giris")
def login_page(request: Request, next: str | None = None):
    u = get_user(request)
    if u and not u["must_change"]:
        return RedirectResponse(_safe_next(next), status_code=303)
    return _page("login.html")


@sub.get("/duvar")
def wall_page(request: Request):
    u = get_user(request)
    if not u or u["must_change"]:
        return RedirectResponse("/giris?next=/duvar", status_code=303)
    return _page("wall.html")


# ------------------------------------------------------------------ giriş / şifre
_fails_ip = defaultdict(deque)
_fails_user = defaultdict(deque)
_fails_lock = threading.Lock()
_dummy_hash = None


def _too_many(q, limit, now):
    while q and now - q[0] > LOGIN_WINDOW:
        q.popleft()
    return len(q) >= limit


def _check_pw(pw: str, stored: str | None) -> bool:
    global _dummy_hash
    if stored is None:
        # kullanıcı yoksa da aynı süre harcansın (kullanıcı adı tahmini zorlaşsın)
        _dummy_hash = _dummy_hash or bcrypt.hashpw(b"-", bcrypt.gensalt())
        bcrypt.checkpw(pw.encode(), _dummy_hash)
        return False
    return bcrypt.checkpw(pw.encode("utf-8"), stored.encode())


def _new_session(request: Request, user_id: int, resp):
    token = secrets.token_urlsafe(32)
    now = time.time()
    c = db()
    with c:
        c.execute("INSERT INTO sessions (token, user_id, created, last, ip) VALUES (?,?,?,?,?)",
                  (_hash(token), user_id, now, now, server.client_ip(request)))
        c.execute("DELETE FROM sessions WHERE last < ?", (now - SESSION_IDLE,))
    # Domain verilmez: çerez sadece kaderoglu.com'a gider (başka sunuculardaki alt alan adlarına değil)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=server.is_https(request),
                    max_age=SESSION_MAX, path="/")


@sub.post("/social/api/login", dependencies=[Depends(require_csrf)])
async def login(request: Request):
    init()
    ip = server.client_ip(request)
    body = await _body(request)
    raw = str(body.get("username", "")).strip()[:120]
    pw = str(body.get("password", ""))[:200]
    uname = raw.lower() if re.fullmatch(r"[a-z0-9.]+", raw.lower()) else slug(raw)
    now = time.time()
    with _fails_lock:
        if _too_many(_fails_ip[ip], LOGIN_MAX_IP, now) or _too_many(_fails_user[uname], LOGIN_MAX_USER, now):
            raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.")
    row = db().execute("SELECT id, pw, must_change FROM users WHERE username = ?", (uname,)).fetchone()
    ok = await run_in_threadpool(_check_pw, pw, row["pw"] if row else None)
    if not ok:
        with _fails_lock:
            _fails_ip[ip].append(now)
            _fails_user[uname].append(now)
        raise HTTPException(401, "Kullanıcı adı veya şifre hatalı")
    with _fails_lock:
        _fails_user.pop(uname, None)
    c = db()
    with c:
        c.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, row["id"]))
    resp = JSONResponse({"ok": True, "must_change": bool(row["must_change"])})
    _new_session(request, row["id"], resp)
    return resp


@sub.post("/social/api/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        c = db()
        with c:
            c.execute("DELETE FROM sessions WHERE token = ?", (_hash(token),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@sub.post("/social/api/password", dependencies=[Depends(require_csrf)])
async def change_password(request: Request, u=Depends(require_login)):
    body = await _body(request)
    cur, new = str(body.get("current", ""))[:200], str(body.get("new", ""))[:200]
    ip = server.client_ip(request)
    now = time.time()
    with _fails_lock:
        if _too_many(_fails_ip[ip], LOGIN_MAX_IP, now):
            raise HTTPException(429, "Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.")
    row = db().execute("SELECT pw FROM users WHERE id = ?", (u["id"],)).fetchone()
    if not await run_in_threadpool(_check_pw, cur, row["pw"]):
        with _fails_lock:
            _fails_ip[ip].append(now)
        raise HTTPException(400, "Mevcut şifre yanlış")
    if len(new) < PASSWORD_MIN:
        raise HTTPException(400, f"Yeni şifre en az {PASSWORD_MIN} karakter olmalı")
    if new == DEFAULT_PASSWORD or new == cur:
        raise HTTPException(400, "Yeni şifre eskisinden farklı olmalı")
    if new.lower() == u["username"] or new.lower() in u["username"].split("."):
        raise HTTPException(400, "Şifre kullanıcı adınızı içermemeli")
    pw = await run_in_threadpool(lambda: bcrypt.hashpw(new.encode("utf-8"), bcrypt.gensalt()).decode())
    c = db()
    with c:
        c.execute("UPDATE users SET pw = ?, must_change = 0, pw_changed = ? WHERE id = ?", (pw, now, u["id"]))
        # diğer cihazlardaki oturumlar kapanır
        c.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?",
                  (u["id"], _hash(request.cookies.get(COOKIE, ""))))
    return {"ok": True}


@sub.get("/social/api/me")
def me(request: Request, u=Depends(require_login)):
    card = users().get(u["id"], {})
    return {**card, "admin": u["admin"], "must_change": u["must_change"]}


@sub.get("/social/api/users")
def list_users(u=Depends(require_user)):
    return {"users": sorted(users().values(), key=lambda c: c["name"])}


@sub.post("/social/api/admin/reset", dependencies=[Depends(require_csrf), Depends(require_admin)])
async def admin_reset(request: Request):
    """Şifresini unutan biri için: şifre başlangıç şifresine döner, girişte yeniden değiştirmesi istenir."""
    name = str((await _body(request)).get("username", "")).strip().lower()
    pw = await run_in_threadpool(lambda: bcrypt.hashpw(DEFAULT_PASSWORD.encode(), bcrypt.gensalt()).decode())
    c = db()
    with c:
        cur = c.execute("UPDATE users SET pw = ?, must_change = 1 WHERE username = ?", (pw, name))
        if not cur.rowcount:
            raise HTTPException(404, "Kullanıcı bulunamadı")
        c.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (name,))
    return {"ok": True}


# ------------------------------------------------------------------ öğeler (fotoğraf / video)
def _check_item(item):
    if not MEDIA_RE.match(item or ""):
        raise HTTPException(404, "Bulunamadı")


def item_info(item: str, u=None):
    """Bir fotoğraf/videonun adresleri ve içindeki kişiler. Hem arşivdekiler hem Duvar yüklemeleri."""
    _check_item(item)
    post = db().execute("SELECT * FROM posts WHERE media = ? AND deleted = 0 ORDER BY id DESC LIMIT 1",
                        (item,)).fetchone()
    ph = index_data()["photos"].get(item)
    if ph and _hidden(ph) and not (u and u["admin"]):
        ph = None
    if post and _visible(post, u):
        base = "/social/media"
        kind = post["kind"]
        people = sorted(set(json.loads(post["people"])) | set((ph or {}).get("people", [])))
        info = {"item": item, "kind": kind, "w": post["w"], "h": post["h"], "taken": post["taken"],
                "thumb": f"{base}/thumb/{item}.jpg", "full": f"{base}/full/{item}.jpg",
                "video": f"{base}/video/{item}.mp4" if kind == "video" else None,
                "people": people, "faces": json.loads(post["faces"]), "post_id": post["id"],
                "owner": post["user_id"], "caption": post["caption"], "status": post["status"],
                "created": post["created"]}
        return info
    if ph:
        kind = ph.get("type", "photo")
        return {"item": item, "kind": kind, "w": ph["w"], "h": ph["h"], "taken": ph.get("taken"),
                "thumb": f"/media/thumb/{item}.jpg", "full": f"/media/full/{item}.jpg",
                "video": f"/media/video/{item}.mp4" if kind == "video" else None,
                "people": ph.get("people", []), "faces": ph.get("faces", []), "post_id": None,
                "owner": None, "caption": "", "status": "ready", "created": ph.get("taken")}
    raise HTTPException(404, "Fotoğraf bulunamadı")


def _hidden(ph):
    """Çıplaklık filtresi: aile sitesi kararı (_hidden, moderation.json dahil) varsa o, yoksa şüpheli işareti."""
    return ph["_hidden"] if "_hidden" in ph else bool(ph.get("hidden") or ph.get("nsfw"))


def _visible(post, u):
    """Hazır ve şüpheli bulunmamış paylaşımlar herkese; diğerleri sahibine ve yöneticiye görünür."""
    if u and (post["user_id"] == u["id"] or u["admin"]):
        return True
    return post["status"] == "ready" and post["nsfw"] == "[]"


def comment_json(r):
    return {"id": r["id"], "user": users().get(r["user_id"]), "body": r["body"], "created": r["created"]}


def item_social(item, u, preview=None):
    c = db()
    likes = c.execute("SELECT COUNT(*) FROM likes WHERE item = ?", (item,)).fetchone()[0]
    liked = bool(c.execute("SELECT 1 FROM likes WHERE item = ? AND user_id = ?", (item, u["id"])).fetchone())
    ncom = c.execute("SELECT COUNT(*) FROM comments WHERE item = ? AND deleted = 0", (item,)).fetchone()[0]
    out = {"likes": likes, "liked": liked, "comment_count": ncom}
    if preview is None:
        rows = c.execute("SELECT * FROM comments WHERE item = ? AND deleted = 0 ORDER BY id LIMIT 500", (item,))
        out["comments"] = [comment_json(r) for r in rows]
        likers = c.execute("SELECT user_id FROM likes WHERE item = ? ORDER BY created DESC LIMIT 30", (item,))
        out["likers"] = [users().get(r["user_id"]) for r in likers]
    else:
        rows = c.execute("SELECT * FROM comments WHERE item = ? AND deleted = 0 ORDER BY id DESC LIMIT ?",
                         (item, preview)).fetchall()
        out["comments"] = [comment_json(r) for r in reversed(rows)]
    return out


@sub.get("/social/api/items/{item}")
def get_item(item: str, u=Depends(require_user)):
    info = item_info(item, u)
    names = users()
    bp = by_person()
    info["people"] = [{"person": p, "name": family_data()["people"].get(p, {}).get("name", p),
                       "user": names.get(bp.get(p))} for p in info["people"]]
    if info.get("owner"):
        info["owner"] = names.get(info["owner"])
    return {**info, **item_social(item, u)}


@sub.post("/social/api/items/{item}/like", dependencies=[Depends(require_csrf)])
def toggle_like(item: str, u=Depends(require_user)):
    info = item_info(item, u)
    c = db()
    now = time.time()
    with c:
        if c.execute("DELETE FROM likes WHERE item = ? AND user_id = ?", (item, u["id"])).rowcount:
            liked = False
        else:
            c.execute("INSERT INTO likes (item, user_id, created) VALUES (?,?,?)", (item, u["id"], now))
            liked = True
            owner = info.get("owner")
            dup = c.execute("SELECT 1 FROM notifications WHERE type='like' AND item=? AND actor_id=?",
                            (item, u["id"])).fetchone()
            if owner and owner != u["id"] and not dup:
                _notify(c, owner, u["id"], "like", item, info.get("post_id"), "")
        n = c.execute("SELECT COUNT(*) FROM likes WHERE item = ?", (item,)).fetchone()[0]
    return {"liked": liked, "likes": n}


def _words(text):
    return set(slug(text).split("."))


@sub.post("/social/api/items/{item}/comments", dependencies=[Depends(require_csrf)])
async def add_comment(item: str, request: Request, u=Depends(require_user)):
    body = str((await _body(request)).get("body", "")).strip()
    body = re.sub(r"[\u0000-\u0008\u000b-\u001f\u007f‪-‮⁦-⁩]", "", body)  # kontrol/yön karakterleri
    if not body:
        raise HTTPException(400, "Yorum boş olamaz")
    if len(body) > COMMENT_MAX:
        raise HTTPException(400, f"Yorum en fazla {COMMENT_MAX} karakter olabilir")
    return await run_in_threadpool(_add_comment, item, body, u)


def _add_comment(item, body, u):
    info = item_info(item, u)
    c = db()
    now = time.time()
    recent = c.execute("SELECT COUNT(*) FROM comments WHERE user_id = ? AND created > ?", (u["id"], now - 60)).fetchone()[0]
    if recent >= 15:
        raise HTTPException(429, "Biraz yavaş: dakikada en fazla 15 yorum")
    with c:
        cid = c.execute("INSERT INTO comments (item, user_id, body, created) VALUES (?,?,?,?)",
                        (item, u["id"], body, now)).lastrowid
        for uid, kind in comment_targets(body, info, u["id"]).items():
            _notify(c, uid, u["id"], kind, item, info.get("post_id"), body[:160])
    row = c.execute("SELECT * FROM comments WHERE id = ?", (cid,)).fetchone()
    return {"comment": comment_json(row), "comment_count":
            c.execute("SELECT COUNT(*) FROM comments WHERE item = ? AND deleted = 0", (item,)).fetchone()[0]}


def comment_targets(body, info, actor):
    """Yorumdan kimin haberi olmalı: @kullanıcı adıyla anılanlar ve fotoğraftaki kişiler (adı geçiyorsa
    'mention', geçmiyorsa 'tagged'), paylaşımın sahibi ('comment'). Herkese tek bildirim."""
    cards = users()
    by_name = {c["username"]: uid for uid, c in cards.items()}
    bp = by_person()
    fam = family_data()["people"]
    words = _words(body)
    targets = {}
    for m in MENTION_RE.findall(body.lower()):
        if m in by_name:
            targets.setdefault(by_name[m], "mention")
    for pid in info.get("people", []):
        uid = bp.get(pid)
        if uid is None or uid in targets:
            continue
        first = slug(fam.get(pid, {}).get("name", "")).split(".")[0]
        targets[uid] = "mention" if first and first in words else "tagged"
    owner = info.get("owner")
    if owner and owner not in targets:
        targets[owner] = "comment"
    targets.pop(actor, None)
    return targets


def _notify(c, user_id, actor_id, kind, item, post_id, body):
    c.execute("INSERT INTO notifications (user_id, actor_id, type, item, post_id, body, created) VALUES (?,?,?,?,?,?,?)",
              (user_id, actor_id, kind, item, post_id, body, time.time()))


@sub.delete("/social/api/comments/{cid}", dependencies=[Depends(require_csrf)])
def delete_comment(cid: int, request: Request, u=Depends(require_user)):
    c = db()
    r = c.execute("SELECT user_id, item FROM comments WHERE id = ? AND deleted = 0", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "Yorum bulunamadı")
    if r["user_id"] != u["id"] and not is_admin(request):
        raise HTTPException(403, "Sadece kendi yorumunuzu silebilirsiniz")
    with c:
        c.execute("UPDATE comments SET deleted = 1 WHERE id = ?", (cid,))
    return {"ok": True}


# ------------------------------------------------------------------ Duvar
def post_json(r, u, preview=2):
    m = r["media"]
    base = "/social/media"
    cards = users()
    fam = family_data()["people"]
    bp = by_person()
    people = json.loads(r["people"])
    return {"id": r["id"], "item": m, "kind": r["kind"], "user": cards.get(r["user_id"]), "caption": r["caption"],
            "created": r["created"], "taken": r["taken"], "status": r["status"], "error": r["error"],
            "held": r["nsfw"] != "[]",
            "w": r["w"], "h": r["h"], "dur": r["dur"], "mine": r["user_id"] == u["id"],
            "thumb": f"{base}/thumb/{m}.jpg", "full": f"{base}/full/{m}.jpg",
            "video": f"{base}/video/{m}.mp4" if r["kind"] == "video" else None,
            "people": [{"person": p, "name": fam.get(p, {}).get("name", p), "user": cards.get(bp.get(p))} for p in people],
            "faces": json.loads(r["faces"]),
            **(item_social(m, u, preview) if r["status"] == "ready" else {"likes": 0, "liked": False,
                                                                          "comment_count": 0, "comments": []})}


@sub.get("/social/api/posts")
def feed(before: int | None = None, limit: int = 8, user: str | None = None, u=Depends(require_user)):
    limit = max(1, min(limit, 30))
    q = "SELECT * FROM posts WHERE deleted = 0 AND ((status = 'ready' AND nsfw = '[]') OR user_id = ? OR ?)"
    args = [u["id"], int(u["admin"])]
    if before:
        q += " AND id < ?"
        args.append(before)
    if user:
        q += " AND user_id = (SELECT id FROM users WHERE username = ?)"
        args.append(user)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = db().execute(q, args).fetchall()
    return {"items": [post_json(r, u) for r in rows], "more": len(rows) == limit}


@sub.get("/social/api/posts/{pid}")
def get_post(pid: int, u=Depends(require_user)):
    r = db().execute("SELECT * FROM posts WHERE id = ? AND deleted = 0", (pid,)).fetchone()
    if not r or not _visible(r, u):
        raise HTTPException(404, "Paylaşım bulunamadı")
    return post_json(r, u, preview=None)


@sub.delete("/social/api/posts/{pid}", dependencies=[Depends(require_csrf)])
def delete_post(pid: int, request: Request, u=Depends(require_user)):
    c = db()
    r = c.execute("SELECT user_id FROM posts WHERE id = ? AND deleted = 0", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "Paylaşım bulunamadı")
    if r["user_id"] != u["id"] and not is_admin(request):
        raise HTTPException(403, "Sadece kendi paylaşımınızı silebilirsiniz")
    with c:
        c.execute("UPDATE posts SET deleted = 1 WHERE id = ?", (pid,))
        c.execute("DELETE FROM notifications WHERE post_id = ?", (pid,))
    return {"ok": True}


def sniff(head: bytes):
    """Dosya türünü uzantıya/başlığa değil içeriğe bakarak belirler -> (tür, uzantı) ya da None."""
    if head[:3] == b"\xff\xd8\xff":
        return "photo", ".jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "photo", ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "photo", ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "photo", ".webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif"):
            return ("photo", ".heic") if _heic_ok() else None
        return "video", ".mov" if brand.startswith(b"qt") else ".mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "video", ".webm"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video", ".avi"
    return None


def _heic_ok():
    try:
        import pillow_heif  # noqa: F401
        return True
    except ImportError:
        return False


@sub.post("/social/api/posts", dependencies=[Depends(require_csrf)])
async def create_post(request: Request, u=Depends(require_user)):
    """Gövde: dosyanın kendisi (multipart değil). Açıklama X-Caption başlığında, URL kodlu."""
    init()
    now = time.time()
    c = db()
    n = c.execute("SELECT COUNT(*) FROM posts WHERE user_id = ? AND created > ?", (u["id"], now - 3600)).fetchone()[0]
    if n >= UPLOADS_PER_HOUR:
        raise HTTPException(429, "Bir saatte çok fazla paylaşım yaptınız, biraz sonra tekrar deneyin")
    caption = unquote(request.headers.get("x-caption", ""))[:CAPTION_MAX].strip()
    caption = re.sub(r"[\u0000-\u0008\u000b-\u001f\u007f‪-‮⁦-⁩]", "", caption)
    limit = max(UPLOAD_MAX.values())
    try:
        if int(request.headers.get("content-length", 0)) > limit:
            raise HTTPException(413, "Dosya çok büyük")
    except ValueError:
        raise HTTPException(400, "Geçersiz istek")
    tmp = UPLOADS / "raw" / f".{secrets.token_hex(8)}.part"
    h = hashlib.sha1()
    size, head, kind = 0, b"", None
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                if len(head) < 32:
                    head += chunk[:32]
                    if len(head) >= 32 and not sniff(head):
                        raise HTTPException(415, "Sadece fotoğraf (JPEG, PNG, HEIC, WebP, GIF) veya video (MP4, MOV, WebM) yüklenebilir")
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, "Dosya çok büyük")
                h.update(chunk)
                f.write(chunk)
        found = sniff(head)
        if not size or not found:
            raise HTTPException(415, "Sadece fotoğraf (JPEG, PNG, HEIC, WebP, GIF) veya video (MP4, MOV, WebM) yüklenebilir")
        kind, ext = found
        if size > UPLOAD_MAX[kind]:
            raise HTTPException(413, f"{'Fotoğraf' if kind == 'photo' else 'Video'} en fazla "
                                     f"{UPLOAD_MAX[kind] // 1024 // 1024} MB olabilir")
        media = h.hexdigest()[:20]  # içe aktarmadaki kimlikle aynı yöntem: aynı dosya arşivde aynı fotoğraftır
        raw = UPLOADS / "raw" / f"{media}{ext}"
        if raw.exists():
            tmp.unlink()
        else:
            tmp.replace(raw)
    finally:
        tmp.unlink(missing_ok=True)
    with c:
        pid = c.execute("INSERT INTO posts (user_id, media, kind, caption, created, status, raw) VALUES (?,?,?,?,?,?,?)",
                        (u["id"], media, kind, caption, now, "processing", raw.name)).lastrowid
    _queue.put(pid)
    return post_json(c.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone(), u)


# ------------------------------------------------------------------ yükleme işleme (arka plan)
_queue = queue.Queue()
_worker = None


def _start_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, name="social-ingest", daemon=True)
        _worker.start()


def _work():
    while True:
        pid = _queue.get()
        try:
            process_post(pid)
        except Exception:
            traceback.print_exc()


def process_post(pid: int):
    """Yüklemeyi küçültür, üst verisini siler, yüzleri tanır; tanınan aile üyelerine bildirim gönderir."""
    import ingest_uploads
    c = db()
    r = c.execute("SELECT * FROM posts WHERE id = ?", (pid,)).fetchone()
    if not r or r["status"] != "processing":
        return
    try:
        res = ingest_uploads.handle(UPLOADS / "raw" / r["raw"], r["media"], r["kind"])
    except Exception as e:
        traceback.print_exc()
        with c:
            c.execute("UPDATE posts SET status = 'failed', error = ? WHERE id = ?", (str(e)[:300] or "işlenemedi", pid))
            _notify(c, r["user_id"], None, "failed", r["media"], pid, "")
        return
    with c:
        c.execute("UPDATE posts SET status='ready', w=?, h=?, dur=?, taken=?, people=?, faces=?, nsfw=? WHERE id=?",
                  (res["w"], res["h"], res.get("dur"), res.get("taken"), json.dumps(res["people"]),
                   json.dumps(res["faces"]), json.dumps(res.get("nsfw", [])), pid))
        if not res.get("nsfw"):
            _announce(c, r, res["people"])
        _notify(c, r["user_id"], None, "held" if res.get("nsfw") else "ready", r["media"], pid,
                ",".join(res["people"]))
    print(f"[social] paylaşım {pid} hazır; {res.get('found', 0)} yüz, tanınan: {', '.join(res['people']) or '-'}"
          f"{'; şüpheli içerik: ' + ','.join(res['nsfw']) if res.get('nsfw') else ''}", flush=True)
    if server.CONFIG.get("social_auto_archive") and not res.get("nsfw"):
        try:
            ingest_uploads.archive(only=[pid])
        except Exception:
            traceback.print_exc()


def _announce(c, r, people):
    """Paylaşım herkese açıldığında: yüzü tanınan aile üyelerine ve açıklamada @ile anılanlara bildirim."""
    bp = by_person()
    told = {r["user_id"]}
    for p in people:
        uid = bp.get(p)
        if uid and uid not in told:
            _notify(c, uid, r["user_id"], "face", r["media"], r["id"], r["caption"][:160])
            told.add(uid)
    by_name = {card["username"]: uid for uid, card in users().items()}
    for m in MENTION_RE.findall(r["caption"].lower()):
        uid = by_name.get(m)
        if uid and uid not in told:
            _notify(c, uid, r["user_id"], "mention", r["media"], r["id"], r["caption"][:160])
            told.add(uid)


@sub.post("/social/api/posts/{pid}/approve", dependencies=[Depends(require_csrf), Depends(require_admin)])
def approve_post(pid: int):
    """Çıplaklık filtresine takılan paylaşımı yönetici yayına alır (bildirimler o zaman gider)."""
    c = db()
    r = c.execute("SELECT * FROM posts WHERE id = ? AND deleted = 0 AND status = 'ready'", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "Paylaşım bulunamadı")
    if r["nsfw"] != "[]":
        with c:
            c.execute("UPDATE posts SET nsfw = '[]' WHERE id = ?", (pid,))
            _announce(c, r, json.loads(r["people"]))
            _notify(c, r["user_id"], None, "ready", r["media"], pid, ",".join(json.loads(r["people"])))
    return {"ok": True}


# ------------------------------------------------------------------ bildirimler
@sub.get("/social/api/notifications")
def notifications(before: int | None = None, limit: int = 30, u=Depends(require_user)):
    limit = max(1, min(limit, 100))
    c = db()
    q, args = "SELECT * FROM notifications WHERE user_id = ?", [u["id"]]
    if before:
        q += " AND id < ?"
        args.append(before)
    rows = c.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    cards = users()
    thumbs = {}
    for r in rows:
        if r["item"] and r["item"] not in thumbs:
            p = c.execute("SELECT 1 FROM posts WHERE media = ? AND deleted = 0", (r["item"],)).fetchone()
            thumbs[r["item"]] = f"/social/media/thumb/{r['item']}.jpg" if p else f"/media/thumb/{r['item']}.jpg"
    unread = c.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0", (u["id"],)).fetchone()[0]
    return {"unread": unread, "more": len(rows) == limit, "items": [
        {"id": r["id"], "type": r["type"], "actor": cards.get(r["actor_id"]), "item": r["item"], "post_id": r["post_id"],
         "body": r["body"], "created": r["created"], "read": bool(r["read"]), "thumb": thumbs.get(r["item"])}
        for r in rows]}


@sub.get("/social/api/notifications/count")
def notification_count(u=Depends(require_user)):
    return {"unread": db().execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read = 0",
                                   (u["id"],)).fetchone()[0]}


@sub.post("/social/api/notifications/read", dependencies=[Depends(require_csrf)])
async def notifications_read(request: Request, u=Depends(require_user)):
    body = await _body(request)
    c = db()
    with c:
        if body.get("all"):
            c.execute("UPDATE notifications SET read = 1 WHERE user_id = ?", (u["id"],))
        else:
            ids = [int(i) for i in body.get("ids", [])[:200]]
            c.executemany("UPDATE notifications SET read = 1 WHERE user_id = ? AND id = ?", [(u["id"], i) for i in ids])
    return {"ok": True}


# ------------------------------------------------------------------ dosyalar
@sub.get("/social/media/{kind}/{name}")
def upload_media(kind: str, name: str, u=Depends(require_user)):
    m = re.fullmatch(r"([0-9a-f]{20})\.(jpg|mp4)", name)
    if kind not in ("full", "thumb", "video") or not m or (kind == "video") != (m.group(2) == "mp4"):
        raise HTTPException(404, "Bulunamadı")
    path = UPLOADS / "media" / kind / name
    if not path.is_file():
        raise HTTPException(404, "Bulunamadı")
    # içerik özetiyle adlandırıldığı için değişmez; ama sadece tarayıcı saklasın (Cloudflare değil: giriş gerekli)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@sub.get("/social/mosaic.jpg")
def mosaic():
    """Giriş ekranının arkasındaki küçük kareler (tek resim). Girişsiz görülür; sadece minik kareler içerir."""
    fresh = MOSAIC.exists() and time.time() - MOSAIC.stat().st_mtime < MOSAIC_MAX_AGE
    if not fresh:
        build_mosaic_async()
    if not MOSAIC.exists():
        raise HTTPException(404, "Hazırlanıyor")
    return FileResponse(MOSAIC, headers={"Cache-Control": "public, max-age=3600"})


_mosaic_lock = threading.Lock()


def build_mosaic_async():
    if _mosaic_lock.locked():
        return
    threading.Thread(target=build_mosaic, name="social-mosaic", daemon=True).start()


def build_mosaic():
    if not _mosaic_lock.acquire(blocking=False):
        return
    try:
        idx = index_data()
        photos = idx["photos"]
        thumb_dir = DATA / "media" / "thumb"
        if "_visible" in idx:  # aile sitesinin süzgeci: çıplaklık kontrolünden geçmiş, gizlenmemiş
            ids = list(idx["_visible"])
        else:
            ids = [i for i, p in photos.items() if not _hidden(p) and "nsfw" in p]
        random.shuffle(ids)
        n = MOSAIC_COLS * MOSAIC_ROWS
        T, G = MOSAIC_TILE, MOSAIC_GAP
        canvas = Image.new("RGB", (MOSAIC_COLS * T, MOSAIC_ROWS * T), (24, 33, 22))
        placed = 0
        tiles = []
        for i in ids:
            if placed >= n:
                break
            p = thumb_dir / f"{i}.jpg"
            if not p.exists():
                continue
            try:
                with Image.open(p) as im:
                    im.draft("RGB", (T * 2, T * 2))  # JPEG'i küçük çöz: hızlı
                    tile = ImageOps.fit(im.convert("RGB"), (T - G, T - G), Image.LANCZOS)
            except Exception:
                continue
            tiles.append(tile)
            placed += 1
        if not tiles:
            return
        for k in range(n):
            x, y = (k % MOSAIC_COLS) * T, (k // MOSAIC_COLS) * T
            canvas.paste(tiles[k % len(tiles)], (x + G // 2, y + G // 2))
        tmp = MOSAIC.with_suffix(".tmp.jpg")
        MOSAIC.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(tmp, "JPEG", quality=72, optimize=True, progressive=True)
        tmp.replace(MOSAIC)
        print(f"[social] giriş mozaiği hazır ({len(tiles)} fotoğraf)", flush=True)
    except Exception:
        traceback.print_exc()
    finally:
        _mosaic_lock.release()


sub.mount("/social/assets", StaticFiles(directory=STATIC), name="social-assets")


# ------------------------------------------------------------------ giriş kapısı
_PUBLIC_INNER = {"/robots.txt"}


class Gate:
    """Aile sitesinin önündeki ASGI katmanı: giriş yapılmadan hiçbir şey görünmez."""

    def __init__(self, inner, inject_script=False):
        self.inner = inner
        self.inject = inject_script

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.inner(scope, receive, send)
        path = scope.get("path", "")
        if path in ("/giris", "/duvar") or path.startswith("/social/"):
            return await sub(scope, receive, send)
        if path in _PUBLIC_INNER:
            return await self.inner(scope, receive, send)
        req = Request(scope)
        u = await run_in_threadpool(session_user, req.cookies.get(COOKIE))
        scope["social_user"] = u
        if not u or u["must_change"]:
            if scope["type"] != "http":
                return  # websocket: kabul etmeden kapanır
            if req.method in ("GET", "HEAD") and not path.startswith(("/api/", "/media/", "/assets/")):
                target = path + (("?" + scope["query_string"].decode("latin-1")) if scope.get("query_string") else "")
                resp = RedirectResponse("/giris?next=" + quote(target, safe="/"), status_code=303)
            else:
                resp = JSONResponse({"detail": "Önce şifrenizi değiştirin" if u else "Oturum gerekli"},
                                    status_code=403 if u else 401)
            resp.headers["Cache-Control"] = "no-store"
            return await resp(scope, receive, send)
        if path.startswith("/media/"):
            send = _private_cache(send)
        if self.inject and path == "/":
            send = _inject(send)
        await self.inner(scope, receive, send)


def _private_cache(send):
    """/media yanıtları Cloudflare'de herkese açık önbelleğe girmesin (giriş zorunlu): 'public' -> 'private'.
    Aile sitesinin verdiği süre ve 'no-store' gibi kararlar korunur."""
    async def wrapped(msg):
        if msg["type"] == "http.response.start":
            hs, seen = [], False
            for k, v in msg.get("headers", []):
                if k.lower() == b"cache-control":
                    seen = True
                    v = v.replace(b"public", b"private")
                    if b"private" not in v and b"no-store" not in v:
                        v = b"private, " + v
                hs.append((k, v))
            if not seen:
                hs.append((b"cache-control", b"private, max-age=86400"))
            msg = {**msg, "headers": hs}
        await send(msg)
    return wrapped


def _inject(send):
    """Geliştirme için: index.html'e dokunmadan social.js'i sayfaya ekler."""
    state = {"start": None, "body": b""}

    async def wrapped(msg):
        if msg["type"] == "http.response.start":
            state["start"] = msg
            return
        if msg["type"] == "http.response.body":
            state["body"] += msg.get("body", b"")
            if msg.get("more_body"):
                return
            body = state["body"]
            if b"/social/assets/social.js" not in body:
                tag = f'<script src="/social/assets/social.js?v={int((STATIC / "social.js").stat().st_mtime)}"></script>'
                body = body.replace(b"</body>", tag.encode() + b"\n</body>", 1)
            start = state["start"]
            hs = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
            hs.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": hs})
            await send({"type": "http.response.body", "body": body})
            return
        await send(msg)
    return wrapped


def wrap(app, inject_script=False):
    """Aile sitesini giriş kapısıyla sarar. main.py: family_app = social.wrap(family.app)"""
    threading.Thread(target=_startup, name="social-init", daemon=True).start()
    return Gate(app, inject_script)


def _startup():
    try:
        init()
        if not MOSAIC.exists() or time.time() - MOSAIC.stat().st_mtime > MOSAIC_MAX_AGE:
            build_mosaic()
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    # python app/social.py users         -> kullanıcı listesi
    # python app/social.py reset <ad>    -> şifreyi başlangıç şifresine döndür
    init()
    cmd = sys.argv[1:] or ["users"]
    if cmd[0] == "users":
        fam = family_data()["people"]
        for r in db().execute("SELECT * FROM users ORDER BY username"):
            print(f"{r['username']:<26} {fam.get(r['person'], {}).get('name', '?'):<24} {r['person']:<12}"
                  f"{' yönetici' if r['admin'] else ''}{' (şifre değişmedi)' if r['must_change'] else ''}")
    elif cmd[0] == "reset" and len(cmd) == 2:
        pw = bcrypt.hashpw(DEFAULT_PASSWORD.encode(), bcrypt.gensalt()).decode()
        c = db()
        with c:
            n = c.execute("UPDATE users SET pw = ?, must_change = 1 WHERE username = ?", (pw, cmd[1])).rowcount
            c.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (cmd[1],))
        print("sıfırlandı" if n else "kullanıcı bulunamadı")
