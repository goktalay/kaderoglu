"""Uzak Panel ve aile sitesini başlatır. Ayarlar config.json'dan okunur.

    python run.py          -> config.json'daki ayarlarla (Cloudflare + HTTPS)
    python run.py --dev    -> sadece bu bilgisayardan, http://127.0.0.1:8765
                              (panel: http://panel.localhost:8765, aile sitesi: http://localhost:8765)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

# pythonw ile (pencere açmadan) çalışırken çıktıyı log dosyasına yaz
if sys.stdout is None or sys.stderr is None:
    (ROOT / "logs").mkdir(exist_ok=True)
    log = open(ROOT / "logs" / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log

import uvicorn  # noqa: E402

if __name__ == "__main__":
    cfg_path = ROOT / "config.json"
    if not cfg_path.exists():
        sys.exit("config.json yok. Önce 'python setup_user.py' çalıştırın.")
    cfg = json.loads(cfg_path.read_text("utf-8"))

    if "--dev" in sys.argv:
        host, port, ssl = "127.0.0.1", 8765, {}
    else:
        host, port, ssl = cfg["host"], cfg["port"], {}
        if cfg.get("ssl_certfile"):
            cert, key = ROOT / cfg["ssl_certfile"], ROOT / cfg["ssl_keyfile"]
            if not cert.exists() or not key.exists():
                sys.exit(f"Sertifika bulunamadı: {cert} / {key}\n"
                         "Cloudflare > SSL/TLS > Origin Server'dan oluşturup bu yollara kaydedin.")
            ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}

    print(f"Sunucu: http{'s' if ssl else ''}://{host}:{port}", flush=True)
    uvicorn.run("main:app", host=host, port=port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_level="info", **ssl)
