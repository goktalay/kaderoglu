"""Aile ağacı sitesi (kaderoglu.com)."""
import json
import os
import sys
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import server
import social  # aile hesapları; Muhammed'in aile hesabı da yönetici (social, family'yi içe aktarmaz)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import avatars  # noqa: E402  yüz bulma ve profil fotoğrafı kırpma (içe aktarma aracıyla ortak)
DATA = Path(os.environ.get("KADEROGLU_DATA", ROOT / "data"))  # test için ayrı veri klasörü verilebilir
MEDIA = DATA / "media"
STATIC = Path(__file__).resolve().parent / "family_static"
for sub in ("full", "thumb", "avatar"):
    (MEDIA / sub).mkdir(parents=True, exist_ok=True)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# ------------------------------------------------------------------ veri
_lock = threading.Lock()
_cache = {"key": None, "family": None, "index": None}


MODERATION = DATA / "moderation.json"  # yönetici kararları: {kimlik: "manual" (gizle) | "kept" (göster)}


def is_hidden(ph, decision):
    """Yönetici kararı önce gelir; yoksa çıplaklık kontrolü (tools/nudity.py) bir şey bulduysa gizli."""
    if decision == "manual":
        return True
    if decision == "kept":
        return False
    if ph.get("type") == "video":
        return False  # kullanıcı kararı: videolarda çıplaklık kontrolü yok (elle gizlenebilir)
    return bool(ph.get("nsfw"))


def _load():
    """family.json, index.json ve moderation.json değiştiyse yeniden okur (yeniden başlatmaya gerek yok).

    Herkese görünen: içinde aileden biri olan, çıplaklık kontrolünden geçmiş ve gizlenmemiş öğeler.
    Kişi listeleri (idx["people"]) bellekte sadece görünenlerle süzülür; dosyaya yazılmaz."""
    fam_p, idx_p = DATA / "family.json", DATA / "index.json"
    mtime = lambda p: p.stat().st_mtime if p.exists() else 0  # noqa: E731
    key = (mtime(fam_p), mtime(idx_p), mtime(MODERATION))
    with _lock:
        if _cache["key"] != key:
            fam = json.loads(fam_p.read_text("utf-8"))
            idx = json.loads(idx_p.read_text("utf-8")) if idx_p.exists() else {"photos": {}, "people": {}}
            mod = json.loads(MODERATION.read_text("utf-8")) if MODERATION.exists() else {}
            visible = set()
            for i, ph in idx["photos"].items():
                ph["_hidden"] = is_hidden(ph, mod.get(i))
                checked = "nsfw" in ph or ph.get("type") == "video"
                if ph.get("people") and checked and not ph["_hidden"]:
                    visible.add(i)
            idx["_visible"] = visible
            idx["_mod"] = mod
            idx["people"] = {p: [i for i in ids if i in visible] for p, ids in idx.get("people", {}).items()}
            _cache.update(key=key, family=fam, index=idx)
            _together_cache.clear()
        return _cache["family"], _cache["index"]


def _media_id(path: str):
    """/media/full/<kimlik>.jpg, /media/thumb/<kimlik>.jpg, /media/video/<kimlik>.mp4 -> kimlik"""
    parts = path.split("/")
    if len(parts) == 4 and parts[2] in ("full", "thumb", "video"):
        return parts[3].split(".")[0]
    return None


