"""
CS2 memory reader.

Attaches to 'cs2.exe' and exposes typed read/write helpers. Full-access
pymem attach is tried first, then a limited OpenProcess path for no-admin
sessions launched by the same Windows user.
"""

import ctypes
import ctypes.wintypes as wt
from struct import pack, unpack

import pymem
import pymem.process


_LIMITED_RIGHTS = 0x0010 | 0x0020 | 0x0008 | 0x0400
_TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


class MemoryReader:
    """External memory reader for CS2."""

    def __init__(self) -> None:
        self.pm: pymem.Pymem | None = None
        self.client_base: int = 0

    def attach(self, *, quiet: bool = False) -> bool:
        """Attach to cs2.exe with the full-access path first, then the no-admin fallback."""
        if self.pm is not None:
            self.detach()
        try:
            self.pm = pymem.Pymem("cs2.exe")
        except pymem.exception.ProcessNotFound:
            if not quiet:
                print("[memory] cs2.exe is not running — please launch CS2 first.")
            return False
        except Exception as e:
            if not quiet:
                print(f"[memory] Full-access attach failed ({e.__class__.__name__}); "
                      "retrying with limited rights (no-admin path)\u2026")
            self.pm = None

        if self.pm is None:
            pid = 0
            kernel32 = ctypes.windll.kernel32
            create_snapshot = kernel32.CreateToolhelp32Snapshot
            create_snapshot.argtypes = [wt.DWORD, wt.DWORD]
            create_snapshot.restype = wt.HANDLE
            process_first = kernel32.Process32FirstW
            process_first.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
            process_first.restype = wt.BOOL
            process_next = kernel32.Process32NextW
            process_next.argtypes = [wt.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
            process_next.restype = wt.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = [wt.HANDLE]
            close_handle.restype = wt.BOOL

            snapshot = create_snapshot(_TH32CS_SNAPPROCESS, 0)
            if snapshot not in (None, ctypes.c_void_p(-1).value):
                try:
                    entry = _PROCESSENTRY32W()
                    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
                    if process_first(snapshot, ctypes.byref(entry)):
                        while True:
                            if entry.szExeFile.lower() == "cs2.exe":
                                pid = int(entry.th32ProcessID)
                                break
                            if not process_next(snapshot, ctypes.byref(entry)):
                                break
                finally:
                    close_handle(snapshot)
            if not pid:
                if not quiet:
                    print("[memory] cs2.exe is not running — please launch CS2 first.")
                return False
            open_process = kernel32.OpenProcess
            open_process.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
            open_process.restype = wt.HANDLE
            process_handle = open_process(_LIMITED_RIGHTS, False, pid)
            handle = int(process_handle) if process_handle else 0
            if not handle:
                error_code = kernel32.GetLastError()
                if not quiet:
                    print(f"[memory] OpenProcess(limited) failed (LastError={error_code}). "
                          "If your CS2 is launched by another user / at higher "
                          "integrity, run this script as Administrator.")
                return False
            self.pm = pymem.Pymem()
            self.pm.process_id = pid
            self.pm.process_handle = handle

        try:
            self.client_base = pymem.process.module_from_name(
                self.pm.process_handle, "client.dll"
            ).lpBaseOfDll
        except Exception as e:
            print(f"[memory] Failed to enumerate CS2 modules: {e}. "
                  "If CS2 just started, give it ~10 seconds and try again.")
            self.detach()
            return False

        print(f"[memory] Attached to cs2.exe (PID {self.pm.process_id})")
        print(f"[memory] client.dll @ 0x{self.client_base:X}")
        return True

    def detach(self) -> None:
        """Close handle."""
        if self.pm:
            try:
                self.pm.close_process()
            except Exception:
                pass
            self.pm = None
            self.client_base = 0

    def is_process_alive(self) -> bool:
        """True iff the attached CS2 process is still running."""
        if self.pm is None or not getattr(self.pm, "process_handle", None):
            return False
        try:
            handle = int(self.pm.process_handle)
            if handle <= 0:
                return False
            WAIT_TIMEOUT = 0x102
            wait_result = ctypes.windll.kernel32.WaitForSingleObject(
                ctypes.c_void_p(handle), 0
            )
            return wait_result == WAIT_TIMEOUT
        except Exception:
            return False

    def read_int(self, address: int) -> int:
        try:
            return self.pm.read_int(address)
        except Exception:
            return 0

    def read_uint(self, address: int) -> int:
        try:
            return self.pm.read_uint(address)
        except Exception:
            return 0

    def read_long(self, address: int) -> int:
        """Read an 8-byte integer (pointer on x64)."""
        try:
            return self.pm.read_longlong(address)
        except Exception:
            return 0

    def read_float(self, address: int) -> float:
        try:
            return self.pm.read_float(address)
        except Exception:
            return 0.0

    def read_byte(self, address: int) -> int:
        try:
            return self.pm.read_uchar(address)
        except Exception:
            return 0

    def read_vec3(self, address: int) -> tuple[float, float, float]:
        try:
            raw = self.pm.read_bytes(address, 12)
            return unpack("<3f", raw)
        except Exception:
            return (0.0, 0.0, 0.0)

    def read_string(self, address: int, max_len: int = 128) -> str:
        try:
            raw = self.pm.read_bytes(address, max_len)
            null_byte_index = raw.find(b"\x00")
            if null_byte_index != -1:
                raw = raw[:null_byte_index]
            return raw.decode("utf-8", errors="replace")
        except Exception:
            return ""

    def read_matrix(self, address: int) -> list[list[float]]:
        """Read a 4x4 float matrix (64 bytes) as row-major list of lists."""
        try:
            raw = self.pm.read_bytes(address, 64)
            floats = unpack("<16f", raw)
            return [
                list(floats[0:4]),
                list(floats[4:8]),
                list(floats[8:12]),
                list(floats[12:16]),
            ]
        except Exception:
            return [[0.0] * 4 for _ in range(4)]

    def client(self, offset: int) -> int:
        return self.client_base + offset

    def write_int(self, address: int, value: int) -> bool:
        try:
            self.pm.write_int(address, value)
            return True
        except Exception:
            return False

    def write_uint(self, address: int, value: int) -> bool:
        try:
            self.pm.write_bytes(address, pack("<I", value & 0xFFFFFFFF), 4)
            return True
        except Exception:
            return False

    def write_byte(self, address: int, value: int) -> bool:
        try:
            self.pm.write_bytes(address, pack("B", value & 0xFF), 1)
            return True
        except Exception:
            return False
