"""
Exact CS2 agent meshes decoded from pak01_dir.vpk by the bundled vpkmesh.dll (VPK tree, KV3, meshopt, Source 2 skin remaps).
Only files on disk are read; the game process is never touched here. Models load on a worker thread and are cached as .npz under config/model_cache.
"""

import ctypes
import hashlib
import os
import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cs2esp.config import CONFIG_DIR

_BIN_DIR = Path(__file__).resolve().parent / "bin"
_DLL_PATH = _BIN_DIR / "vpkmesh.dll"
_CACHE_DIR = CONFIG_DIR / "model_cache"
_CACHE_VERSION = 1

VERTEX_DTYPE = np.dtype([("pos", np.float32, 3), ("normal", np.float32, 3), ("joints", np.uint16, 4), ("weights", np.float32, 4)])
assert VERTEX_DTYPE.itemsize == 48

CT_DEFAULT = "agents/models/ctm_sas/ctm_sas.vmdl"
T_DEFAULT = "agents/models/tm_phoenix/tm_phoenix.vmdl"


@dataclass(frozen=True)
class AgentMesh:
	"""Decoded agent mesh with skinning data and a parent-bone table."""
	model_path: str
	vertices: np.ndarray  # VERTEX_DTYPE[N]
	indices: np.ndarray  # uint32[M]
	inv_bind: np.ndarray  # float32[B, 3, 4]
	parents: np.ndarray  # int16[B]

	@property
	def bone_count(self) -> int:
		return int(self.parents.shape[0])


def normalize_model_path(path: str) -> str:
	'''Normalize separators and case and map a compiled .vmdl_c path to its source name.'''
	path = path.replace("\\", "/").strip().lower()
	if path.endswith(".vmdl_c"):
		path = path[:-2]
	return path


def _load_dll():
	'''Load vpkmesh.dll and bind its C signatures; a missing optional DLL returns None.'''
	if not _DLL_PATH.exists():
		return None
	try:
		dll = ctypes.CDLL(str(_DLL_PATH))
	except OSError as error:
		print(f"[vpk] cannot load {_DLL_PATH.name}: {error}")
		return None
	dll.vm_open.argtypes = [ctypes.c_char_p]
	dll.vm_open.restype = ctypes.c_int
	dll.vm_load.argtypes = [ctypes.c_char_p]
	dll.vm_load.restype = ctypes.c_void_p
	for name in ("vm_vertex_count", "vm_index_count", "vm_bone_count"):
		count_function = getattr(dll, name)
		count_function.argtypes = [ctypes.c_void_p]
		count_function.restype = ctypes.c_uint32
	dll.vm_copy.argtypes = [ctypes.c_void_p] * 5
	dll.vm_copy.restype = None
	dll.vm_free.argtypes = [ctypes.c_void_p]
	dll.vm_free.restype = None
	dll.vm_last_error.argtypes = []
	dll.vm_last_error.restype = ctypes.c_char_p
	return dll


def _cache_path(model_path: str, vpk_stamp: str) -> Path:
	'''Build the versioned .npz cache path for one model and VPK stamp.'''
	digest = hashlib.sha1(f"{_CACHE_VERSION}|{vpk_stamp}|{model_path}".encode()).hexdigest()[:16]
	return _CACHE_DIR / f"{Path(model_path).stem}_{digest}.npz"


