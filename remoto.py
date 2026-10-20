#!/usr/bin/env python3
"""
Remoto GF
Controle o PC pelo celular: mouse, teclado, volume, mixer por app, janelas abertas,
tela ao vivo, energia e área de transferência. O celular abre uma página pelo Wi-Fi,
não precisa instalar nada nele.

Uso:  python remoto.py              (abre a janela com QR code)
      python remoto.py --minimizado (inicia escondido na bandeja)
      python remoto.py --sem-janela (só console)
      python remoto.py --admin      (reabre como administrador)
"""
import asyncio
import ctypes
import io
import json
import os
import platform
import queue
import secrets
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import web, WSMsgType

APP_NAME = "Remoto GF"
VERSION = "1.0"
IS_WIN = sys.platform == "win32"
FROZEN = getattr(sys, "frozen", False)

# ------------------------------------------------------------------ caminhos e config

def resource_dir() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


WEB_DIR = resource_dir() / "web"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") if IS_WIN else os.path.expanduser("~/.config")
    p = Path(base or ".") / "RemotoGF"
    p.mkdir(parents=True, exist_ok=True)
    return p


def files_dir() -> Path:
    p = Path.home() / "Downloads" / "Remoto GF"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = config_dir() / "Arquivos"
        p.mkdir(parents=True, exist_ok=True)
    return p


CONFIG_PATH = config_dir() / "config.json"

DEFAULT_APPS = [
    {"nome": "DaVinci Resolve", "abrir": r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"},
    {"nome": "Navegador", "abrir": "https://www.google.com"},
    {"nome": "YouTube", "abrir": "https://www.youtube.com"},
    {"nome": "Spotify", "abrir": "spotify:"},
    {"nome": "Explorador", "abrir": "explorer.exe"},
    {"nome": "Configurações", "abrir": "ms-settings:"},
    {"nome": "Calculadora", "abrir": "calc.exe"},
    {"nome": "Bloco de notas", "abrir": "notepad.exe"},
]


def load_config() -> dict:
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    changed = False
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(24)
        changed = True
    if "porta" not in cfg:
        cfg["porta"] = 8765
        changed = True
    if "apps" not in cfg:
        cfg["apps"] = DEFAULT_APPS
        changed = True
    if changed:
        save_config(cfg)
    return cfg


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def log(*a):
    try:
        print(time.strftime("[%H:%M:%S]"), *a, flush=True)
    except Exception:
        pass


# ------------------------------------------------------------------ Win32 (ctypes)

if IS_WIN:
    from ctypes import wintypes

    # coordenadas em pixels reais, iguais às da captura de tela
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except Exception:
        dwmapi = None

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]


def is_admin() -> bool:
    if not IS_WIN:
        try:
            return os.geteuid() == 0
        except Exception:
            return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    """Reabre o programa pedindo permissão de administrador (aparece o UAC no PC)."""
    if not IS_WIN:
        return False
    args = [a for a in sys.argv[1:] if a != "--admin"]
    if not FROZEN:
        args = [str(Path(__file__).resolve())] + args
    params = " ".join(f'"{a}"' for a in args)
    exe = sys.executable
    pyw = Path(exe).with_name("pythonw.exe")
    if not FROZEN and pyw.exists() and "--sem-janela" not in args:
        exe = str(pyw)
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, str(resource_dir()), 1)
    return r > 32


def cursor_pos():
    if IS_WIN:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    return (0, 0)


def process_elevated(pid: int) -> bool:
    """True se o processo roda como administrador (o Windows bloqueia controle remoto nele)."""
    if not IS_WIN or not pid:
        return False
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return True
    tok = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
            return True
        elev = wintypes.DWORD()
        ret = wintypes.DWORD()
        ok = advapi32.GetTokenInformation(tok, 20, ctypes.byref(elev), 4, ctypes.byref(ret))  # TokenElevation
        kernel32.CloseHandle(tok)
        return bool(elev.value) if ok else False
    finally:
        kernel32.CloseHandle(h)


# ------------------------------------------------------------------ entrada (mouse e teclado)

KEY_ALIASES = {
    "win": "cmd", "windows": "cmd", "super": "cmd", "control": "ctrl", "del": "delete",
    "pgup": "page_up", "pgdn": "page_down", "pageup": "page_up", "pagedown": "page_down",
    "return": "enter", "escape": "esc", "bksp": "backspace", "prtsc": "print_screen",
    "printscreen": "print_screen", "plus": "+", "minus": "-", "espaco": "space", "espaço": "space",
    "capslock": "caps_lock", "ins": "insert",
}

MEDIA_KEYS = {
    "play_pause": "media_play_pause", "next": "media_next", "prev": "media_previous",
    "vol_up": "media_volume_up", "vol_down": "media_volume_down", "mute": "media_volume_mute",
}


class Input:
    def __init__(self):
        self.ok = False
        self.err = None
        try:
            from pynput.mouse import Controller as MouseController, Button
            from pynput.keyboard import Controller as KeyController, Key, KeyCode
            self.m = MouseController()
            self.k = KeyController()
            self.Button, self.Key, self.KeyCode = Button, Key, KeyCode
            self.ok = True
        except Exception as e:  # sem tela (servidor de teste) ou pynput ausente
            self.err = str(e)
        self.ax = self.ay = 0.0
        self.sx = self.sy = 0.0
        self.screen = None

    # mouse
    def move(self, dx, dy):
        self.ax += dx
        self.ay += dy
        ix, iy = int(self.ax), int(self.ay)
        if not ix and not iy:
            return
        self.ax -= ix
        self.ay -= iy
        if IS_WIN:
            user32.mouse_event(0x0001, ix & 0xFFFFFFFF, iy & 0xFFFFFFFF, 0, 0)
        elif self.ok:
            self.m.move(ix, iy)

    def scroll(self, sx, sy):
        if IS_WIN:
            # rolagem suave: 120 = um "clique" da rodinha
            self.sy += sy * 3
            self.sx += sx * 3
            wy, wx = int(self.sy), int(self.sx)
            self.sy -= wy
            self.sx -= wx
            if wy:
                user32.mouse_event(0x0800, 0, 0, wy & 0xFFFFFFFF, 0)
            if wx:
                user32.mouse_event(0x01000, 0, 0, wx & 0xFFFFFFFF, 0)
        elif self.ok:
            self.sy += sy / 40
            self.sx += sx / 40
            wy, wx = int(self.sy), int(self.sx)
            self.sy -= wy
            self.sx -= wx
            if wy or wx:
                self.m.scroll(wx, wy)

    def zoom(self, d):
        """Pinça no touchpad: Ctrl + rodinha."""
        self.zacc = getattr(self, "zacc", 0.0) + d * 40
        w = int(self.zacc)
        if not w:
            return
        self.zacc -= w
        if IS_WIN:
            user32.keybd_event(0x11, 0, 0, 0)
            user32.mouse_event(0x0800, 0, 0, w & 0xFFFFFFFF, 0)
            user32.keybd_event(0x11, 0, 2, 0)
        elif self.ok:
            with self.k.pressed(self.Key.ctrl):
                self.m.scroll(0, 1 if w > 0 else -1)

    def _btn(self, b):
        return {"left": self.Button.left, "right": self.Button.right,
                "middle": self.Button.middle}.get(b, self.Button.left)

    def click(self, b="left", n=1):
        if self.ok:
            self.m.click(self._btn(b), max(1, min(int(n), 3)))

    def press(self, b="left"):
        if self.ok:
            self.m.press(self._btn(b))

    def release(self, b="left"):
        if self.ok:
            self.m.release(self._btn(b))

    def abs_point(self, mon_idx, x, y, action=None):
        mons = (self.screen.monitors if self.screen else []) or (self.screen.list() if self.screen else [])
        if not mons:
            return
        mon = mons[max(0, min(len(mons) - 1, int(mon_idx) - 1))]
        px = int(mon["left"] + max(0.0, min(1.0, x)) * (mon["width"] - 1))
        py = int(mon["top"] + max(0.0, min(1.0, y)) * (mon["height"] - 1))
        if IS_WIN:
            user32.SetCursorPos(px, py)
        elif self.ok:
            self.m.position = (px, py)
        if action == "left":
            self.click("left")
        elif action == "right":
            self.click("right")
        elif action == "double":
            self.click("left", 2)

    # teclado
    def resolve(self, name):
        n = name if len(name) == 1 else name.strip().lower()
        n = KEY_ALIASES.get(n, n)
        if len(n) == 1:
            return n.lower() if n.isalpha() else n
        if n == "media_stop":
            return self.KeyCode.from_vk(0xB2)
        k = getattr(self.Key, n, None)
        if k is None:
            raise ValueError(f"Tecla desconhecida: {name}")
        return k

    def tap(self, name):
        if self.ok:
            self.k.tap(self.resolve(name))

    def combo(self, names):
        if not self.ok or not names:
            return
        keys = [self.resolve(n) for n in names][:6]
        pressed = []
        try:
            for k in keys:
                self.k.press(k)
                pressed.append(k)
                time.sleep(0.015)
        finally:
            for k in reversed(pressed):
                self.k.release(k)
                time.sleep(0.01)

    def type(self, text):
        if self.ok and text:
            self.k.type(text)

    def backspace(self, n):
        if self.ok:
            for _ in range(n):
                self.k.tap(self.Key.backspace)

    def media(self, action):
        if action == "stop":
            self.tap("media_stop")
        elif action in MEDIA_KEYS:
            self.tap(MEDIA_KEYS[action])


