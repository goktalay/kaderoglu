"""Yüz tanıma ve çıplaklık modellerini indirip models/ klasörüne hazırlar (yeni kurulumda bir kez).

Kullanım: .venv\\Scripts\\python.exe tools\\get_models.py

- YuNet (yüz bulma) ve SFace (yüz tanıma): github.com/opencv/opencv_zoo
- NudeNet 320n: PyPI'daki nudenet paketinin içinden; ardından prepare_nudenet.py ile OpenCV'ye uyarlanır
"""
import io
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
FILES = {
    "face_detection_yunet_2023mar.onnx": f"{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
NUDENET_WHEEL = "https://files.pythonhosted.org/packages/py3/n/nudenet/nudenet-3.4.2-py3-none-any.whl"


def fetch(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def main():
    MODELS.mkdir(exist_ok=True)
    for name, url in FILES.items():
        out = MODELS / name
        if not out.exists():
            print("indiriliyor:", name)
            out.write_bytes(fetch(url))
    raw = MODELS / "nudenet_320n.onnx"
    if not raw.exists():
        print("indiriliyor: nudenet 320n")
        with zipfile.ZipFile(io.BytesIO(fetch(NUDENET_WHEEL))) as z:
            raw.write_bytes(z.read("nudenet/320n.onnx"))
    cv = MODELS / "nudenet_320n_cv.onnx"
    if not cv.exists():
        subprocess.run([sys.executable, str(ROOT / "tools" / "prepare_nudenet.py"), str(raw), str(cv)], check=True)
    print("modeller hazır:", ", ".join(p.name for p in sorted(MODELS.glob("*.onnx"))))


if __name__ == "__main__":
    main()
