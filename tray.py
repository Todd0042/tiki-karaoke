"""
System Tray Icon Manager for Tiki Karaoke Lounge
Provides cross-platform system tray integration with native right-click menu:
- Open Stage Display
- Open Host DJ Remote
- Open Singer Join Page
- Open Setup & Display Config
- Copy Public URL to clipboard
- Quit Tiki Karaoke cleanly

Engine Selection:
- On Linux (KDE Plasma, GNOME, Wayland): Prefers PyQt6 StatusNotifierItem (SNI)
  for 100% native Wayland integration, high-DPI icons with true alpha, and responsive menus.
- On Windows: Uses pystray with native Win32 Shell_NotifyIconW.
- Graceful console fallback if no GUI/tray desktop environment is present.
"""
import os
import sys
import shutil
import argparse
import webbrowser
import subprocess
from pathlib import Path
from typing import Optional, Callable

qt_tray_proc = None
tray_icon = None

def get_tray_icon_path() -> Optional[Path]:
    """Finds best icon image path across platforms."""
    candidates = [
        Path(__file__).resolve().parent / "static" / "tiki-icon.png",
        Path(__file__).resolve().parent / "packaging" / "windows" / "tiki-icon.ico",
        Path(__file__).resolve().parent / "static" / "favicon.ico",
        Path(__file__).resolve().parent / "tiki-icon.ico",
    ]
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(sys.executable).parent
        candidates.insert(0, base / "tiki-icon.png")
        candidates.insert(1, base / "static" / "tiki-icon.png")
        candidates.insert(2, base / "tiki-icon.ico")

    for path in candidates:
        if path.exists():
            return path
    return None

def get_tray_image():
    """Loads PIL icon image for pystray (Windows / fallback)."""
    from PIL import Image

    icon_path = get_tray_icon_path()
    if icon_path:
        try:
            return Image.open(icon_path)
        except Exception:
            pass

    # Fallback generated icon if no file found: vibrant tropical cyan circle with pink border
    from PIL import ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=(0, 240, 255, 230), outline=(255, 42, 133, 255), width=4)
    return img