# ------------------------------------------------------------------ áudio

class Volume:
    def __init__(self):
        self.ok = False
        self._fake = [0.5, False]
        if IS_WIN:
            try:
                import pycaw.pycaw  # noqa: F401
                self.ok = True
            except Exception as e:
                log("pycaw indisponível:", e)

    def _ep(self):
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()
        if hasattr(dev, "EndpointVolume"):
            return dev.EndpointVolume
        from comtypes import CLSCTX_ALL
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return ctypes.cast(iface, ctypes.POINTER(IAudioEndpointVolume))

    def get(self):
        if not self.ok:
            return {"v": self._fake[0], "m": self._fake[1]}
        ep = self._ep()
        return {"v": round(float(ep.GetMasterVolumeLevelScalar()), 3), "m": bool(ep.GetMute())}

    def set(self, v):
        v = max(0.0, min(1.0, float(v)))
        if not self.ok:
            self._fake[0] = v
            return
        ep = self._ep()
        ep.SetMasterVolumeLevelScalar(v, None)
        if v > 0 and ep.GetMute():
            ep.SetMute(0, None)

    def step(self, d):
        self.set(self.get()["v"] + float(d))

    def mute(self, m=None):
        if not self.ok:
            self._fake[1] = (not self._fake[1]) if m is None else bool(m)
            return
        ep = self._ep()
        ep.SetMute(int((not ep.GetMute()) if m is None else bool(m)), None)

    def sessions(self):
        if not self.ok:
            return []
        from pycaw.pycaw import AudioUtilities
        out, seen = [], set()
        for s in AudioUtilities.GetAllSessions():
            try:
                if not s.Process or s.ProcessId in seen:
                    continue
                seen.add(s.ProcessId)
                sv = s.SimpleAudioVolume
                out.append({"pid": s.ProcessId, "nome": friendly_name(s.Process.name()),
                            "v": round(float(sv.GetMasterVolume()), 3), "m": bool(sv.GetMute())})
            except Exception:
                continue
        out.sort(key=lambda x: x["nome"].lower())
        return out

    def _session(self, pid):
        from pycaw.pycaw import AudioUtilities
        for s in AudioUtilities.GetAllSessions():
            if s.ProcessId == pid:
                return s
        raise ValueError("Esse app não está mais tocando som")

    def session_set(self, pid, v):
        if self.ok:
            self._session(int(pid)).SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, float(v))), None)

    def session_mute(self, pid, m):
        if self.ok:
            self._session(int(pid)).SimpleAudioVolume.SetMute(int(bool(m)), None)


# ------------------------------------------------------------------ janelas

FRIENDLY = {
    "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "opera": "Opera", "brave": "Brave",
    "resolve": "DaVinci Resolve", "explorer": "Explorador", "code": "VS Code", "spotify": "Spotify",
    "discord": "Discord", "whatsapp": "WhatsApp", "obs64": "OBS Studio", "vlc": "VLC",
    "afterfx": "After Effects", "photoshop": "Photoshop", "adobe premiere pro": "Premiere Pro",
    "winword": "Word", "excel": "Excel", "powerpnt": "PowerPoint", "notepad": "Bloco de notas",
    "taskmgr": "Gerenciador de Tarefas", "steam": "Steam", "telegram": "Telegram",
    "applicationframehost": "App do Windows", "windowsterminal": "Terminal", "cmd": "Prompt",
    "python": "Python", "pythonw": "Python", "systemsettings": "Configurações", "figma": "Figma",
}


def friendly_name(exe: str) -> str:
    base = exe[:-4] if exe.lower().endswith(".exe") else exe
    return FRIENDLY.get(base.lower(), base[:1].upper() + base[1:])


class Windows:
    def __init__(self):
        self.ok = IS_WIN
        self._names = {}
        try:
            import psutil
            self.psutil = psutil
        except Exception:
            self.psutil = None

    def proc_name(self, pid):
        if pid in self._names:
            return self._names[pid]
        name = "?"
        if self.psutil:
            try:
                name = self.psutil.Process(pid).name()
            except Exception:
                pass
        if len(self._names) > 500:
            self._names.clear()
        self._names[pid] = name
        return name

    def list(self):
        if not IS_WIN:
            return []
        out = []
        fg = user32.GetForegroundWindow() or 0

        def cb(hwnd, _):
            try:
                if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
                    return True
                if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
                    return True
                n = user32.GetWindowTextLengthW(hwnd)
                if n == 0:
                    return True
                if dwmapi:
                    cloaked = wintypes.DWORD()
                    dwmapi.DwmGetWindowAttribute(wintypes.HWND(hwnd), 14, ctypes.byref(cloaked), 4)
                    if cloaked.value:
                        return True
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
                if title in ("Program Manager",):
                    return True
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                exe = self.proc_name(pid.value)
                out.append({"h": int(hwnd), "titulo": title, "app": friendly_name(exe), "exe": exe,
                            "pid": pid.value, "min": bool(user32.IsIconic(hwnd)),
                            "max": bool(user32.IsZoomed(hwnd)), "ativo": int(hwnd) == int(fg)})
            except Exception:
                pass
            return True

        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return out

    def foreground(self):
        if not IS_WIN:
            return None
        h = user32.GetForegroundWindow()
        if not h:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        n = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        exe = self.proc_name(pid.value)
        return {"h": int(h), "pid": pid.value, "titulo": buf.value, "app": friendly_name(exe)}

    def _check(self, h):
        if not IS_WIN or not user32.IsWindow(h):
            raise ValueError("Essa janela não existe mais")

    def focus(self, h):
        self._check(h)
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)  # SW_RESTORE
        # truque do Alt: o Windows só deixa trocar o foco se houver "entrada recente"
        user32.keybd_event(0x12, 0, 0, 0)
        user32.SetForegroundWindow(h)
        user32.BringWindowToTop(h)
        user32.keybd_event(0x12, 0, 2, 0)

    def minimize(self, h):
        self._check(h)
        user32.ShowWindow(h, 6)

    def maximize(self, h):
        self._check(h)
        user32.ShowWindow(h, 9 if user32.IsZoomed(h) else 3)
        self.focus(h)

    def close(self, h):
        self._check(h)
        user32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE

    def kill(self, h):
        self._check(h)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if self.psutil and pid.value:
            self.psutil.Process(pid.value).kill()

    def uac_open(self):
        if not self.psutil:
            return False
        try:
            for p in self.psutil.process_iter(["name"]):
                if (p.info.get("name") or "").lower() == "consent.exe":
                    return True
        except Exception:
            pass
        return False


# ------------------------------------------------------------------ tela

