"""Windows'ta tüm süreçleri tek bir NtQuerySystemInformation çağrısıyla okur.

psutil, Windows'ta memory_info/num_threads/status gibi alanlar için her süreçte
sistemin tüm süreç listesini yeniden çeker; 350+ süreçte bu, tur başına saniyeler
süren CPU yükü ve sunucuda gecikme demek. Burada liste bir kez alınıp ayrıştırılır.
"""
import ctypes
import struct

_ntdll = ctypes.WinDLL("ntdll")
_ntdll.NtQuerySystemInformation.restype = ctypes.c_long
_ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]

SystemProcessInformation = 5
STATUS_INFO_LENGTH_MISMATCH = -1073741820  # 0xC0000004
THREAD_SIZE = 80  # SYSTEM_THREAD_INFORMATION, x64
_buf_size = 1 << 20


def snapshot():
    """[(pid, name, threads, private_ws, working_set, cpu_100ns, create_100ns, read_bytes, write_bytes, suspended)]"""
    global _buf_size
    while True:
        buf = ctypes.create_string_buffer(_buf_size)
        needed = ctypes.c_ulong(0)
        st = _ntdll.NtQuerySystemInformation(SystemProcessInformation, buf, _buf_size, ctypes.byref(needed))
        if st == STATUS_INFO_LENGTH_MISMATCH:
            _buf_size = max(needed.value + 65536, _buf_size * 2)
            continue
        if st != 0:
            raise OSError(f"NtQuerySystemInformation hata kodu {st & 0xFFFFFFFF:#x}")
        break

    raw = buf.raw
    base = ctypes.addressof(buf)
    out = []
    off = 0
    while True:
        nxt, nthreads = struct.unpack_from("<II", raw, off)
        ws_private, = struct.unpack_from("<q", raw, off + 8)
        create, user, kernel = struct.unpack_from("<qqq", raw, off + 32)
        name_len, = struct.unpack_from("<H", raw, off + 56)
        name_ptr, = struct.unpack_from("<Q", raw, off + 64)
        pid, = struct.unpack_from("<Q", raw, off + 80)
        working_set, = struct.unpack_from("<Q", raw, off + 144)
        read_b, write_b = struct.unpack_from("<qq", raw, off + 232)
        if name_ptr and name_len:
            name = raw[name_ptr - base:name_ptr - base + name_len].decode("utf-16-le", "replace")
        else:
            name = "System Idle Process" if pid == 0 else ""

        # tüm iş parçacıkları "askıda" bekliyorsa süreç durdurulmuştur
        suspended = nthreads > 0
        toff = off + 256
        for i in range(nthreads):
            state, reason = struct.unpack_from("<II", raw, toff + i * THREAD_SIZE + 68)
            if not (state == 5 and reason == 5):
                suspended = False
                break

        out.append((pid, name, nthreads, ws_private, working_set, user + kernel, create, read_b, write_b, suspended))
        if not nxt:
            break
        off += nxt
    return out
