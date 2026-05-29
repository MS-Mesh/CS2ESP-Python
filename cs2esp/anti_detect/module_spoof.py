"""Masquerade the interpreter DLL name in the Windows PEB module list."""

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from shutil import copy2
from tempfile import gettempdir

# Cache the spoof handle so repeat call sites get the same answer instead of duplicating the work.
_SPOOFED_NAME: str | None = None


def is_supported() -> bool:
    """Return whether the Windows-only implementation is available."""
    return sys.platform.startswith("win")


def spoof_python_module_name(
    *,
    masquerade_name: str | None = None,
    unlink_original: bool = True,
) -> str | None:
    """Rename the interpreter's PEB entry, returning the fake name on success."""
    global _SPOOFED_NAME

    if not is_supported():
        return None
    if _SPOOFED_NAME is not None:
        # Idempotent: a second call returns the cached result.
        return _SPOOFED_NAME

    masquerade_name = masquerade_name or "Microsoft.UI.Xaml.dll"

    # Find the interpreter DLL in the two locations used by standard installs.
    base_prefix = Path(sys.base_prefix)
    candidates = list(base_prefix.glob("python3*.dll"))
    candidates.extend((base_prefix / "DLLs").glob("python3*.dll"))
    python_dll = next((path for path in candidates if path.is_file()), None)
    if python_dll is None:
        return None

    # Copy the interpreter DLL to %TEMP% under the masquerade name.
    fake_path = Path(gettempdir()) / masquerade_name
    try:
        copy2(python_dll, fake_path)
    except OSError as e:
        # Can't read the original (sandbox) or write to %TEMP% (locked-down account); bail.
        print(f"[anti_detect.module_spoof] copy failed: {e!r}")
        return None

    # LoadLibrary registers the copy and keeps it mapped until process exit.
    LoadLibraryW = ctypes.windll.kernel32.LoadLibraryW
    LoadLibraryW.argtypes = [wintypes.LPCWSTR]
    LoadLibraryW.restype = wintypes.HMODULE
    handle = LoadLibraryW(str(fake_path))
    if not handle:
        err = ctypes.get_last_error()
        print(f"[anti_detect.module_spoof] LoadLibraryW failed: {err}")
        return None

    # Walk the PEB load-order list and rewrite the matching interpreter entry.
    rewrite_succeeded = False
    try:
        peb_ldr_offset = 0x18
        load_order_offset = 0x10
        full_name_offset = 0x48
        base_name_offset = 0x58

        current_teb = ctypes.windll.ntdll.NtCurrentTeb
        current_teb.restype = ctypes.c_void_p
        teb = current_teb()
        peb_address = ctypes.c_void_p.from_address(teb + 0x60).value
        loader_address = (
            ctypes.c_void_p.from_address(
                peb_address + peb_ldr_offset
            ).value
            if peb_address
            else None
        )
        if loader_address:
            head = loader_address + load_order_offset
            entry = ctypes.c_void_p.from_address(head).value
            for _ in range(1024):
                if not entry or entry == head:
                    break
                unicode_address = entry + full_name_offset
                length = ctypes.c_ushort.from_address(unicode_address).value
                buffer_pointer = ctypes.c_void_p.from_address(
                    unicode_address + 0x08
                ).value
                full_name = None
                if length and buffer_pointer:
                    raw_name = (
                        ctypes.c_char * length
                    ).from_address(buffer_pointer).raw
                    try:
                        full_name = raw_name.decode("utf-16-le")
                    except UnicodeDecodeError:
                        pass
                if (full_name
                        and full_name.lower().endswith(
                            python_dll.name.lower()
                        )):
                    _overwrite_unicode_string(
                        entry + full_name_offset,
                        str(fake_path),
                    )
                    _overwrite_unicode_string(
                        entry + base_name_offset,
                        masquerade_name,
                    )
                    if unlink_original:
                        forward = ctypes.c_void_p.from_address(entry).value
                        backward = ctypes.c_void_p.from_address(
                            entry + 0x08
                        ).value
                        if forward:
                            ctypes.c_void_p.from_address(
                                forward + 0x08
                            ).value = backward
                        if backward:
                            ctypes.c_void_p.from_address(backward).value = forward
                    rewrite_succeeded = True
                    break
                entry = ctypes.c_void_p.from_address(entry).value
    except Exception as error:
        print(f"[anti_detect.module_spoof] PEB walk failed: {error!r}")

    if not rewrite_succeeded:
        print(
            "[anti_detect.module_spoof] PEB rewrite failed - file copy "
            "still happened so the masquerade DLL is on disk but the "
            "running process still reports the real python name."
        )
        return None

    _SPOOFED_NAME = masquerade_name
    return masquerade_name


def _overwrite_unicode_string(addr: int, new_value: str) -> None:
    """Overwrite a UNICODE_STRING within its existing buffer capacity."""
    length_addr = addr
    max_len_addr = addr + 0x02
    buf_ptr = ctypes.c_void_p.from_address(addr + 0x08).value
    if not buf_ptr:
        return
    max_len = ctypes.c_ushort.from_address(max_len_addr).value or 0
    encoded = new_value.encode("utf-16-le")
    write_len = min(len(encoded), max_len)
    ctypes.memmove(buf_ptr, encoded[:write_len], write_len)
    ctypes.c_ushort.from_address(length_addr).value = write_len
