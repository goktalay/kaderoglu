"""Google Takeout (Google Fotoğraflar) arşivinden kişi etiketli fotoğrafları aile sitesine aktarır.

Kullanım:
    .venv\\Scripts\\python.exe tools\\import_takeout.py "D:\\Takeout"            (açılmış klasör)
    .venv\\Scripts\\python.exe tools\\import_takeout.py "D:\\indirilenler"        (takeout-*.zip dosyalarının olduğu klasör)
    .venv\\Scripts\\python.exe tools\\import_takeout.py takeout-001.zip takeout-002.zip

Takeout her fotoğrafın yanına bir .json koyar; içindeki "people" alanında Google Fotoğraflar'da
o fotoğrafa etiketlenmiş kişilerin adları yazar. Adlar data/family.json'daki "name" ve "aliases"
alanlarıyla eşleştirilir. Eşleşmeyen adlar sonunda listelenir; bunları family.json'a alias olarak
ekleyip aracı yeniden çalıştırabilirsiniz (daha önce işlenen fotoğraflar yeniden işlenmez).

Fotoğraflar küçültülür (1600 px) ve EXIF/GPS bilgisi silinerek data/media altına yazılır.
Konum (Takeout geoData veya EXIF GPS) sadece data/index.json'da saklanır; harita için site bunu
config.json'daki "map_precision" kadar yuvarlayarak yayımlar.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import unicodedata
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import avatars  # noqa: E402
import videos  # noqa: E402

# Makes the calls do nothing where claude-progress is not installed.
_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in the plugin
try:
    sys.path.insert(0, os.environ["CLAUDE_PROGRESS_DIR"]); from claude_progress import claude_progress
except Exception:
    def claude_progress(*args): pass
finally:
    sys.dont_write_bytecode = _bytecode

try:  # iPhone fotoğrafları için isteğe bağlı
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC = True
except ImportError:
    HEIC = False

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("KADEROGLU_DATA", ROOT / "data"))  # test için ayrı veri klasörü verilebilir
MEDIA = DATA / "media"
FULL_PX, THUMB_PX = 1600, 480
TMP = DATA / "tmp"
EDITED_RE = re.compile(r"-(edited|düzenlendi|bearbeitet|modifié|editado|modificato)(\(\d+\))?$", re.I)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"} | ({".heic", ".heif"} if HEIC else set())
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".3gp", ".mkv", ".mp", ".webm"}
SIDECAR_RE = re.compile(r"(?:\.(s|su|sup|supp|suppl|supple|supplem|suppleme|supplemen|supplement|supplementa"
                        r"|supplemental|supplemental-|supplemental-m|supplemental-me|supplemental-met"
                        r"|supplemental-meta|supplemental-metad|supplemental-metada|supplemental-metadat"
                        r"|supplemental-metadata))?(\(\d+\))?\.json$", re.I)


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name.replace("ı", "i").replace("I", "i"))
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return " ".join(s.split())


# ------------------------------------------------------------------ kaynak okuma
class Source:
    """Açılmış klasör veya zip dosyası; dosyaları klasör bazında listeler."""

    def __init__(self, path: Path):
        self.path = path
        self.zip = zipfile.ZipFile(path) if path.suffix.lower() == ".zip" else None

    def dirs(self):
        groups = defaultdict(list)
        if self.zip:
            for n in self.zip.namelist():
                if not n.endswith("/"):
                    d, _, f = n.rpartition("/")
                    groups[d].append(f)
        else:
            for d, _, files in os.walk(self.path):
                groups[d].extend(files)
        return groups

    def read(self, d, f) -> bytes:
        if self.zip:
            return self.zip.read(f"{d}/{f}" if d else f)
        return (Path(d) / f).read_bytes()

    def extract(self, d, f, dest: Path) -> str:
        """Dosyayı belleğe almadan diske kopyalar (videolar GB'larca olabilir); içerik özetini döndürür."""
        h = hashlib.sha1()
        src = self.zip.open(f"{d}/{f}" if d else f) if self.zip else open(Path(d) / f, "rb")
        with src, open(dest, "wb") as out:
            while chunk := src.read(4 << 20):
                h.update(chunk)
                out.write(chunk)
        return h.hexdigest()[:20]

    def info(self, d, f):
        """(boyut, diskteki konum): kopyaları elemek ve okumayı disk sırasına dizmek için."""
        if self.zip:
            zi = self.zip.getinfo(f"{d}/{f}" if d else f)
            return zi.file_size, zi.header_offset
        return (Path(d) / f).stat().st_size, 0


def find_media(json_name, title, files):
    """Takeout'un kısaltılmış/numaralı dosya adlarına rağmen .json'un fotoğrafını bulur."""
    m = SIDECAR_RE.search(json_name)
    base = json_name[:m.start()] if m else json_name[:-5]
    dup = m.group(2) if m and m.group(2) else ""
    cands = []
    if dup:  # "IMG.jpg(1).json" -> "IMG(1).jpg"
        stem, ext = os.path.splitext(base)
        cands.append(f"{stem}{dup}{ext}")
    cands += [base, title or ""]
    lower = {f.lower(): f for f in files}
    for c in cands:
        if c and c.lower() in lower:
            return lower[c.lower()]
    # Takeout uzun adları ~47 karakterde keser (fotoğrafta uzantı korunur): önek eşleşmesi
    base_l = base.lower()
    for f in files:
        fstem, fext = os.path.splitext(f.lower())
        if fext in IMAGE_EXT | VIDEO_EXT and len(fstem) >= 20 and (base_l.startswith(fstem) or fstem.startswith(base_l)):
            return f
    return None


# ------------------------------------------------------------------ konum
def _valid(lat, lng):
    return lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0, 0)


