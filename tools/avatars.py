"""Aile ağacı için profil fotoğraflarını seçer ve yüze odaklanarak kırpar.

Kullanım:  .venv\\Scripts\\python.exe tools\\avatars.py          (tüm kişiler için yeniden seç)

Kişinin tek başına etiketlendiği en yeni fotoğraflara bakılır; içinde tek ve belirgin bir yüz
olan fotoğraf seçilir ve kare olarak yüzün çevresinden kırpılır. family.json'da bir kişiye
"avatar_photo": "<fotoğraf kimliği>" yazılırsa o fotoğraf kullanılır.
OpenCV yoksa fotoğrafın üst-orta kısmı kırpılır.
"""
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image

try:
    import cv2
    import numpy as np
    _CASCADE = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
except Exception:
    cv2 = None

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("KADEROGLU_DATA", ROOT / "data"))
MEDIA = DATA / "media"
AVATAR_PX = 400
CANDIDATES = 80


def detect_faces(img: Image.Image):
    if cv2 is None:
        return []
    gray = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    gray = cv2.equalizeHist(gray)
    min_side = int(min(gray.shape) * 0.08)
    faces = _CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=7, minSize=(min_side, min_side))
    return [tuple(map(int, f)) for f in faces]


def score(img, faces):
    """Tek, büyük ve kenarlara yapışık olmayan yüz en iyisi."""
    if len(faces) != 1:
        return 0
    w, h = img.size
    x, y, fw, fh = faces[0]
    ratio = (fw * fh) / (w * h)
    if ratio > 0.35:  # çok yakın çekim/yarım yüz olabilir
        return 0.2
    cx, cy = (x + fw / 2) / w, (y + fh / 2) / h
    centered = 1 - min(1, abs(cx - .5) * 1.2 + abs(cy - .42) * .8)
    return ratio * 10 + centered


def crop_face(img: Image.Image, face, scale=2.6):
    """scale: yüz boyutunun kaçı kadar alan alınsın (yakın duran biri kadraja girmesin diye küçültülebilir)."""
    w, h = img.size
    if face:
        x, y, fw, fh = face
        side = int(min(max(fw, fh) * scale, w, h))
        cx, cy = x + fw / 2, y + fh / 2 + fh * 0.15  # omuzlar da biraz görünsün
    else:
        side = min(w, h)
        cx, cy = w / 2, (side / 2 + h * 0.08) if h > w else h / 2
    left = int(min(max(cx - side / 2, 0), w - side))
    top = int(min(max(cy - side / 2, 0), h - side))
    return img.crop((left, top, left + side, top + side)).resize((AVATAR_PX, AVATAR_PX), Image.LANCZOS)


def faces_in(photo_id):
    """Sitede yüz seçtirmek için: fotoğraf boyutu ve bulunan yüz kutuları."""
    with Image.open(MEDIA / "full" / f"{photo_id}.jpg") as im:
        return im.size, detect_faces(im)


def save_avatar(index, pid, photo_id, face=None, scale=2.6):
    """Verilen fotoğraftan (ve seçilmişse yüz kutusundan) profil fotoğrafını kırpıp kaydeder.
    Yüz verilmezse en büyük yüz, o da yoksa üst-orta kullanılır."""
    with Image.open(MEDIA / "full" / f"{photo_id}.jpg") as im:
        im = im.convert("RGB")
        if face is None:
            faces = detect_faces(im)
            face = max(faces, key=lambda f: f[2] * f[3]) if faces else None
        (MEDIA / "avatar").mkdir(parents=True, exist_ok=True)
        crop_face(im, face, scale).save(MEDIA / "avatar" / f"{pid}.jpg", "JPEG", quality=86, optimize=True)
    index.setdefault("avatars", {})[pid] = photo_id
    # adres sürümü: aynı fotoğrafta başka yüz seçilse de Cloudflare eski kırpmayı vermesin
    index.setdefault("avatar_v", {})[pid] = int(time.time())
    return face


def pick_and_save(index, family, only_missing=False, log=print):
    (MEDIA / "avatar").mkdir(parents=True, exist_ok=True)
    index.setdefault("avatars", {})
    photos = index["photos"]
    for pid, p in family["people"].items():
        ids = index["people"].get(pid, [])
        if not ids:
            continue
        if only_missing and index["avatars"].get(pid) in photos and (MEDIA / "avatar" / f"{pid}.jpg").exists():
            continue
        if p.get("avatar_generated") and (MEDIA / "avatar" / f"{pid}.jpg").exists():
            continue  # yapay zekayla üretilmiş stüdyo portresi (tools/set_generated_avatars.py): dokunma
        best, best_face, best_score = None, None, -1
        forced = p.get("avatar_photo")
        if forced in photos:
            # sitede elle seçilmiş yüz varsa onu koru
            box = p.get("avatar_face")
            save_avatar(index, pid, forced, tuple(box) if box else None, p.get("avatar_scale", 2.6))
            log(f"  {p['name']:<22} {forced} (elle seçildi)")
            continue
        else:
            solo = [i for i in ids if len(photos[i].get("people", [])) == 1]
            for i in (solo or ids)[:CANDIDATES]:
                with Image.open(MEDIA / "full" / f"{i}.jpg") as im:
                    faces = detect_faces(im)
                    s = score(im, faces)
                if s > best_score:
                    best, best_face, best_score = i, (faces[0] if len(faces) == 1 else None), s
        save_avatar(index, pid, best, best_face)
        log(f"  {p['name']:<22} {best} {'(yüz)' if best_face else '(orta)'}")


def main():
    family = json.loads((DATA / "family.json").read_text("utf-8"))
    idx_path = DATA / "index.json"
    index = json.loads(idx_path.read_text("utf-8"))
    if cv2 is None:
        print("Uyarı: OpenCV yok, yüz algılamadan kırpılacak (pip install opencv-python-headless).")
    print("Profil fotoğrafları seçiliyor:")
    pick_and_save(index, family)
    tmp = idx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), "utf-8")
    tmp.replace(idx_path)


if __name__ == "__main__":
    sys.exit(main())
