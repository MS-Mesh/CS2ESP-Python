"""
Edit-mode backdrop: a real blurred snapshot of the CS2 window, captured with PrintWindow once a second, downsampled to 160 px,
uploaded as a GL texture and drawn stretched across the overlay; the dim veil on top is drawn in main.py.
"""

import ctypes
from ctypes import wintypes
from time import perf_counter

import OpenGL.GL as gl
from imgui_bundle import imgui

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

_PW_RENDERFULLCONTENT = 0x02
_BLUR_W = 160


class EditBackdrop:
	"""Owns the blurred-game texture; refreshes on a slow cadence."""

	def __init__(self) -> None:
		self._tex: int = 0
		self._w = 0
		self._h = 0
		self._next_refresh = 0.0
		self._ok = False

	def _release(self) -> None:
		'''Delete the texture, ignoring a lost GL context.'''
		if self._tex:
			try:
				gl.glDeleteTextures(1, [self._tex])
			except Exception:
				pass
			self._tex = 0

	def invalidate(self) -> None:
		'''Drop the texture (call when the GL context is recreated).'''
		self._release()
		self._ok = False
		self._next_refresh = 0.0

	def _capture(self, hwnd: int, out_w: int, out_h: int):
		'''PrintWindow the game window, heavily downsampled; a blank capture falls back to the desktop composite.'''
		# Pillow is optional and only needed when edit-mode capture is requested
		try:
			from PIL import Image
		except Exception:
			return None
		img = self._printwindow(hwnd)
		if img is None:
			try:
				from PIL import ImageGrab

				img = ImageGrab.grab(all_screens=False).convert("RGB")
			except Exception:
				return None
		try:
			w, h = img.size
			blur_h = max(1, round(_BLUR_W * h / w))
			return img.resize((_BLUR_W, blur_h), Image.BILINEAR)
		except Exception:
			return None

	def _printwindow(self, hwnd: int):
		'''Game window bitmap via GDI, or None when the surface reads blank.'''
		try:
			from PIL import Image

			rect = wintypes.RECT()
			if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
				return None
			w = int(rect.right - rect.left)
			h = int(rect.bottom - rect.top)
			if w <= 0 or h <= 0:
				return None

			hdc_wnd = user32.GetDC(hwnd)
			if not hdc_wnd:
				return None
			hbm = 0
			hdc_mem = 0
			try:
				hdc_mem = gdi32.CreateCompatibleDC(hdc_wnd)
				hbm = gdi32.CreateCompatibleBitmap(hdc_wnd, w, h)
				gdi32.SelectObject(hdc_mem, hbm)
				if not user32.PrintWindow(hwnd, hdc_mem, _PW_RENDERFULLCONTENT):
					return None

				class _BI(ctypes.Structure):
					_fields_ = [
						("biSize", wintypes.DWORD),
						("biWidth", wintypes.LONG),
						("biHeight", wintypes.LONG),
						("biPlanes", wintypes.WORD),
						("biBitCount", wintypes.WORD),
						("biCompression", wintypes.DWORD),
						("biSizeImage", wintypes.DWORD),
						("biXPelsPerMeter", wintypes.LONG),
						("biYPelsPerMeter", wintypes.LONG),
						("biClrUsed", wintypes.DWORD),
						("biClrImportant", wintypes.DWORD),
					]

				bi = _BI()
				bi.biSize = ctypes.sizeof(_BI)
				bi.biWidth = w
				bi.biHeight = -h  # top-down
				bi.biPlanes = 1
				bi.biBitCount = 32
				buf = ctypes.create_string_buffer(w * h * 4)
				if not gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bi), 0):
					return None
				img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
			finally:
				if hbm:
					gdi32.DeleteObject(hbm)
				if hdc_mem:
					gdi32.DeleteDC(hdc_mem)
				user32.ReleaseDC(hwnd, hdc_wnd)

			# A pure-black capture means the DX surface refused GDI, so the caller should fall back to the desktop composite
			small = img.resize((32, 18))
			if max(small.convert("L").getdata()) < 8:
				return None
			return img
		except Exception:
			return None

	def _upload(self, img) -> None:
		'''Upload the downsampled image as the backdrop texture.'''
		self._release()
		self._w, self._h = img.size
		data = img.tobytes()
		tex = gl.glGenTextures(1)
		gl.glBindTexture(gl.GL_TEXTURE_2D, tex)
		gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
		gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
		gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
		gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
		gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA, self._w, self._h, 0, gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, data)
		gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
		self._tex = tex

	def draw(self, draw_list, screen_w: int, screen_h: int, cs2_hwnd: int) -> bool:
		'''Draw the blurred snapshot full-screen. False = no texture yet.'''
		if not cs2_hwnd:
			return False
		now = perf_counter()
		if now >= self._next_refresh:
			self._next_refresh = now + 1.0
			img = self._capture(cs2_hwnd, screen_w, screen_h)
			if img is not None:
				try:
					self._upload(img)
					self._ok = True
				except Exception:
					self._ok = False
		if not self._ok or not self._tex:
			return False
		draw_list.add_image(self._tex, (0, 0), (screen_w, screen_h), col=imgui.IM_COL32(255, 255, 255, 255))
		return True