def geo_from_meta(meta):
    for key in ("geoData", "geoDataExif"):
        g = meta.get(key) or {}
        lat, lng = g.get("latitude"), g.get("longitude")
        if _valid(lat, lng):
            return [round(lat, 6), round(lng, 6)]
    return None


def geo_from_exif(data: bytes):
    """Takeout'ta konum yoksa fotoğrafın kendi EXIF GPS'ine bakar (sadece başlık okunur)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            gps = im.getexif().get_ifd(0x8825)
        if not gps or 2 not in gps or 4 not in gps:
            return None
        to_deg = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
        lat, lng = to_deg(gps[2]), to_deg(gps[4])
        if str(gps.get(1, "N")).upper().startswith("S"):
            lat = -lat
        if str(gps.get(3, "E")).upper().startswith("W"):
            lng = -lng
        return [round(lat, 6), round(lng, 6)] if _valid(lat, lng) else None
    except Exception:
        return None


def exif_taken(data: bytes):
    """Bilgi dosyası olmayan fotoğraflar için EXIF çekim tarihi (yerel saat)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            ex = im.getexif()
            s = ex.get_ifd(0x8769).get(36867) or ex.get(306)
        return int(time.mktime(time.strptime(str(s).strip()[:19], "%Y:%m:%d %H:%M:%S"))) if s else None
    except Exception:
        return None


# ------------------------------------------------------------------ görüntü işleme
def save_jpeg(img, path, quality):
    img.save(path, "JPEG", quality=quality, optimize=True, progressive=True)  # EXIF (ve GPS) yazılmaz


def process(data: bytes, pid: str):
    with Image.open(io.BytesIO(data)) as im:
        im.seek(0)
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
            im = bg
        im = im.convert("RGB")
        w, h = im.size
        full = im.copy()
        full.thumbnail((FULL_PX, FULL_PX), Image.LANCZOS)
        save_jpeg(full, MEDIA / "full" / f"{pid}.jpg", 82)
        thumb = im.copy()
        thumb.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        save_jpeg(thumb, MEDIA / "thumb" / f"{pid}.jpg", 78)
        return full.size


# ------------------------------------------------------------------ dizin
def build_people(index):
    """kişi -> fotoğraflar, en yeniden eskiye (tarihsizler sonda)."""
    people = defaultdict(list)
    for photo_id, ph in index["photos"].items():
        for pid in ph.get("people", []):
            people[pid].append(photo_id)
    for pid in people:
        people[pid].sort(key=lambda i: index["photos"][i].get("taken") or 0, reverse=True)
    index["people"] = dict(people)
    return people


def save_index(index, path):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), "utf-8")
    tmp.replace(path)


# başka araçların (yüz tanıma, çıplaklık, elle ekleme, profil fotoğrafı) yazdığı alanlar
FOREIGN = ("nsfw", "faces", "recognized", "extra")