class Screen:
    def __init__(self):
        self.ok = False
        self.err = None
        self.monitors = []
        self.local = threading.local()
        try:
            import mss
            from PIL import Image, ImageDraw
            self.mss, self.Image, self.Draw = mss, Image, ImageDraw
            self.ok = True
        except Exception as e:
            self.err = str(e)

    def _sct(self):
        s = getattr(self.local, "s", None)
        if s is None:
            s = self.mss.mss()
            self.local.s = s
        return s

    def list(self):
        if not self.ok:
            return []
        try:
            self.monitors = [dict(m) for m in self._sct().monitors[1:]]
        except Exception as e:
            self.err = str(e)
            self.monitors = []
        return self.monitors

    def grab(self, idx, maxw, quality):
        sct = self._sct()
        mons = sct.monitors
        self.monitors = [dict(m) for m in mons[1:]]
        if idx < 1 or idx >= len(mons):
            idx = 1
        mon = mons[idx]
        shot = sct.grab(mon)
        img = self.Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        cx, cy = cursor_pos()
        rx, ry = cx - mon["left"], cy - mon["top"]
        if 0 <= rx < mon["width"] and 0 <= ry < mon["height"]:
            d = self.Draw.Draw(img)
            r = max(8, mon["width"] // 140)
            d.ellipse([rx - r, ry - r, rx + r, ry + r], outline=(255, 107, 44), width=max(3, r // 3))
        if img.width > maxw:
            img = img.resize((maxw, max(1, int(img.height * maxw / img.width))), self.Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality)
        return buf.getvalue()


# ------------------------------------------------------------------ sistema

class System:
    def __init__(self):
        try:
            import psutil
            self.psutil = psutil
            psutil.cpu_percent(None)
        except Exception:
            self.psutil = None
        self._sbc = None
        self._sbc_tried = False

    def stats(self):
        p = self.psutil
        if not p:
            return {}
        out = {"cpu": p.cpu_percent(None), "ram": p.virtual_memory().percent,
               "uptime": int(time.time() - p.boot_time())}
        try:
            b = p.sensors_battery()
            if b:
                out["bat"] = round(b.percent)
                out["carregando"] = bool(b.power_plugged)
        except Exception:
            pass
        return out

    def _need_win(self):
        if not IS_WIN:
            raise RuntimeError("Disponível só no Windows")

    def power(self, action, minutes=0):
        self._need_win()
        flags = 0x08000000  # sem janela de console
        if action == "lock":
            ctypes.windll.user32.LockWorkStation()
        elif action == "monitor_off":
            ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)
        elif action == "sleep":
            subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=flags)
        elif action == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", "30"], creationflags=flags)
        elif action == "restart":
            subprocess.run(["shutdown", "/r", "/t", "30"], creationflags=flags)
        elif action == "timer":
            secs = max(60, min(int(minutes) * 60, 24 * 3600))
            subprocess.run(["shutdown", "/a"], creationflags=flags)
            subprocess.run(["shutdown", "/s", "/t", str(secs)], creationflags=flags)
        elif action == "cancel":
            subprocess.run(["shutdown", "/a"], creationflags=flags)
        else:
            raise ValueError("Ação desconhecida")

    def clip_set(self, text):
        import pyperclip
        pyperclip.copy(text)

    def clip_get(self):
        import pyperclip
        return pyperclip.paste() or ""

    def _brightness(self):
        if not self._sbc_tried:
            self._sbc_tried = True
            try:
                import screen_brightness_control as sbc
                sbc.get_brightness(display=0)
                self._sbc = sbc
            except Exception:
                self._sbc = None
        return self._sbc

    def bright_get(self):
        sbc = self._brightness()
        if not sbc:
            return None
        v = sbc.get_brightness(display=0)
        return int(v[0] if isinstance(v, list) else v)

    def bright_set(self, v):
        sbc = self._brightness()
        if sbc:
            sbc.set_brightness(max(0, min(100, int(v))), display=0)

    def launch(self, target):
        if IS_WIN:
            os.startfile(target)  # noqa
        else:
            subprocess.Popen(["xdg-open", target])


# ------------------------------------------------------------------ rede

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_cert():
    """Certificado próprio para o modo HTTPS (libera giroscópio e tela ligada no celular)."""
    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except Exception:
        return None
    ip = lan_ip()
    crt, key, tag = config_dir() / "cert.pem", config_dir() / "key.pem", config_dir() / "cert_ip.txt"
    if crt.exists() and key.exists() and tag.exists() and tag.read_text().strip() == ip:
        return crt, key
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Remoto GF")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip)),
                                                        x509.DNSName("localhost")]), critical=False)
            .sign(k, hashes.SHA256()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    tag.write_text(ip)
    return crt, key


def _com_init():
    if IS_WIN:
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass


def _num(d, k, default=0.0):
    try:
        v = float(d.get(k, default))
        return v if v == v else default
    except Exception:
        return default


class Hub:
    def __init__(self, cfg):
        self.cfg = cfg
        self.port = int(cfg.get("porta", 8765))
        self.https_port = None
        self.pin = f"{secrets.randbelow(10 ** 6):06d}"
        self.fails = 0
        self.lock_until = 0.0
        self.clients = {}
        self.phones = {}
        self.phone_cb = None  # a janela do PC registra aqui para saber de celulares
        self.file_cb = None   # a janela do PC registra aqui para avisos de arquivos
        self.files = {}       # id -> {"id", "nome", "path", "tam", "origem", "ts"}
        self.loop = None
        self.admin = is_admin()
        self.dirty = True  # a janela do PC precisa redesenhar
        self._last_vol = None
        self._scan_files_dir()

        self.inp = Input()
        self.scr = Screen()
        self.inp.screen = self.scr
        self.vol = Volume()
        self.win = Windows()
        self.sys = System()

        self.in_exec = ThreadPoolExecutor(1, thread_name_prefix="entrada")
        self.sys_exec = ThreadPoolExecutor(1, initializer=_com_init, thread_name_prefix="sistema")
        self.scr_exec = ThreadPoolExecutor(2, thread_name_prefix="tela")

    # -------- util
    def base_url(self):
        return f"http://{lan_ip()}:{self.port}"

    def pair_url(self):
        return f"{self.base_url()}/#t={self.cfg['token']}"

    def token_ok(self, t):
        return bool(t) and secrets.compare_digest(str(t), self.cfg["token"])

    async def run_sys(self, fn, *a):
        return await self.loop.run_in_executor(self.sys_exec, fn, *a)

    def queue_input(self, fn, *a):
        def job():
            try:
                fn(*a)
            except Exception as e:
                log("erro de entrada:", e)
        self.in_exec.submit(job)

    async def send(self, ws, msg):
        try:
            await ws.send_str(json.dumps(msg, ensure_ascii=False))
        except Exception:
            pass

    async def broadcast(self, msg, exclude=None):
        for ws in list(self.clients):
            if ws is not exclude:
                await self.send(ws, msg)

    async def notify(self, title, msg, level="info", actions=None, nid=None, sticky=False, exclude=None):
        await self.broadcast({"t": "notify", "title": title, "msg": msg, "level": level,
                              "actions": actions or [], "id": nid, "sticky": sticky}, exclude)

    def features(self):
        return {"entrada": self.inp.ok, "volume": self.vol.ok, "tela": self.scr.ok,
                "janelas": self.win.ok, "windows": IS_WIN, "arquivos": True}

    # -------- arquivos entre PC e celular
    def _scan_files_dir(self):
        try:
            for p in sorted(files_dir().iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
                if p.is_file():
                    fid = secrets.token_hex(6)
                    st = p.stat()
                    self.files[fid] = {"id": fid, "nome": p.name, "path": p,
                                       "tam": st.st_size, "origem": "Pasta Remoto GF", "ts": int(st.st_mtime)}
        except Exception:
            pass

    def files_list(self):
        out = []
        for fid, f in list(self.files.items()):
            p = f["path"]
            if not p.exists():
                self.files.pop(fid, None)
                continue
            out.append({"id": fid, "nome": f["nome"], "tam": f["tam"],
                        "origem": f["origem"], "ts": f["ts"]})
        out.sort(key=lambda x: x["ts"], reverse=True)
        return out

    def share_pc_files(self, paths):
        added = []
        for raw in paths:
            p = Path(raw)
            if not p.is_file():
                continue
            fid = secrets.token_hex(6)
            st = p.stat()
            item = {"id": fid, "nome": p.name, "path": p,
                    "tam": st.st_size, "origem": "PC", "ts": int(time.time())}
            self.files[fid] = item
            added.append(item)
        if added and self.loop:
            nomes = ", ".join(a["nome"] for a in added[:3])
            if len(added) > 3:
                nomes += f" (+{len(added) - 3})"
            asyncio.run_coroutine_threadsafe(self._announce_pc_files(nomes, added[0]["id"]), self.loop)
        return len(added)

    async def _announce_pc_files(self, nomes, first_id):
        await self.broadcast({"t": "files", "list": self.files_list(), "pasta": str(files_dir())})
        await self.notify("Arquivo enviado pelo PC", f"{nomes} disponível na aba Arquivos.", "ok",
                          actions=[{"label": "Ver arquivos", "send": {"t": "files_get"}}])

    async def api_files(self, req):
        if not self.token_ok(req.query.get("t")):
            raise web.HTTPForbidden()
        return web.json_response({"list": self.files_list(), "pasta": str(files_dir())})

    async def api_upload(self, req):
        if not self.token_ok(req.query.get("t")):
            raise web.HTTPForbidden()
        sender = (req.query.get("n") or "Celular")[:40]
        reader = await req.multipart()
        saved = []
        dest_dir = files_dir()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name != "file" or not part.filename:
                continue
            safe_name = Path(part.filename).name.strip() or f"arquivo_{int(time.time())}"
            target = dest_dir / safe_name
            stem, suf = target.stem, target.suffix
            idx = 1
            while target.exists():
                target = dest_dir / f"{stem} ({idx}){suf}"
                idx += 1
            size = 0
            with target.open("wb") as f:
                while True:
                    chunk = await part.read_chunk(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
            fid = secrets.token_hex(6)
            item = {"id": fid, "nome": target.name, "path": target,
                    "tam": size, "origem": sender, "ts": int(time.time())}
            self.files[fid] = item
            saved.append(item)
            log(f"Arquivo recebido de {sender}: {target.name} ({size} bytes)")
        if saved:
            await self.broadcast({"t": "files", "list": self.files_list(), "pasta": str(dest_dir)})
            if self.file_cb:
                try:
                    self.file_cb(saved)
                except Exception:
                    pass
        return web.json_response({"ok": True, "recebidos": len(saved), "list": self.files_list()})

    async def api_file_download(self, req):
        if not self.token_ok(req.query.get("t")):
            raise web.HTTPForbidden()
        fid = req.match_info.get("id", "")
        item = self.files.get(fid)
        if not item or not item["path"].exists():
            raise web.HTTPNotFound(text="Arquivo não encontrado")
        p = item["path"]
        from urllib.parse import quote
        disp = f"attachment; filename=\"{p.name.encode('ascii', 'ignore').decode() or 'arquivo'}\"; filename*=UTF-8''{quote(p.name)}"
        return web.FileResponse(p, headers={"Content-Disposition": disp, "Cache-Control": "no-store"})

    # -------- descoberta: o app do celular pergunta "REMOTO_GF?" por broadcast UDP
    def discovery_thread(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", self.port))
        except OSError as e:
            log("Busca automática indisponível:", e)
            return
        resp = json.dumps({"app": "RemotoGF", "nome": platform.node(), "porta": self.port,
                           "versao": VERSION}).encode()
        while True:
            try:
                data, addr = sock.recvfrom(256)
                if data.startswith(b"REMOTO_GF?"):
                    sock.sendto(resp, addr)
            except Exception:
                time.sleep(0.5)

    # -------- HTTP
    @web.middleware
    async def cors(self, req, handler):
        if req.method == "OPTIONS":
            resp = web.Response()
        else:
            resp = await handler(req)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    async def index(self, req):
        return web.FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def pair(self, req):
        now = time.time()
        if now < self.lock_until:
            return web.json_response({"erro": "Muitas tentativas erradas. Espere 1 minuto."}, status=429)
        try:
            data = await req.json()
        except Exception:
            data = {}
        pin = "".join(ch for ch in str(data.get("pin", "")) if ch.isdigit())
        name = str(data.get("nome") or "Celular")[:40]
        if pin and secrets.compare_digest(pin, self.pin):
            self.fails = 0
            log(f"Aparelho pareado: {name} ({req.remote})")
            await self.notify("Novo aparelho", f"{name} foi conectado a este PC.", "info")
            return web.json_response({"token": self.cfg["token"]})
        self.fails += 1
        log(f"PIN errado vindo de {req.remote}")
        if self.fails >= 5:
            self.fails = 0
            self.lock_until = now + 60
            await self.notify("Tentativa de acesso", f"Alguém em {req.remote} errou o PIN 5 vezes. "
                              "O pareamento ficou bloqueado por 1 minuto.", "warn", sticky=True)
        return web.json_response({"erro": "PIN incorreto. Confira o número na janela do PC."}, status=403)

    async def screen(self, req):
        if not self.token_ok(req.query.get("t")):
            raise web.HTTPForbidden()
        if not self.scr.ok:
            return web.Response(status=503, text="Captura de tela indisponível")
        try:
            m = int(req.query.get("m", 1))
            w = max(320, min(int(req.query.get("w", 1100)), 2560))
            q = max(25, min(int(req.query.get("q", 60)), 92))
        except ValueError:
            raise web.HTTPBadRequest()
        try:
            data = await self.loop.run_in_executor(self.scr_exec, self.scr.grab, m, w, q)
        except Exception as e:
            return web.Response(status=500, text=str(e))
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def ws(self, req):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=8 * 1024 * 1024)
        await ws.prepare(req)
        if not self.token_ok(req.query.get("t")):
            await ws.close(code=4001, message=b"nao autorizado")
            return ws
        name = (req.query.get("n") or "Celular")[:40]
        self.clients[ws] = {"nome": name, "ip": req.remote, "desde": time.time()}
        self.dirty = True
        log(f"Conectado: {name} ({req.remote})")
        mons = await self.loop.run_in_executor(self.scr_exec, self.scr.list) if self.scr.ok else []
        await self.send(ws, {
            "t": "hello", "pc": platform.node(), "versao": VERSION, "admin": self.admin,
            "recursos": self.features(), "monitores": len(mons) or 1, "https_port": self.https_port,
            "apps": [a.get("nome", "?") for a in self.cfg.get("apps", [])],
        })
        if not self.inp.ok:
            await self.send(ws, {"t": "notify", "level": "error", "title": "Mouse e teclado indisponíveis",
                                 "msg": f"O PC não conseguiu iniciar o controle: {self.inp.err}", "sticky": True})
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    d = json.loads(msg.data)
                except Exception:
                    continue
                try:
                    await self.dispatch(ws, d)
                except Exception as e:
                    await self.send(ws, {"t": "notify", "level": "error", "title": "Não deu certo", "msg": str(e)})
        finally:
            self.clients.pop(ws, None)
            self.dirty = True
            log(f"Desconectado: {name}")
        return ws

    # -------- celular sendo controlado pelo PC: recebe a tela, envia os toques
    async def ws_phone(self, req):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(req)
        if not self.token_ok(req.query.get("t")):
            await ws.close(code=4001, message=b"nao autorizado")
            return ws
        info = {"nome": (req.query.get("n") or "Celular")[:40], "ip": req.remote,
                "w": int(req.query.get("w", 0) or 0), "h": int(req.query.get("h", 0) or 0),
                "frame": None, "ws": ws, "desde": time.time()}
        self.phones[ws] = info
        log(f"Celular compartilhando a tela: {info['nome']} ({req.remote})")
        if self.phone_cb:
            try:
                self.phone_cb("on", info)
            except Exception:
                pass
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    info["frame"] = msg.data
                elif msg.type == WSMsgType.TEXT:
                    try:
                        info.update(json.loads(msg.data))
                    except Exception:
                        pass
        finally:
            self.phones.pop(ws, None)
            log(f"Celular parou de compartilhar: {info['nome']}")
            if self.phone_cb:
                try:
                    self.phone_cb("off", info)
                except Exception:
                    pass
        return ws

    def phone_send(self, info, obj):
        ws = info.get("ws")
        if ws is not None and self.loop:
            asyncio.run_coroutine_threadsafe(self.send(ws, obj), self.loop)

    # -------- mensagens do celular
    async def dispatch(self, ws, d):
        t = d.get("t")
        I = self.inp
        # entrada: fila única para manter a ordem dos comandos
        if t == "m":
            return self.queue_input(I.move, _num(d, "x"), _num(d, "y"))
        if t == "s":
            return self.queue_input(I.scroll, _num(d, "x"), _num(d, "y"))
        if t == "c":
            return self.queue_input(I.click, str(d.get("b", "left")), int(_num(d, "n", 1)))
        if t == "md":
            return self.queue_input(I.press, str(d.get("b", "left")))
        if t == "mu":
            return self.queue_input(I.release, str(d.get("b", "left")))
        if t == "zoom":
            return self.queue_input(I.zoom, _num(d, "d"))
        if t == "abs":
            return self.queue_input(I.abs_point, int(_num(d, "mon", 1)), _num(d, "x"), _num(d, "y"), d.get("a"))
        if t == "key":
            return self.queue_input(I.tap, str(d.get("k", "")))
        if t == "combo":
            return self.queue_input(I.combo, [str(k) for k in d.get("keys", [])])
        if t == "type":
            return self.queue_input(I.type, str(d.get("text", ""))[:10000])
        if t == "bksp":
            return self.queue_input(I.backspace, max(0, min(int(_num(d, "n", 1)), 500)))
        if t == "media":
            return self.queue_input(I.media, str(d.get("a", "")))

        # volume
        if t in ("vol_get", "vol_set", "vol_step", "vol_mute"):
            if t == "vol_set":
                await self.run_sys(self.vol.set, _num(d, "v", 0.5))
            elif t == "vol_step":
                await self.run_sys(self.vol.step, _num(d, "d"))
            elif t == "vol_mute":
                await self.run_sys(self.vol.mute, d.get("m"))
            v = await self.run_sys(self.vol.get)
            self._last_vol = (v["v"], v["m"])
            return await self.broadcast({"t": "vol", **v})
        if t == "sessions":
            return await self.send(ws, {"t": "sessions", "list": await self.run_sys(self.vol.sessions)})
        if t == "session_set":
            return await self.run_sys(self.vol.session_set, d.get("pid"), _num(d, "v"))
        if t == "session_mute":
            await self.run_sys(self.vol.session_mute, d.get("pid"), bool(d.get("m")))
            return await self.send(ws, {"t": "sessions", "list": await self.run_sys(self.vol.sessions)})
        if t == "bright_get":
            return await self.send(ws, {"t": "bright", "v": await self.run_sys(self.sys.bright_get)})
        if t == "bright_set":
            return await self.run_sys(self.sys.bright_set, _num(d, "v", 50))

        # janelas
        if t == "windows":
            return await self.send(ws, {"t": "windows", "list": await self.run_sys(self.win.list)})
        if t in ("win_focus", "win_min", "win_max", "win_close", "win_kill"):
            fn = {"win_focus": self.win.focus, "win_min": self.win.minimize, "win_max": self.win.maximize,
                  "win_close": self.win.close, "win_kill": self.win.kill}[t]
            await self.run_sys(fn, int(_num(d, "h")))
            await asyncio.sleep(0.35)
            return await self.send(ws, {"t": "windows", "list": await self.run_sys(self.win.list)})

        # abrir coisas
        if t == "launch":
            apps = self.cfg.get("apps", [])
            i = int(_num(d, "i", -1))
            if 0 <= i < len(apps):
                await self.run_sys(self.sys.launch, apps[i]["abrir"])
                return await self.send(ws, {"t": "notify", "level": "ok", "title": "Abrindo",
                                            "msg": apps[i].get("nome", "")})
            return
        if t == "open_url":
            url = str(d.get("url", "")).strip()
            if not url.lower().startswith(("http://", "https://")):
                url = "https://" + url
            await self.run_sys(webbrowser.open, url)
            return await self.send(ws, {"t": "notify", "level": "ok", "title": "Link aberto no PC", "msg": url})

        # energia
        if t == "power":
            a = str(d.get("a", ""))
            await self.run_sys(self.sys.power, a, int(_num(d, "min", 0)))
            if a in ("shutdown", "restart", "timer"):
                quando = "em 30 segundos" if a != "timer" else f"em {int(_num(d, 'min'))} minutos"
                oque = "reiniciar" if a == "restart" else "desligar"
                await self.notify("Agendado", f"O PC vai {oque} {quando}.", "warn", nid="power", sticky=True,
                                  actions=[{"label": "Cancelar", "send": {"t": "power", "a": "cancel"}}])
            elif a == "cancel":
                await self.broadcast({"t": "dismiss", "id": "power"})
                await self.notify("Cancelado", "O desligamento foi cancelado.", "ok")
            return

        # área de transferência
        if t == "clip_set":
            await self.run_sys(self.sys.clip_set, str(d.get("text", "")))
            return await self.send(ws, {"t": "notify", "level": "ok", "title": "Copiado no PC",
                                        "msg": "Agora é só dar Ctrl+V lá."})
        if t == "clip_get":
            return await self.send(ws, {"t": "clip", "text": await self.run_sys(self.sys.clip_get)})

        # arquivos
        if t == "files_get":
            return await self.send(ws, {"t": "files", "list": self.files_list(), "pasta": str(files_dir())})
        if t == "files_open_dir":
            await self.run_sys(self.sys.launch, str(files_dir()))
            return await self.send(ws, {"t": "notify", "level": "ok", "title": "Pasta aberta no PC",
                                        "msg": str(files_dir())})
        if t == "file_del":
            fid = str(d.get("id", ""))
            self.files.pop(fid, None)
            return await self.broadcast({"t": "files", "list": self.files_list(), "pasta": str(files_dir())})

        if t == "stats":
            return await self.send(ws, {"t": "stats", **(await self.run_sys(self.sys.stats))})
        if t == "ping":
            return await self.send(ws, {"t": "pong", "ts": d.get("ts")})

    # -------- vigia: volume, janelas, pedidos de autorização
    def watch_tick(self):
        out = {"vol": None, "fg": None, "uac": False, "wins": None}
        try:
            v = self.vol.get()
            out["vol"] = (v["v"], v["m"])
        except Exception:
            pass
        if IS_WIN:
            try:
                fg = self.win.foreground()
                if fg:
                    fg["elev"] = process_elevated(fg["pid"])
                out["fg"] = fg
                out["uac"] = self.win.uac_open()
                out["wins"] = self.win.list()
            except Exception as e:
                log("vigia:", e)
        return out

    async def watch(self):
        last_wins = None
        uac_prev = False
        warned = set()
        while True:
            await asyncio.sleep(1.5)
            if not self.clients:
                continue
            try:
                st = await self.run_sys(self.watch_tick)
            except Exception:
                continue
            if st["vol"] is not None and st["vol"] != self._last_vol:
                self._last_vol = st["vol"]
                await self.broadcast({"t": "vol", "v": st["vol"][0], "m": st["vol"][1]})
            if st["wins"] is not None:
                sig = [(w["h"], w["titulo"], w["min"], w["ativo"]) for w in st["wins"]]
                if sig != last_wins:
                    last_wins = sig
                    await self.broadcast({"t": "windows", "list": st["wins"]})
            if st["uac"] and not uac_prev:
                await self.notify(
                    "O Windows está pedindo autorização",
                    "Apareceu uma janela de permissão (Controle de Conta de Usuário) no PC. "
                    "Por segurança, o próprio Windows só aceita que ela seja confirmada fisicamente, no PC.",
                    "warn", nid="uac", sticky=True)
            elif not st["uac"] and uac_prev:
                await self.broadcast({"t": "dismiss", "id": "uac"})
            uac_prev = st["uac"]
            fg = st["fg"]
            if fg and fg.get("elev") and not self.admin and fg["h"] not in warned:
                warned.add(fg["h"])
                if len(warned) > 200:
                    warned.clear()
                await self.notify(
                    "Precisa de autorização",
                    f"\"{fg['app']}\" está aberto como administrador, e o Windows bloqueia mouse e teclado "
                    "remotos nele. Para liberar, abra o Remoto GF como administrador (botão na janela do PC "
                    "ou o arquivo iniciar_como_admin.bat).",
                    "warn", nid=f"elev{fg['h']}", sticky=True)

    # -------- ciclo de vida
    async def start(self):
        self.loop = asyncio.get_running_loop()
        app = web.Application(middlewares=[self.cors], client_max_size=1024 * 1024 * 1024)
        app.router.add_route("OPTIONS", "/api/pair", lambda r: web.Response())
        app.router.add_route("OPTIONS", "/api/upload", lambda r: web.Response())
        app.router.add_get("/", self.index)
        app.router.add_get("/ws", self.ws)
        app.router.add_get("/wsphone", self.ws_phone)
        app.router.add_post("/api/pair", self.pair)
        app.router.add_get("/api/screen", self.screen)
        app.router.add_get("/api/files", self.api_files)
        app.router.add_post("/api/upload", self.api_upload)
        app.router.add_get("/api/file/{id}", self.api_file_download)
        app.router.add_static("/", WEB_DIR, show_index=False)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await site.start()
        try:
            pair = await self.loop.run_in_executor(None, ensure_cert)
            if pair:
                import ssl
                ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
                ctx.load_cert_chain(str(pair[0]), str(pair[1]))
                await web.TCPSite(self.runner, "0.0.0.0", self.port + 1, ssl_context=ctx).start()
                self.https_port = self.port + 1
        except Exception as e:
            log("Modo HTTPS indisponível:", e)
        threading.Thread(target=self.discovery_thread, daemon=True, name="descoberta").start()
        self.loop.create_task(self.watch())

    async def kick_all(self):
        for ws in list(self.clients):
            try:
                await ws.close(code=4001, message=b"novo acesso")
            except Exception:
                pass

    def regenerate_token(self):
        self.cfg["token"] = secrets.token_urlsafe(24)
        self.pin = f"{secrets.randbelow(10 ** 6):06d}"
        save_config(self.cfg)
        if self.loop:
            asyncio.run_coroutine_threadsafe(self.kick_all(), self.loop)
        self.dirty = True


# ------------------------------------------------------------------ ícone, QR e início automático

def make_icon(size=64):
    from PIL import Image, ImageDraw
    s = size * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 4, fill=(20, 22, 28, 255))
    # corpo do mouse
    w, h = int(s * 0.42), int(s * 0.62)
    x0, y0 = (s - w) // 2, int(s * 0.19)
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=w // 2, fill=(255, 107, 44, 255))
    d.line([s // 2, y0 + int(h * 0.08), s // 2, y0 + int(h * 0.36)], fill=(20, 22, 28, 255), width=max(2, s // 28))
    d.ellipse([s // 2 - s // 30, y0 + int(h * 0.5), s // 2 + s // 30, y0 + int(h * 0.5) + s // 15],
              fill=(20, 22, 28, 255))
    return im.resize((size, size), Image.LANCZOS)


def make_qr(url, box=7):
    import qrcode
    q = qrcode.QRCode(border=2, box_size=box, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(url)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").convert("RGB")


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_cmd():
    if FROZEN:
        return f'"{sys.executable}" --minimizado'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return f'"{exe}" "{Path(__file__).resolve()}" --minimizado'


def get_autostart():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "RemotoGF")
            return True
    except OSError:
        return False


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "RemotoGF", 0, winreg.REG_SZ, autostart_cmd())
        else:
            try:
                winreg.DeleteValue(k, "RemotoGF")
            except OSError:
                pass


# ------------------------------------------------------------------ janela do PC

def open_phone_window(hub, root, info):
    """Janela no PC que mostra a tela do celular (fluida, redimensionável e com tela cheia) e manda os toques de volta."""
    import tkinter as tk
    from tkinter import filedialog
    from PIL import Image, ImageTk

    BG, FG, ACC, CARD = "#0e1014", "#e9ecf1", "#ff6b2c", "#1c2027"
    win = tk.Toplevel(root)
    win.title(f"Celular: {info['nome']}")
    win.configure(bg=BG)
    win.resizable(True, True)
    F = "Segoe UI" if IS_WIN else "Helvetica"

    scr_h = max(600, win.winfo_screenheight())
    init_h = max(560, min(920, int(scr_h * 0.78)))
    init_w = max(380, int(init_h * 0.48))
    win.geometry(f"{init_w + 24}x{init_h + 135}")
    win.minsize(340, 520)

    bar = tk.Frame(win, bg=CARD)
    bar.pack(fill="x")

    def nav(which):
        return lambda: hub.phone_send(info, {"t": "key", "k": which})

    for label, which in [("‹ Voltar", "back"), ("● Início", "home"), ("▣ Recentes", "recents"),
                         ("🔒 Bloquear", "lock"), ("⤓ Print", "screenshot")]:
        tk.Button(bar, text=label, command=nav(which), font=(F, 10), bg=CARD, fg=FG,
                  activebackground=ACC, relief="flat", bd=0, padx=9, pady=7,
                  cursor="hand2").pack(side="left", padx=2, pady=4)

    tk.Label(bar, text="Modo", bg=CARD, fg="#8d95a4", font=(F, 9)).pack(side="left", padx=(8, 3))
    qvar = tk.StringVar(value="Fluida (24 FPS)")
    QMAP = {
        "Leve (10 FPS)": (10, 42, 420),
        "Normal (16 FPS)": (16, 52, 540),
        "Fluida (24 FPS)": (24, 60, 640),
        "Ultra HD (30 FPS)": (30, 72, 860),
    }

    def set_q(*_):
        fps, q, w = QMAP[qvar.get()]
        hub.phone_send(info, {"t": "quality", "fps": fps, "q": q, "w": w})

    om = tk.OptionMenu(bar, qvar, *QMAP.keys(), command=set_q)
    om.configure(bg=CARD, fg=FG, relief="flat", highlightthickness=0, font=(F, 9), activebackground=ACC)
    om.pack(side="left", pady=4)

    fs_state = {"on": False}

    def toggle_fs(*_):
        fs_state["on"] = not fs_state["on"]
        win.attributes("-fullscreen", fs_state["on"])

    tk.Button(bar, text="⛶ Tela cheia", command=toggle_fs, font=(F, 9), bg=CARD, fg=FG,
              activebackground=ACC, relief="flat", bd=0, padx=8, pady=7,
              cursor="hand2").pack(side="right", padx=6, pady=4)

    bar2 = tk.Frame(win, bg=BG)
    bar2.pack(fill="x", padx=10, pady=(6, 0))

    def open_app(pkg):
        return lambda: hub.phone_send(info, {"t": "open_app", "pkg": pkg})

    tk.Button(bar2, text="💬 WhatsApp", command=open_app("whatsapp"), font=(F, 10, "bold"),
              bg="#25D366", fg="#0b141a", activebackground="#1da851", relief="flat", bd=0,
              padx=12, pady=6, cursor="hand2").pack(side="left", padx=(0, 6))

    def show_apps_menu(ev):
        m = tk.Menu(win, tearoff=0, bg=CARD, fg=FG, activebackground=ACC, activeforeground="#111", font=(F, 10))
        presets = [
            ("WhatsApp", "whatsapp"),
            ("WhatsApp Business", "com.whatsapp.w4b"),
            ("Instagram", "com.instagram.android"),
            ("YouTube", "com.google.android.youtube"),
            ("Chrome", "com.android.chrome"),
            ("Câmera", "camera"),
            ("Galeria / Fotos", "gallery"),
            ("Configurações do Android", "settings"),
        ]
        for nome, pkg in presets:
            m.add_command(label=nome, command=open_app(pkg))
        extra = info.get("apps") or []
        if extra:
            m.add_separator()
            for a in sorted(extra, key=lambda x: str(x.get("nome", "")).lower())[:45]:
                if isinstance(a, dict) and a.get("pkg"):
                    m.add_command(label=a.get("nome", a["pkg"]), command=open_app(a["pkg"]))
        m.tk_popup(ev.x_root, ev.y_root)

    apps_btn = tk.Button(bar2, text="📱 Abrir app ▾", font=(F, 10), bg=CARD, fg=FG,
                         activebackground=ACC, relief="flat", bd=0, padx=10, pady=6, cursor="hand2")
    apps_btn.bind("<Button-1>", show_apps_menu)
    apps_btn.pack(side="left", padx=(0, 6))

    def send_file_here():
        paths = filedialog.askopenfilenames(parent=win, title="Escolher arquivo(s) para enviar ao celular")
        if paths:
            hub.share_pc_files(paths)

    tk.Button(bar2, text="📁 Enviar arquivo", command=send_file_here, font=(F, 10), bg=CARD, fg=FG,
              activebackground=ACC, relief="flat", bd=0, padx=10, pady=6, cursor="hand2").pack(side="left")

    # Barra inferior rápida de texto (ideal para digitar com acentos/frases no WhatsApp)
    bottom = tk.Frame(win, bg=BG)
    bottom.pack(side="bottom", fill="x", padx=10, pady=(2, 8))
    txt_in = tk.Entry(bottom, bg=CARD, fg=FG, insertbackground=FG, relief="flat", font=(F, 10))
    txt_in.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))

    def send_txt(*_):
        v = txt_in.get()
        if v:
            hub.phone_send(info, {"t": "type", "text": v})
            txt_in.delete(0, "end")
        else:
            hub.phone_send(info, {"t": "type", "text": "\n"})

    txt_in.bind("<Return>", send_txt)
    tk.Button(bottom, text="Enviar texto", command=send_txt, font=(F, 9, "bold"), bg=ACC, fg="#111",
              activebackground=ACC, relief="flat", bd=0, padx=12, pady=6, cursor="hand2").pack(side="right")

    canvas = tk.Canvas(win, bg="#000", highlightthickness=0)
    canvas.pack(fill="both", expand=True, padx=10, pady=6)

    st = {"img": None, "raw": None, "fid": None, "ox": 0, "oy": 0, "dw": init_w, "dh": init_h,
          "down": None, "moved": False, "lp": None, "lpfired": False, "alive": True}

    def norm_xy(px, py):
        rx = (px - st["ox"]) / max(1, st["dw"])
        ry = (py - st["oy"]) / max(1, st["dh"])
        return max(0.0, min(1.0, rx)), max(0.0, min(1.0, ry))

    def on_down(e):
        canvas.focus_set()
        st["down"] = (e.x, e.y, time.time())
        st["moved"] = False
        st["lpfired"] = False
        x, y = norm_xy(e.x, e.y)
        st["lp"] = win.after(550, lambda: long_fire(x, y))

    def long_fire(x, y):
        if not st["moved"]:
            st["lpfired"] = True
            hub.phone_send(info, {"t": "long", "x": x, "y": y})

    def on_move(e):
        if st["down"] and (abs(e.x - st["down"][0]) + abs(e.y - st["down"][1])) > 10:
            st["moved"] = True
            if st["lp"]:
                win.after_cancel(st["lp"])
                st["lp"] = None

    def on_up(e):
        if st["lp"]:
            win.after_cancel(st["lp"])
            st["lp"] = None
        if not st["down"] or st["lpfired"]:
            st["down"] = None
            return
        x0, y0, t0 = st["down"]
        st["down"] = None
        if st["moved"]:
            nx0, ny0 = norm_xy(x0, y0)
            nx1, ny1 = norm_xy(e.x, e.y)
            hub.phone_send(info, {"t": "swipe", "x": nx0, "y": ny0, "x2": nx1, "y2": ny1,
                                  "ms": int(max(70, min(600, (time.time() - t0) * 1000)))})
        else:
            x, y = norm_xy(e.x, e.y)
            hub.phone_send(info, {"t": "tap", "x": x, "y": y})

    def on_wheel(e):
        dy = -0.22 if e.delta > 0 else 0.22
        y1 = max(0.15, min(0.85, 0.5 + dy))
        y2 = max(0.15, min(0.85, 0.5 - dy))
        hub.phone_send(info, {"t": "swipe", "x": 0.5, "y": y1, "x2": 0.5, "y2": y2, "ms": 150})

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_move)
    canvas.bind("<ButtonRelease-1>", on_up)
    canvas.bind("<MouseWheel>", on_wheel)

    def render_current():
        im = st["raw"]
        if im is None:
            return
        cw = max(60, canvas.winfo_width())
        ch = max(60, canvas.winfo_height())
        scale = min(cw / max(1, im.width), ch / max(1, im.height))
        dw = max(1, int(im.width * scale))
        dh = max(1, int(im.height * scale))
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2
        st["ox"], st["oy"], st["dw"], st["dh"] = ox, oy, dw, dh
        resized = im if (dw == im.width and dh == im.height) else im.resize((dw, dh), Image.BILINEAR)
        st["img"] = ImageTk.PhotoImage(resized)
        canvas.delete("all")
        canvas.create_image(ox, oy, anchor="nw", image=st["img"])

    canvas.bind("<Configure>", lambda _e: render_current())

    def on_key(e):
        if e.keysym == "F11":
            return toggle_fs()
        if win.focus_get() is txt_in:
            return
        if (e.state & 0x4) and e.keysym.lower() == "v":
            try:
                txt = win.clipboard_get()
                if txt:
                    hub.phone_send(info, {"t": "type", "text": txt})
            except Exception:
                pass
            return
        if e.keysym == "BackSpace":
            hub.phone_send(info, {"t": "bksp"})
        elif e.keysym == "Escape":
            if fs_state["on"]:
                toggle_fs()
            else:
                hub.phone_send(info, {"t": "key", "k": "back"})
        elif e.keysym == "Home":
            hub.phone_send(info, {"t": "key", "k": "home"})
        elif e.char and e.char.isprintable():
            hub.phone_send(info, {"t": "type", "text": e.char})
        elif e.keysym == "Return":
            hub.phone_send(info, {"t": "type", "text": "\n"})

    win.bind("<Key>", on_key)
    canvas.focus_set()

    def tick():
        if not st["alive"]:
            return
        if info.get("ws") not in hub.phones:
            canvas.delete("all")
            canvas.create_text(max(120, canvas.winfo_width() // 2), max(120, canvas.winfo_height() // 2),
                               text="O celular parou de compartilhar.", fill="#8d95a4", font=(F, 12))
            win.after(600, close)
            return
        fr = info.get("frame")
        if fr is not None and id(fr) != st["fid"]:
            st["fid"] = id(fr)
            try:
                st["raw"] = Image.open(io.BytesIO(fr))
                render_current()
            except Exception:
                pass
        win.after(15, tick)

    def close():
        st["alive"] = False
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", close)
    tick()
    win.lift()
    return win


def run_gui(hub, start_hidden=False):
    import tkinter as tk
    from tkinter import messagebox
    from PIL import ImageTk

    BG, CARD, FG, MUTED, ACC = "#0e1014", "#171a21", "#e9ecf1", "#8b93a3", "#ff6b2c"
    root = tk.Tk()
    root.title(APP_NAME)
    root.configure(bg=BG)
    root.resizable(False, False)
    try:
        root._ico = ImageTk.PhotoImage(make_icon(64))
        root.iconphoto(True, root._ico)
    except Exception:
        pass

    cmds = queue.Queue()
    F = "Segoe UI" if IS_WIN else "Helvetica"

    tk.Label(root, text=APP_NAME, font=(F, 20, "bold"), bg=BG, fg=FG).pack(padx=28, pady=(18, 0))
    tk.Label(root, text="Aponte a câmera do celular para o código", font=(F, 10), bg=BG, fg=MUTED).pack()
    qr_lbl = tk.Label(root, bg=BG, bd=0)
    qr_lbl.pack(pady=(12, 6))
    url_lbl = tk.Label(root, font=("Consolas", 10), bg=BG, fg=MUTED)
    url_lbl.pack()
    pin_lbl = tk.Label(root, font=(F, 15, "bold"), bg=BG, fg=ACC)
    pin_lbl.pack(pady=(6, 0))
    tk.Label(root, text="ou abra o endereço acima no navegador do celular e digite o PIN",
             font=(F, 9), bg=BG, fg=MUTED).pack()

    box = tk.Frame(root, bg=CARD, padx=14, pady=10)
    box.pack(fill="x", padx=20, pady=14)
    status_lbl = tk.Label(box, font=(F, 10), bg=CARD, fg=FG, justify="left", anchor="w")
    status_lbl.pack(fill="x")
    admin_lbl = tk.Label(box, font=(F, 9), bg=CARD, fg=MUTED, anchor="w",
                         text="Rodando como administrador" if hub.admin else "Modo normal (sem administrador)")
    admin_lbl.pack(fill="x", pady=(4, 0))

    opts = tk.Frame(root, bg=BG)
    opts.pack(fill="x", padx=20)
    auto_var = tk.BooleanVar(value=get_autostart())

    def toggle_auto():
        try:
            set_autostart(auto_var.get())
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Não consegui alterar: {e}")

    if IS_WIN:
        tk.Checkbutton(opts, text="Iniciar junto com o Windows", variable=auto_var, command=toggle_auto,
                       bg=BG, fg=FG, selectcolor=CARD, activebackground=BG, activeforeground=FG,
                       font=(F, 10), bd=0, highlightthickness=0).pack(anchor="w")

    def mkbtn(parent, text, cmd, accent=False):
        b = tk.Button(parent, text=text, command=cmd, font=(F, 10, "bold" if accent else "normal"),
                      bg=ACC if accent else CARD, fg="#111" if accent else FG, activebackground=ACC,
                      activeforeground="#111", relief="flat", bd=0, padx=12, pady=7, cursor="hand2")
        b.pack(side="left", padx=4, expand=True, fill="x")
        return b

    row1 = tk.Frame(root, bg=BG)
    row1.pack(fill="x", padx=16, pady=(10, 4))

    def regen():
        if messagebox.askyesno(APP_NAME, "Isso desconecta todos os celulares e cria um novo QR code e PIN. Continuar?"):
            hub.regenerate_token()

    mkbtn(row1, "Novo acesso", regen)

    def edit_apps():
        try:
            if IS_WIN:
                os.startfile(str(CONFIG_PATH))  # noqa
            else:
                subprocess.Popen(["xdg-open", str(CONFIG_PATH)])
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    mkbtn(row1, "Editar apps rápidos", edit_apps)

    phone_wins = {}

    def open_phone(info):
        w = phone_wins.get(id(info))
        if w and w.winfo_exists():
            w.lift()
            return
        phone_wins[id(info)] = open_phone_window(hub, root, info)

    def ver_celular():
        if hub.phones:
            open_phone(next(reversed(list(hub.phones.values()))))
        else:
            messagebox.showinfo(APP_NAME, "Nenhum celular compartilhando a tela agora.\n\n"
                                "No app do celular, abra \"Deixar o PC me controlar\" e toque em Iniciar.")

    mkbtn(row1, "Ver meu celular", ver_celular)
    hub.phone_cb = lambda ev, info: cmds.put(("phone", ev, info))
    hub.file_cb = lambda saved: cmds.put(("files", saved))

    row_files = tk.Frame(root, bg=BG)
    row_files.pack(fill="x", padx=16, pady=(0, 4))

    def enviar_arquivo():
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(parent=root, title="Escolher arquivo(s) para enviar ao celular")
        if paths:
            n = hub.share_pc_files(paths)
            if n:
                messagebox.showinfo(APP_NAME, f"{n} arquivo(s) disponibilizado(s) para o celular na aba Arquivos.")

    def abrir_pasta_arquivos():
        try:
            p = files_dir()
            if IS_WIN:
                os.startfile(str(p))  # noqa
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as e:
            messagebox.showerror(APP_NAME, str(e))

    mkbtn(row_files, "Enviar arquivo ao celular", enviar_arquivo)
    mkbtn(row_files, "Pasta de arquivos", abrir_pasta_arquivos)

    row2 = tk.Frame(root, bg=BG)
    row2.pack(fill="x", padx=16, pady=(0, 18))
    if IS_WIN and not hub.admin:
        def as_admin():
            if relaunch_as_admin():
                cmds.put("quit")
        mkbtn(row2, "Reabrir como admin", as_admin)
    mkbtn(row2, "Sair", lambda: cmds.put("quit"), accent=True)

    tray = None
    try:
        import pystray
        tray = pystray.Icon("RemotoGF", make_icon(64), APP_NAME, menu=pystray.Menu(
            pystray.MenuItem("Mostrar", lambda *_: cmds.put("show"), default=True),
            pystray.MenuItem("Sair", lambda *_: cmds.put("quit"))))
        tray.run_detached()
    except Exception as e:
        log("Bandeja indisponível:", e)
        tray = None

    state = {"token": None, "notified": False}

    def redraw():
        if state["token"] != hub.cfg["token"] + hub.pin:
            state["token"] = hub.cfg["token"] + hub.pin
            img = make_qr(hub.pair_url(), box=6)
            photo = ImageTk.PhotoImage(img)
            qr_lbl.configure(image=photo)
            qr_lbl.image = photo
            url_lbl.configure(text=hub.base_url())
            pin_lbl.configure(text=f"PIN  {hub.pin[:3]} {hub.pin[3:]}")
        if hub.clients:
            names = ", ".join(c["nome"] for c in hub.clients.values())
            status_lbl.configure(text=f"●  Conectado: {names}", fg="#57d68d")
        else:
            status_lbl.configure(text="○  Nenhum celular conectado", fg=FG)

    def quit_all():
        try:
            if tray:
                tray.stop()
        except Exception:
            pass
        root.destroy()
        os._exit(0)

    def poll():
        try:
            while True:
                c = cmds.get_nowait()
                if c == "quit":
                    return quit_all()
                if c == "show":
                    root.deiconify()
                    root.lift()
                    root.focus_force()
                elif isinstance(c, tuple) and c[0] == "phone":
                    _, ev, info = c
                    if ev == "on":
                        open_phone(info)
                elif isinstance(c, tuple) and c[0] == "files":
                    _, saved = c
                    nomes = ", ".join(s["nome"] for s in saved[:2])
                    if len(saved) > 2:
                        nomes += f" (+{len(saved) - 2})"
                    if tray:
                        try:
                            tray.notify(f"Salvo em Downloads\\Remoto GF: {nomes}", "Arquivo recebido do celular")
                        except Exception:
                            pass
        except queue.Empty:
            pass
        if hub.dirty:
            hub.dirty = False
            redraw()
        root.after(400, poll)

    def on_close():
        if tray:
            root.withdraw()
            if not state["notified"]:
                state["notified"] = True
                try:
                    tray.notify("Continua rodando aqui na bandeja. Clique no ícone para abrir.", APP_NAME)
                except Exception:
                    pass
        else:
            quit_all()

    root.protocol("WM_DELETE_WINDOW", on_close)
    redraw()
    if start_hidden:
        if tray:
            root.withdraw()
        else:
            root.iconify()
    root.after(400, poll)
    root.mainloop()


def print_banner(hub):
    log(f"{APP_NAME} {VERSION} rodando")
    print()
    print(f"  No celular (mesmo Wi-Fi), abra:  {hub.base_url()}")
    print(f"  PIN de pareamento:               {hub.pin}")
    print(f"  Configurações em:                {CONFIG_PATH}")
    print()
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(hub.pair_url())
        q.print_ascii(invert=True)
    except Exception:
        pass
    print()


def main():
    if "--admin" in sys.argv and IS_WIN and not is_admin():
        if relaunch_as_admin():
            return
    cfg = load_config()
    hub = Hub(cfg)
    headless = "--sem-janela" in sys.argv

    if headless:
        async def run():
            await hub.start()
            print_banner(hub)
            await asyncio.Event().wait()
        try:
            asyncio.run(run())
        except OSError as e:
            log(f"Não consegui abrir a porta {hub.port}. O Remoto GF já está aberto? ({e})")
        except KeyboardInterrupt:
            pass
        return

    started = threading.Event()
    error = []

    def server_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(hub.start())
        except Exception as e:
            error.append(e)
            started.set()
            return
        started.set()
        loop.run_forever()

    threading.Thread(target=server_thread, daemon=True, name="servidor").start()
    started.wait(15)
    if error:
        msg = f"Não consegui abrir a porta {hub.port}. Talvez o Remoto GF já esteja aberto.\n\n{error[0]}"
        log(msg)
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(APP_NAME, msg)
        except Exception:
            pass
        return
    print_banner(hub)
    try:
        run_gui(hub, start_hidden="--minimizado" in sys.argv)
    except Exception as e:
        log("Janela indisponível, seguindo só no console:", e)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
