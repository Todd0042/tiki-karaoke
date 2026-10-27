"""
Tiki Karaoke Lounge - Native Desktop Control Hub Window
Provides a lightweight, zero-terminal desktop launcher with:
- Live Server & Public Tunnel Status
- Scannable Dual QR Codes (Singer Join & Host DJ Remote)
- One-Click Browser Launchers (Host Desk, TV Stage, Setup Wizard)
- Live Party Ticker (Now Playing & Queue count)
- Log Viewer and Clean Server Shutdown
"""
import os
import sys
import time
import subprocess
import webbrowser
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any

HAS_GUI = False
_gui_import_error = ""

try:
    import tkinter as tk
    from tkinter import messagebox
    from PIL import Image, ImageTk
    import qrcode
    HAS_GUI = True
except Exception as e:
    HAS_GUI = False
    _gui_import_error = str(e)


class TikiControlHub:
    def __init__(
        self,
        urls: Dict[str, str],
        on_shutdown: Optional[Callable] = None,
        bar_name: str = "Tiki Karaoke Lounge",
        host_pin: str = "1234",
        log_file: Optional[Path] = None,
        is_tray_active: bool = False
    ):
        self.urls = urls
        self.on_shutdown = on_shutdown
        self.bar_name = bar_name
        self.host_pin = host_pin
        self.log_file = log_file
        self.is_tray_active = is_tray_active
        self.root: Optional[tk.Tk] = None
        self._is_closing = False
        self._qr_images = []  # keep references to prevent GC

    def start(self):
        """Starts the Tkinter main event loop on the calling thread."""
        if not HAS_GUI:
            print(f"[Control Hub] GUI unavailable: {_gui_import_error}")
            return

        self.root = tk.Tk()
        self.root.title(f"🌴 {self.bar_name} — Control Hub")
        self.root.geometry("520x720")
        self.root.minsize(480, 640)
        self.root.configure(bg="#0d0814")

        # Set window icon if available
        self._set_app_icon()

        # Handle window close protocol
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)

        # Build UI layout
        self._build_header()
        self._build_qr_panel()
        self._build_action_buttons()
        self._build_live_ticker()
        self._build_footer()

        # Start periodic live state polling
        self.root.after(1500, self._poll_live_state)

        # Run event loop
        self.root.mainloop()

    def show_window(self):
        """Thread-safe method to un-hide and bring the Control Hub window to the front."""
        if not self.root:
            return
        def _bring():
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                self.root.attributes("-topmost", True)
                self.root.after(150, lambda: self.root.attributes("-topmost", False) if self.root else None)
            except Exception:
                pass
        try:
            self.root.after_idle(_bring)
        except Exception:
            pass

    def _set_app_icon(self):
        try:
            from tray import get_tray_icon_path
            icon_p = get_tray_icon_path()
            if icon_p and icon_p.exists():
                if sys.platform == "win32" and icon_p.suffix == ".ico":
                    self.root.iconbitmap(str(icon_p))
                else:
                    img = Image.open(str(icon_p))
                    photo = ImageTk.PhotoImage(img)
                    self.root.iconphoto(False, photo)
                    self._qr_images.append(photo)
        except Exception:
            pass

    def _build_header(self):
        header_frame = tk.Frame(self.root, bg="#0d0814")
        header_frame.pack(fill="x", padx=20, pady=(16, 8))

        title_lbl = tk.Label(
            header_frame,
            text=f"🌴 {self.bar_name}",
            font=("Helvetica", 16, "bold"),
            fg="#00f0ff",
            bg="#0d0814"
        )
        title_lbl.pack(anchor="w")

        # Status Line
        status_frame = tk.Frame(header_frame, bg="#0d0814")
        status_frame.pack(fill="x", pady=(4, 0))

        dot_lbl = tk.Label(status_frame, text="●", font=("Helvetica", 10), fg="#39ff14", bg="#0d0814")
        dot_lbl.pack(side="left")

        port = self.urls.get("port", "8000")
        pub_url = self.urls.get("public") or self.urls.get("join") or f"http://127.0.0.1:{port}"
        status_text = f" Server Online on :{port}  •  v1.0.3"
        txt_lbl = tk.Label(status_frame, text=status_text, font=("Helvetica", 9), fg="#94a3b8", bg="#0d0814")
        txt_lbl.pack(side="left")

        # Public URL pill
        url_frame = tk.Frame(header_frame, bg="#1a1325", highlightbackground="#2e1f47", highlightthickness=1)
        url_frame.pack(fill="x", pady=(8, 0))

        clean_url = pub_url.replace("http://", "").replace("https://", "")
        url_lbl = tk.Label(
            url_frame,
            text=f"🔗 {pub_url}",
            font=("Helvetica", 8, "bold"),
            fg="#ffd166",
            bg="#1a1325",
            pady=4,
            padx=8
        )
        url_lbl.pack(side="left")

    def _build_qr_panel(self):
        panel = tk.Frame(self.root, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        panel.pack(fill="x", padx=20, pady=8)

        # 2-column layout for QRs
        col_frame = tk.Frame(panel, bg="#130d1f")
        col_frame.pack(fill="x", padx=10, pady=10)

        # Singer QR
        singer_box = tk.Frame(col_frame, bg="#130d1f")
        singer_box.pack(side="left", expand=True, fill="both")

        singer_title = tk.Label(singer_box, text="📱 SINGER JOIN", font=("Helvetica", 9, "bold"), fg="#00f0ff", bg="#130d1f")
        singer_title.pack(pady=(0, 4))

        join_url = self.urls.get("join", "http://127.0.0.1:8000/join")
        singer_photo = self._generate_qr_photo(join_url, size=135)
        if singer_photo:
            self._qr_images.append(singer_photo)
            s_img_lbl = tk.Label(singer_box, image=singer_photo, bg="#ffffff", relief="ridge", borderwidth=1)
            s_img_lbl.pack()

        s_hint = tk.Label(singer_box, text="Scan with Phone Camera", font=("Helvetica", 7), fg="#94a3b8", bg="#130d1f")
        s_hint.pack(pady=(4, 0))

        # Host QR
        host_box = tk.Frame(col_frame, bg="#130d1f")
        host_box.pack(side="right", expand=True, fill="both")

        host_title = tk.Label(host_box, text="👑 HOST DJ REMOTE", font=("Helvetica", 9, "bold"), fg="#ff2a85", bg="#130d1f")
        host_title.pack(pady=(0, 4))

        host_url = self.urls.get("host", "http://127.0.0.1:8000/host")
        host_photo = self._generate_qr_photo(host_url, size=135)
        if host_photo:
            self._qr_images.append(host_photo)
            h_img_lbl = tk.Label(host_box, image=host_photo, bg="#ffffff", relief="ridge", borderwidth=1)
            h_img_lbl.pack()

        h_hint = tk.Label(host_box, text=f"PIN: {self.host_pin} (Pre-Paired)", font=("Helvetica", 7, "bold"), fg="#ffd166", bg="#130d1f")
        h_hint.pack(pady=(4, 0))

    def _generate_qr_photo(self, data: str, size: int = 140) -> Optional[ImageTk.PhotoImage]:
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=4,
                border=1
            )
            qr.add_data(data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            img = img.resize((size, size), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception as e:
            print(f"[Control Hub] QR generation error: {e}")
            return None

    def _build_action_buttons(self):
        btn_frame = tk.Frame(self.root, bg="#0d0814")
        btn_frame.pack(fill="x", padx=20, pady=6)

        # Primary buttons grid
        # 1. Open Host Desk
        host_btn = tk.Button(
            btn_frame,
            text="👑 Open Host Desk (Laptop Controls)",
            font=("Helvetica", 10, "bold"),
            bg="#2a1435",
            fg="#ff2a85",
            activebackground="#ff2a85",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            pady=8,
            command=lambda: webbrowser.open(self.urls.get("host", "http://127.0.0.1:8000/host"))
        )
        host_btn.pack(fill="x", pady=3)

        # 2. Open TV Stage Display
        stage_btn = tk.Button(
            btn_frame,
            text="📺 Open TV Stage Screen (Lyrics & Video)",
            font=("Helvetica", 10, "bold"),
            bg="#122436",
            fg="#00f0ff",
            activebackground="#00f0ff",
            activeforeground="#0d0814",
            relief="flat",
            cursor="hand2",
            pady=8,
            command=lambda: webbrowser.open(self.urls.get("stage", "http://127.0.0.1:8000/stage"))
        )
        stage_btn.pack(fill="x", pady=3)

        # 3. Open Smart Media Importer
        importer_btn = tk.Button(
            btn_frame,
            text="📂 Smart Media Importer (CompuHost & YouTube)",
            font=("Helvetica", 9, "bold"),
            bg="#1a1424",
            fg="#ffd166",
            activebackground="#2a1f3d",
            activeforeground="#ffd166",
            relief="flat",
            cursor="hand2",
            pady=7,
            command=self._launch_importer
        )
        importer_btn.pack(fill="x", pady=3)

        # 4. Open CD+G Optical Disc Ripper
        ripper_btn = tk.Button(
            btn_frame,
            text="📀 CD+G Disc Ripper (Extract Physical Discs)",
            font=("Helvetica", 9, "bold"),
            bg="#1a1424",
            fg="#00f0ff",
            activebackground="#2a1f3d",
            activeforeground="#00f0ff",
            relief="flat",
            cursor="hand2",
            pady=7,
            command=self._launch_cdg_ripper
        )
        ripper_btn.pack(fill="x", pady=3)

        # Secondary row: Setup & Copy Link
        row2 = tk.Frame(btn_frame, bg="#0d0814")
        row2.pack(fill="x", pady=3)

        setup_btn = tk.Button(
            row2,
            text="⚙️ Setup & Screens",
            font=("Helvetica", 9),
            bg="#1a1325",
            fg="#cbd5e1",
            activebackground="#2a1b40",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            pady=6,
            command=lambda: webbrowser.open(self.urls.get("setup", "http://127.0.0.1:8000/setup"))
        )
        setup_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))

        copy_btn = tk.Button(
            row2,
            text="📋 Copy Singer Link",
            font=("Helvetica", 9),
            bg="#1a1325",
            fg="#ffd166",
            activebackground="#2a1b40",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            pady=6,
            command=self._copy_singer_url
        )
        copy_btn.pack(side="right", expand=True, fill="x", padx=(4, 0))

    def _copy_singer_url(self):
        target = self.urls.get("public") or self.urls.get("join") or "http://127.0.0.1:8000/join"
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(target)
            self.root.update()
            self._flash_toast("✅ Copied singer join link to clipboard!")
        except Exception as e:
            print(f"[Control Hub] Clipboard error: {e}")

    def _flash_toast(self, msg: str):
        if hasattr(self, "ticker_lbl") and self.ticker_lbl and self.root:
            def _apply():
                try:
                    old = self.ticker_lbl.cget("text")
                    self.ticker_lbl.config(text=msg, fg="#39ff14")
                    self.root.after(3000, lambda: self.ticker_lbl.config(text=old, fg="#94a3b8"))
                except Exception:
                    pass
            self.root.after(0, _apply)

    def _launch_importer(self):
        def _call():
            try:
                import urllib.request
                import json
                port = self.urls.get("port", "8000")
                url = f"http://127.0.0.1:{port}/api/host/launch-importer"
                req = urllib.request.Request(
                    url,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "User-Agent": "TikiHub/1.0"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        self._flash_toast("🚀 Smart Media Importer launched!")
                        return
            except Exception as e:
                try:
                    from app import find_importer_executable, get_db_path, get_tracks_dir
                    found = find_importer_executable()
                    if found:
                        cmd_base, work_dir = found
                        cmd = list(cmd_base) + ["--db", str(get_db_path()), "--folder", str(get_tracks_dir())]
                        if sys.platform == "win32":
                            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
                            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
                        else:
                            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
                        self._flash_toast("🚀 Smart Media Importer launched!")
                        return
                except Exception:
                    pass
                if HAS_GUI:
                    self.root.after(0, lambda: messagebox.showerror("Smart Importer", f"Could not launch Smart Importer:\n{e}"))

        threading.Thread(target=_call, daemon=True).start()

    def _launch_cdg_ripper(self):
        def _call():
            try:
                import urllib.request
                import json
                port = self.urls.get("port", "8000")
                url = f"http://127.0.0.1:{port}/api/host/launch-cdg-ripper"
                req = urllib.request.Request(
                    url,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "User-Agent": "TikiHub/1.0"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        self._flash_toast("📀 CD+G Disc Ripper launched!")
                        return
            except Exception as e:
                try:
                    from app import find_importer_executable, get_tracks_dir
                    found = find_importer_executable()
                    if found:
                        cmd_base, work_dir = found
                        cmd = list(cmd_base) + ["--cdg-ripper", "--folder", str(get_tracks_dir())]
                        if sys.platform == "win32":
                            flags = 0x00000008 | subprocess.CREATE_NEW_PROCESS_GROUP
                            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, creationflags=flags)
                        else:
                            subprocess.Popen(cmd, cwd=str(work_dir) if work_dir else None, start_new_session=True)
                        self._flash_toast("📀 CD+G Disc Ripper launched!")
                        return
                except Exception:
                    pass
                if HAS_GUI:
                    self.root.after(0, lambda: messagebox.showerror("CD+G Ripper", f"Could not launch CD+G Ripper:\n{e}"))

        threading.Thread(target=_call, daemon=True).start()

    def _build_live_ticker(self):
        card = tk.Frame(self.root, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        card.pack(fill="x", padx=20, pady=8)

        self.ticker_title = tk.Label(
            card,
            text="STAGE STATUS: Intermission (Open Mic)",
            font=("Helvetica", 9, "bold"),
            fg="#00f0ff",
            bg="#130d1f",
            anchor="w"
        )
        self.ticker_title.pack(fill="x", padx=12, pady=(8, 2))

        self.ticker_lbl = tk.Label(
            card,
            text="Queue is open • Ready for singers to scan and pick songs",
            font=("Helvetica", 8),
            fg="#94a3b8",
            bg="#130d1f",
            anchor="w"
        )
        self.ticker_lbl.pack(fill="x", padx=12, pady=(0, 8))

    def _build_footer(self):
        footer = tk.Frame(self.root, bg="#0d0814")
        footer.pack(fill="x", side="bottom", padx=20, pady=(4, 14))

        log_btn = tk.Button(
            footer,
            text="📄 View Logs",
            font=("Helvetica", 8),
            bg="#161224",
            fg="#94a3b8",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            command=self._open_log_file
        )
        log_btn.pack(side="left")

        quit_btn = tk.Button(
            footer,
            text="🛑 Shut Down Lounge",
            font=("Helvetica", 8, "bold"),
            bg="#2b1117",
            fg="#ff5555",
            activebackground="#ff5555",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=4,
            command=self._confirm_shutdown
        )
        quit_btn.pack(side="right")

    def _open_log_file(self):
        if self.log_file and self.log_file.exists():
            try:
                if sys.platform == "win32":
                    os.startfile(str(self.log_file))
                elif sys.platform == "darwin":
                    subprocess.run(["open", str(self.log_file)], check=False)
                else:
                    subprocess.run(["xdg-open", str(self.log_file)], check=False)
            except Exception as e:
                messagebox.showinfo("Log File", f"Log path: {self.log_file}\n(Error opening: {e})")
        else:
            messagebox.showinfo("Logs", "Logs are currently streaming to terminal.")

    def _poll_live_state(self):
        if self._is_closing or not self.root:
            return

        def _fetch():
            try:
                import urllib.request
                import json
                port = self.urls.get("port", "8000")
                url = f"http://127.0.0.1:{port}/api/state"
                req = urllib.request.Request(url, headers={"User-Agent": "TikiHub/1.0"})
                with urllib.request.urlopen(req, timeout=1.2) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if self.root:
                            self.root.after(0, lambda: self._apply_state(data))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()
        if not self._is_closing and self.root:
            self.root.after(3000, self._poll_live_state)

    def _apply_state(self, state: dict):
        if not self.root or self._is_closing:
            return
        track = state.get("current_track")
        q_count = len(state.get("queue", []))

        if track and track.get("title"):
            artist = track.get("artist") or "Unknown"
            title = track.get("title")
            singer = track.get("singer_name") or "Singer"
            self.ticker_title.config(text=f"NOW SINGING: {singer} 🎤", fg="#ffd166")
            self.ticker_lbl.config(text=f"♪ {artist} - {title}  •  ({q_count} up next in queue)", fg="#cbd5e1")
        else:
            self.ticker_title.config(text="STAGE STATUS: Intermission (Open Mic)", fg="#00f0ff")
            if q_count > 0:
                self.ticker_lbl.config(text=f"Queue has {q_count} song(s) lined up! Ready for next singer.", fg="#ffd166")
            else:
                self.ticker_lbl.config(text="Queue is open • Ready for singers to scan and pick songs", fg="#94a3b8")

    def _on_window_close(self):
        if self.is_tray_active:
            res = messagebox.askyesno(
                "Tiki Karaoke",
                "Do you want to shut down Tiki Karaoke?\n\nClick 'No' to minimize to the System Tray and keep music running in the background."
            )
            if res:
                self._shutdown_and_exit()
            else:
                self.root.withdraw()
        else:
            if messagebox.askokcancel("Exit Tiki Karaoke", "Are you sure you want to shut down Tiki Karaoke and stop all music?"):
                self._shutdown_and_exit()

    def _confirm_shutdown(self):
        if messagebox.askokcancel("Shut Down Lounge", "Stop all music and shut down the Tiki Karaoke server?"):
            self._shutdown_and_exit()

    def _shutdown_and_exit(self):
        self._is_closing = True
        if self.on_shutdown:
            try:
                self.on_shutdown()
            except Exception:
                pass
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