def run_qt_tray_daemon(args):
    """
    Runs the native Qt system tray icon on Linux (KDE / GNOME / Wayland / X11).
    Speaks native DBus org.kde.StatusNotifierItem.
    """
    try:
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QIcon, QAction
        from PyQt6.QtCore import QUrl
    except ImportError:
        try:
            from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
            from PySide6.QtGui import QIcon, QAction
            from PySide6.QtCore import QUrl
        except ImportError:
            sys.exit(1)

    app = QApplication([args.bar_name])
    app.setQuitOnLastWindowClosed(False)

    icon_path = get_tray_icon_path()
    if icon_path:
        icon = QIcon(str(icon_path))
    else:
        icon = QIcon()

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip(f"🌴 {args.bar_name}")

    menu = QMenu()

    # Title header
    header_action = QAction(f"🌴 {args.bar_name}", menu)
    header_action.setEnabled(False)
    menu.addAction(header_action)
    menu.addSeparator()

    def _open(url):
        def _h():
            if url:
                webbrowser.open(url)
        return _h

    def _copy_url():
        target = args.public or args.join
        if target:
            cb = app.clipboard()
            if cb:
                cb.setText(target.strip())

    def _quit():
        if args.shutdown:
            try:
                import urllib.request
                req = urllib.request.Request(
                    args.shutdown,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "User-Agent": "TikiTray/1.0"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=1.5):
                    pass
            except Exception:
                pass
        app.quit()

    act_stage = QAction("📺 Open Stage Display", menu)
    act_stage.triggered.connect(_open(args.stage))
    menu.addAction(act_stage)

    act_host = QAction("👑 Open Host DJ Remote", menu)
    act_host.triggered.connect(_open(args.host))
    menu.addAction(act_host)

    act_join = QAction("📱 Open Singer Join Page", menu)
    act_join.triggered.connect(_open(args.join))
    menu.addAction(act_join)

    act_setup = QAction("⚙️ Open Setup & Screens", menu)
    act_setup.triggered.connect(_open(args.setup))
    menu.addAction(act_setup)

    menu.addSeparator()

    if args.public or args.join:
        act_copy = QAction("📋 Copy Public Join Link", menu)
        act_copy.triggered.connect(_copy_url)
        menu.addAction(act_copy)

    act_quit = QAction("🛑 Quit Tiki Karaoke", menu)
    act_quit.triggered.connect(_quit)
    menu.addAction(act_quit)

    tray.setContextMenu(menu)

    # Left click opens Host DJ remote
    def _activated(reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            _open(args.host)()

    tray.activated.connect(_activated)
    tray.show()

    sys.exit(app.exec())

def start_tray(
    urls: dict,
    on_quit_callback: Optional[Callable] = None,
    bar_name: str = "Tiki Karaoke Lounge"
):
    """
    Initializes and starts the system tray icon across Linux and Windows.
    Uses native Qt on Linux (Wayland / KDE) and pystray on Windows.
    """
    global qt_tray_proc, tray_icon

    stage_url = urls.get("stage", "http://localhost:5000/stage")
    host_url = urls.get("host", "http://localhost:5000/host")
    join_url = urls.get("join", "http://localhost:5000/join")
    setup_url = urls.get("setup", "http://localhost:5000/setup")
    public_url = urls.get("public", "")
    shutdown_url = urls.get("shutdown", "")

    # On Linux: Prefer Qt StatusNotifierItem for native Wayland / KDE Plasma support (non-frozen dev runs only)
    if sys.platform != "win32" and not getattr(sys, "frozen", False):
        try:
            import PyQt6.QtWidgets  # noqa: F401
            has_qt = True
        except ImportError:
            try:
                import PySide6.QtWidgets  # noqa: F401
                has_qt = True
            except ImportError:
                has_qt = False

        if has_qt:
            try:
                cmd = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--qt-daemon",
                    "--stage", stage_url,
                    "--host", host_url,
                    "--join", join_url,
                    "--setup", setup_url,
                    "--shutdown", shutdown_url,
                    "--public", public_url,
                    "--bar-name", bar_name,
                ]
                qt_tray_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                print("[System Tray] ✅ Native Qt system tray icon active (Right-click icon in taskbar for options).")
                return qt_tray_proc
            except Exception as e:
                print(f"[System Tray] Notice: Could not launch Qt tray daemon ({e}), falling back to pystray...")

    # Windows (or Linux fallback): Use pystray Win32 / X11
    try:
        import pystray
        from pystray import MenuItem as item, Menu
    except ImportError:
        print("[System Tray] Notice: pystray not installed, running in console mode.")
        return None

    try:
        image = get_tray_image()
    except Exception as e:
        print(f"[System Tray] Notice: Could not generate tray icon image: {e}")
        return None

    def _open(url):
        def _handler(icon=None, item=None):
            try:
                webbrowser.open(url)
            except Exception as e:
                print(f"[System Tray] Error opening {url}: {e}")
        return _handler

    def _copy_url(icon=None, item=None):
        target = public_url or join_url
        try:
            if sys.platform == "win32":
                subprocess.run("clip", input=target.strip().encode("utf-8"), check=False)
            elif shutil.which("wl-copy"):
                subprocess.run(["wl-copy"], input=target.strip().encode("utf-8"), check=False)
            elif shutil.which("xclip"):
                subprocess.run(["xclip", "-selection", "clipboard"], input=target.strip().encode("utf-8"), check=False)
        except Exception:
            pass

    def _quit_app(icon=None, item=None):
        print("\n🛑 [System Tray] Quit requested from system tray icon...")
        stop_tray()
        if shutdown_url:
            try:
                import urllib.request
                req = urllib.request.Request(
                    shutdown_url,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "User-Agent": "TikiTray/1.0"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=1.5):
                    pass
                return
            except Exception as e:
                print(f"[System Tray] Notice: API broadcast shutdown unavailable ({e}), calling local quit...")

        if on_quit_callback:
            on_quit_callback()

    menu_items = [
        item(f"🌴 {bar_name}", None, enabled=False),
        Menu.SEPARATOR,
        item("📺 Open Stage Display", _open(stage_url)),
        item("👑 Open Host DJ Remote", _open(host_url), default=True),
        item("📱 Open Singer Join Page", _open(join_url)),
        item("⚙️ Open Setup & Screens", _open(setup_url)),
        Menu.SEPARATOR,
    ]

    if public_url:
        menu_items.append(item("📋 Copy Public Join Link", _copy_url))

    menu_items.append(item("🛑 Quit Tiki Karaoke", _quit_app))

    menu = Menu(*menu_items)

    try:
        tray_icon = pystray.Icon("tiki_karaoke", image, f"Tiki Karaoke - {bar_name}", menu)
        tray_icon.run_detached()
        engine_name = "Win32" if sys.platform == "win32" else "X11/AppIndicator"
        print(f"[System Tray] ✅ Native {engine_name} system tray icon active (Right-click icon in taskbar for options).")
        return tray_icon
    except Exception as e:
        print(f"[System Tray] Notice: System tray not available in this desktop session ({e}). Running in console mode.")
        tray_icon = None
        return None

def stop_tray():
    """Stops and removes any active system tray icon process or thread."""
    global qt_tray_proc, tray_icon
    if qt_tray_proc and qt_tray_proc.poll() is None:
        try:
            qt_tray_proc.terminate()
            qt_tray_proc.wait(timeout=1.0)
        except Exception:
            try:
                qt_tray_proc.kill()
            except Exception:
                pass
        qt_tray_proc = None

    if tray_icon:
        try:
            tray_icon.stop()
        except Exception:
            pass
        tray_icon = None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tiki Karaoke Tray Daemon")
    parser.add_argument("--qt-daemon", action="store_true", help="Run Qt tray daemon")
    parser.add_argument("--stage", default="", help="Stage display URL")
    parser.add_argument("--host", default="", help="Host DJ remote URL")
    parser.add_argument("--join", default="", help="Singer join URL")
    parser.add_argument("--setup", default="", help="Setup wizard URL")
    parser.add_argument("--shutdown", default="", help="Shutdown API URL")
    parser.add_argument("--public", default="", help="Public join URL")
    parser.add_argument("--bar-name", default="The Tiki Hut Karaoke Lounge", help="Bar name")
    args = parser.parse_args()

    if args.qt_daemon:
        run_qt_tray_daemon(args)