# ------------------------------------------------------------------ güvenlik / önbellek
@app.middleware("http")
async def headers(request: Request, call_next):
    host = request.client.host if request.client else ""
    if server.CONFIG.get("cloudflare_only") and not server.peer_allowed(host):
        return JSONResponse({"detail": "Forbidden"}, status_code=403)
    path = request.url.path
    private_media = False
    if path.startswith("/media/vframes/"):
        return JSONResponse({"detail": "Bulunamadı"}, status_code=404)  # analiz kareleri hiç yayımlanmaz
    mid = _media_id(path)
    if mid:
        _, idx = _load()
        if mid not in idx["_visible"]:
            # gizli, henüz kontrol edilmemiş ya da kimsenin olmadığı öğe: sadece yönetici görebilir
            if not social.is_admin(request):
                return JSONResponse({"detail": "Bulunamadı"}, status_code=404)
            private_media = True
    response = await call_next(request)
    h = response.headers
    h["X-Content-Type-Options"] = "nosniff"
    h["Referrer-Policy"] = "strict-origin-when-cross-origin"
    h["X-Frame-Options"] = "DENY"
    h["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data: https://tile.openstreetmap.org https://cdnjs.cloudflare.com; "
        "connect-src 'self'; frame-ancestors 'none'"
    )
    if server.CONFIG.get("family_noindex", True):
        h["X-Robots-Tag"] = "noindex, noimageindex"
    if server.is_https(request):
        h["Strict-Transport-Security"] = "max-age=31536000"
    if private_media:
        h["Cache-Control"] = "private, no-store"
    elif path.startswith("/media/") and response.status_code == 200:
        # adlar içerik özetidir, değişmez; ama sonradan gizlenebileceği için Cloudflare'de en fazla 1 gün kalsın
        h["Cache-Control"] = "private, max-age=86400"  # giriş zorunlu: Cloudflare saklamasın
    elif path.startswith(("/api/", "/assets/")):
        # site güncellemeleri hemen görünsün (Cloudflare ve tarayıcı her seferinde doğrular)
        h["Cache-Control"] = "no-cache"
    return response


# ------------------------------------------------------------------ API
@app.get("/api/family")
def api_family():
    fam, idx = _load()
    people = {}
    photos = idx["photos"]
    for pid, p in fam["people"].items():
        av = idx.get("avatar_v", {}).get(pid) or idx.get("avatars", {}).get(pid)
        ids = idx["people"].get(pid, [])
        people[pid] = {
            "name": p["name"], "role": p.get("role", ""), "born": p.get("born"),
            "count": len(ids), "geo_count": sum(1 for i in ids if photos[i].get("geo")),
            "avatar": f"/media/avatar/{pid}.jpg?v={av}" if av else None,
        }
    vis = idx["_visible"]
    return {"title": fam.get("title"), "subtitle": fam.get("subtitle"), "tree": fam["tree"],
            "extra_trees": fam.get("extra_trees", []), "people": people,
            "total_photos": len(vis), "geo_photos": sum(1 for i in vis if photos[i].get("geo")),
            "videos": sum(1 for i in vis if photos[i].get("type") == "video")}


_together_cache = {}


def _together(fam, idx, people: tuple):
    """Seçilen kişilerin HEPSİNİN bulunduğu fotoğraflar, en yeniden eskiye (tarihsizler sonda)."""
    for p in people:
        if p not in fam["people"]:
            raise HTTPException(404, f"Kişi bulunamadı: {p}")
    key = people
    if key not in _together_cache:
        if len(_together_cache) > 200:
            _together_cache.clear()
        lists = sorted((idx["people"].get(p, []) for p in people), key=len)
        need = set(people)
        # kişi listeleri zaten tarihe göre sıralı; en kısa listeyi süzmek sırayı korur
        _together_cache[key] = [i for i in lists[0] if need <= set(idx["photos"][i].get("people", []))]
    return _together_cache[key]


def _parse_people(people: str) -> tuple:
    ids = tuple(sorted({p for p in people.split(",") if p}))
    if not ids:
        raise HTTPException(400, "En az bir kişi seçin")
    return ids


@app.get("/api/photos")
def api_photos(people: str, offset: int = 0, limit: int = 10):
    """people=a,b -> a ve b'nin birlikte olduğu fotoğraflar (sayfalı)."""
    fam, idx = _load()
    sel = _parse_people(people)
    ids = _together(fam, idx, sel)
    limit = max(1, min(limit, 1000))  # tarih çubuğuyla uzağa atlarken tek seferde çok kayıt gerekebilir
    offset = max(0, offset)
    items = []
    for i in ids[offset:offset + limit]:
        ph = idx["photos"][i]
        items.append({"id": i, "w": ph["w"], "h": ph["h"], "taken": ph.get("taken"),
                      "thumb": f"/media/thumb/{i}.jpg", "full": f"/media/full/{i}.jpg",
                      "people": ph.get("people", []), "with": [p for p in ph.get("people", []) if p not in sel],
                      "faces": ph.get("faces", []), "type": ph.get("type", "photo"), "dur": ph.get("dur"),
                      **({"video": f"/media/video/{i}.mp4"} if ph.get("type") == "video" else {})})
    return {"total": len(ids), "offset": offset, "items": items}


