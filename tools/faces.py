"""Fotoğraflardaki yüzleri bulur ve Google Fotoğraflar etiketlerindeki kişilerle eşleştirir.

Kullanım:  .venv\\Scripts\\python.exe tools\\faces.py            (yeni fotoğrafları tara + eşleştir)
           .venv\\Scripts\\python.exe tools\\faces.py --match    (sadece eşleştirmeyi yeniden yap)

Takeout bir fotoğrafta kimlerin olduğunu söyler ama hangi yüzün kim olduğunu söylemez.
1) Tarama: her fotoğrafta YuNet ile yüzler bulunur, SFace ile her yüzün 128 boyutlu izi çıkarılır
   (data/faces_cache.npz; yeni eklenen fotoğraflar dışında tekrar taranmaz).
2) Eşleştirme: kişinin tek başına etiketlendiği ve tek yüz bulunan fotoğraflar o kişinin örnekleri olur.
   Çocuklar büyüdükçe değiştiği için bir yüz, kişinin tarihçe yakın örnekleriyle karşılaştırılır.
   Toplu fotoğraflarda etiketli kişiler en benzer yüzlere atanır; benzerlik eşiğin altındaysa isim yazılmaz.
Sonuç data/index.json'da her fotoğrafın "faces" alanına yazılır: [[x, y, w, h, kişi], ...] (0-1 oranları).
Modeller: models/face_detection_yunet_2023mar.onnx, models/face_recognition_sface_2021dec.onnx
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
MODELS = ROOT / "models"
CACHE = DATA / "faces_cache.npz"
DETECT_PX = 1280          # tarama çözünürlüğü (uzun kenar)
MIN_FACE = 0.035          # fotoğrafın kısa kenarına göre en küçük yüz
DET_SCORE = 0.82
MATCH_MIN = 0.36          # SFace'in "aynı kişi" kosinüs eşiği (0.363) civarı
RECOG_MIN = 0.50          # etiketsiz yüzlerde isim vermek için (bilerek yüksek: yanlış kişi eklenmesin)
RECOG_MARGIN = 0.06       # en benzer kişi ikinciden en az bu kadar önde olmalı
RECOG_MIN_SEEDS = 15      # bir kişiyi etiketsiz fotoğraflarda tanımak için gereken örnek yüz
SEEDS_PER_PERSON = 600
YEAR = 365 * 86400

_local = threading.local()


def models():
    if not hasattr(_local, "det"):
        _local.det = cv2.FaceDetectorYN.create(str(MODELS / "face_detection_yunet_2023mar.onnx"), "", (320, 320),
                                              DET_SCORE, 0.3, 5000)
        _local.rec = cv2.FaceRecognizerSF.create(str(MODELS / "face_recognition_sface_2021dec.onnx"), "")
    return _local.det, _local.rec


def scan_photo(item):
    """-> [(x, y, w, h oranları), iz] listesi. Videolarda tüm analiz karelerindeki yüzler."""
    photo_id, kind = item
    if kind == "video":
        fdir = MEDIA / "vframes" / photo_id
        frames = sorted(fdir.glob("*.jpg")) if fdir.exists() else []
        return [f for fr in frames for f in _scan_image(fr)]
    return _scan_image(MEDIA / "full" / f"{photo_id}.jpg")


def _scan_image(path):
    det, rec = models()
    img = cv2.imread(str(path))
    if img is None:
        return []
    h, w = img.shape[:2]
    s = min(1.0, DETECT_PX / max(w, h))
    small = cv2.resize(img, (int(w * s), int(h * s))) if s < 1 else img
    sh, sw = small.shape[:2]
    det.setInputSize((sw, sh))
    _, faces = det.detect(small)
    out = []
    if faces is None:
        return out
    for f in faces:
        x, y, fw, fh = f[:4]
        if min(fw, fh) < MIN_FACE * min(sw, sh):
            continue
        aligned = rec.alignCrop(small, f)
        feat = rec.feature(aligned).flatten()
        feat = feat / (np.linalg.norm(feat) + 1e-9)
        out.append(((x / sw, y / sh, fw / sw, fh / sh), feat.astype(np.float32)))
    return out


def load_cache():
    if not CACHE.exists():
        return {}
    z = np.load(CACHE, allow_pickle=False)
    by = {}
    for pid in z["scanned"]:
        by[str(pid)] = []
    for pid, box, feat in zip(z["photo"], z["box"], z["feat"]):
        by[str(pid)].append((tuple(box), feat))
    return by


def save_cache(by):
    photo, box, feat = [], [], []
    for pid, faces in by.items():
        for b, f in faces:
            photo.append(pid)
            box.append(b)
            feat.append(f)
    tmp = CACHE.with_suffix(".tmp.npz")
    np.savez_compressed(tmp, scanned=np.array(list(by.keys())), photo=np.array(photo),
                        box=np.array(box, dtype=np.float32).reshape(-1, 4),
                        feat=np.array(feat, dtype=np.float32).reshape(-1, 128))
    tmp.replace(CACHE)


def scan(index, by, workers):
    todo = [(i, p.get("type", "photo")) for i, p in index["photos"].items() if i not in by]
    total = len(todo)
    if not total:
        return
    print(f"{total} öğede yüz aranıyor ({workers} iş parçacığı)…", flush=True)
    done, last = 0, 0.0
    with ThreadPoolExecutor(workers) as pool:
        for (pid, _), faces in zip(todo, pool.map(scan_photo, todo)):
            by[pid] = faces
            done += 1
            now = time.monotonic()
            if now - last > 1 or done == total:
                last = now
                claude_progress("-n", "faces", f"{done}/{total}", "yüzler taranıyor")
                print(f"\r  {done}/{total}", end="", flush=True)
            if done % 3000 == 0:
                save_cache(by)
    print()
    save_cache(by)


def known_people(ph):
    """Google etiketleri + elle eklenenler (yüz tanımanın bulduklarından ayrı tutulur)."""
    return sorted(set(ph.get("tags", ph.get("people", []))) | set(ph.get("extra", [])))


def match(index, family, by):
    photos = index["photos"]
    people = list(family["people"])
    not_before = {p: int(time.mktime(time.strptime(i["not_before"], "%Y-%m-%d")))
                  for p, i in family["people"].items() if i.get("not_before")}
    not_after = {p: int(time.mktime(time.strptime(i["not_after"], "%Y-%m-%d"))) + 86399
                 for p, i in family["people"].items() if i.get("not_after")}
    # 1) örnekler: tek etiket + tek yüz (sadece fotoğraflar)
    seeds = {p: [] for p in people}
    for pid, ph in photos.items():
        if ph.get("type") == "video":
            continue
        tags = [t for t in known_people(ph) if t in seeds]
        faces = by.get(pid, [])
        if len(tags) == 1 and len(faces) == 1:
            seeds[tags[0]].append((ph.get("taken") or 0, faces[0][1]))
    # elle seçilmiş profil fotoğrafındaki yüz de güçlü bir örnektir
    for p, info in family["people"].items():
        photo, box = info.get("avatar_photo"), info.get("avatar_face")
        if photo in photos and box and by.get(photo):
            with_size = cv2.imread(str(MEDIA / "full" / f"{photo}.jpg"))
            if with_size is not None:
                H, W = with_size.shape[:2]
                cx, cy = (box[0] + box[2] / 2) / W, (box[1] + box[3] / 2) / H
                best = min(by[photo], key=lambda f: (f[0][0] + f[0][2] / 2 - cx) ** 2 + (f[0][1] + f[0][3] / 2 - cy) ** 2)
                seeds[p].extend([(photos[photo].get("taken") or 0, best[1])] * 5)

    seed_t, seed_f = {}, {}

    def build_seeds():
        seed_t.clear()
        seed_f.clear()
        rng = np.random.default_rng(7)
        for p, lst in seeds.items():
            if not lst:
                continue
            if len(lst) > SEEDS_PER_PERSON:
                lst = [lst[i] for i in sorted(rng.choice(len(lst), SEEDS_PER_PERSON, replace=False))]
            T = np.array([t for t, _ in lst], dtype=np.float64)
            F = np.stack([f for _, f in lst])
            # aykırı örnekleri ele: kişinin ortalamasına en uzak %15
            center = F.mean(0)
            center /= np.linalg.norm(center) + 1e-9
            sims = F @ center
            keep = sims >= np.quantile(sims, 0.15) if len(sims) >= 8 else np.ones(len(sims), bool)
            seed_f[p], seed_t[p] = F[keep], T[keep]

    def person_score(feat, p, taken):
        F, T = seed_f[p], seed_t[p]
        sims = F @ feat
        if taken and T.any():
            # yaşla değişen yüzler: tarihçe yakın örneklere ağırlık ver
            near = np.abs(T - taken) < 3 * YEAR
            if near.sum() >= 3:
                sims = sims[near]
        k = min(5, len(sims))
        return float(np.sort(sims)[-k:].mean())

    build_seeds()

    # 2) eleme: tek başına fotoğrafı olmayan kişiler için. "Kerim + Mehmet" etiketli, iki yüzlü bir
    #    fotoğrafta Kerim tanınınca kalan yüz Mehmet'tir. Bu yüzler o kişinin örneği olur.
    #    Tekrarlı: bir turda örnek kazanan kişi (ör. Nazife) sonraki turda başkasını (ör. Mehmet) çözer.
    weak = {p for p in people if len(seed_f.get(p, [])) < RECOG_MIN_SEEDS}
    gained = {p: [] for p in weak}
    done_photos = set()
    for _round in range(4):
        anchors = {p for p in seed_f if len(seed_f[p]) >= 3}
        added = 0
        for pid, ph in photos.items():
            faces = by.get(pid, [])
            if pid in done_photos or ph.get("type") == "video" or len(faces) < 2:
                continue
            known = known_people(ph)
            # eksik: hâlâ az örneği olan; birden fazlaysa, hiç/çok az örneği olan tek kişi
            missing = [t for t in known if len(seed_f.get(t, [])) < RECOG_MIN_SEEDS]
            if len(missing) > 1:
                missing = [t for t in missing if t not in anchors]
            strong = [t for t in known if t not in missing and t in anchors]
            if len(missing) != 1 or not strong or len(faces) != len(strong) + 1:
                continue
            S = np.array([[person_score(f, p, ph.get("taken")) for p in strong] for _, f in faces])
            used_f, used_p = set(), set()
            for fi, pi in sorted(np.ndindex(S.shape), key=lambda ij: -S[ij]):
                if fi in used_f or pi in used_p or S[fi, pi] < MATCH_MIN:
                    continue
                used_f.add(fi)
                used_p.add(pi)
            if len(used_p) == len(strong):  # tanınanların hepsi yerine oturdu: kalan tek yüz eksik kişinin
                (rest,) = set(range(len(faces))) - used_f
                item = (ph.get("taken") or 0, faces[rest][1])
                gained[missing[0]].append(item)
                seeds[missing[0]].append(item)
                done_photos.add(pid)
                added += 1
        if not added:
            break
        build_seeds()
    if any(gained.values()):
        print("Elemeyle eklenen örnek:", ", ".join(f"{family['people'][p]['name'].split()[0]} +{len(l)}" for p, l in gained.items() if l))
    print("Kişi başına örnek yüz:", ", ".join(f"{family['people'][p]['name'].split()[0]} {len(seed_f[p])}" for p in seed_f))

    # etiketsiz tanıma için yeterli örneği olan kişiler
    recog_people = [p for p in seed_f if len(seed_f[p]) >= RECOG_MIN_SEEDS]

    def allowed(p, taken):
        if p in not_before and taken and taken < not_before[p]:
            return False
        return not (p in not_after and taken and taken > not_after[p])

    labeled = total_faces = recognized_photos = 0
    for pid, ph in photos.items():
        faces = by.get(pid, [])
        taken = ph.get("taken")
        known = known_people(ph)
        tags = [t for t in known if t in seed_f]
        assign = {}
        if faces and tags:
            S = np.array([[person_score(f, p, taken) for p in tags] for _, f in faces])
            if ph.get("type") == "video":
                # video kareleri: aynı kişi birçok karede; kişi başına en benzer yüz yeterli
                for pi, p in enumerate(tags):
                    fi = int(S[:, pi].argmax())
                    if S[fi, pi] >= MATCH_MIN:
                        assign.setdefault(fi, p)
            else:
                # en yüksek benzerlikten başlayarak bire bir ata
                taken_f, taken_p = set(), set()
                for fi, pi in sorted(np.ndindex(S.shape), key=lambda ij: -S[ij]):
                    if fi in taken_f or pi in taken_p or S[fi, pi] < MATCH_MIN:
                        continue
                    assign[fi] = tags[pi]
                    taken_f.add(fi)
                    taken_p.add(pi)
                # tek etiket + tek yüz: eşik altında olsa da büyük olasılıkla odur
                if len(faces) == 1 and len(tags) == 1 and 0 not in assign and S[0, 0] > MATCH_MIN * 0.6:
                    assign[0] = tags[0]
        # etiketlenmemiş yüzler: tüm aile üyeleriyle karşılaştır, sadece yüksek benzerlikte isim ver
        found = set(assign.values())
        for fi, (_, feat) in enumerate(faces):
            if fi in assign:
                continue
            cands = [p for p in recog_people if p not in found and p not in known and allowed(p, taken)]
            if not cands:
                continue
            scores = [person_score(feat, p, taken) for p in cands]
            order = np.argsort(scores)[::-1]
            best = scores[order[0]]
            second = scores[order[1]] if len(order) > 1 else -1
            # belirgin üstünlük şartı: iki kişiye birden benziyorsa (kardeşler) isim verme
            if best >= RECOG_MIN and best - second >= RECOG_MARGIN:
                assign[fi] = cands[order[0]]
                found.add(cands[order[0]])
        recognized = sorted(set(assign.values()) - set(known))
        if recognized:
            recognized_photos += 1
        ph["recognized"] = recognized
        ph["people"] = sorted(set(known) | set(recognized))
        result = [] if ph.get("type") == "video" else \
            [[round(float(v), 4) for v in faces[fi][0]] + [p] for fi, p in sorted(assign.items())]
        total_faces += len(faces)
        labeled += len(assign)
        if result:
            ph["faces"] = result
        else:
            ph.pop("faces", None)
    print(f"{total_faces} yüz bulundu, {labeled} tanesine isim verildi; "
          f"{recognized_photos} öğeye etiketi olmayan aile üyesi yüzünden eklendi.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", action="store_true", help="Taramayı atla, sadece eşleştir")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    args = ap.parse_args()
    family = json.loads((DATA / "family.json").read_text("utf-8"))
    idx_path = DATA / "index.json"
    index = json.loads(idx_path.read_text("utf-8"))
    by = load_cache()
    if not args.match:
        scan(index, by, args.workers)
    match(index, family, by)
    # index.json'u en güncel haliyle birleştir (tarama sırasında başka bir şey yazmış olabilir)
    fresh = json.loads(idx_path.read_text("utf-8"))
    for pid, ph in fresh["photos"].items():
        mine = index["photos"].get(pid)
        if not mine:
            continue
        if mine.get("faces"):
            ph["faces"] = mine["faces"]
        else:
            ph.pop("faces", None)
        ph["recognized"] = mine.get("recognized", [])
        ph["people"] = sorted(set(known_people(ph)) | set(ph["recognized"]))
    from import_takeout import build_people, save_index
    build_people(fresh)
    save_index(fresh, idx_path)
    claude_progress("-n", "faces", "done", "yüzler eşleştirildi")


if __name__ == "__main__":
    main()