class ModelLibrary:
	"""Async, cached agent-mesh loader. ``get`` never blocks the frame loop."""
	def __init__(self, vpk_path: str | None = None) -> None:
		self._vpk_path = vpk_path
		self._dll = None
		self._ready: dict[str, AgentMesh] = {}
		self._failed: dict[str, str] = {}
		self._pending: set[str] = set()
		self._lock = threading.Lock()
		self._jobs: "queue.Queue[str]" = queue.Queue()
		self._thread: threading.Thread | None = None
		self._vpk_stamp = ""
		self._status = "idle"

	@property
	def status(self) -> str:
		return self._status

	def available(self) -> bool:
		'''Return whether the bundled decoder DLL is present.'''
		return _DLL_PATH.exists()

	def start(self) -> None:
		'''Start the single loader thread and queue the default agent meshes.'''
		with self._lock:
			if self._thread is not None:
				return
			self._thread = threading.Thread(target=self._worker, name="vpk-models", daemon=True)
			self._thread.start()
		self.request(CT_DEFAULT)
		self.request(T_DEFAULT)

	def request(self, model_path: str) -> None:
		'''Queue a mesh once per normalized path, skipping loaded, failed and pending entries.'''
		key = normalize_model_path(model_path)
		if not key:
			return
		with self._lock:
			if key in self._ready or key in self._failed or key in self._pending:
				return
			self._pending.add(key)
		self._jobs.put(key)

	def get(self, model_path: str) -> AgentMesh | None:
		'''Return a cached mesh immediately without blocking, queueing an unseen path.'''
		key = normalize_model_path(model_path)
		with self._lock:
			mesh = self._ready.get(key)
			known = key in self._failed or key in self._pending
		if mesh is None and not known:
			self.request(key)
		return mesh

	def failure(self, model_path: str) -> str | None:
		'''Return the recorded load error for a model, if any.'''
		with self._lock:
			return self._failed.get(normalize_model_path(model_path))

	def _open(self) -> bool:
		'''Open the VPK once and record the archive stamp used by the cache key.'''
		if self._dll is not None:
			return True
		self._dll = _load_dll()
		if self._dll is None:
			self._status = "vpkmesh.dll missing - run cs2esp\\native\\vpkmesh\\build.bat"
			return False
		archive_path = (self._vpk_path or "").encode("utf-8")
		if not self._dll.vm_open(archive_path or None):
			self._status = self._dll.vm_last_error().decode(errors="replace")
			self._dll = None
			return False
		try:
			found = self._vpk_path or _find_vpk()
			stat = os.stat(found) if found else None
			self._vpk_stamp = f"{stat.st_size}:{int(stat.st_mtime)}" if stat else "unknown"
		except OSError:
			self._vpk_stamp = "unknown"
		self._status = "vpk open"
		return True

	def _worker(self) -> None:
		'''Load queued meshes and record each success or failure under the lock.'''
		while True:
			key = self._jobs.get()
			try:
				mesh = self._load(key)
				with self._lock:
					self._pending.discard(key)
					self._ready[key] = mesh
				print(f"[vpk] {key}: {mesh.vertices.shape[0]} verts, {mesh.indices.shape[0] // 3} tris, {mesh.bone_count} bones")
			except Exception as error:
				with self._lock:
					self._pending.discard(key)
					self._failed[key] = str(error)
				print(f"[vpk] {key}: {error}")

	def _load(self, key: str) -> AgentMesh:
		'''Return the cached mesh for a model key or decode it through the native DLL.'''
		if not self._open():
			raise RuntimeError(self._status)
		cache = _cache_path(key, self._vpk_stamp)
		if cache.exists():
			try:
				with np.load(cache) as data:
					return AgentMesh(key, data["vertices"].view(VERTEX_DTYPE).reshape(-1), data["indices"], data["inv_bind"], data["parents"])
			except Exception:
				cache.unlink(missing_ok=True)

		handle = self._dll.vm_load(key.encode("utf-8"))
		if not handle:
			raise RuntimeError(self._dll.vm_last_error().decode(errors="replace"))
		try:
			vertex_count = self._dll.vm_vertex_count(handle)
			index_count = self._dll.vm_index_count(handle)
			bone_count = self._dll.vm_bone_count(handle)
			vertices = np.empty(vertex_count, dtype=VERTEX_DTYPE)
			indices = np.empty(index_count, dtype=np.uint32)
			inv_bind = np.empty((bone_count, 3, 4), dtype=np.float32)
			parents = np.empty(bone_count, dtype=np.int16)
			self._dll.vm_copy(
				handle,
				vertices.ctypes.data_as(ctypes.c_void_p),
				indices.ctypes.data_as(ctypes.c_void_p),
				inv_bind.ctypes.data_as(ctypes.c_void_p),
				parents.ctypes.data_as(ctypes.c_void_p),
			)
		finally:
			self._dll.vm_free(handle)

		try:
			_CACHE_DIR.mkdir(parents=True, exist_ok=True)
			np.savez(cache, vertices=vertices.view(np.uint8), indices=indices, inv_bind=inv_bind, parents=parents)
		except OSError:
			pass
		return AgentMesh(key, vertices, indices, inv_bind, parents)


def _find_vpk() -> str | None:
	'''Find pak01_dir.vpk through the Steam registry and library folders; registry access stays deferred until loading.'''
	try:
		import winreg
	except ImportError:
		return None
	roots: list[Path] = []
	for hive, key in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"), (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")):
		try:
			handle = winreg.OpenKey(hive, key)
		except OSError:
			continue
		for name in ("InstallPath", "SteamPath"):
			try:
				roots.append(Path(winreg.QueryValueEx(handle, name)[0]))
			except OSError:
				pass
	libraries = set(roots)
	for root in roots:
		vdf = root / "steamapps" / "libraryfolders.vdf"
		if vdf.exists():
			for match in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore")):
				libraries.add(Path(match.group(1).replace("\\\\", "\\")))
	for library in libraries:
		candidate = library / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo" / "pak01_dir.vpk"
		if candidate.exists():
			return str(candidate)
	return None


_library: ModelLibrary | None = None


def library() -> ModelLibrary:
	'''Return the process-wide mesh library, creating it on first use.'''
	global _library
	if _library is None:
		_library = ModelLibrary()
	return _library