@app.get("/api/timeline")
def api_timeline(people: str):
    """Tarih çubuğu için aylık özet: her ayın kaç fotoğrafı var ve listede kaçıncı sıradan başlıyor."""
    fam, idx = _load()
    ids = _together(fam, idx, _parse_people(people))
    months, last = [], None
    for n, i in enumerate(ids):
        t = idx["photos"][i].get("taken")
        key = time.strftime("%Y-%m", time.gmtime(t)) if t else "tarihsiz"
        if key != last:
            months.append({"key": key, "offset": n, "count": 0})
            last = key
        months[-1]["count"] += 1
    return {"total": len(ids), "months": months}


@app.get("/api/faces")
def api_faces(photo: str, request: Request):
    """Fotoğraftaki isimli yüzler: [[x, y, w, h (0-1 oranları), kişi], ...] (tools/faces.py üretir)."""
    _, idx = _load()
    ph = idx["photos"].get(photo)
    if not ph or (photo not in idx["_visible"] and not social.is_admin(request)):
        raise HTTPException(404, "Fotoğraf bulunamadı")
    return {"faces": ph.get("faces", [])}


@app.get("/api/people/{pid}/photos")
def api_person_photos(pid: str, offset: int = 0, limit: int = 10):
    return api_photos(pid, offset, limit)


@app.get("/api/map")
def api_map(person: str | None = None):
    """Konumu bilinen fotoğraflar. Koordinatlar config'deki hassasiyete yuvarlanır
    (2 basamak ~1 km: semt görünür, ev adresi görünmez)."""
    fam, idx = _load()
    if person and person not in fam["people"]:
        raise HTTPException(404, "Kişi bulunamadı")
    digits = int(server.CONFIG.get("map_precision", 2))
    ids = idx["people"].get(person, []) if person else \
        sorted(idx["_visible"], key=lambda i: idx["photos"][i].get("taken") or 0, reverse=True)
    items = []
    for i in ids:
        ph = idx["photos"][i]
        geo = ph.get("geo")
        if not geo:
            continue
        # binlerce işaretçi için kısa alanlar: [kimlik, enlem, boylam, tarih, kişiler]; adresleri tarayıcı üretir
        items.append([i, round(geo[0], digits), round(geo[1], digits), ph.get("taken"), ph.get("people", []),
                      1 if ph.get("type") == "video" else 0])
    return {"total": len(items), "items": items}


# ------------------------------------------------------------------ yönetici (panel hesabıyla)
# Giriş, paneldeki hesap ve korumaları (bcrypt, hatalı deneme kilidi) kullanır; oturum çerezi sadece
# kaderoglu.com'a verilir (başka sunucularda duran alt alan adlarına gitmesin diye alan adı geneline değil).
@app.post("/api/login", dependencies=[Depends(server.require_csrf)])
async def login(request: Request):
    return await server.login(request)


@app.post("/api/logout", dependencies=[Depends(server.require_csrf)])
async def logout(request: Request):
    return await server.logout(request)


@app.get("/api/me")
def me(request: Request):
    return {"admin": social.is_admin(request), "panel": bool(server.get_session(request))}


@app.get("/api/admin/faces", dependencies=[Depends(social.require_admin)])
def admin_faces(photo: str):
    _, idx = _load()
    if photo not in idx["photos"]:
        raise HTTPException(404, "Fotoğraf bulunamadı")
    (w, h), faces = avatars.faces_in(photo)
    return {"w": w, "h": h, "faces": faces}


_write_lock = threading.Lock()


