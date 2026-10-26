"""Çıplaklık, mayo ve iç çamaşırı içeren fotoğrafları/videoları bulup siteden gizler.

Kullanım:  .venv\\Scripts\\python.exe tools\\nudity.py          (henüz kontrol edilmemiş her şeyi kontrol et)
           .venv\\Scripts\\python.exe tools\\nudity.py --all    (hepsini yeniden kontrol et)

Model: NudeNet 320n (models/nudenet_320n_cv.onnx, tools/prepare_nudenet.py ile OpenCV'ye uyarlanmış).
Fotoğrafın tamamına ve ayrıca her yüzün altındaki gövdeye (kalabalık/havuz fotoğraflarında insanlar
küçük kaldığı için) bakılır; video karelerinde kare 2x2 parçaya bölünür. Bulunan nedenler data/index.json'da
"nsfw" alanına yazılır (boş liste = temiz); site şüphelileri ve henüz kontrol edilmemişleri göstermez.
Yönetici sitede gizlenenleri görüp geri açabilir veya kaçanları elle gizleyebilir (data/moderation.json).
Kural bilerek katıdır: yanlışlıkla gizlenen bir fotoğrafı geri açmak, yanlışlıkla yayımlananı geri almaktan kolaydır.
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

# Makes the calls do nothing where claude-progress is not installed.
_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in the plugin
try:
    sys.path.insert(0, os.environ["CLAUDE_PROGRESS_DIR"]); from claude_progress import claude_progress
except Exception:
    def claude_progress(*args): pass
finally:
    sys.dont_write_bytecode = _bytecode

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("KADEROGLU_DATA", ROOT / "data"))
MEDIA = DATA / "media"
MODEL = ROOT / "models" / "nudenet_320n_cv.onnx"
SIZE = 320
LABELS = [
    "FEMALE_GENITALIA_COVERED", "FACE_FEMALE", "BUTTOCKS_EXPOSED", "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED", "MALE_BREAST_EXPOSED", "ANUS_EXPOSED", "FEET_EXPOSED", "BELLY_COVERED",
    "FEET_COVERED", "ARMPITS_COVERED", "ARMPITS_EXPOSED", "FACE_MALE", "BELLY_EXPOSED",
    "MALE_GENITALIA_EXPOSED", "ANUS_COVERED", "FEMALE_BREAST_COVERED", "BUTTOCKS_COVERED",
]
# çıplaklık: tek başına yeterli
EXPLICIT = {"FEMALE_BREAST_EXPOSED": .30, "FEMALE_GENITALIA_EXPOSED": .30, "MALE_GENITALIA_EXPOSED": .30,
            "BUTTOCKS_EXPOSED": .35, "ANUS_EXPOSED": .30}
# üstü çıplak / açık karın (plaj, havuz, iç çamaşırı): tek başına yeterli
SKIN = {"MALE_BREAST_EXPOSED": .40, "BELLY_EXPOSED": .40}
# mayo / iç çamaşırı: örtülü göğüs, kalça veya kasık; açık ten ile birlikteyse ya da yüksek skorla
COVERED = {"FEMALE_BREAST_COVERED": .45, "FEMALE_GENITALIA_COVERED": .40, "BUTTOCKS_COVERED": .45}
COVERED_ALONE = .70

_local = threading.local()


def net():
    if not hasattr(_local, "net"):
        _local.net = cv2.dnn.readNetFromONNX(str(MODEL))
    return _local.net


def detect(img):
    """BGR görüntü -> {etiket: en yüksek skor}"""
    h, w = img.shape[:2]
    side = max(h, w)
    pad = cv2.copyMakeBorder(img, 0, side - h, 0, side - w, cv2.BORDER_CONSTANT)
    blob = cv2.dnn.blobFromImage(pad, 1 / 255.0, (SIZE, SIZE), (0, 0, 0), swapRB=True, crop=False)
    n = net()
    n.setInput(blob)
    out = np.squeeze(n.forward()).T  # 2100 x 22
    scores = out[:, 4:]
    best = {}
    cls = scores.argmax(1)
    conf = scores.max(1)
    for c, s in zip(cls[conf >= .2], conf[conf >= .2]):
        lab = LABELS[c]
        best[lab] = max(best.get(lab, 0), float(s))
    return best


def body_crops(img, face_boxes):
    """Her yüzün altındaki gövde bölgesi (yüz genişliğinin ~4 katı, boyunun ~5 katı).
    Kalabalık/uzak çekimlerde tüm fotoğraf 320 piksele küçülünce gövdeler kayboluyor; böylece her kişiye
    yakından bakılır."""
    h, w = img.shape[:2]
    crops = []
    for x, y, fw, fh in face_boxes:  # 0-1 oranları
        fx, fy, fW, fH = x * w, y * h, fw * w, fh * h
        if fW < 12:
            continue
        cx = fx + fW / 2
        x0, x1 = int(max(0, cx - fW * 2.2)), int(min(w, cx + fW * 2.2))
        y0, y1 = int(max(0, fy - fH * 0.2)), int(min(h, fy + fH * 5.5))
        if x1 - x0 > 20 and y1 - y0 > 20:
            crops.append(img[y0:y1, x0:x1])
    return crops


def detect_all(img, face_boxes=()):
    """Tüm fotoğraf + kişi başına gövde kırpmaları; etiket başına en yüksek skor."""
    best = detect(img)
    for crop in body_crops(img, face_boxes):
        for k, v in detect(crop).items():
            best[k] = max(best.get(k, 0), v)
    return best


def verdict(found):
    """Gizlenmesi gerekiyorsa nedenini döndürür."""
    hits = [k for k, t in EXPLICIT.items() if found.get(k, 0) >= t]
    hits += [k for k, t in SKIN.items() if found.get(k, 0) >= t]
    covered = [k for k, t in COVERED.items() if found.get(k, 0) >= t]
    skin = found.get("BELLY_EXPOSED", 0) >= .25 or found.get("ARMPITS_EXPOSED", 0) >= .35 \
        or found.get("MALE_BREAST_EXPOSED", 0) >= .25
    if covered and skin:
        hits += covered
    hits += [k for k in COVERED if found.get(k, 0) >= COVERED_ALONE]
    return sorted(set(hits))


def frames_of(photo_id, ph):
    if ph.get("type") == "video":
        fdir = MEDIA / "vframes" / photo_id
        return sorted(fdir.glob("*.jpg")) if fdir.exists() else [MEDIA / "full" / f"{photo_id}.jpg"]
    return [MEDIA / "full" / f"{photo_id}.jpg"]


def quadrants(img):
    """Örtüşen 2x2 parça: video karelerinde küçük kalan gövdeler için."""
    h, w = img.shape[:2]
    out = []
    for y0 in (0, int(h * .4)):
        for x0 in (0, int(w * .4)):
            out.append(img[y0:y0 + int(h * .6), x0:x0 + int(w * .6)])
    return out


FACE_BOXES = {}  # fotoğraf kimliği -> yüz kutuları (tools/faces.py önbelleğinden)


def check(item):
    photo_id, ph = item
    if ph.get("type") == "video":
        return photo_id, []  # kullanıcı kararı: videolarda çıplaklık kontrolü yapılmaz
    reasons = set()
    for f in frames_of(photo_id, ph):
        img = cv2.imread(str(f))
        if img is None:
            continue
        if ph.get("type") == "video":
            found = detect(img)
            for part in quadrants(img):
                for k, v in detect(part).items():
                    found[k] = max(found.get(k, 0), v)
        else:
            found = detect_all(img, FACE_BOXES.get(photo_id, ()))
        reasons |= set(verdict(found))
    return photo_id, sorted(reasons)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="Daha önce kontrol edilenleri de yeniden kontrol et")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    args = ap.parse_args()
    idx_path = DATA / "index.json"
    index = json.loads(idx_path.read_text("utf-8"))
    todo = [(i, p) for i, p in index["photos"].items() if args.all or "nsfw" not in p]
    total = len(todo)
    # kalabalık/uzak çekimlerde her kişinin gövdesine yakından bakmak için yüz konumları
    try:
        import faces
        FACE_BOXES.update({i: [b for b, _ in lst] for i, lst in faces.load_cache().items()})
    except Exception as e:
        print(f"Uyarı: yüz önbelleği okunamadı ({e}); sadece tüm fotoğrafa bakılacak.")
    print(f"{total} öğe kontrol ediliyor…", flush=True)
    results = {}
    done, last = 0, 0.0
    with ThreadPoolExecutor(args.workers) as pool:
        for photo_id, reasons in pool.map(check, todo):
            results[photo_id] = reasons
            done += 1
            now = time.monotonic()
            if now - last > 1 or done == total:
                last = now
                flagged = sum(1 for r in results.values() if r)
                claude_progress("-n", "nudity", f"{done}/{total}", f"{flagged} gizlenecek")
                print(f"\r  {done}/{total}  ({flagged} şüpheli)", end="", flush=True)
    print()

    # en güncel index ile birleştir. Burada sadece bulunan nedenler ("nsfw") yazılır; gizleme kararını
    # site verir: yöneticinin data/moderation.json'daki "kept"/"manual" kararları her zaman önce gelir.
    fresh = json.loads(idx_path.read_text("utf-8"))
    for photo_id, reasons in results.items():
        if photo_id in fresh["photos"]:
            fresh["photos"][photo_id]["nsfw"] = reasons
    tmp = idx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(fresh, ensure_ascii=False), "utf-8")
    tmp.replace(idx_path)
    flagged = sum(1 for p in fresh["photos"].values() if p.get("nsfw"))
    claude_progress("-n", "nudity", "done", f"{flagged} şüpheli")
    print(f"Bu turda {sum(1 for r in results.values() if r)} şüpheli; toplam şüpheli: {flagged} / {len(fresh['photos'])}")


if __name__ == "__main__":
    main()
