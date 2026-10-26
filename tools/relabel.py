"""Yanlış kişiye bağlanmış fotoğrafları başka bir kişiye taşır (içe aktarmayı yeniden çalıştırmadan).

Kullanım:  .venv\\Scripts\\python.exe tools\\relabel.py <eski-kimlik> <yeni-kimlik>
Örnek:     .venv\\Scripts\\python.exe tools\\relabel.py hatice elmas-anne

Kimlikler data/family.json'daki anahtarlardır. Etiketin gelecekteki içe aktarmalarda da doğru kişiye
gitmesi için family.json'daki "aliases" listesini de güncelleyin. Taşınan kişilerin profil fotoğrafı
yeniden seçilir.
"""
import json
import sys

import avatars
from import_takeout import DATA, MEDIA, build_people, save_index


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    old, new = sys.argv[1:]
    family = json.loads((DATA / "family.json").read_text("utf-8"))
    for pid in (old, new):
        if pid not in family["people"]:
            sys.exit(f"family.json'da böyle bir kişi yok: {pid}")
    idx_path = DATA / "index.json"
    index = json.loads(idx_path.read_text("utf-8"))

    moved = 0
    for ph in index["photos"].values():
        if old in ph.get("people", []):
            moved += 1
        for field in ("people", "tags", "extra", "recognized"):
            vals = ph.get(field)
            if vals and old in vals:
                ph[field] = sorted((set(vals) - {old}) | {new})
    build_people(index)

    # iki kişinin de profil fotoğrafını yeniden seç (elle seçilmiş olanlar korunur)
    for pid in (old, new):
        if family["people"][pid].get("avatar_photo") and family["people"][pid]["avatar_photo"] in index["photos"] \
                and pid in index["photos"][family["people"][pid]["avatar_photo"]].get("people", []):
            continue
        family["people"][pid].pop("avatar_photo", None)
        family["people"][pid].pop("avatar_face", None)
        index.get("avatars", {}).pop(pid, None)
        index.get("avatar_v", {}).pop(pid, None)
        (MEDIA / "avatar" / f"{pid}.jpg").unlink(missing_ok=True)
    avatars.pick_and_save(index, family, only_missing=True)
    save_index(index, idx_path)
    print(f"{moved} fotoğraf {family['people'][old]['name']} -> {family['people'][new]['name']} taşındı.")
    for pid in (old, new):
        print(f"  {family['people'][pid]['name']}: {len(index['people'].get(pid, []))} fotoğraf")


if __name__ == "__main__":
    main()
