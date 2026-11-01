"""
Win32 process/CPU counters used by the HUD metrics.
"""

import ctypes
import ctypes.wintypes as wintypes
import os
from math import isfinite
from threading import Event, Lock, Thread
from time import perf_counter


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
PDH_FMT_DOUBLE = 0x00000200
PDH_CSTATUS_VALID_DATA = 0
PDH_CSTATUS_NEW_DATA = 1

kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

# These handles are pointer-sized on Win64; ctypes' default c_int return type
# otherwise truncates them and makes valid process samples fail unpredictably
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetActiveProcessorCount.argtypes = [wintypes.WORD]
kernel32.GetActiveProcessorCount.restype = wintypes.DWORD
kernel32.GetActiveProcessorGroupCount.argtypes = []
kernel32.GetActiveProcessorGroupCount.restype = wintypes.WORD


class _FileTime(ctypes.Structure):
	_fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

	def ticks(self) -> int:
		return (self.high << 32) | self.low


kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(_FileTime)] * 4)]
kernel32.GetProcessTimes.restype = wintypes.BOOL
kernel32.GetSystemTimes.argtypes = [ctypes.POINTER(_FileTime)] * 3
kernel32.GetSystemTimes.restype = wintypes.BOOL


class _MemoryStatusEx(ctypes.Structure):
	_fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
		("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
		("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def system_ram_gb() -> tuple[float, float] | None:
	'''(used_gb, total_gb) physical memory, or None when unavailable.'''
	try:
		status = _MemoryStatusEx()
		status.dwLength = ctypes.sizeof(_MemoryStatusEx)
		if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
			return None
		bytes_per_gb = 1024.0 ** 3
		total = status.ullTotalPhys / bytes_per_gb
		available = status.ullAvailPhys / bytes_per_gb
		return max(0.0, total - available), total
	except Exception:
		return None


class _ProcessMemoryCounters(ctypes.Structure):
	_fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
		("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
		("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemoryCounters), wintypes.DWORD]
psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


class _ProcessEntry32(ctypes.Structure):
	_fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
				("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
				("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32)]
kernel32.Process32NextW.restype = wintypes.BOOL


# 100-ns ticks per second
_TICKS_PER_SEC = 10_000_000.0


def process_times(pid: int) -> tuple[int, int, int] | None:
	'''(creation, kernel, user) ticks, or None when the process is unavailable.'''
	handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
	if not handle:
		return None
	try:
		creation, exit_t, kernel, user = (_FileTime() for _ in range(4))
		if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t), ctypes.byref(kernel), ctypes.byref(user)):
			return None
		return creation.ticks(), kernel.ticks(), user.ticks()
	finally:
		kernel32.CloseHandle(handle)


def process_ram_mb(pid: int) -> float | None:
	'''Resident working set of pid in MB, or None when unavailable.'''
	handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
	if not handle:
		return None
	try:
		counters = _ProcessMemoryCounters()
		counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
		if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
			return None
		return counters.WorkingSetSize / (1024.0 * 1024.0)
	finally:
		kernel32.CloseHandle(handle)


def process_tree(roots: tuple[int, ...]) -> tuple[int, ...] | None:
	'''Include wrapper children, without capturing unrelated Python processes.'''
	roots = tuple(dict.fromkeys(int(pid) for pid in roots if int(pid) > 0))
	if not roots:
		return ()
	handle = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
	if handle in (None, ctypes.c_void_p(-1).value):
		return None
	try:
		entry = _ProcessEntry32()
		entry.dwSize = ctypes.sizeof(entry)
		if not kernel32.Process32FirstW(handle, ctypes.byref(entry)):
			return None
		edges = []
		while True:
			edges.append((int(entry.th32ParentProcessID), int(entry.th32ProcessID)))
			if not kernel32.Process32NextW(handle, ctypes.byref(entry)):
				break
	finally:
		kernel32.CloseHandle(handle)
	members = set(roots)
	while True:
		expanded = members | {child for parent, child in edges if parent in members}
		if expanded == members:
			return tuple(sorted(members))
		members = expanded


class _CpuUtilitySampler:
	"""Read Windows Processor Utility off the render thread; unsupported counters leave busy-time fallback available."""
	def __init__(self) -> None:
		self._lock = Lock()
		self._stop = Event()
		self._thread = None
		self._value = None

	def sample(self) -> float | None:
		with self._lock:
			if self._stop.is_set():
				return None
			if self._thread is None:
				self._thread = Thread(target=self._run, name="metrics-cpu-utility", daemon=True)
				self._thread.start()
			value = self._value
		return value[0] if value is not None and 0 <= perf_counter() - value[1] <= 1.5 else None

	def _run(self) -> None:
		query = wintypes.HANDLE()
		api = None
		try:
			api = ctypes.WinDLL("pdh")
			api.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
			api.PdhAddEnglishCounterW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(wintypes.HANDLE)]
			api.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
			api.PdhCloseQuery.argtypes = [wintypes.HANDLE]
			class CounterValue(ctypes.Structure):
				_fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_double)]
			api.PdhGetFormattedCounterValue.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(CounterValue)]
			counter = wintypes.HANDLE()
			if api.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
				return
			if api.PdhAddEnglishCounterW(query, r"\Processor Information(_Total)\% Processor Utility", 0, ctypes.byref(counter)) != 0:
				return
			while not self._stop.is_set():
				formatted = CounterValue()
				valid = (api.PdhCollectQueryData(query) == 0
					and api.PdhGetFormattedCounterValue(counter, PDH_FMT_DOUBLE, None, ctypes.byref(formatted)) == 0
					and formatted.status in (PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA)
					and isfinite(formatted.value) and formatted.value >= 0)
				with self._lock:
					self._value = (min(100.0, formatted.value), perf_counter()) if valid else None
				self._stop.wait(0.5)
		except (OSError, AttributeError, ValueError):
			with self._lock:
				self._value = None
		finally:
			if api is not None and query:
				api.PdhCloseQuery(query)

	def close(self) -> bool:
		self._stop.set()
		with self._lock:
			thread = self._thread
			self._value = None
		if thread is not None:
			thread.join(timeout=0.25)
		return thread is None or not thread.is_alive()


