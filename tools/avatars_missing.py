"""Sadece profil fotoğrafı olmayan kişiler için seçim yapar (diğerlerinin profil fotoğrafına dokunmaz).

Kullanım: .venv\\Scripts\\python.exe tools\\avatars_missing.py
"""
import json

import avatars
from import_takeout import DATA, save_index


def main():
    family = json.loads((DATA / "family.json").read_text("utf-8"))
    idx_path = DATA / "index.json"
    index = json.loads(idx_path.read_text("utf-8"))
    avatars.pick_and_save(index, family, only_missing=True)
    # içe aktarma sürüyorsa araya girmiş olabilir: sadece profil alanlarını en güncel dosyaya yaz
    fresh = json.loads(idx_path.read_text("utf-8"))
    fresh["avatars"], fresh["avatar_v"] = index.get("avatars", {}), index.get("avatar_v", {})
    save_index(fresh, idx_path)


if __name__ == "__main__":
    main()
