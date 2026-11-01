// Consume the aim hold's secondary-fire click only in the configured foreground window.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <atomic>
#include <cstdint>

namespace {
constexpr UINT UPDATE_POLICY = WM_APP + 1;
std::atomic<std::uintptr_t> target_window{0};
std::atomic<unsigned> modifiers{0};
std::atomic<bool> enabled{false}, captured{false};
std::atomic<int> hook_status{0};
DWORD thread_id = 0;
HANDLE thread_handle = nullptr, ready_event = nullptr;
HHOOK mouse_hook = nullptr;

// Only the paired right-button edges are consumed; motion and every other button pass unchanged.
LRESULT CALLBACK mouse_proc(int code, WPARAM message, LPARAM parameter) {
	if (code >= 0 && (message == WM_RBUTTONDOWN || message == WM_RBUTTONUP)) {
		const MSLLHOOKSTRUCT *event = reinterpret_cast<const MSLLHOOKSTRUCT *>(parameter);
		if (!(event->flags & LLMHF_INJECTED)) {
			if (message == WM_RBUTTONUP && captured.exchange(false)) {
				PostThreadMessageW(thread_id, UPDATE_POLICY, 0, 0);
				return 1;
			}
			if (message == WM_RBUTTONDOWN && enabled.load()
					&& reinterpret_cast<std::uintptr_t>(GetForegroundWindow()) == target_window.load()) {
				const unsigned required = modifiers.load();
				const bool shift = !(required & 1) || (GetAsyncKeyState(VK_SHIFT) & 0x8000);
				const bool control = !(required & 2) || (GetAsyncKeyState(VK_CONTROL) & 0x8000);
				const bool alt = !(required & 4) || (GetAsyncKeyState(VK_MENU) & 0x8000);
				if (shift && control && alt) {
					captured.store(true);
					return 1;
				}
			}
		}
	}
	return CallNextHookEx(mouse_hook, code, message, parameter);
}

// Keep callbacks entirely native so a busy Python frame cannot stall the Windows input chain.
DWORD WINAPI run_guard(void *) {
	MSG message;
	PeekMessageW(&message, nullptr, WM_USER, WM_USER, PM_NOREMOVE);
	SetEvent(ready_event);
	while (GetMessageW(&message, nullptr, 0, 0) > 0) {
		if (message.message != UPDATE_POLICY) continue;
		if (enabled.load() && !mouse_hook) {
			HMODULE module = nullptr;
			GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
				reinterpret_cast<LPCWSTR>(&mouse_proc), &module);
			mouse_hook = SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, module, 0);
			hook_status.store(mouse_hook ? 1 : -static_cast<int>(GetLastError()));
		} else if (!enabled.load() && !captured.load() && mouse_hook) {
			UnhookWindowsHookEx(mouse_hook);
			mouse_hook = nullptr;
			hook_status.store(0);
		}
	}
	if (mouse_hook) UnhookWindowsHookEx(mouse_hook);
	mouse_hook = nullptr;
	hook_status.store(0);
	captured.store(false);
	return 0;
}
}

extern "C" {
// Start the policy thread without installing a global hook until a matching weapon is active.
__declspec(dllexport) int ig_start() {
	if (thread_handle) return 1;
	ready_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
	if (!ready_event) return 0;
	thread_handle = CreateThread(nullptr, 0, run_guard, nullptr, 0, &thread_id);
	if (!thread_handle) {
		CloseHandle(ready_event);
		ready_event = nullptr;
		return 0;
	}
	return WaitForSingleObject(ready_event, 2000) == WAIT_OBJECT_0;
}

// Policy updates never inspect game memory or run Python on the input thread.
__declspec(dllexport) void ig_configure(std::uintptr_t window, unsigned keys, int active) {
	target_window.store(window);
	modifiers.store(keys);
	enabled.store(active != 0);
	if (thread_id) PostThreadMessageW(thread_id, UPDATE_POLICY, 0, 0);
}

__declspec(dllexport) int ig_captured() { return captured.load() ? 1 : 0; }
__declspec(dllexport) int ig_status() { return hook_status.load(); }

// Stop before the owning runtime unloads the library; no physical input is synthesised.
__declspec(dllexport) void ig_stop() {
	enabled.store(false);
	if (!thread_handle) return;
	PostThreadMessageW(thread_id, WM_QUIT, 0, 0);
	WaitForSingleObject(thread_handle, INFINITE);
	CloseHandle(thread_handle);
	CloseHandle(ready_event);
	thread_handle = ready_event = nullptr;
	thread_id = 0;
}
}