class CpuSampler:
	"""System-wide and per-process CPU load from kernel/user time deltas."""
	def __init__(self, *, utility_sampler=None) -> None:
		self._utility = utility_sampler if utility_sampler is not None else _CpuUtilitySampler()
		self._prev_sys: tuple[int, int] | None = None
		self._prev_proc: dict[int, tuple[int, int, int]] = {}
		self._prev_at: float = 0.0
		self._prev_window: float = 0.0
		self.system_percent: float | None = None
		self.process_percent: float | None = None
		self.process_ram_mb: float | None = None
		self._cpu_count = max(1, int(kernel32.GetActiveProcessorCount(0xFFFF) or os.cpu_count() or 1))
		try:
			self._single_group = int(kernel32.GetActiveProcessorGroupCount()) == 1
		except Exception:
			self._single_group = self._cpu_count <= 64

	def sample(self, pids: list[int], min_interval: float = 0.4) -> None:
		now = perf_counter()
		if now - self._prev_at < min_interval:
			return
		window = now - self._prev_at if self._prev_at else 0.0

		idle, kernel_t, user_t = _FileTime(), _FileTime(), _FileTime()
		self.system_percent = None
		if self._single_group and kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel_t), ctypes.byref(user_t)):
			total = kernel_t.ticks() + user_t.ticks()
			if self._prev_sys is not None:
				total_delta = total - self._prev_sys[0]
				idle_delta = idle.ticks() - self._prev_sys[1]
				if total_delta > 0:
					self.system_percent = max(0.0, min(100.0, 100.0 * (total_delta - idle_delta) / total_delta))
			self._prev_sys = (total, idle.ticks())
		else:
			self._prev_sys = None

		busy = 0
		ram = 0.0
		complete_times = bool(pids) and bool(window)
		complete_ram = bool(pids)
		for pid in pids:
			times = process_times(pid)
			if times is None:
				self._prev_proc.pop(pid, None)
				complete_times = False
				complete_ram = False
				continue
			prev = self._prev_proc.get(pid)
			if prev is not None and prev[0] == times[0] and times[1] >= prev[1] and times[2] >= prev[2]:
				busy += (times[1] - prev[1]) + (times[2] - prev[2])
			else:
				complete_times = False
			self._prev_proc[pid] = times
			current_ram = process_ram_mb(pid)
			if current_ram is None:
				complete_ram = False
			else:
				ram += current_ram

		if complete_times and window > 0:
			# Normalise to whole-machine capacity (100% == every core busy),
			# so the HUD number reads the same way Task Manager does
			self.process_percent = max(0.0, min(100.0, (busy / (window * _TICKS_PER_SEC * self._cpu_count)) * 100.0))
		else:
			self.process_percent = None
		self.process_ram_mb = ram if complete_ram else None

		# Drop pids that vanished so the dict cannot grow unbounded
		for gone in [process_id for process_id in self._prev_proc if process_id not in pids]:
			self._prev_proc.pop(gone, None)

		self._prev_at = now

	@property
	def utility_percent(self) -> float | None:
		return self._utility.sample()

	def close(self) -> bool:
		return self._utility.close()
