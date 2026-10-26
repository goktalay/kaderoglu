"""Duvar'a yüklenen fotoğraf/videoları işler ve arşive alır.

Site (app/social.py) her yüklemeyi arka planda handle() ile işler:
  - fotoğraf: 1600 px tam boy + 480 px küçük resim (JPEG; EXIF ve konum yazılmaz), video: tools/videos.py transcode()
  - yüzler YuNet + SFace ile bulunur (tools/faces.py), arşivdeki örnek yüzlerle karşılaştırılıp aile üyeleri tanınır
  - çıplaklık kontrolü (tools/nudity.py, model varsa)
Çıktılar data/uploads/media/{full,thumb,video,vframes} altına yazılır; data/index.json'a dokunulmaz.

Komut satırı:
  .venv\\Scripts\\python.exe tools\\ingest_uploads.py             bekleyen yüklemeleri işle (site kapalıyken)
  .venv\\Scripts\\python.exe tools\\ingest_uploads.py --archive   hazır yüklemeleri arşive (data/index.json + data/media) al
  .venv\\Scripts\\python.exe tools\\ingest_uploads.py --seeds     örnek yüzleri yeniden hesapla

--archive, index.json'u yazan başka bir araç (import_takeout, faces, nudity) çalışırken çalıştırılmamalı:
o araçlar dizini kendi kopyalarıyla üzerine yazar ve buradaki eklemeler kaybolur. Arşive alınanlar sonraki
tools\\faces.py çalışmasında normal fotoğraflar gibi yeniden eşleştirilir.
"""
import argparse
import io
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import faces  # noqa: E402
import import_takeout  # noqa: E402  (HEIC desteği, save_jpeg, exif_taken, build_people, save_index)
import videos  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("KADEROGLU_DATA", ROOT / "data"))
MEDIA = DATA / "media"
UPLOADS = DATA / "uploads"
UMEDIA = UPLOADS / "media"
DB_PATH = DATA / "social.db"
SEEDS = UPLOADS / "seeds.npz"
SEEDS_MAX_AGE = 6 * 3600  # içe aktarma sürerken faces_cache sık değişir; örnekler en fazla bu sıklıkla yenilenir

_seed_lock = threading.Lock()
_seeds = {"key": None, "data": None}


# ------------------------------------------------------------------ dosya işleme
def process_image(raw: Path, media_id: str):
    data = raw.read_bytes()
    taken = import_takeout.exif_taken(data)
    with Image.open(io.BytesIO(data)) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        im = im.convert("RGB")
        full = im.copy()
        full.thumbnail((import_takeout.FULL_PX, import_takeout.FULL_PX), Image.LANCZOS)
        import_takeout.save_jpeg(full, UMEDIA / "full" / f"{media_id}.jpg", 82)  # EXIF (ve GPS) yazılmaz
        im.thumbnail((import_takeout.THUMB_PX, import_takeout.THUMB_PX), Image.LANCZOS)
        import_takeout.save_jpeg(im, UMEDIA / "thumb" / f"{media_id}.jpg", 78)
        return full.size, taken


def frames(media_id, kind):
    if kind == "video":
        fdir = UMEDIA / "vframes" / media_id
        return sorted(fdir.glob("*.jpg")) if fdir.exists() else []
    return [UMEDIA / "full" / f"{media_id}.jpg"]


# ------------------------------------------------------------------ yüz tanıma
def build_seeds():
    """Arşivden kişi başına örnek yüzler: tek kişinin etiketli olduğu ve tek yüz bulunan fotoğraflar
    (tools/faces.py match() ile aynı yöntem), aykırı %15 atılır."""
    index = json.loads((DATA / "index.json").read_text("utf-8"))
    family = json.loads((DATA / "family.json").read_text("utf-8"))
    by = faces.load_cache()
    photos = index["photos"]
    seeds = {p: [] for p in family["people"]}
    for pid, ph in photos.items():
        if ph.get("type") == "video":
            continue
        tags = [t for t in faces.known_people(ph) if t in seeds]
        found = by.get(pid, [])
        if len(tags) == 1 and len(found) == 1:
            seeds[tags[0]].append((ph.get("taken") or 0, found[0][1]))
    rng = np.random.default_rng(7)
    person, taken, feat = [], [], []
    for p, lst in seeds.items():
        if not lst:
            continue
        if len(lst) > faces.SEEDS_PER_PERSON:
            lst = [lst[i] for i in sorted(rng.choice(len(lst), faces.SEEDS_PER_PERSON, replace=False))]
        F = np.stack([f for _, f in lst])
        T = np.array([t for t, _ in lst], dtype=np.float64)
        center = F.mean(0)
        center /= np.linalg.norm(center) + 1e-9
        sims = F @ center
        keep = sims >= np.quantile(sims, 0.15) if len(sims) >= 8 else np.ones(len(sims), bool)
        person += [p] * int(keep.sum())
        taken.append(T[keep])
        feat.append(F[keep])
    UPLOADS.mkdir(parents=True, exist_ok=True)
    tmp = SEEDS.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, person=np.array(person), taken=np.concatenate(taken) if taken else np.zeros(0),
                        feat=np.concatenate(feat).astype(np.float32) if feat else np.zeros((0, 128), np.float32))
    tmp.replace(SEEDS)
    print(f"[ingest] örnek yüzler: {len(person)} yüz, {len(set(person))} kişi", flush=True)


