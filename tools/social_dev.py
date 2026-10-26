r"""Sosyal katmanı canlı sunucuya dokunmadan denemek için geliştirme sunucusu (http://127.0.0.1:8766).

    set KADEROGLU_DATA=C:\yol\test-verisi
    .venv\Scripts\python.exe tools\social_dev.py

main.py'nin yapacağını taklit eder: aile sitesi social.wrap() ile sarılır. index.html değiştirilmeden
social.js sayfaya eklenir (inject_script=True). Canlı veriye yazmamak için KADEROGLU_DATA zorunludur.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if not os.environ.get("KADEROGLU_DATA"):
    sys.exit("KADEROGLU_DATA ayarlanmalı (test verisi klasörü); canlı data/ klasörüyle çalışmaz.")
if Path(os.environ["KADEROGLU_DATA"]).resolve() == (ROOT / "data").resolve():
    sys.exit("KADEROGLU_DATA canlı data/ klasörünü gösteriyor; bir test kopyası verin.")
sys.path.insert(0, str(ROOT / "app"))

import uvicorn  # noqa: E402

import family  # noqa: E402
import social  # noqa: E402

app = social.wrap(family.app, inject_script=True)

if __name__ == "__main__":
    port = int(os.environ.get("SOCIAL_DEV_PORT", 8766))
    print(f"Geliştirme sunucusu: http://127.0.0.1:{port}  (veri: {os.environ['KADEROGLU_DATA']})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
