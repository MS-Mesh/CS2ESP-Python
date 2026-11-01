"""
Alias this interpreter's own loader display path without copying or loading another Python DLL.
BaseDllName stays unchanged because extension-module resolution depends on it. The mapped image and other loader indexes retain their original identity.
"""

import atexit
import ctypes
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PureWindowsPath


class _ListEntry(ctypes.Structure):
	'''Doubly linked loader list entry.'''
	_fields_ = [("forward", ctypes.c_void_p), ("backward", ctypes.c_void_p)]


class _UnicodeString(ctypes.Structure):
	'''Windows UNICODE_STRING descriptor.'''
	_fields_ = [("length", ctypes.c_uint16), ("capacity", ctypes.c_uint16), ("buffer", ctypes.c_void_p)]


class _LoaderEntry(ctypes.Structure):
	'''Prefix of LDR_DATA_TABLE_ENTRY used for names and list links.'''
	_fields_ = [
		("load_order", _ListEntry), ("memory_order", _ListEntry), ("initialization_order", _ListEntry),
		("module_base", ctypes.c_void_p), ("entry_point", ctypes.c_void_p), ("image_size", ctypes.c_uint32),
		("full_name", _UnicodeString), ("base_name", _UnicodeString),
	]


class _LoaderData(ctypes.Structure):
	'''Prefix of PEB_LDR_DATA that exposes the load-order head.'''
	_fields_ = [("length", ctypes.c_uint32), ("initialized", ctypes.c_uint8), ("handle", ctypes.c_void_p), ("load_order", _ListEntry)]


class _Peb(ctypes.Structure):
	'''Partial PEB layout up to the loader pointer.'''
	_fields_ = [("reserved", ctypes.c_uint8 * 4), ("reserved_pointers", ctypes.c_void_p * 2), ("loader", ctypes.c_void_p)]


class _ProcessBasicInformation(ctypes.Structure):
	'''PROCESS_BASIC_INFORMATION result from ProcessBasicInformation.'''
	_fields_ = [
		("exit_status", ctypes.c_int32), ("peb", ctypes.c_void_p), ("affinity", ctypes.c_size_t), ("priority", ctypes.c_int32),
		("process_id", ctypes.c_size_t), ("parent_process_id", ctypes.c_size_t),
	]


@dataclass
class _IdentityState:
	'''Original loader labels and list links captured before the display-path alias.'''
	entry: int
	head: int
	module_handle: int
	original_path: str
	original_name: str
	original_full: bytes
	original_base: bytes
	name: str
	display_path: str
	unlinked: bool


_SPOOFED_NAME: str | None = None
_IDENTITY: _IdentityState | None = None
_UNICODE_BUFFERS: list = []
_NATIVE = None
_RESTORE_REGISTERED = False


def is_supported() -> bool:
	'''Require a Windows interpreter exposing its actual DLL handle.'''
	return sys.platform.startswith("win") and bool(getattr(sys, "dllhandle", 0)) and ctypes.sizeof(ctypes.c_void_p) in (4, 8)


def _native_api():
	'''Bind only APIs used on the current process and its existing loader records.'''
	global _NATIVE
	if _NATIVE is not None:
		return _NATIVE
	kernel = ctypes.WinDLL("kernel32", use_last_error=True)
	native = ctypes.WinDLL("ntdll", use_last_error=True)
	kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
	kernel.GetModuleFileNameW.restype = ctypes.c_uint32
	kernel.GetCurrentProcess.argtypes = []
	kernel.GetCurrentProcess.restype = ctypes.c_void_p
	native.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
	native.NtQueryInformationProcess.restype = ctypes.c_int32
	native.LdrLockLoaderLock.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_size_t)]
	native.LdrLockLoaderLock.restype = ctypes.c_int32
	native.LdrUnlockLoaderLock.argtypes = [ctypes.c_uint32, ctypes.c_size_t]
	native.LdrUnlockLoaderLock.restype = ctypes.c_int32
	_NATIVE = kernel, native
	return _NATIVE


@contextmanager
def _loader_lock(native):
	'''Serialize traversal and name/list updates with the native loader.'''
	disposition, cookie = ctypes.c_uint32(), ctypes.c_size_t()
	status = native.LdrLockLoaderLock(0, ctypes.byref(disposition), ctypes.byref(cookie))
	if status < 0 or disposition.value != 1 or not cookie.value:
		raise RuntimeError(f"Loader lock failed: status={status}, disposition={disposition.value}")
	try:
		yield
	finally:
		if native.LdrUnlockLoaderLock(0, cookie.value) < 0:
			raise RuntimeError("Loader unlock failed")


