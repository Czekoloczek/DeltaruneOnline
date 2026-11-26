#!/usr/bin/env python3
"""Screenshots of the game window for tools/deepseek_player.py: standard library only.

    python3 tools/screen_capture.py shot.png                  save one screenshot and print its size
    python3 tools/screen_capture.py shot.png --window-pid 1234
    python3 tools/screen_capture.py shot.png --screen-grab    (see below: when PrintWindow gives black)
    python3 tools/screen_capture.py shot.png --cmd "import -window root png:-"

On Windows the game's window is found and captured directly (ctypes, GDI): a GameMaker window (class
YYGameMakerYY) whose title contains --window (default DELTARUNE), or the one of process --window-pid. Its client
area is captured with PrintWindow (which asks the game's window for its own pixels, so windows over it are never in
the picture), cut to the 4:3 picture in the middle when the window is wider or taller (fullscreen), and scaled
with nearest neighbour to fit 640x480, the game's own resolution, so pixel art stays sharp.

If PrintWindow gives a black picture (some GPU-drawn windows do), there is no screenshot, unless --screen-grab is
given: then the game's part of the screen is copied instead. That copy would hold any window over the game (a chat,
a browser) and the picture is sent to DeepSeek, so it is taken only when no other visible window overlaps the
game; otherwise that turn goes without a picture and says which kind of window (its class) is in the way.

On other systems a command does the capture (--cmd): it must print one PNG, JPEG, GIF or WebP image on its
standard output; it is sent as it is, so let it crop and scale. It has 10 s; then it and everything it started
are stopped. Under Proton on X11 or XWayland, for example:

    import -window "$(xdotool search --name '^DELTARUNE$' | head -1)" -resize 640x480 png:-

Nothing here touches the game: it only reads pixels of the screen, so the lockstep is not affected.
"""
import argparse
import os
import signal
import struct
import subprocess
import sys
import zlib

MAX_W, MAX_H = 640, 480         # the game's own resolution
GM_CLASS = "YYGameMakerYY"      # the window class of GameMaker games
FORMATS = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"),
           (b"GIF89a", "image/gif"))


class CaptureError(Exception):
    """No screenshot this time; the message says why."""


class Shot:
    """One screenshot: the encoded image, its type and size."""

    def __init__(self, data, mime, width, height):
        self.data, self.mime, self.width, self.height = data, mime, width, height

    def describe(self):
        return "%dx%d %s, %d bytes" % (self.width, self.height, self.mime.split("/")[1].upper(), len(self.data))


# ---------------------------------------------------------------------------------------------- images


