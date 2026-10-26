"""Etiketi olmayan bir kişiyi örnek bir fotoğraftaki yüzünden yola çıkarak tüm fotoğraflarda bulur.

Kullanım:
  .venv\\Scripts\\python.exe tools\\find_person.py <kişi> <örnek-foto> --pick right --dry-run   (önizleme)
  .venv\\Scripts\\python.exe tools\\find_person.py <kişi> <örnek-foto> --pick right            (siteye yaz)

<örnek-foto>: herhangi bir görüntü dosyası (sohbette gönderilen fotoğraf gibi).
--pick: örnekte birden fazla yüz varsa hangisi: left | right | top | bottom | largest
tools/faces.py'nin yüz önbelleğini (data/faces_cache.npz) kullanır; başka birine isim verilmiş yüzler aranmaz.
Bulunan fotoğraflara kişi eklenir (data/index.json "people" ve "faces"), sonraki içe aktarmalarda korunur.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

import faces as F

THRESHOLD = 0.50   # SFace'in olağan eşiğinden (0.363) bilerek yüksek: etiketsiz aramada yanlış eşleşme olmasın
EXPAND = 0.55      # bulunanlardan bu kadar emin olunanlar sonraki turda örnek olur


def seed_from_image(path, pick):
    det, rec = F.models()
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:  # webp gibi biçimler için PIL yedeği
        img = cv2.cvtColor(np.asarray(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    det.setInputSize((w, h))
    _, found = det.detect(img)
    if found is None or not len(found):
        sys.exit("Örnek fotoğrafta yüz bulunamadı.")
    key = {"left": lambda f: f[0], "right": lambda f: -f[0], "top": lambda f: f[1], "bottom": lambda f: -f[1],
           "largest": lambda f: -f[2] * f[3]}[pick]
    face = sorted(found, key=key)[0]
    feat = rec.feature(rec.alignCrop(img, face)).flatten()
    print(f"Örnekte {len(found)} yüz, '{pick}' seçildi: {[int(v) for v in face[:4]]}")
    return feat / np.linalg.norm(feat)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("person")
    ap.add_argument("image")
    ap.add_argument("--pick", default="largest", choices=["left", "right", "top", "bottom", "largest"])
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rounds", type=int, default=1,
                    help="Bulunanlarla örnekleri genişletme turu (1 = sadece örnek yüze benzeyenler; fazlası kayabilir)")
    ap.add_argument("--sheet", help="Bulunan yüzlerden kontrol görüntüsü (jpg) yaz")
    args = ap.parse_args()

    family = json.loads((F.DATA / "family.json").read_text("utf-8"))
    if args.person not in family["people"]:
        sys.exit(f"family.json'da böyle bir kişi yok: {args.person}")
    idx_path = F.DATA / "index.json"
    index = json.loads(idx_path.read_text("utf-8"))
    by = F.load_cache()

    # aranacak yüzler: başka birine isim verilmemiş olanlar
    keys, feats = [], []
    for pid, lst in by.items():
        named = {tuple(round(v, 4) for v in f[:4]) for f in index["photos"].get(pid, {}).get("faces", [])
                 if f[4] != args.person}
        for n, (box, feat) in enumerate(lst):
            if tuple(round(float(v), 4) for v in box) in named:
                continue
            keys.append((pid, n))
            feats.append(feat)
    feats = np.stack(feats)
    print(f"{len(keys)} isimsiz yüz aranıyor…")

    seeds = seed_from_image(Path(args.image), args.pick)[None, :]
    found = {}
    for rnd in range(args.rounds):
        sims = feats @ seeds.T
        score = np.sort(sims, axis=1)[:, -min(5, seeds.shape[0]):].mean(1) if seeds.shape[0] > 1 else sims[:, 0]
        score = np.maximum(score, sims.max(1) * 0.97)  # tek bir örneğe çok benziyorsa da say
        hits = np.where(score >= args.threshold)[0]
        found = {}
        for i in hits:  # fotoğraf başına en benzer tek yüz
            pid = keys[i][0]
            if pid not in found or score[i] > found[pid][1]:
                found[pid] = (keys[i][1], float(score[i]))
        strong = [i for i in hits if score[i] >= EXPAND]
        print(f"  tur {rnd + 1}: {len(found)} fotoğraf (örnek sayısı {seeds.shape[0]})")
        new_seeds = np.vstack([seeds[:1], feats[strong]]) if strong else seeds[:1]
        if new_seeds.shape[0] == seeds.shape[0]:
            break
        seeds = new_seeds

    if args.sheet:
        tiles = []
        for pid, (n, s) in sorted(found.items(), key=lambda kv: kv[1][1])[:64]:  # en az emin olunanlar önce
            box = by[pid][n][0]
            with Image.open(F.MEDIA / "full" / f"{pid}.jpg") as im:
                W, H = im.size
                x, y, w, h = box[0] * W, box[1] * H, box[2] * W, box[3] * H
                pad = max(w, h) * 0.4
                crop = im.convert("RGB").crop((x - pad, y - pad, x + w + pad, y + h + pad)).resize((150, 150))
            ImageDraw.Draw(crop).text((4, 4), f"{s:.2f}", fill="yellow")
            tiles.append(crop)
        sheet = Image.new("RGB", (8 * 150, ((len(tiles) + 7) // 8) * 150), "white")
        for k, t in enumerate(tiles):
            sheet.paste(t, ((k % 8) * 150, (k // 8) * 150))
        sheet.save(args.sheet, quality=85)

    print(f"Sonuç: {len(found)} fotoğrafta {family['people'][args.person]['name']} bulundu.")
    if found:
        best = max(found.items(), key=lambda kv: kv[1][1])
        print(f"En güçlü eşleşme: {best[0]} (yüz {best[1][0]}, {best[1][1]:.2f})")
    if args.dry_run:
        return

    fresh = json.loads(idx_path.read_text("utf-8"))
    for pid, (n, _) in found.items():
        ph = fresh["photos"].get(pid)
        if not ph:
            continue
        # "extra": elle eklenen kişiler; yüz eşleştirme ve içe aktarma yeniden çalışsa da korunur
        ph["extra"] = sorted(set(ph.get("extra", [])) | {args.person})
        ph["people"] = sorted(set(ph.get("people", [])) | {args.person})
        box = [round(float(v), 4) for v in by[pid][n][0]]
        faces = [f for f in ph.get("faces", []) if f[4] != args.person]
        ph["faces"] = faces + [box + [args.person]]
    from import_takeout import build_people, save_index
    build_people(fresh)
    save_index(fresh, idx_path)
    print("index.json güncellendi.")


if __name__ == "__main__":
    main()