def _running_interpreter(kernel) -> tuple[int, str]:
	'''Resolve the module actually executing Python, not the stable-ABI python3.dll stub.'''
	handle = int(getattr(sys, "dllhandle", 0) or 0)
	if not handle:
		raise RuntimeError("Interpreter DLL handle is unavailable")
	for capacity in (512, 2048, 8192, 32768):
		buffer = ctypes.create_unicode_buffer(capacity)
		length = kernel.GetModuleFileNameW(handle, buffer, capacity)
		if 0 < length < capacity - 1:
			return handle, buffer.value
		if not length:
			break
	raise RuntimeError("Interpreter DLL path lookup failed or was truncated")


def _load_order_head(kernel, native) -> int:
	'''Obtain this process's PEB through ProcessBasicInformation.'''
	information, returned = _ProcessBasicInformation(), ctypes.c_uint32()
	status = native.NtQueryInformationProcess(kernel.GetCurrentProcess(), 0, ctypes.byref(information), ctypes.sizeof(information), ctypes.byref(returned))
	if status < 0 or returned.value < ctypes.sizeof(information) or not information.peb:
		raise RuntimeError(f"ProcessBasicInformation failed: status={status}, length={returned.value}")
	loader = _Peb.from_address(information.peb).loader
	if not loader:
		raise RuntimeError("Current process loader is unavailable")
	return int(loader) + _LoaderData.load_order.offset


def _find_module(head: int, module_handle: int) -> int | None:
	'''Check reciprocal links while searching the load-order list by exact DllBase.'''
	entry = _ListEntry.from_address(head).forward
	seen = set()
	for _count in range(1024):
		if entry == head:
			return None
		if not entry or entry in seen:
			raise RuntimeError("Malformed or cyclic loader list")
		seen.add(entry)
		links = _ListEntry.from_address(entry)
		if not links.forward or not links.backward:
			raise RuntimeError("Null loader neighbor")
		if _ListEntry.from_address(links.forward).backward != entry or _ListEntry.from_address(links.backward).forward != entry:
			raise RuntimeError("Inconsistent loader neighbors")
		if _LoaderEntry.from_address(entry).module_base == module_handle:
			return int(entry)
		entry = links.forward
	raise RuntimeError("Loader traversal exceeded its bound")


def _read_unicode_string(address: int) -> str:
	'''Read the complete UTF-16 contents, rejecting malformed descriptor lengths.'''
	value = _UnicodeString.from_address(address)
	if value.length > value.capacity or value.length % 2 or (value.length and not value.buffer):
		raise ValueError("Invalid loader Unicode descriptor")
	return ctypes.string_at(value.buffer, value.length).decode("utf-16-le") if value.length else ""


def _overwrite_unicode_string(address: int, new_value: str) -> None:
	'''Replace a descriptor with a complete, process-lifetime UTF-16 allocation.'''
	encoded = new_value.encode("utf-16-le")
	if len(encoded) > 65532 or "\0" in new_value:
		raise ValueError("Loader display name exceeds UNICODE_STRING capacity or contains NUL")
	buffer = ctypes.create_string_buffer(encoded + b"\0\0", len(encoded) + 2)
	_UNICODE_BUFFERS.append(buffer)
	value = _UnicodeString.from_address(address)
	value.buffer, value.capacity, value.length = ctypes.addressof(buffer), len(encoded) + 2, len(encoded)
	if _read_unicode_string(address) != new_value:
		raise RuntimeError("Loader display-name readback failed")


def _unlink_load_order(entry: int) -> None:
	'''Detach just InLoadOrderLinks after checking both neighboring records.'''
	links = _ListEntry.from_address(entry)
	forward, backward = links.forward, links.backward
	if not forward or not backward or _ListEntry.from_address(forward).backward != entry or _ListEntry.from_address(backward).forward != entry:
		raise RuntimeError("Load-order unlink precondition failed")
	_ListEntry.from_address(forward).backward = backward
	_ListEntry.from_address(backward).forward = forward
	links.forward = links.backward = entry
	if _ListEntry.from_address(forward).backward != backward or _ListEntry.from_address(backward).forward != forward:
		raise RuntimeError("Load-order unlink neighbor readback failed")


def _restore_module_identity() -> bool:
	'''Restore original labels and reattach a detached entry before interpreter teardown.'''
	global _IDENTITY, _SPOOFED_NAME
	state = _IDENTITY
	if state is None:
		return True
	try:
		_kernel, native = _native_api()
		with _loader_lock(native):
			entry = _LoaderEntry.from_address(state.entry)
			if entry.module_base != state.module_handle:
				raise RuntimeError("Interpreter entry identity changed before restoration")
			ctypes.memmove(state.entry + _LoaderEntry.full_name.offset, state.original_full, len(state.original_full))
			ctypes.memmove(state.entry + _LoaderEntry.base_name.offset, state.original_base, len(state.original_base))
			if state.unlinked:
				if entry.load_order.forward != state.entry or entry.load_order.backward != state.entry:
					raise RuntimeError("Detached load-order links changed before restoration")
				head = _ListEntry.from_address(state.head)
				tail = head.backward
				if not tail or _ListEntry.from_address(tail).forward != state.head:
					raise RuntimeError("Loader tail changed before restoration")
				entry.load_order.forward, entry.load_order.backward = state.head, tail
				_ListEntry.from_address(tail).forward, head.backward = state.entry, state.entry
			if _find_module(state.head, state.module_handle) != state.entry:
				raise RuntimeError("Restored interpreter entry was not found")
			if _read_unicode_string(state.entry + _LoaderEntry.full_name.offset) != state.original_path:
				raise RuntimeError("Original display-path restoration failed")
			if _read_unicode_string(state.entry + _LoaderEntry.base_name.offset) != state.original_name:
				raise RuntimeError("Original display-name restoration failed")
			_IDENTITY, _SPOOFED_NAME = None, None
		return True
	except Exception as error:
		print(f'[anti_detect.module_spoof] restoration failed: {error!r}')
		return False