@app.get("/api/admin/hidden", dependencies=[Depends(social.require_admin)])
def admin_hidden(offset: int = 0, limit: int = 60):
    """Gizlenenler (çıplaklık kontrolü veya yönetici), en yeniden eskiye."""
    _, idx = _load()
    photos = idx["photos"]
    ids = sorted((i for i, p in photos.items() if p.get("_hidden")), key=lambda i: photos[i].get("taken") or 0, reverse=True)
    items = []
    for i in ids[max(0, offset):max(0, offset) + max(1, min(limit, 200))]:
        ph = photos[i]
        items.append({"id": i, "taken": ph.get("taken"), "type": ph.get("type", "photo"), "dur": ph.get("dur"),
                      "thumb": f"/media/thumb/{i}.jpg", "full": f"/media/full/{i}.jpg",
                      **({"video": f"/media/video/{i}.mp4"} if ph.get("type") == "video" else {}),
                      "people": ph.get("people", []), "with": ph.get("people", []), "faces": ph.get("faces", []),
                      "why": "manual" if idx["_mod"].get(i) == "manual" else "auto", "nsfw": ph.get("nsfw", [])})
    return {"total": len(ids), "items": items}


@app.post("/api/admin/hide", dependencies=[Depends(server.require_csrf), Depends(social.require_admin)])
async def admin_hide(request: Request):
    """{"photo": kimlik, "hide": true|false} -> moderation.json (içe aktarma index.json'u yazarken çakışmasın diye ayrı)."""
    body = await request.json()
    photo, hide = str(body.get("photo", "")), bool(body.get("hide"))
    _, idx = _load()
    if photo not in idx["photos"]:
        raise HTTPException(404, "Fotoğraf bulunamadı")
    with _write_lock:
        mod = json.loads(MODERATION.read_text("utf-8")) if MODERATION.exists() else {}
        mod[photo] = "manual" if hide else "kept"
        _write_json(MODERATION, mod)
    return {"hidden": hide}


def _write_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def _set_avatar(pid, photo, face):
    with _write_lock:
        fam_p, idx_p = DATA / "family.json", DATA / "index.json"
        fam = json.loads(fam_p.read_text("utf-8"))
        idx = json.loads(idx_p.read_text("utf-8"))
        if pid not in fam["people"] or photo not in idx["photos"]:
            raise HTTPException(404, "Kişi veya fotoğraf bulunamadı")
        fam["people"][pid].pop("avatar_generated", None)  # yönetici elle seçti: üretilmiş portrenin yerine geçer
        avatars.save_avatar(idx, pid, photo, face)
        # içe aktarma veya tools\avatars.py yeniden çalışınca bu seçim korunur
        fam["people"][pid]["avatar_photo"] = photo
        fam["people"][pid]["avatar_face"] = list(face) if face else None
        _write_json(fam_p, fam)
        idx_p.with_suffix(".tmp").write_text(json.dumps(idx, ensure_ascii=False), "utf-8")
        idx_p.with_suffix(".tmp").replace(idx_p)


@app.post("/api/admin/avatar", dependencies=[Depends(server.require_csrf), Depends(social.require_admin)])
async def admin_avatar(request: Request):
    body = await request.json()
    pid, photo = str(body.get("person", "")), str(body.get("photo", ""))
    face = body.get("face")
    if face is not None:
        if not (isinstance(face, list) and len(face) == 4 and all(isinstance(v, (int, float)) for v in face)):
            raise HTTPException(400, "Geçersiz yüz kutusu")
        face = tuple(int(v) for v in face)
    await run_in_threadpool(_set_avatar, pid, photo, face)
    _, idx = _load()
    return {"avatar": f"/media/avatar/{pid}.jpg?v={idx['avatar_v'][pid]}"}


# ------------------------------------------------------------------ sayfalar
@app.get("/")
def index():
    # CSS/JS adreslerine değişiklik zamanını ekle: Cloudflare eski sürümü önbellekten vermesin
    html = (STATIC / "index.html").read_text("utf-8")
    for name in ("family.css", "timeline.js", "family.js"):
        v = int((STATIC / name).stat().st_mtime)
        html = html.replace(f"/assets/{name}", f"/assets/{name}?v={v}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/robots.txt")
def robots_txt():
    noindex = server.CONFIG.get("family_noindex", True)
    return PlainTextResponse("User-agent: *\nDisallow: /\n" if noindex else "User-agent: *\nAllow: /\n")


app.mount("/media", StaticFiles(directory=MEDIA), name="media")
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