def merge_save(index, path):
    """Uzun içe aktarma sırasında diğer araçlar da index.json'a yazabilir: onların alanlarını koruyarak kaydet."""
    if path.exists():
        disk = json.loads(path.read_text("utf-8"))
        for pid, ph in disk.get("photos", {}).items():
            mine = index["photos"].get(pid)
            if mine is None:
                index["photos"][pid] = ph
                continue
            for k in FOREIGN:
                if k in ph:
                    mine[k] = ph[k]
            mine["people"] = sorted(set(mine.get("tags", [])) | set(mine.get("extra", [])) | set(mine.get("recognized", [])))
        for k in ("avatars", "avatar_v"):
            if k in disk:
                index[k] = disk[k]
    build_people(index)
    save_index(index, path)


# ------------------------------------------------------------------ ana akış
def collect_sources(args):
    out = []
    for a in args:
        p = Path(a)
        if p.is_dir() and not any(p.glob("**/*.json")) and list(p.glob("*.zip")):
            out += sorted(p.glob("*.zip"))
        elif p.exists():
            out.append(p)
        else:
            sys.exit(f"Bulunamadı: {p}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="Takeout klasörü, zip dosyaları veya zip'lerin olduğu klasör")
    ap.add_argument("--reset", action="store_true", help="Önceki içe aktarmayı silip baştan başla")
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    ap.add_argument("--dry-run", action="store_true", help="Sadece tara: kişi başına sayıları ve eşleşmeyen etiketleri göster")
    ap.add_argument("--video-workers", type=int, default=3, help="Aynı anda dönüştürülen video (ekran kartı)")
    ap.add_argument("--only", choices=["photo", "video"], help="Sadece fotoğrafları veya sadece videoları işle")
    args = ap.parse_args()

    for sub in ("full", "thumb", "avatar"):
        (MEDIA / sub).mkdir(parents=True, exist_ok=True)
    family = json.loads((DATA / "family.json").read_text("utf-8"))
    idx_path = DATA / "index.json"
    index = {"photos": {}, "people": {}, "avatars": {}}
    if idx_path.exists() and not args.reset:
        index = json.loads(idx_path.read_text("utf-8"))
        index.setdefault("avatars", {})

    # ad -> kişi kimliği (aynı ad iki kişide varsa belirsiz sayılır)
    name_map = defaultdict(set)
    for pid, p in family["people"].items():
        # "match_name": false -> görünen ad eşleştirmede kullanılmaz, sadece aliases (aynı adlı iki kişi için)
        names = ([p["name"]] if p.get("match_name", True) else []) + p.get("aliases", [])
        for n in names:
            name_map[norm(n)].add(pid)
    ambiguous = {n for n, ids in name_map.items() if len(ids) > 1}
    # "not_before": "YYYY-AA-GG" -> bu tarihten önce çekilmiş fotoğraflardaki etiketi yok say
    # (ör. çocuk doğmadan önceki fotoğraflarda yüz tanımanın yanlış eşleştirmesi)
    not_before = {pid: int(time.mktime(time.strptime(p["not_before"], "%Y-%m-%d")))
                  for pid, p in family["people"].items() if p.get("not_before")}
    # "not_after": "YYYY-AA-GG" -> bu tarihten sonra çekilmiş fotoğraflardaki etiketi yok say (ör. vefat)
    not_after = {pid: int(time.mktime(time.strptime(p["not_after"], "%Y-%m-%d"))) + 86399
                 for pid, p in family["people"].items() if p.get("not_after")}

    unmatched, ambiguous_hits = Counter(), Counter()
    stats = Counter()
    jobs = []  # (source, dir, media_file, json_meta, person_ids)

    # Büyük arşivlerde Takeout bir fotoğrafın .json'unu ve kendisini farklı zip'lere koyabilir:
    # tüm kaynakları klasör yolu üzerinden tek bir dizinde birleştir.
    claude_progress("-n", "takeout", "Arşivlerin içeriği listeleniyor")
    dirfiles = defaultdict(dict)  # klasör -> {dosya adı: kaynak}
    for src_path in collect_sources(args.paths):
        print(f"Listeleniyor: {src_path}", flush=True)
        src = Source(src_path)
        for d, files in src.dirs().items():
            for f in files:
                dirfiles[d].setdefault(f, src)

    json_refs = []  # (kaynak, diskteki konum, klasör, json adı)
    for d, files in dirfiles.items():
        for f, src in files.items():
            if f.lower().endswith(".json"):
                json_refs.append((src, src.info(d, f)[1], d, f))
    # disk sırasıyla oku: büyük zip'lerde rastgele okumaya göre çok daha hızlı
    json_refs.sort(key=lambda r: (str(r[0].path), r[1]))
    print(f"{len(json_refs)} bilgi dosyası taranıyor…", flush=True)

    meta_of = {}  # (klasör, medya dosyası) -> (bilgi, kişiler)
    last_report = 0.0
    for n_json, (src, _, d, jf) in enumerate(json_refs, 1):
        now = time.monotonic()
        if now - last_report > 1:
            last_report = now
            claude_progress("-n", "takeout", f"{n_json}/{len(json_refs)}", "bilgi dosyaları taranıyor")
            print(f"\r  {n_json}/{len(json_refs)} bilgi dosyası", end="", flush=True)
        try:
            meta = json.loads(src.read(d, jf))
        except (ValueError, KeyError, OSError, zipfile.BadZipFile):
            stats["okunamayan json"] += 1
            continue
        if not isinstance(meta, dict) or not isinstance(meta.get("title"), str):
            continue  # albüm bilgisi vb. (başlığı olmayan ya da metin olmayan bilgi dosyaları)
        pids = set()
        for person in meta.get("people") or []:
            n = norm(person.get("name", ""))
            if not n:
                continue
            if n in ambiguous:
                ambiguous_hits[person["name"]] += 1
            elif n in name_map:
                pids |= name_map[n]
            else:
                unmatched[person["name"]] += 1
        taken_ts = int((meta.get("photoTakenTime") or {}).get("timestamp") or 0)
        for pid in [p for p in pids if p in not_before]:
            if taken_ts and taken_ts < not_before[pid]:
                pids.discard(pid)
                stats[f"{family['people'][pid]['name']} doğmadan önceki etiket (atlandı)"] += 1
        for pid in [p for p in pids if p in not_after]:
            if taken_ts and taken_ts > not_after[pid]:
                pids.discard(pid)
                stats[f"{family['people'][pid]['name']} için tarih sınırından sonraki etiket (atlandı)"] += 1
        media = find_media(jf, meta.get("title"), dirfiles[d])
        if media:
            meta_of[(d, media)] = (meta, sorted(pids))
        else:
            stats["fotoğrafı bulunamayan json"] += 1
    print()

    # arşivdeki TÜM fotoğraf ve videolar (etiketsizler dahil)
    seen = set()
    for d, files in dirfiles.items():
        lower = {f.lower() for f in files}
        for media, msrc in files.items():
            stem, ext = os.path.splitext(media)
            ext = ext.lower()
            if ext not in IMAGE_EXT and ext not in VIDEO_EXT:
                if not media.lower().endswith(".json"):
                    stats[f"desteklenmeyen biçim {ext or '(uzantısız)'}"] += 1
                continue
            # Google'ın düzenlenmiş kopyaları: aslı aynı klasördeyse atla
            m = EDITED_RE.search(stem)
            if m and (stem[:m.start()] + ext).lower() in lower:
                stats["düzenlenmiş kopya (atlandı)"] += 1
                continue
            meta, pids = meta_of.get((d, media), ({}, []))
            size, offset = msrc.info(d, media)
            # aynı dosya hem "Photos from 2019" hem albüm klasöründe olabilir: okumadan ele
            taken = (meta.get("photoTakenTime") or {}).get("timestamp")
            key = (meta.get("title") or media, taken, size)
            if key in seen:
                stats["kopya (atlandı)"] += 1
                continue
            seen.add(key)
            kind = "video" if ext in VIDEO_EXT else "photo"
            jobs.append((msrc, d, media, meta, pids, offset, kind, size))

    photos_n = sum(1 for j in jobs if j[6] == "photo")
    videos_n = len(jobs) - photos_n
    tagged_n = sum(1 for j in jobs if j[4])
    video_gb = sum(j[7] for j in jobs if j[6] == "video") / 1e9
    print(f"Arşivde {photos_n} fotoğraf ve {videos_n} video ({video_gb:.0f} GB) var; {tagged_n} tanesi etiketli.")

    if args.dry_run:
        claude_progress("-n", "takeout", "done", f"{len(jobs)} öğe bulundu")
        per = Counter(p for j in jobs for p in j[4])
        print(f"\nÖN KONTROL (henüz işlenmedi).\nKişi başına etiketli öğe:")
        for pid, p in family["people"].items():
            print(f"  {p['name']:<22} {per.get(pid, 0):>7}")
        print("\nÖzet:", ", ".join(f"{k}: {v}" for k, v in stats.items()) or "-")
        if ambiguous_hits:
            print("\nBirden fazla kişiye uyan adlar:")
            for n, c in ambiguous_hits.most_common():
                print(f"  {n!r}: {c} fotoğraf")
        if unmatched:
            print("\nAğaçta karşılığı olmayan etiketler:")
            for n, c in unmatched.most_common(60):
                print(f"  {n!r}: {c} fotoğraf")
        return

    jobs.sort(key=lambda j: (str(j[0].path), j[5]))
    for sub in ("video", "vframes"):
        (MEDIA / sub).mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)

    def taken_of(meta, data_or_path=None):
        t = int((meta.get("photoTakenTime") or {}).get("timestamp") or 0)
        return t or None

    # başka bir hesabın arşivindeki aynı fotoğraf/video farklı baytlarla gelebilir (Google'ın sıkıştırılmış
    # kopyası): aynı saniyede çekilmiş ve aynı en-boy oranındaki (videoda aynı süredeki) öğeyi kopya say
    same_time = defaultdict(list)
    for pid_, ph_ in index["photos"].items():
        if ph_.get("taken"):
            same_time[ph_["taken"]].append((pid_, ph_))

    def near_duplicate(taken, kind, aspect=None, dur=None):
        for pid_, ph_ in same_time.get(taken, []):
            if ph_.get("type", "photo") != kind:
                continue
            if kind == "photo" and aspect and ph_.get("h") and abs(ph_["w"] / ph_["h"] - aspect) < 0.02:
                return pid_
            if kind == "video" and dur is not None and abs((ph_.get("dur") or 0) - dur) < 1.5:
                return pid_
        return None

    def work_photo(job):
        src, d, media, meta, pids = job[:5]
        data = src.read(d, media)
        photo_id = hashlib.sha1(data).hexdigest()[:20]
        taken = taken_of(meta) or exif_taken(data)
        geo = geo_from_meta(meta) or geo_from_exif(data)
        if photo_id in index["photos"] and (MEDIA / "full" / f"{photo_id}.jpg").exists():
            return photo_id, None, taken, geo, pids, "zaten var", {}
        if taken and same_time.get(taken):
            try:
                with Image.open(io.BytesIO(data)) as im:
                    w, h = ImageOps.exif_transpose(im).size
                dup = near_duplicate(taken, "photo", aspect=w / h)
                if dup:
                    return dup, None, taken, geo, pids, "zaten var", {}
            except Exception:
                pass
        try:
            size = process(data, photo_id)
        except Exception as e:  # bozuk veya okunamayan görüntü
            return photo_id, None, taken, geo, pids, f"hata: {type(e).__name__}", {}
        return photo_id, size, taken, geo, pids, "yeni", {"type": "photo"}

    def work_video(job):
        src, d, media, meta, pids = job[:5]
        tmp = TMP / f"{threading.get_ident()}{os.path.splitext(media)[1].lower()}"
        # yarıda kalan bir içe aktarmayı sürdürürken: aynı saniyede çekilmiş video zaten varsa
        # dosyayı zip'ten okumadan atla (yüzlerce GB'yi yeniden okumamak için)
        t0 = taken_of(meta)
        if t0:
            for pid_, ph_ in same_time.get(t0, []):
                if ph_.get("type") == "video" and (MEDIA / "video" / f"{pid_}.mp4").exists():
                    return pid_, None, t0, geo_from_meta(meta), pids, "zaten var", {}
        try:
            photo_id = src.extract(d, media, tmp)  # akış halinde kopyalar ve özetini döndürür
            taken = taken_of(meta)
            geo = geo_from_meta(meta)
            if photo_id in index["photos"] and (MEDIA / "video" / f"{photo_id}.mp4").exists():
                return photo_id, None, taken, geo, pids, "zaten var", {}
            if taken and same_time.get(taken):
                dup = near_duplicate(taken, "video", dur=videos.probe_duration(tmp))
                if dup:
                    return dup, None, taken, geo, pids, "zaten var", {}
            info = videos.transcode(tmp, photo_id, MEDIA)
            return photo_id, info["size"], taken, geo, pids, "yeni", {"type": "video", "dur": info["dur"]}
        except Exception as e:
            return None, None, None, None, pids, f"hata: {type(e).__name__}: {str(e)[:120]}", {}
        finally:
            tmp.unlink(missing_ok=True)

    def run(kind, jobs_k, worker, workers):
        total = len(jobs_k)
        if not total:
            return
        label = "fotoğraf" if kind == "photo" else "video"
        print(f"{total} {label} işleniyor ({workers} iş parçacığı)…", flush=True)
        done, last, new_since_save = 0, 0.0, 0
        with ThreadPoolExecutor(workers) as pool:
            for photo_id, size, taken, geo, pids, status, extra in pool.map(worker, jobs_k):
                done += 1
                if status.startswith("hata"):
                    stats[f"okunamayan {label}"] += 1
                    if kind == "video":
                        errors.append(status)
                    continue
                stats[f"{label}: {status}"] += 1
                if geo:
                    stats["konumlu"] += 1
                if status == "yeni":
                    ph = {"w": size[0], "h": size[1], "taken": taken, "tags": pids, "people": list(pids), **extra}
                    if geo:
                        ph["geo"] = geo
                    index["photos"][photo_id] = ph
                    new_since_save += 1
                else:
                    ph = index["photos"][photo_id]
                    ph["tags"] = sorted(set(ph.get("tags", [])) | set(pids))
                    ph["people"] = sorted(set(ph.get("people", [])) | set(pids))
                    if geo and not ph.get("geo"):
                        ph["geo"] = geo
                    if taken and not ph.get("taken"):
                        ph["taken"] = taken
                now = time.monotonic()
                if now - last > 1 or done == total:
                    last = now
                    claude_progress("-n", f"takeout-{kind}", f"{done}/{total}", f"{label} işleniyor")
                    print(f"\r  {done}/{total}", end="", flush=True)
                # uzun içe aktarmalarda ara kayıt: yarıda kesilirse işlenenler kaybolmaz
                if new_since_save >= (1000 if kind == "photo" else 100):
                    new_since_save = 0
                    merge_save(index, idx_path)
        print()
        claude_progress("-n", f"takeout-{kind}", "done", f"{total} {label}")
        merge_save(index, idx_path)

    errors = []
    kinds = {"photo", "video"} if not args.only else {args.only}
    if "photo" in kinds:
        run("photo", [j for j in jobs if j[6] == "photo"], work_photo, args.workers)
    if "video" in kinds:
        run("video", [j for j in jobs if j[6] == "video"], work_video, args.video_workers)
    if errors:
        (DATA.parent / "logs").mkdir(exist_ok=True)
        (DATA.parent / "logs" / "video-errors.log").write_text("\n".join(errors), "utf-8")
        print(f"{len(errors)} video işlenemedi (logs\\video-errors.log)")

    people = build_people(index)
    # profil fotoğrafı henüz olmayanlar için yüz algılamayla seç (yeniden seçmek için tools\avatars.py)
    avatars.pick_and_save(index, family, only_missing=True, log=lambda *_: None)

    save_index(index, idx_path)
    claude_progress("-n", "takeout", "done", f"{len(index['photos'])} fotoğraf")

    print("\nKişi başına fotoğraf:")
    for pid, p in family["people"].items():
        print(f"  {p['name']:<22} {len(people.get(pid, [])):>6}")
    print("\nÖzet:", ", ".join(f"{k}: {v}" for k, v in stats.items()) or "-")
    if ambiguous_hits:
        print("\nBirden fazla kişiye uyan adlar (family.json'da farklı bir alias verin):")
        for n, c in ambiguous_hits.most_common():
            print(f"  {n!r}: {c} fotoğraf")
    if unmatched:
        print("\nAğaçta karşılığı olmayan etiketler (gerekiyorsa family.json'da aliases'a ekleyin):")
        for n, c in unmatched.most_common(40):
            print(f"  {n!r}: {c} fotoğraf")
    if not HEIC:
        print("\nNot: HEIC (iPhone) fotoğrafları için: .venv\\Scripts\\python.exe -m pip install pillow-heif")


if __name__ == "__main__":
    main()
