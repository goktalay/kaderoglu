"""Epic Games Launcher / Fab indirmelerini Launcher'ın günlüğünden izler.

Launcher indirme ilerlemesini bir API ile vermiyor, ama günlüğüne (EpicGamesLauncher.log) yazıyor:
  LogDownloadManager: Queued install for the <AppId>, Queue length is N        -> sıraya girdi
  LogDownloadManager: HandleTaskComplete: AppId [<AppId>] AlertCode=[ok]       -> bitti
  LogBuildPatchServices: Starting Installation                                -> sıradaki başladı
  LogBuildPatchServices: Display: Requiring <N> files                         -> toplam dosya
  LogBuildPatchServices: Display: Initial Disk Sizes: Required: <bayt>        -> toplam boyut
  LogBuildPatchServices: Display: Starting File: <yol> [<bayt> bytes, ...]    -> dosya başladı
  LogBuildPatchServices: Display: Completing: <yol>                           -> dosya bitti
Yüzde = biten dosyaların baytı / toplam. Adlar ve kapaklar Fab kütüphane önbelleğinden
(VaultCache/FabLibrary/listings_v1.db, "catalog" tablosu) okunur. Dosya sadece okunur, hiçbir şey değiştirilmez.
"""
import calendar
import os
import re
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "EpicGamesLauncher" / "Saved" / "Logs" / "EpicGamesLauncher.log"
FAB_DB = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\VaultCache\FabLibrary\listings_v1.db")

TS = re.compile(r"^\[(\d{4})\.(\d\d)\.(\d\d)-(\d\d)\.(\d\d)\.(\d\d):(\d{3})\]")
QUEUED = re.compile(r"Queued install for the (\S+?), Queue length")
DONE = re.compile(r"HandleTaskComplete: AppId \[(\S+?)\] AlertCode=\[(\w*)\]")
REQ_FILES = re.compile(r"Requiring (\d+) files")
REQ_BYTES = re.compile(r"Initial Disk Sizes: Required: ([\d.,]+)")
START_FILE = re.compile(r"Starting File: (.+?) \[([\d.,]+) bytes")
COMPLETE = re.compile(r"Completing: (.+)$")
STALL_SECS = 90  # bu kadar süre yeni satır yoksa "bekliyor/duraklatıldı"


def _num(s):
    return int(re.sub(r"\D", "", s) or 0)


def _ts(line):
    m = TS.match(line)
    if not m:
        return None
    y, mo, d, h, mi, s, ms = map(int, m.groups())
    return calendar.timegm((y, mo, d, h, mi, s)) + ms / 1000  # günlük UTC yazar


def _short_name(app_id):
    """'ns:catalog:Realistic257a7c1e145V1' -> 'Realistic' (katalogda ad yoksa)."""
    tail = app_id.split(":")[-1].split("/")[-1]
    tail = re.sub(r"[0-9a-f]{10,}V\d+$", "", tail) or tail
    return tail.replace("_", " ")


