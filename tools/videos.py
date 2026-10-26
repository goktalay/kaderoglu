"""Videoları tarayıcıda oynayacak şekilde dönüştürür, kapak resmi ve analiz kareleri çıkarır.

- 720p sınıfı (uzun kenar en fazla 1280) H.264 + AAC MP4, "faststart" ile (oynatma beklemeden başlar)
- Ekran kartı varsa NVENC (h264_nvenc), yoksa libx264
- Konum dahil tüm üst veriler silinir (-map_metadata -1)
- media/full ve media/thumb altına kapak resmi (zaman çizelgesi ve harita bunları kullanır)
- media/vframes/<kimlik>/ altına en fazla 12 kare (yüz tanıma ve çıplaklık kontrolü için)
"""
import re
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
MAX_SIDE = 1280
FULL_PX, THUMB_PX = 1600, 480
_nvenc_ok = None
_SCALE = (f"scale=w='if(gte(iw,ih),trunc(min({MAX_SIDE},iw)/2)*2,-2)'"
          f":h='if(gte(iw,ih),-2,trunc(min({MAX_SIDE},ih)/2)*2)'")
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # pythonw altında konsol penceresi açılmasın


def _run(args, timeout=3600):
    return subprocess.run([FFMPEG, "-hide_banner", "-nostdin", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NOWIN)


def _duration(stderr):
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    return round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 1) if m else 0.0


def probe_duration(src) -> float:
    """Videoyu dönüştürmeden süresini okur (kopya kontrolü için)."""
    return _duration(_run(["-i", str(src)], timeout=60).stderr)


def _encode(src, out, encoder):
    vopts = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "27", "-b:v", "0"] if encoder == "nvenc"
             else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24"])
    return _run(["-y", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-sn",
                 "-vf", _SCALE, *vopts, "-pix_fmt", "yuv420p", "-profile:v", "high",
                 "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", str(out)])


def transcode(src: Path, vid: str, media: Path) -> dict:
    global _nvenc_ok
    out = media / "video" / f"{vid}.mp4"
    part = out.with_suffix(".part.mp4")
    r = None
    if _nvenc_ok is not False:
        r = _encode(src, part, "nvenc")
        if r.returncode != 0 and _nvenc_ok is None and "nvenc" in r.stderr.lower():
            _nvenc_ok = False  # ekran kartı kodlayıcısı yok: hep yazılımla devam
        elif r.returncode == 0:
            _nvenc_ok = True
    if r is None or r.returncode != 0:
        r = _encode(src, part, "x264")
    if r.returncode != 0 or not part.exists():
        part.unlink(missing_ok=True)
        tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "bilinmeyen hata"
        raise RuntimeError(tail)
    dur = _duration(r.stderr)
    part.replace(out)

    # kapak: ilk saniyeden (çok kısa videolarda ortadan)
    poster = media / "video" / f"{vid}.poster.jpg"
    _run(["-y", "-ss", str(min(1.0, dur / 2) if dur else 0), "-i", str(out), "-frames:v", "1", "-q:v", "2", str(poster)])
    if not poster.exists():
        raise RuntimeError("kapak resmi çıkarılamadı")
    with Image.open(poster) as im:
        im = im.convert("RGB")
        size = im.size
        full = im.copy()
        full.thumbnail((FULL_PX, FULL_PX), Image.LANCZOS)
        full.save(media / "full" / f"{vid}.jpg", "JPEG", quality=82, optimize=True, progressive=True)
        im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        im.save(media / "thumb" / f"{vid}.jpg", "JPEG", quality=78, optimize=True, progressive=True)
    poster.unlink(missing_ok=True)

    # analiz kareleri: videoya eşit aralıklı, 3-12 kare
    fdir = media / "vframes" / vid
    shutil.rmtree(fdir, ignore_errors=True)
    fdir.mkdir(parents=True)
    n = max(3, min(12, round(dur / 4))) if dur else 3
    fps = n / dur if dur else 1
    _run(["-y", "-i", str(out), "-vf", f"fps={fps:.5f},scale=w='if(gte(iw,ih),640,-2)':h='if(gte(iw,ih),-2,640)'",
          "-frames:v", str(n), "-q:v", "4", str(fdir / "%02d.jpg")])
    return {"size": size, "dur": dur}