def png(width, height, rgb):
    """RGB bytes (rows top to bottom, 3 bytes a pixel) as a PNG file."""
    if len(rgb) != width * height * 3:
        raise ValueError("expected %d bytes of RGB, got %d" % (width * height * 3, len(rgb)))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)

    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def image_info(data):
    """(MIME type, width, height) of a PNG, JPEG, GIF or WebP file; width and height are 0 when not read.
    Raises CaptureError for anything else."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", 0, 0
    for magic, mime in FORMATS:
        if data.startswith(magic):
            if mime == "image/png" and len(data) >= 24:
                w, h = struct.unpack(">II", data[16:24])
                return mime, w, h
            if mime == "image/gif" and len(data) >= 10:
                w, h = struct.unpack("<HH", data[6:10])
                return mime, w, h
            return mime, 0, 0
    raise CaptureError("not a PNG, JPEG, GIF or WebP image (%d bytes)" % len(data))


def game_area(w, h):
    """(x, y, w, h) of the 4:3 picture in the middle of a w x h client area (bars at the sides or top)."""
    if w * 3 > h * 4:
        gw = h * 4 // 3
        return (w - gw) // 2, 0, gw, h
    if w * 3 < h * 4:
        gh = w * 3 // 4
        return 0, (h - gh) // 2, w, gh
    return 0, 0, w, h


def fit(w, h, max_w=MAX_W, max_h=MAX_H):
    """The size w x h scaled to fit max_w x max_h, keeping its shape (never larger than it is)."""
    scale = min(max_w / w, max_h / h, 1.0)
    return max(1, round(w * scale)), max(1, round(h * scale))


def bgra_to_rgb(src, stride, area, size):
    """Cut `area` (x, y, w, h) out of top-down BGRA rows of `stride` bytes and scale it to `size` with nearest
    neighbour; returns RGB bytes."""
    ax, ay, aw, ah = area
    dw, dh = size
    xs = [(ax + (x * aw) // dw) * 4 for x in range(dw)]
    out = bytearray()
    for y in range(dh):
        off = (ay + (y * ah) // dh) * stride
        row = src[off:off + stride]
        bgr = bytearray(b"".join(row[i:i + 3] for i in xs))
        bgr[0::3], bgr[2::3] = bgr[2::3], bgr[0::3]
        out += bgr
    return bytes(out)


# ---------------------------------------------------------------------------------------------- capture


class CommandCapture:
    """A command that prints one image on its standard output."""

    def __init__(self, cmd, timeout=10):
        self.cmd, self.timeout = cmd, timeout
        self.what = "command"

    def __call__(self):
        # its own process group, so that a timeout stops the shell and everything it started: a child left
        # running would hold the output pipes open and the wait for them would not end
        kw = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
              else {"start_new_session": True})
        try:
            p = subprocess.Popen(self.cmd, shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, **kw)
        except OSError as e:
            raise CaptureError("the screenshot command failed: %s" % e)
        try:
            out, err = p.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            stop_tree(p)
            raise CaptureError("the screenshot command took more than %d s (stopped)" % self.timeout)
        if p.returncode != 0 or not out:
            lines = err.decode("utf-8", "replace").strip().splitlines()
            raise CaptureError("the screenshot command failed (exit %d)%s" % (
                p.returncode, ": " + lines[-1][:200] if lines else ""))
        mime, w, h = image_info(out)
        return Shot(out, mime, w, h)


def stop_tree(p):
    """Stops a process started in its own group and everything it started, then waits a little for it."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.communicate(timeout=2)
    except (subprocess.TimeoutExpired, ValueError):
        for f in (p.stdout, p.stderr):
            try:
                f.close()
            except OSError:
                pass


