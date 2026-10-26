"""Giriş bilgilerini ayarlar. Şifre config.json'a sadece bcrypt özeti olarak yazılır.

Kullanım:  python setup_user.py
"""
import getpass
import json
import sys
from pathlib import Path

import bcrypt

CONFIG = Path(__file__).resolve().parent / "config.json"

DEFAULTS = {
    "host": "0.0.0.0",
    "port": 443,
    "ssl_certfile": "certs/origin.pem",
    "ssl_keyfile": "certs/origin.key",
    "cloudflare_only": True,
    "trust_cf_header": True,
    "session_idle_minutes": 120,
    "session_max_hours": 12,
    "tree_max_entries": 300000,
}


def main():
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        cfg.update(json.loads(CONFIG.read_text("utf-8")))

    if sys.stdin.isatty():
        user = input(f"E-posta [{cfg.get('username', '')}]: ").strip() or cfg.get("username", "")
        pw = getpass.getpass("Şifre: ")
        if pw != getpass.getpass("Şifre (tekrar): "):
            sys.exit("Şifreler eşleşmiyor.")
    else:  # otomasyon için: ilk satır e-posta, ikinci satır şifre
        lines = sys.stdin.buffer.read().decode("utf-8-sig").splitlines()
        user, pw = lines[0].strip(), lines[1].rstrip("\r")

    if not user or len(pw) < 8:
        sys.exit("E-posta boş olamaz, şifre en az 8 karakter olmalı.")

    cfg["username"] = user.lstrip("﻿").lower()
    cfg["password_hash"] = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(12)).decode()
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")
    print(f"Kaydedildi: {CONFIG}")


if __name__ == "__main__":
    main()
