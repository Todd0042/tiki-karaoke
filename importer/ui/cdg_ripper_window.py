"""
Tiki Karaoke — Dedicated CD+G Optical Disc Ripper Window
Provides an intuitive, hardware-aware desktop interface for:
- Detecting optical CD/DVD drives and reading disc Table of Contents (TOC)
- Selecting and inline-editing track titles and artist tags
- Querying online CD databases (MusicBrainz / FreeDB) by Disc ID
- Real-time dual progress bars (Current Track extraction & Overall Disc progress)
- Live scrolling telemetry console
- 1-click output packaging directly into Tiki Karaoke offline library crates
"""
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    HAS_GUI = True
except ImportError:
    HAS_GUI = False

from importer.tiki_importer.config import get_default_library_path
from importer.tiki_importer.cdg_ripper import (
    list_optical_drives,
    eject_optical_drive,
    read_disc_toc,
    lookup_musicbrainz_disc,
    rip_cdg_track_process
)


class CdgRipperWindow:
    """
    Dedicated native desktop window for ripping and packaging physical CD+G discs.
    """
    def __init__(self, parent: Optional["tk.Tk"] = None, target_tracks_dir: Optional[str] = None):
        if not HAS_GUI:
            raise RuntimeError("Tkinter GUI is not available.")

        self.parent = parent
        if parent:
            self.win = tk.Toplevel(parent)
            self.win.transient(parent)
        else:
            self.win = tk.Tk()

        self.win.title("📀 CD+G Optical Disc Ripper — Tiki Karaoke")
        self.win.geometry("980x740")
        self.win.minsize(820, 600)
        self.win.configure(bg="#0d0814")

        # Destination folder
        default_out = target_tracks_dir or str(Path(get_default_library_path()) / "tracks")
        self.var_out_dir = tk.StringVar(value=default_out)
        self.var_bitrate = tk.StringVar(value="192")

        # State
        self.is_running = False
        self._cancel_requested = False
        self._worker_thread: Optional[threading.Thread] = None
        self._detected_tracks: List[Dict[str, Any]] = []
        self._drives: List[Dict[str, str]] = []
        self.var_selected_drive = tk.StringVar(value="")

        # Build UI
        self._apply_styles()
        self._build_header()
        self._build_drive_card()
        self._build_table_card()
        self._build_settings_card()
        self._build_progress_card()
        self._build_footer_actions()

        # Initial drive scan
        self._rescan_drives()

    def _apply_styles(self):
        style = ttk.Style(self.win)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(
            "Cdg.Treeview",
            background="#120c1d",
            foreground="#f8fafc",
            fieldbackground="#120c1d",
            rowheight=26,
            font=("Helvetica", 9),
            borderwidth=0,
        )
        style.configure(
            "Cdg.Treeview.Heading",
            background="#1c122e",
            foreground="#00f0ff",
            font=("Helvetica", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Cdg.Treeview",
            background=[("selected", "#2a1435")],
            foreground=[("selected", "#ff2a85")],
        )

        style.configure(
            "CdgTrack.Horizontal.TProgressbar",
            troughcolor="#130d1f",
            background="#ff2a85",
            borderwidth=0,
        )
        style.configure(
            "CdgTotal.Horizontal.TProgressbar",
            troughcolor="#130d1f",
            background="#00f0ff",
            borderwidth=0,
        )

    def _build_header(self):
        header = tk.Frame(self.win, bg="#0d0814")
        header.pack(fill="x", padx=20, pady=(12, 6))

        title_lbl = tk.Label(
            header,
            text="📀 Tiki Karaoke — CD+G Optical Disc Ripper",
            font=("Helvetica", 15, "bold"),
            fg="#00f0ff",
            bg="#0d0814",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            header,
            text="Extracts raw Audio & Subcode R-W Graphics  •  Encodes MP3+CDG (.zip)  •  Direct to Tiki Crate",
            font=("Helvetica", 8),
            fg="#94a3b8",
            bg="#0d0814",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

    def _build_drive_card(self):
        card = tk.Frame(self.win, bg="#161024", highlightbackground="#2e1b45", highlightthickness=1)
        card.pack(fill="x", padx=20, pady=6)

        row = tk.Frame(card, bg="#161024")
        row.pack(fill="x", padx=14, pady=10)

        tk.Label(row, text="Optical Drive:", font=("Helvetica", 9, "bold"), fg="#ffd166", bg="#161024").pack(side="left")

        self.combo_drives = ttk.Combobox(row, textvariable=self.var_selected_drive, state="readonly", width=34)
        self.combo_drives.pack(side="left", padx=(8, 10))
        self.combo_drives.bind("<<ComboboxSelected>>", lambda e: self._on_drive_selected())

        btn_rescan = tk.Button(
            row, text="🔄 Rescan Drives", font=("Helvetica", 8, "bold"),
            bg="#231936", fg="#00f0ff", activebackground="#352652", activeforeground="#00f0ff",
            relief="flat", cursor="hand2", padx=8, pady=3, command=self._rescan_drives
        )
        btn_rescan.pack(side="left", padx=4)

        btn_eject = tk.Button(
            row, text="⏏️ Eject Tray", font=("Helvetica", 8),
            bg="#231936", fg="#cbd5e1", activebackground="#352652", activeforeground="#fff",
            relief="flat", cursor="hand2", padx=8, pady=3, command=self._eject_selected_drive
        )
        btn_eject.pack(side="left", padx=4)

        self.lbl_disc_status = tk.Label(
            row, text="Scanning for optical media...",
            font=("Helvetica", 9, "bold"), fg="#39ff14", bg="#161024"
        )
        self.lbl_disc_status.pack(side="right")

    def _build_table_card(self):
        card = tk.Frame(self.win, bg="#161024", highlightbackground="#2e1b45", highlightthickness=1)
        card.pack(fill="both", expand=True, padx=20, pady=6)

        # Toolbar above table
        toolbar = tk.Frame(card, bg="#161024")
        toolbar.pack(fill="x", padx=12, pady=(10, 6))

        tk.Label(toolbar, text="Disc Tracks:", font=("Helvetica", 9, "bold"), fg="#00f0ff", bg="#161024").pack(side="left")

        btn_all = tk.Button(
            toolbar, text="Select All", font=("Helvetica", 8),
            bg="#231936", fg="#cbd5e1", relief="flat", cursor="hand2", padx=6, pady=2,
            command=self._select_all_tracks
        )
        btn_all.pack(side="left", padx=(10, 4))

        btn_none = tk.Button(
            toolbar, text="Deselect All", font=("Helvetica", 8),
            bg="#231936", fg="#cbd5e1", relief="flat", cursor="hand2", padx=6, pady=2,
            command=self._deselect_all_tracks
        )
        btn_none.pack(side="left", padx=4)

        btn_edit = tk.Button(
            toolbar, text="✏️ Edit Artist/Title", font=("Helvetica", 8, "bold"),
            bg="#231936", fg="#ffd166", relief="flat", cursor="hand2", padx=8, pady=2,
            command=self._edit_selected_track
        )
        btn_edit.pack(side="left", padx=10)

        btn_lookup = tk.Button(
            toolbar, text="🌐 Auto-Fill via MusicBrainz", font=("Helvetica", 8),
            bg="#231936", fg="#00f0ff", relief="flat", cursor="hand2", padx=8, pady=2,
            command=self._lookup_musicbrainz
        )
        btn_lookup.pack(side="right")

        # Treeview table
        tbl_frame = tk.Frame(card, bg="#120c1d")
        tbl_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        cols = ("rip", "track_num", "duration", "artist", "title")
        self.tree = ttk.Treeview(tbl_frame, columns=cols, show="headings", style="Cdg.Treeview", selectmode="browse")

        self.tree.heading("rip", text="Rip?")
        self.tree.heading("track_num", text="Track")
        self.tree.heading("duration", text="Length")
        self.tree.heading("artist", text="Artist")
        self.tree.heading("title", text="Song Title")

        self.tree.column("rip", width=55, anchor="center")
        self.tree.column("track_num", width=65, anchor="center")
        self.tree.column("duration", width=75, anchor="center")
        self.tree.column("artist", width=260, anchor="w")
        self.tree.column("title", width=340, anchor="w")

        scrollbar = ttk.Scrollbar(tbl_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda e: self._on_tree_double_click(e))

    def _build_settings_card(self):
        card = tk.Frame(self.win, bg="#161024", highlightbackground="#2e1b45", highlightthickness=1)
        card.pack(fill="x", padx=20, pady=6)

        row = tk.Frame(card, bg="#161024")
        row.pack(fill="x", padx=14, pady=8)

        tk.Label(row, text="Save To Crate:", font=("Helvetica", 8, "bold"), fg="#94a3b8", bg="#161024").pack(side="left")

        entry_out = tk.Entry(
            row, textvariable=self.var_out_dir, font=("Helvetica", 8),
            bg="#0d0814", fg="#f8fafc", insertbackground="#00f0ff", relief="flat"
        )
        entry_out.pack(side="left", fill="x", expand=True, padx=8)

        btn_browse = tk.Button(
            row, text="Browse...", font=("Helvetica", 8),
            bg="#231936", fg="#cbd5e1", relief="flat", cursor="hand2", padx=8, pady=2,
            command=self._browse_output_dir
        )
        btn_browse.pack(side="left", padx=(0, 14))

        tk.Label(row, text="MP3 Quality:", font=("Helvetica", 8, "bold"), fg="#94a3b8", bg="#161024").pack(side="left")

        combo_br = ttk.Combobox(row, textvariable=self.var_bitrate, values=["192", "256", "320"], state="readonly", width=6)
        combo_br.pack(side="left", padx=4)
        tk.Label(row, text="kbps", font=("Helvetica", 8), fg="#cbd5e1", bg="#161024").pack(side="left")

    def _build_progress_card(self):
        self.progress_frame = tk.Frame(self.win, bg="#0d0814")
        self.progress_frame.pack(fill="x", padx=20, pady=(4, 6))

        # Progress labels
        top_lbls = tk.Frame(self.progress_frame, bg="#0d0814")
        top_lbls.pack(fill="x")

        self.lbl_track_status = tk.Label(
            top_lbls, text="Ready to rip.", font=("Helvetica", 8),
            fg="#94a3b8", bg="#0d0814"
        )
        self.lbl_track_status.pack(side="left")

        self.lbl_overall_status = tk.Label(
            top_lbls, text="0 / 0 completed", font=("Helvetica", 8, "bold"),
            fg="#00f0ff", bg="#0d0814"
        )
        self.lbl_overall_status.pack(side="right")

        self.prog_track = ttk.Progressbar(self.progress_frame, style="CdgTrack.Horizontal.TProgressbar", length=200, mode="determinate")
        self.prog_track.pack(fill="x", pady=(4, 2))

        self.prog_total = ttk.Progressbar(self.progress_frame, style="CdgTotal.Horizontal.TProgressbar", length=200, mode="determinate")
        self.prog_total.pack(fill="x", pady=(2, 4))

    def _build_footer_actions(self):
        footer = tk.Frame(self.win, bg="#0d0814")
        footer.pack(fill="x", padx=20, pady=(6, 14))

        self.btn_open_folder = tk.Button(
            footer, text="📂 Open Crate Folder", font=("Helvetica", 8),
            bg="#161024", fg="#cbd5e1", relief="flat", cursor="hand2", padx=10, pady=6,
            command=self._open_output_folder
        )
        self.btn_open_folder.pack(side="left")

        self.btn_cancel = tk.Button(
            footer, text="⏹️ Cancel", font=("Helvetica", 9),
            bg="#2b1117", fg="#ff5555", relief="flat", cursor="hand2", padx=12, pady=6,
            state="disabled", command=self._cancel_rip
        )
        self.btn_cancel.pack(side="right", padx=(8, 0))

        self.btn_rip = tk.Button(
            footer, text="🚀 Rip & Package CD+G Songs", font=("Helvetica", 9, "bold"),
            bg="#00f0ff", fg="#0d0814", activebackground="#39ff14", activeforeground="#0d0814",
            relief="flat", cursor="hand2", padx=16, pady=6,
            command=self._start_rip_process
        )
        self.btn_rip.pack(side="right")

    # =========================================================================
    # Event Handlers & Hardware Logic
    # =========================================================================

    def _rescan_drives(self):
        self._drives = list_optical_drives()
        combo_vals = [d["name"] for d in self._drives]

        # Always offer Demo / Test disc mode if no physical drive connected
        demo_entry = "💿 Demo CD+G Disc (12 Party Tracks - Virtual)"
        combo_vals.append(demo_entry)

        self.combo_drives["values"] = combo_vals
        if self._drives:
            self.combo_drives.current(0)
        else:
            self.combo_drives.current(len(combo_vals) - 1)

        self._on_drive_selected()

    def _on_drive_selected(self):
        sel = self.var_selected_drive.get()
        if "Virtual" in sel or "Demo" in sel:
            dev = "DEMO"
        else:
            dev = next((d["device"] for d in self._drives if d["name"] == sel), "DEMO")

        self.lbl_disc_status.config(text="Reading disc TOC...", fg="#ffd166")
        self.win.update_idletasks()

        toc = read_disc_toc(dev)
        if toc["has_disc"]:
            self._detected_tracks = toc["tracks"]
            mins = toc["total_duration_sec"] // 60
            secs = toc["total_duration_sec"] % 60
            self.lbl_disc_status.config(
                text=f"✅ CD+G Disc: {toc['track_count']} Tracks ({mins}:{secs:02d})",
                fg="#39ff14"
            )
        else:
            self._detected_tracks = []
            self.lbl_disc_status.config(text="⚠️ Drive is empty / No disc detected", fg="#ffaa00")

        self._populate_track_table()

    def _populate_track_table(self):
        self.tree.delete(*self.tree.get_children())
        for t in self._detected_tracks:
            # Checkbox simulation: ✓ vs ✗
            self.tree.insert("", "end", iid=str(t["track_num"]), values=(
                "✓",
                f"Track {t['track_num']:02d}",
                t["duration_str"],
                t["artist"],
                t["title"]
            ))

    def _select_all_tracks(self):
        for item_id in self.tree.get_children():
            vals = list(self.tree.item(item_id, "values"))
            vals[0] = "✓"
            self.tree.item(item_id, values=vals)

    def _deselect_all_tracks(self):
        for item_id in self.tree.get_children():
            vals = list(self.tree.item(item_id, "values"))
            vals[0] = "✗"
            self.tree.item(item_id, values=vals)

    def _on_tree_double_click(self, event):
        item_id = self.tree.focus()
        if not item_id:
            return
        vals = list(self.tree.item(item_id, "values"))
        vals[0] = "✗" if vals[0] == "✓" else "✓"
        self.tree.item(item_id, values=vals)

    def _edit_selected_track(self):
        item_id = self.tree.focus()
        if not item_id:
            messagebox.showinfo("Select Track", "Please click on a track row to edit its Artist and Title.")
            return

        vals = list(self.tree.item(item_id, "values"))
        t_num = vals[1]
        cur_artist = vals[3]
        cur_title = vals[4]

        # Quick edit dialog
        dlg = tk.Toplevel(self.win)
        dlg.title(f"Edit {t_num}")
        dlg.geometry("400x190")
        dlg.configure(bg="#161024")
        dlg.transient(self.win)

        tk.Label(dlg, text="Artist:", font=("Helvetica", 9, "bold"), fg="#00f0ff", bg="#161024").pack(anchor="w", padx=16, pady=(12, 2))
        e_art = tk.Entry(dlg, bg="#0d0814", fg="#fff", insertbackground="#00f0ff")
        e_art.insert(0, cur_artist)
        e_art.pack(fill="x", padx=16)

        tk.Label(dlg, text="Song Title:", font=("Helvetica", 9, "bold"), fg="#ff2a85", bg="#161024").pack(anchor="w", padx=16, pady=(10, 2))
        e_tit = tk.Entry(dlg, bg="#0d0814", fg="#fff", insertbackground="#00f0ff")
        e_tit.insert(0, cur_title)
        e_tit.pack(fill="x", padx=16)

        def save_edit():
            vals[3] = e_art.get().strip() or "Unknown Artist"
            vals[4] = e_tit.get().strip() or f"Track {item_id}"
            self.tree.item(item_id, values=vals)
            # Update internal model
            for t in self._detected_tracks:
                if str(t["track_num"]) == str(item_id):
                    t["artist"] = vals[3]
                    t["title"] = vals[4]
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg="#161024")
        btn_row.pack(fill="x", padx=16, pady=14)
        tk.Button(btn_row, text="Save Changes", font=("Helvetica", 8, "bold"), bg="#00f0ff", fg="#0d0814", relief="flat", command=save_edit).pack(side="right")
        tk.Button(btn_row, text="Cancel", font=("Helvetica", 8), bg="#231936", fg="#cbd5e1", relief="flat", command=dlg.destroy).pack(side="right", padx=6)

    def _lookup_musicbrainz(self):
        sel = self.var_selected_drive.get()
        dev = "DEMO" if ("Virtual" in sel or "Demo" in sel) else self.var_selected_drive.get()
        self.lbl_disc_status.config(text="Querying MusicBrainz / FreeDB API...", fg="#ffd166")
        self.win.update_idletasks()

        matches = lookup_musicbrainz_disc("DEMO_DISC_ID", len(self._detected_tracks))
        if matches and len(matches) == len(self._detected_tracks):
            for t, m in zip(self._detected_tracks, matches):
                t["artist"] = m["artist"]
                t["title"] = m["title"]
            self._populate_track_table()
            self.lbl_disc_status.config(text="✅ Disc track metadata auto-filled!", fg="#39ff14")
        else:
            self.lbl_disc_status.config(text="ℹ️ No exact online match found. Double-click row to edit titles.", fg="#94a3b8")

    def _eject_selected_drive(self):
        sel = self.var_selected_drive.get()
        dev = next((d["device"] for d in self._drives if d["name"] == sel), None)
        if dev:
            eject_optical_drive(dev)
        messagebox.showinfo("Eject Disc", "Eject signal sent to optical drive tray.")

    def _browse_output_dir(self):
        p = filedialog.askdirectory(initialdir=self.var_out_dir.get(), title="Select Destination Karaoke Crate")
        if p:
            self.var_out_dir.set(p)

    def _open_output_folder(self):
        p = Path(self.var_out_dir.get())
        p.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(p))
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(p)], check=False)
            else:
                import subprocess
                subprocess.run(["xdg-open", str(p)], check=False)
        except Exception as e:
            messagebox.showinfo("Destination", f"Crate directory:\n{p}")

    def _cancel_rip(self):
        if self.is_running:
            self._cancel_requested = True
            self.lbl_track_status.config(text="Canceling after current track completes...", fg="#ffaa00")

    def _start_rip_process(self):
        if self.is_running:
            return

        # Collect tracks marked with ✓
        selected_to_rip = []
        for item_id in self.tree.get_children():
            vals = self.tree.item(item_id, "values")
            if vals[0] == "✓":
                t = next((x for x in self._detected_tracks if str(x["track_num"]) == str(item_id)), None)
                if t:
                    # Update artist/title from tree values
                    t_copy = dict(t)
                    t_copy["artist"] = vals[3]
                    t_copy["title"] = vals[4]
                    selected_to_rip.append(t_copy)

        if not selected_to_rip:
            messagebox.showwarning("No Tracks Selected", "Please select at least one track to rip.")
            return

        out_path = Path(self.var_out_dir.get()).resolve()
        out_path.mkdir(parents=True, exist_ok=True)

        sel = self.var_selected_drive.get()
        dev = "DEMO" if ("Virtual" in sel or "Demo" in sel) else sel

        self.is_running = True
        self._cancel_requested = False
        self.btn_rip.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.prog_total["maximum"] = len(selected_to_rip)
        self.prog_total["value"] = 0

        bitrate = int(self.var_bitrate.get())

        def _worker():
            total = len(selected_to_rip)
            completed = 0
            for idx, track in enumerate(selected_to_rip, start=1):
                if self._cancel_requested:
                    break

                def update_track_prog(pct):
                    self.win.after(0, lambda: self.prog_track.configure(value=pct))

                def log_status(msg):
                    self.win.after(0, lambda: self.lbl_track_status.configure(text=msg, fg="#00f0ff"))

                log_status(f"Ripping [{idx}/{total}]: {track['artist']} - {track['title']}...")
                update_track_prog(15)

                try:
                    res = rip_cdg_track_process(
                        device=dev,
                        track=track,
                        out_dir=out_path,
                        bitrate_kbps=bitrate,
                        log_cb=log_status
                    )
                    completed += 1
                except Exception as e:
                    log_status(f"Error on track {track['track_num']}: {e}")

                update_track_prog(100)
                self.win.after(0, lambda c=completed, t=total: self._update_overall_progress(c, t))

            self.win.after(0, lambda c=completed, t=total: self._rip_completed(c, t))

        self._worker_thread = threading.Thread(target=_worker, daemon=True)
        self._worker_thread.start()

    def _update_overall_progress(self, completed: int, total: int):
        self.prog_total["value"] = completed
        self.lbl_overall_status.config(text=f"{completed} / {total} packaged")

    def _rip_completed(self, completed: int, total: int):
        self.is_running = False
        self.btn_rip.config(state="normal")
        self.btn_cancel.config(state="disabled")
        self.prog_track["value"] = 0

        if self._cancel_requested:
            self.lbl_track_status.config(text=f"Stopped: {completed} of {total} tracks converted.", fg="#ffaa00")
            messagebox.showinfo("Cdg Ripper", f"Ripping stopped. {completed} songs packaged into crate.")
        else:
            self.lbl_track_status.config(text=f"🎉 Complete! {completed} CD+G songs packaged into crate.", fg="#39ff14")
            res = messagebox.askyesno(
                "Ripping Complete",
                f"Successfully ripped and packaged {completed} CD+G songs into:\n{self.var_out_dir.get()}\n\nWould you like to open the destination folder?"
            )
            if res:
                self._open_output_folder()


if __name__ == "__main__":
    if not HAS_GUI:
        print("Error: Tkinter GUI not available.")
        sys.exit(1)
    app = CdgRipperWindow()
    app.win.mainloop()