class WindowsCapture:
    """The game's window on Windows, through GDI (ctypes). The window is looked up again at every capture, so a
    chapter change (a new game process with a new window) is followed."""

    def __init__(self, title="DELTARUNE", pid=None, screen=False):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.title, self.pid = title, pid
        self.screen = screen    # --screen-grab: copy from the screen when PrintWindow gives black
        self.what = "window"
        self.note = ""          # set when several windows match
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        u, g, vp = self.user32, self.gdi32, ctypes.c_void_p
        for f, args, res in ((u.GetDC, [vp], vp), (u.ReleaseDC, [vp, vp], ctypes.c_int),
                             (u.PrintWindow, [vp, vp, ctypes.c_uint], wintypes.BOOL),
                             (u.GetClientRect, [vp, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
                             (u.ClientToScreen, [vp, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
                             (u.IsWindowVisible, [vp], wintypes.BOOL), (u.IsIconic, [vp], wintypes.BOOL),
                             (u.GetWindowTextW, [vp, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                             (u.GetClassNameW, [vp, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                             (u.GetWindowThreadProcessId, [vp, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
                             (u.GetWindow, [vp, ctypes.c_uint], vp),
                             (u.GetWindowRect, [vp, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
                             (g.CreateCompatibleDC, [vp], vp), (g.CreateCompatibleBitmap, [vp, ctypes.c_int,
                                                                                         ctypes.c_int], vp),
                             (g.SelectObject, [vp, vp], vp), (g.DeleteObject, [vp], wintypes.BOOL),
                             (g.DeleteDC, [vp], wintypes.BOOL),
                             (g.BitBlt, [vp] + [ctypes.c_int] * 4 + [vp, ctypes.c_int, ctypes.c_int, wintypes.DWORD],
                              wintypes.BOOL),
                             (g.GetDIBits, [vp, vp, ctypes.c_uint, ctypes.c_uint, vp, vp, ctypes.c_uint],
                              ctypes.c_int)):
            f.argtypes, f.restype = args, res
        try:
            self.dwmapi = ctypes.WinDLL("dwmapi")
            self.dwmapi.DwmGetWindowAttribute.argtypes = [vp, wintypes.DWORD, vp, wintypes.DWORD]
            self.dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        except (AttributeError, OSError):
            self.dwmapi = None
        # physical pixels, not the scaled ones of a high-DPI display
        try:
            u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try:
                u.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass

    def windows(self):
        """[(hwnd, pid, title)] of the visible GameMaker windows that match."""
        ctypes, wintypes, u = self.ctypes, self.wintypes, self.user32
        found = []
        proc = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.c_void_p)

        def each(hwnd, _):
            if not u.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, buf, 256)
            if buf.value != GM_CLASS:
                return True
            u.GetWindowTextW(hwnd, buf, 256)
            title = buf.value
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self.pid is not None and pid.value != self.pid:
                return True
            if self.title and self.title.lower() not in title.lower():
                return True
            found.append((hwnd, pid.value, title))
            return True

        u.EnumWindows(proc(each), None)
        return found

    def window(self):
        found = self.windows()
        if not found:
            raise CaptureError("no game window found (a GameMaker window titled %r%s)" % (
                self.title, ", process %d" % self.pid if self.pid is not None else ""))
        if len(found) > 1 and self.pid is None:
            # two copies on one PC: say which, and take the first (--window-pid picks one)
            self.note = "%d game windows (processes %s); using %d, --window-pid picks another" % (
                len(found), ", ".join(str(f[1]) for f in found), found[0][1])
        else:
            self.note = ""
        return found[0][0]

    def covered_by(self, hwnd, w, h):
        """The class name of a visible window of another process that lies over part of the game's client area
        (w x h), or None. Goes up the z-order from the game's window: everything above it, topmost ones too."""
        ctypes, wintypes, u = self.ctypes, self.wintypes, self.user32
        pt = wintypes.POINT(0, 0)
        u.ClientToScreen(hwnd, ctypes.byref(pt))
        game = (pt.x, pt.y, pt.x + w, pt.y + h)
        own = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(own))
        other = u.GetWindow(hwnd, 3)            # GW_HWNDPREV: the window just above
        for _ in range(100000):
            if not other:
                return None
            pid, r = wintypes.DWORD(), wintypes.RECT()
            u.GetWindowThreadProcessId(other, ctypes.byref(pid))
            if (pid.value != own.value and u.IsWindowVisible(other) and not u.IsIconic(other)
                    and not self.cloaked(other) and u.GetWindowRect(other, ctypes.byref(r))
                    and r.left < game[2] and r.right > game[0] and r.top < game[3] and r.bottom > game[1]):
                buf = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(other, buf, 256)
                return buf.value or "?"
            other = u.GetWindow(other, 3)
        return "?"                              # a z-order this long is not walked to its end: count it covered

    def cloaked(self, hwnd):
        """Whether the window is hidden by the system although marked visible (another virtual desktop, a
        suspended app)."""
        if self.dwmapi is None:
            return False
        value = self.wintypes.DWORD()
        ok = self.dwmapi.DwmGetWindowAttribute(hwnd, 14, self.ctypes.byref(value), 4) == 0     # DWMWA_CLOAKED
        return ok and value.value != 0

    def grab(self, hwnd, w, h, from_screen):
        ctypes, wintypes, u, g = self.ctypes, self.wintypes, self.user32, self.gdi32
        src = u.GetDC(None if from_screen else hwnd)
        if not src:
            raise CaptureError("GetDC failed")
        mem = bmp = old = None
        try:
            mem = g.CreateCompatibleDC(src)
            bmp = g.CreateCompatibleBitmap(src, w, h)
            if not mem or not bmp:
                raise CaptureError("could not make a %dx%d bitmap" % (w, h))
            old = g.SelectObject(mem, bmp)
            if from_screen:
                pt = wintypes.POINT(0, 0)
                u.ClientToScreen(hwnd, ctypes.byref(pt))
                ok = g.BitBlt(mem, 0, 0, w, h, src, pt.x, pt.y, 0x00CC0020)      # SRCCOPY
            else:
                ok = u.PrintWindow(hwnd, mem, 3)        # PW_CLIENTONLY | PW_RENDERFULLCONTENT
            g.SelectObject(mem, old)
            old = None
            if not ok:
                return None

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                            ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                            ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

            info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            # room for the colour table that BITMAPINFO may have; none is used at 32 bits
            holder = ctypes.create_string_buffer(ctypes.sizeof(BITMAPINFOHEADER) + 16)
            ctypes.memmove(holder, ctypes.byref(info), ctypes.sizeof(BITMAPINFOHEADER))
            if g.GetDIBits(mem, bmp, 0, h, buf, holder, 0) != h:
                raise CaptureError("GetDIBits failed")
            return buf.raw
        finally:
            if old:
                g.SelectObject(mem, old)
            if bmp:
                g.DeleteObject(bmp)
            if mem:
                g.DeleteDC(mem)
            u.ReleaseDC(None if from_screen else hwnd, src)

    def __call__(self):
        try:
            return self.shot()
        except CaptureError:
            raise
        except Exception as e:      # any failure of the Windows calls costs a screenshot, never the run
            raise CaptureError("the capture failed: %r" % e)

    def shot(self):
        ctypes, wintypes, u = self.ctypes, self.wintypes, self.user32
        hwnd = self.window()
        if u.IsIconic(hwnd):
            raise CaptureError("the game window is minimized")
        rect = wintypes.RECT()
        u.GetClientRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            raise CaptureError("the game window has no size")
        raw = self.grab(hwnd, w, h, False)
        # PrintWindow can give black for a window drawn by the GPU. Copying the game's part of the screen instead
        # would take whatever window lies over the game, and the picture leaves the PC: so only with
        # --screen-grab, and only while no other window overlaps the game.
        if raw is None or not any(raw[i:i + 3] != b"\x00\x00\x00" for i in range(0, len(raw), 4 * 997)):
            if not self.screen:
                raise CaptureError("PrintWindow gave %s picture of the game window; --screen-grab copies the "
                                   "game's part of the screen instead (only while no other window overlaps it)"
                                   % ("no" if raw is None else "a black"))
            cover = self.covered_by(hwnd, w, h)
            if cover is not None:
                raise CaptureError("PrintWindow gave a black picture and another window (class %s) overlaps the "
                                   "game, so the screen is not copied" % cover)
            raw = self.grab(hwnd, w, h, True)
            if raw is None:
                raise CaptureError("the game window could not be captured")
        area = game_area(w, h)
        size = fit(area[2], area[3])
        return Shot(png(size[0], size[1], bgra_to_rgb(raw, w * 4, area, size)), "image/png", size[0], size[1])


def make_capture(cmd=None, title="DELTARUNE", pid=None, screen=False):
    """The capture to use: the command if one is given, else the window on Windows (screen: --screen-grab).
    Raises CaptureError when neither is possible here."""
    if cmd:
        return CommandCapture(cmd)
    if os.name == "nt":
        try:
            return WindowsCapture(title, pid, screen)
        except Exception as e:
            raise CaptureError("cannot capture windows here: %r" % e)
    raise CaptureError("screenshots need --screenshot-cmd on this system (built in only on Windows)")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", help="the file to write")
    ap.add_argument("--cmd", default=None, help="a command that prints an image (instead of the window)")
    ap.add_argument("--window", default="DELTARUNE", help="part of the game window's title")
    ap.add_argument("--window-pid", type=int, default=None, help="the game's process id")
    ap.add_argument("--screen-grab", action="store_true",
                    help="when PrintWindow gives black, copy the game's part of the screen (only if nothing covers it)")
    args = ap.parse_args(argv)
    try:
        shot = make_capture(args.cmd, args.window, args.window_pid, args.screen_grab)()
    except CaptureError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    with open(args.out, "wb") as f:
        f.write(shot.data)
    print("%s: %s" % (args.out, shot.describe()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
