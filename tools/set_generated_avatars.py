"""Yapay zekayla üretilen portreleri (data/generated_avatars/<kimlik>.png) profil fotoğrafı yapar.

Kullanım: .venv\\Scripts\\python.exe tools\\set_generated_avatars.py              (klasördeki hepsi)
          .venv\\Scripts\\python.exe tools\\set_generated_avatars.py kerim mert   (sadece bunlar)
          .venv\\Scripts\\python.exe tools\\set_generated_avatars.py --except emine yavuz

Her kişi için: 400x400 EXIF'siz JPEG -> data/media/avatar/<kimlik>.jpg, family.json'a "avatar_generated": true
(tools/avatars.py bu kişilere dokunmaz; sitede yönetici elle seçerse bayrak kalkar) ve index.json'da
avatar_v[kimlik] = şimdiki zaman (önbellek kırma). index.json'a başka araçlar da yazdığı için dosya
yazmadan hemen önce yeniden okunur ve sadece avatar_v güncellenir.
"""
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("KADEROGLU_DATA", ROOT / "data"))
GEN = DATA / "generated_avatars"
AVATAR = DATA / "media" / "avatar"


def write_json(path, obj, indent=None):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent), "utf-8")
    os.replace(tmp, path)


def main():
    args = sys.argv[1:]
    family_path = DATA / "family.json"
    family = json.loads(family_path.read_text("utf-8"))
    people = family["people"]
    available = sorted(p.stem for p in GEN.glob("*.png") if p.stem in people)
    if args and args[0] == "--except":
        chosen = [p for p in available if p not in args[1:]]
    else:
        chosen = [p for p in available if not args or p in args]
    if not chosen:
        sys.exit("Ayarlanacak portre yok.")

    AVATAR.mkdir(parents=True, exist_ok=True)
    for pid in chosen:
        img = ImageOps.fit(Image.open(GEN / f"{pid}.png").convert("RGB"), (400, 400), Image.LANCZOS)
        tmp = AVATAR / f"{pid}.jpg.tmp"
        img.save(tmp, "JPEG", quality=90)  # yeni Image: EXIF taşımaz
        os.replace(tmp, AVATAR / f"{pid}.jpg")
        people[pid]["avatar_generated"] = True
        print("ok", pid)
    write_json(family_path, family, indent=2)

    idx_path = DATA / "index.json"
    now = int(time.time())
    fresh = json.loads(idx_path.read_text("utf-8"))
    av_v = fresh.setdefault("avatar_v", {})
    for pid in chosen:
        av_v[pid] = now
    write_json(idx_path, fresh)
    print(f"{len(chosen)} profil fotoğrafı ayarlandı.")


if __name__ == "__main__":
    main()