def load_seeds():
    """-> {kişi: (izler, tarihler)}"""
    with _seed_lock:
        stale = not SEEDS.exists() or (
            faces.CACHE.exists() and faces.CACHE.stat().st_mtime > SEEDS.stat().st_mtime
            and time.time() - SEEDS.stat().st_mtime > SEEDS_MAX_AGE)
        if stale and faces.CACHE.exists():
            build_seeds()
        if not SEEDS.exists():
            return {}
        key = SEEDS.stat().st_mtime
        if _seeds["key"] != key:
            z = np.load(SEEDS, allow_pickle=False)
            person = z["person"].astype(str)
            _seeds["data"] = {p: (z["feat"][person == p], z["taken"][person == p]) for p in set(person)}
            _seeds["key"] = key
        return _seeds["data"]


def person_score(feat, F, T, taken):
    sims = F @ feat
    if taken and T.any():
        near = np.abs(T - taken) < 3 * faces.YEAR  # yaşla değişen yüzler: tarihçe yakın örnekler
        if near.sum() >= 3:
            sims = sims[near]
    k = min(5, len(sims))
    return float(np.sort(sims)[-k:].mean())


def recognize(found, taken, kind):
    """found: [(kutu, iz)] -> (kişiler, [[x, y, w, h, kişi]]). Etiket olmadığı için arşivdeki etiketsiz yüzlerle
    aynı sıkı eşik uygulanır: en benzer kişi RECOG_MIN'i geçmeli ve ikinciden RECOG_MARGIN kadar önde olmalı."""
    seeds = load_seeds()
    family = json.loads((DATA / "family.json").read_text("utf-8"))
    not_before = {p: time.mktime(time.strptime(i["not_before"], "%Y-%m-%d"))
                  for p, i in family["people"].items() if i.get("not_before")}
    cands = [p for p, (F, _) in seeds.items() if len(F) >= faces.RECOG_MIN_SEEDS and p in family["people"]
             and not (p in not_before and taken and taken < not_before[p])]
    hits = []
    for fi, (_, feat) in enumerate(found):
        if not cands:
            break
        scores = sorted(((person_score(feat, *seeds[p], taken), p) for p in cands), reverse=True)
        best, second = scores[0][0], scores[1][0] if len(scores) > 1 else -1
        if best >= faces.RECOG_MIN and best - second >= faces.RECOG_MARGIN:
            hits.append((best, fi, scores[0][1]))
    assign, used = {}, set()
    for s, fi, p in sorted(hits, reverse=True):  # bir kişi bir fotoğrafta bir kez
        if p not in used and fi not in assign:
            assign[fi] = p
            used.add(p)
    boxes = [] if kind == "video" else \
        [[round(float(v), 4) for v in found[fi][0]] + [p] for fi, p in sorted(assign.items())]
    return sorted(used), boxes


def nudity_reasons(media_id, kind):
    if kind == "video":
        return []  # kullanıcı kararı: videolarda çıplaklık kontrolü yapılmaz
    try:
        import nudity
        if not nudity.MODEL.exists():
            return []
        import cv2
    except Exception:
        return []
    reasons = set()
    for f in frames(media_id, kind):
        img = cv2.imread(str(f))
        if img is not None:
            reasons |= set(nudity.verdict(nudity.detect(img)))
    return sorted(reasons)