class EpicWatcher:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = OrderedDict()  # app_id -> öğe
        self.active = None          # app_id
        self.cur = None             # etkin kurulumun sayaçları
        self.last_line_ts = 0
        self._pos = 0
        self._size = 0
        self._titles = {}
        self._titles_at = 0
        self.available = LOG.exists()

    # ---------------------------------------------------------------- günlük okuma
    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                self._read_new()
            except Exception:
                pass
            time.sleep(2)

    def _read_new(self):
        if not LOG.exists():
            self.available = False
            return
        self.available = True
        size = LOG.stat().st_size
        if size < self._size:  # Launcher yeniden başladı: yeni günlük
            with self.lock:
                self.items.clear()
                self.active, self.cur = None, None
            self._pos = 0
        self._size = size
        if size == self._pos:
            return
        with open(LOG, "rb") as f:  # ikili: Windows satır sonlarında konum kaymasın
            f.seek(self._pos)
            chunk = f.read()
        # yarım kalan son satırı bir sonraki tura bırak
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return
        self._pos += cut + 1
        lines = chunk[:cut].decode("utf-8", errors="replace").splitlines()
        with self.lock:
            for line in lines:
                self._handle(line)

    def _handle(self, line):
        t = _ts(line)
        if t:
            self.last_line_ts = t
        if "LogDownloadManager" in line:
            m = QUEUED.search(line)
            if m:
                aid = m.group(1)
                it = self.items.pop(aid, None) or {"id": aid}
                it.update(status="queued", queued_at=t, done_at=None)
                self.items[aid] = it
                return
            m = DONE.search(line)
            if m:
                aid, code = m.groups()
                it = self.items.setdefault(aid, {"id": aid})
                it.update(status="done" if code.lower() == "ok" else "failed", done_at=t, alert=code)
                if self.active == aid:
                    if self.cur and it["status"] == "done":
                        self.cur["done_bytes"] = self.cur.get("total_bytes") or self.cur["done_bytes"]
                    it["total_bytes"] = (self.cur or {}).get("total_bytes")
                    self.active, self.cur = None, None
                return
        if "LogBuildPatch" not in line:
            return
        if "Starting Installation" in line:
            nxt = next((a for a, it in self.items.items() if it["status"] in ("queued", "active")), None)
            if nxt:
                self.active = nxt
                self.items[nxt]["status"] = "active"
                self.items[nxt]["started_at"] = t
                self.cur = {"files": 0, "done_files": 0, "total_bytes": 0, "done_bytes": 0,
                            "sizes": {}, "samples": deque(maxlen=120)}
            return
        if not self.cur:
            return
        m = START_FILE.search(line)
        if m:
            self.cur["sizes"][m.group(1)] = _num(m.group(2))
            return
        m = COMPLETE.search(line)
        if m:
            self.cur["done_files"] += 1
            self.cur["done_bytes"] += self.cur["sizes"].pop(m.group(1).strip(), 0)
            if t:
                self.cur["samples"].append((t, self.cur["done_bytes"]))
            return
        m = REQ_FILES.search(line)
        if m:
            self.cur["files"] = int(m.group(1))
            return
        m = REQ_BYTES.search(line)
        if m:
            self.cur["total_bytes"] = _num(m.group(1))

    # ---------------------------------------------------------------- adlar
    def _catalog(self):
        if time.time() - self._titles_at < 300 or not FAB_DB.exists():
            return self._titles
        try:
            con = sqlite3.connect(f"file:{FAB_DB.as_posix()}?mode=ro", uri=True, timeout=2)
            rows = con.execute("select listing_uid, title, thumbnail from catalog").fetchall()
            con.close()
            self._titles = {uid: (title, thumb) for uid, title, thumb in rows if title}
        except sqlite3.Error:
            pass
        self._titles_at = time.time()
        return self._titles

    def _describe(self, it):
        parts = it["id"].split(":")
        title, thumb = self._catalog().get(parts[1] if len(parts) > 1 else "", (None, None))
        return {"id": it["id"], "title": title or _short_name(it["id"]), "thumb": thumb,
                "status": it["status"], "queued_at": it.get("queued_at"), "done_at": it.get("done_at")}

    # ---------------------------------------------------------------- durum
    def snapshot(self):
        with self.lock:
            now = time.time()
            stalled = bool(self.active) and now - self.last_line_ts > STALL_SECS
            active = None
            if self.active and self.cur:
                c = self.cur
                total = c["total_bytes"] or 0
                pct = (c["done_bytes"] / total * 100) if total else \
                      (c["done_files"] / c["files"] * 100 if c["files"] else 0)
                speed = 0.0
                s = c["samples"]
                if len(s) >= 2 and not stalled:
                    t_end, b_end = s[-1]
                    # son ~30 saniyelik pencere
                    t0, b0 = next(((ts, b) for ts, b in s if t_end - ts <= 30), s[0])
                    if t_end > t0:
                        speed = (b_end - b0) / (t_end - t0)
                eta = (total - c["done_bytes"]) / speed if speed > 0 and total else None
                active = {**self._describe(self.items[self.active]), "percent": round(min(pct, 100), 1),
                          "done_bytes": c["done_bytes"], "total_bytes": total,
                          "done_files": c["done_files"], "files": c["files"],
                          "speed": speed, "eta": eta, "stalled": stalled}
            queued = [self._describe(it) for it in self.items.values() if it["status"] == "queued"]
            done = [self._describe(it) for it in reversed(self.items.values()) if it["status"] in ("done", "failed")][:8]
            return {"available": self.available, "active": active, "queued": queued, "done": done,
                    "last_activity": self.last_line_ts or None}