def get_module_identity_status() -> dict:
	'''Observe the display-path alias, preserved loader basename and list operation without making detection claims.'''
	state = _IDENTITY
	if state is None:
		return {"applied": False, "supported": is_supported()}
	try:
		_kernel, native = _native_api()
		with _loader_lock(native):
			entry = _LoaderEntry.from_address(state.entry)
			full_name = _read_unicode_string(state.entry + _LoaderEntry.full_name.offset)
			base_name = _read_unicode_string(state.entry + _LoaderEntry.base_name.offset)
			found = _find_module(state.head, state.module_handle)
			detached = entry.load_order.forward == state.entry and entry.load_order.backward == state.entry
			consistent = (found is None and detached) if state.unlinked else found == state.entry
			preserved = base_name == state.original_name
			verified = entry.module_base == state.module_handle and full_name == state.display_path and preserved and consistent
			return {"applied": verified, "supported": True, "base_name": base_name, "full_name": full_name, "original_path": state.original_path,
				"original_base_name": state.original_name, "display_basename": state.name, "loader_base_name_preserved": preserved,
				"module_handle": state.module_handle, "load_order_unlinked": state.unlinked and consistent, "load_order_consistent": consistent}
	except Exception as error:
		return {"applied": False, "supported": True, "error": str(error)}


def spoof_python_module_name(*, masquerade_name: str | None = None, unlink_original: bool = True) -> str | None:
	"""Alias FullDllName only and return its requested basename after readback. Preserve BaseDllName for subsequent extension imports."""
	global _IDENTITY, _SPOOFED_NAME, _RESTORE_REGISTERED
	if not is_supported():
		return None
	if _IDENTITY is not None:
		return _SPOOFED_NAME if get_module_identity_status().get("applied") else None
	name = masquerade_name or "Microsoft.UI.Xaml.dll"
	if not isinstance(name, str) or name in (".", "..") or any(character in name for character in ("/", "\\", ":", "\0")):
		return None
	try:
		kernel, native = _native_api()
		handle, original_path = _running_interpreter(kernel)
		display_path = str(PureWindowsPath(original_path).with_name(name))
		if not _RESTORE_REGISTERED:
			atexit.register(_restore_module_identity)
			_RESTORE_REGISTERED = True
		with _loader_lock(native):
			head = _load_order_head(kernel, native)
			address = _find_module(head, handle)
			if address is None:
				raise RuntimeError("Exact running interpreter entry was not found")
			entry = _LoaderEntry.from_address(address)
			original_path = _read_unicode_string(address + _LoaderEntry.full_name.offset)
			original_name = _read_unicode_string(address + _LoaderEntry.base_name.offset)
			original_full = ctypes.string_at(address + _LoaderEntry.full_name.offset, ctypes.sizeof(_UnicodeString))
			original_base = ctypes.string_at(address + _LoaderEntry.base_name.offset, ctypes.sizeof(_UnicodeString))
			forward, backward = entry.load_order.forward, entry.load_order.backward
			try:
				_overwrite_unicode_string(address + _LoaderEntry.full_name.offset, display_path)
				if unlink_original:
					_unlink_load_order(address)
				state = _IdentityState(entry=address, head=head, module_handle=handle, original_path=original_path, original_name=original_name,
					original_full=original_full, original_base=original_base, name=name, display_path=display_path, unlinked=bool(unlink_original))
				_IDENTITY = state
				if not get_module_identity_status().get("applied"):
					raise RuntimeError("Final loader label/list verification failed")
			except Exception:
				ctypes.memmove(address + _LoaderEntry.full_name.offset, original_full, len(original_full))
				ctypes.memmove(address + _LoaderEntry.base_name.offset, original_base, len(original_base))
				entry.load_order.forward, entry.load_order.backward = forward, backward
				_ListEntry.from_address(forward).backward, _ListEntry.from_address(backward).forward = address, address
				_IDENTITY = None
				raise
		_SPOOFED_NAME = name
		return name
	except Exception as error:
		if _IDENTITY is not None:
			_restore_module_identity()
		print(f'[anti_detect.module_spoof] loader display-name update failed: {error!r}')
		return None