# ------------------------------------------------------------------ tek yükleme
def handle(raw: Path, media_id: str, kind: str) -> dict:
    """Yüklemeyi işler -> {w, h, dur, taken, people, faces, nsfw}. Çıktılar zaten varsa yeniden üretilmez."""
    for sub in ("full", "thumb", "video", "vframes"):
        (UMEDIA / sub).mkdir(parents=True, exist_ok=True)
    dur = None
    taken = None
    if kind == "video":
        if not (UMEDIA / "video" / f"{media_id}.mp4").exists() or not (UMEDIA / "vframes" / media_id).exists():
            info = videos.transcode(raw, media_id, UMEDIA)
            dur = info.get("dur")
            (UMEDIA / "video" / f"{media_id}.json").write_text(json.dumps({"dur": dur}), "utf-8")
        else:
            meta = UMEDIA / "video" / f"{media_id}.json"
            dur = json.loads(meta.read_text("utf-8")).get("dur") if meta.exists() else None
        with Image.open(UMEDIA / "full" / f"{media_id}.jpg") as im:
            size = im.size
    else:
        size, taken = process_image(raw, media_id)
    found = [f for fr in frames(media_id, kind) for f in faces._scan_image(fr)]
    people, boxes = recognize(found, taken or time.time(), kind)
    return {"w": size[0], "h": size[1], "dur": dur, "taken": taken, "people": people, "faces": boxes,
            "nsfw": nudity_reasons(media_id, kind), "found": len(found)}


# ------------------------------------------------------------------ arşive alma
def archive(only=None, force=False):
    """Hazır (ve gizlenmemiş) yüklemeleri data/media'ya kopyalar ve data/index.json'a ekler."""
    idx_path = DATA / "index.json"
    if not force and idx_path.exists() and time.time() - idx_path.stat().st_mtime < 120:
        raise RuntimeError("index.json son 2 dakikada değişmiş: başka bir araç yazıyor olabilir (--force ile zorla)")
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    q = ("SELECT p.*, u.person FROM posts p JOIN users u ON u.id = p.user_id "
         "WHERE p.status = 'ready' AND p.archived = 0 AND p.deleted = 0 AND p.nsfw = '[]'")
    rows = con.execute(q).fetchall()
    if only is not None:
        rows = [r for r in rows if r["id"] in set(only)]
    if not rows:
        print("[ingest] arşive alınacak yükleme yok", flush=True)
        return []
    index = json.loads(idx_path.read_text("utf-8"))
    done = []
    for r in rows:
        m = r["media"]
        if m not in index["photos"]:
            for sub in ("full", "thumb"):
                (MEDIA / sub).mkdir(parents=True, exist_ok=True)
                shutil.copy2(UMEDIA / sub / f"{m}.jpg", MEDIA / sub / f"{m}.jpg")
            if r["kind"] == "video":
                (MEDIA / "video").mkdir(parents=True, exist_ok=True)
                shutil.copy2(UMEDIA / "video" / f"{m}.mp4", MEDIA / "video" / f"{m}.mp4")
                shutil.copytree(UMEDIA / "vframes" / m, MEDIA / "vframes" / m, dirs_exist_ok=True)
            people = json.loads(r["people"])
            entry = {"w": r["w"], "h": r["h"], "taken": int(r["taken"] or r["created"]), "type": r["kind"],
                     "tags": [], "recognized": people, "people": people, "source": "upload", "uploader": r["person"],
                     # çıplaklık kontrolü yüklemede yapıldı (ya da yönetici onayladı): aile sitesi bu alan olmadan göstermez
                     "nsfw": json.loads(r["nsfw"])}
            if json.loads(r["faces"]):
                entry["faces"] = json.loads(r["faces"])
            if r["kind"] == "video" and r["dur"]:
                entry["dur"] = r["dur"]
            index["photos"][m] = entry
        done.append(r["id"])
    import_takeout.build_people(index)
    import_takeout.save_index(index, idx_path)
    with con:
        con.executemany("UPDATE posts SET archived = 1 WHERE id = ?", [(i,) for i in done])
    print(f"[ingest] {len(done)} yükleme arşive alındı", flush=True)
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", action="store_true", help="hazır yüklemeleri data/index.json'a ekle")
    ap.add_argument("--force", action="store_true", help="index.json yakın zamanda değişmiş olsa da arşive al")
    ap.add_argument("--seeds", action="store_true", help="örnek yüzleri yeniden hesapla")
    args = ap.parse_args()
    if args.seeds:
        build_seeds()
        return
    if args.archive:
        archive(force=args.force)
        return
    # bekleyenleri sitenin kendi işleyicisiyle işle (bildirimler dahil)
    sys.path.insert(0, str(ROOT / "app"))
    import social
    social.init(start_worker=False)
    pending = [r["id"] for r in social.db().execute("SELECT id FROM posts WHERE status = 'processing' AND deleted = 0")]
    for pid in pending:
        social.process_post(pid)
    print(f"[ingest] {len(pending)} yükleme işlendi", flush=True)


if __name__ == "__main__":
    main()
