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

from importer.tiki_importer.config import get_default_compuhost_path, get_default_db_path
from importer.tiki_importer.engine import TikiImportEngine


class CompuHostMigrationWindow:
    """
    Dedicated migration wizard for directly importing CompuHost databases (SongList.tps / .ciqbackup)
    into Tiki Karaoke without requiring a raw directory scan.
    """
    def __init__(self, parent: "tk.Tk", target_db_path: str):
        if not HAS_GUI:
            raise RuntimeError("Tkinter GUI is not available.")

        self.parent = parent
        self.target_db_path = target_db_path

        self.win = tk.Toplevel(parent)
        self.win.title("🌴 CompuHost Database Migration Wizard — Tiki Karaoke")
        self.win.geometry("1020x720")
        self.win.minsize(860, 580)
        self.win.configure(bg="#0d0814")

        # Make transient to parent
        self.win.transient(parent)

        # Variables
        default_path = get_default_compuhost_path()
        self.var_tps_path = tk.StringVar(value=default_path)
        self.var_remap_from = tk.StringVar(value="")
        self.var_remap_to = tk.StringVar(value="")
        self.var_skip_missing = tk.BooleanVar(value=True)
        self.var_enrich = tk.BooleanVar(value=True)

        # State
        self.is_running = False
        self._cancel_requested = False
        self._worker_thread: Optional[threading.Thread] = None
        self._records: List[Dict[str, Any]] = []

        # Build UI
        self._build_header()
        self._build_config_card()
        self._build_actions_card()
        self._build_stats_card()
        self._build_table_card()

        # Run initial auto-detection
        self._auto_detect(initial=True)

    def _build_header(self):
        header = tk.Frame(self.win, bg="#0d0814")
        header.pack(fill="x", padx=20, pady=(14, 8))

        lbl_title = tk.Label(
            header,
            text="🌴 CompuHost Database Migration Wizard",
            font=("Helvetica", 15, "bold"),
            fg="#00f0ff",
            bg="#0d0814",
        )
        lbl_title.pack(anchor="w")

        lbl_sub = tk.Label(
            header,
            text="Directly imports tracks from SongList.tps  •  Validates file paths on disk  •  Zero scanning overhead",
            font=("Helvetica", 9),
            fg="#94a3b8",
            bg="#0d0814",
        )
        lbl_sub.pack(anchor="w", pady=(2, 0))

    def _build_config_card(self):
        card = tk.Frame(self.win, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        card.pack(fill="x", padx=20, pady=6)

        inner = tk.Frame(card, bg="#130d1f")
        inner.pack(fill="x", padx=12, pady=10)
        inner.columnconfigure(1, weight=1)

        # Row 0: CompuHost TPS Path
        tk.Label(inner, text="CompuHost DB:", font=("Helvetica", 9, "bold"), fg="#ffd166", bg="#130d1f").grid(row=0, column=0, sticky="w", pady=4)
        entry_tps = tk.Entry(inner, textvariable=self.var_tps_path, font=("Courier", 8), bg="#1a1325", fg="#cbd5e1", insertbackground="#00f0ff", relief="flat")
        entry_tps.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=4, ipady=4)

        btn_box = tk.Frame(inner, bg="#130d1f")
        btn_box.grid(row=0, column=2, sticky="e", pady=4)

        btn_browse = tk.Button(btn_box, text="Browse Custom...", font=("Helvetica", 8, "bold"), bg="#1a1325", fg="#00f0ff", relief="flat", cursor="hand2", command=self._browse_custom)
        btn_browse.pack(side="left", padx=(0, 4))

        btn_auto = tk.Button(btn_box, text="🔍 Auto-Detect", font=("Helvetica", 8), bg="#1a1325", fg="#ffd166", relief="flat", cursor="hand2", command=self._auto_detect)
        btn_auto.pack(side="left", padx=(0, 4))

        btn_clear = tk.Button(btn_box, text="✕ Clear", font=("Helvetica", 8), bg="#1a1325", fg="#94a3b8", relief="flat", cursor="hand2", command=self._clear_path)
        btn_clear.pack(side="left")

        # Auto-detect status label
        self.lbl_auto_status = tk.Label(inner, text="", font=("Helvetica", 8), fg="#39ff14", bg="#130d1f")
        self.lbl_auto_status.grid(row=1, column=1, sticky="w", padx=8, pady=(0, 4))

        # Row 2: Path Remapping
        remap_frame = tk.Frame(inner, bg="#130d1f")
        remap_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 6))
        remap_frame.columnconfigure(1, weight=1)
        remap_frame.columnconfigure(3, weight=1)

        tk.Label(remap_frame, text="Path Remapping:", font=("Helvetica", 9, "bold"), fg="#ff2a85", bg="#130d1f").grid(row=0, column=0, sticky="w", pady=2)
        tk.Label(remap_frame, text="Old Prefix:", font=("Helvetica", 8), fg="#94a3b8", bg="#130d1f").grid(row=0, column=1, sticky="w", padx=(8, 2))
        entry_rf = tk.Entry(remap_frame, textvariable=self.var_remap_from, font=("Courier", 8), bg="#1a1325", fg="#cbd5e1", relief="flat")
        entry_rf.grid(row=0, column=2, sticky="ew", padx=(0, 8), ipady=3)

        tk.Label(remap_frame, text="New Prefix:", font=("Helvetica", 8), fg="#94a3b8", bg="#130d1f").grid(row=0, column=3, sticky="w", padx=(0, 2))
        entry_rt = tk.Entry(remap_frame, textvariable=self.var_remap_to, font=("Courier", 8), bg="#1a1325", fg="#cbd5e1", relief="flat")
        entry_rt.grid(row=0, column=4, sticky="ew", padx=(0, 6), ipady=3)

        btn_browse_remap = tk.Button(remap_frame, text="Browse Folder...", font=("Helvetica", 8), bg="#1a1325", fg="#00f0ff", relief="flat", cursor="hand2", command=self._browse_remap_folder)
        btn_browse_remap.grid(row=0, column=5, sticky="e")

        # Row 3: Checkboxes
        opts_frame = tk.Frame(card, bg="#130d1f")
        opts_frame.pack(fill="x", padx=12, pady=(0, 8))

        chk_skip = tk.Checkbutton(
            opts_frame,
            text="⚠️ Skip missing files (only import tracks physically verified on disk)",
            variable=self.var_skip_missing,
            font=("Helvetica", 9),
            fg="#cbd5e1",
            bg="#130d1f",
            activebackground="#130d1f",
            activeforeground="#00f0ff",
            selectcolor="#1a1325",
        )
        chk_skip.pack(side="left", padx=(0, 16))

        chk_enrich = tk.Checkbutton(
            opts_frame,
            text="✨ Web Metadata Enrichment (iTunes API lookup for Year & Genre)",
            variable=self.var_enrich,
            font=("Helvetica", 9),
            fg="#cbd5e1",
            bg="#130d1f",
            activebackground="#130d1f",
            activeforeground="#00f0ff",
            selectcolor="#1a1325",
        )
        chk_enrich.pack(side="left")

    def _build_actions_card(self):
        act_frame = tk.Frame(self.win, bg="#0d0814")
        act_frame.pack(fill="x", padx=20, pady=(4, 6))

        self.btn_preview = tk.Button(
            act_frame,
            text="🔍 Preview Migration (Dry Run)",
            font=("Helvetica", 9, "bold"),
            bg="#1a1424",
            fg="#ffd166",
            activebackground="#2a1f3d",
            activeforeground="#ffd166",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=6,
            command=lambda: self._start_migration(dry_run=True),
        )
        self.btn_preview.pack(side="left", padx=(0, 10))

        self.btn_migrate = tk.Button(
            act_frame,
            text="🚀 Run Full Migration",
            font=("Helvetica", 9, "bold"),
            bg="#00f0ff",
            fg="#0d0814",
            activebackground="#ff2a85",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            padx=18,
            pady=6,
            command=lambda: self._start_migration(dry_run=False),
        )
        self.btn_migrate.pack(side="left", padx=(0, 10))

        self.btn_cancel = tk.Button(
            act_frame,
            text="Cancel",
            font=("Helvetica", 9),
            bg="#1a1325",
            fg="#94a3b8",
            relief="flat",
            state="disabled",
            padx=12,
            pady=6,
            command=self._cancel_migration,
        )
        self.btn_cancel.pack(side="left")

    def _build_stats_card(self):
        stats_frame = tk.Frame(self.win, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        stats_frame.pack(fill="x", padx=20, pady=4)

        top_row = tk.Frame(stats_frame, bg="#130d1f")
        top_row.pack(fill="x", padx=12, pady=(8, 4))

        self.lbl_status = tk.Label(
            top_row,
            text="Ready. Click 'Preview Migration' to test or 'Run Full Migration' to write into karaoke.db.",
            font=("Helvetica", 9),
            fg="#00f0ff",
            bg="#130d1f",
        )
        self.lbl_status.pack(side="left")

        self.lbl_counts = tk.Label(
            top_row,
            text="Total: 0  •  Verified: 0  •  Missing: 0  •  Enriched: 0  •  In DB: 0",
            font=("Helvetica", 8, "bold"),
            fg="#cbd5e1",
            bg="#130d1f",
        )
        self.lbl_counts.pack(side="right")

        self.progress_bar = ttk.Progressbar(stats_frame, style="Tiki.Horizontal.TProgressbar", mode="determinate")
        self.progress_bar.pack(fill="x", padx=12, pady=(0, 8))

    def _build_table_card(self):
        card = tk.Frame(self.win, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        card.pack(fill="both", expand=True, padx=20, pady=(4, 14))

        columns = ("status", "artist", "title", "year", "genre", "media", "exists", "path")
        self.tree = ttk.Treeview(card, columns=columns, show="headings", style="Tiki.Treeview")

        self.tree.heading("status", text="STATUS")
        self.tree.heading("artist", text="ARTIST")
        self.tree.heading("title", text="TITLE")
        self.tree.heading("year", text="YEAR")
        self.tree.heading("genre", text="GENRE")
        self.tree.heading("media", text="TYPE")
        self.tree.heading("exists", text="FILE ON DISK")
        self.tree.heading("path", text="TARGET FILE PATH")

        self.tree.column("status", width=110, anchor="center")
        self.tree.column("artist", width=170, anchor="w")
        self.tree.column("title", width=210, anchor="w")
        self.tree.column("year", width=55, anchor="center")
        self.tree.column("genre", width=95, anchor="w")
        self.tree.column("media", width=65, anchor="center")
        self.tree.column("exists", width=90, anchor="center")
        self.tree.column("path", width=280, anchor="w")

        v_scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        h_scroll = ttk.Scrollbar(card, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        card.rowconfigure(0, weight=1)
        card.columnconfigure(0, weight=1)

    def _auto_detect(self, initial: bool = False):
        detected = get_default_compuhost_path()
        if detected and Path(detected).is_file():
            self.var_tps_path.set(detected)
            self.lbl_auto_status.config(
                text=f"✓ Auto-detected CompuHost database: {detected}",
                fg="#39ff14"
            )
        else:
            self.lbl_auto_status.config(
                text="No default CompuHost database found on standard drives. Please click 'Browse Custom...'",
                fg="#ffd166" if not initial else "#94a3b8"
            )

    def _browse_custom(self):
        path = filedialog.askopenfilename(
            title="Select Custom CompuHost Database File",
            filetypes=[
                ("CompuHost Files (*.tps, *.ciqbackup, *.csv)", "*.tps;*.ciqbackup;*.csv"),
                ("TopSpeed Database (*.tps)", "*.tps"),
                ("CatalogIQ Backup (*.ciqbackup)", "*.ciqbackup"),
                ("CompuHost Export (*.csv)", "*.csv"),
                ("All Files", "*.*"),
            ],
            initialdir=str(Path(self.var_tps_path.get()).parent if self.var_tps_path.get() else Path.home()),
        )
        if path:
            self.var_tps_path.set(path)
            self.lbl_auto_status.config(text=f"Custom database selected: {path}", fg="#00f0ff")

    def _browse_remap_folder(self):
        folder = filedialog.askdirectory(title="Select New Base Karaoke Media Folder")
        if folder:
            self.var_remap_to.set(folder)

    def _clear_path(self):
        self.var_tps_path.set("")
        self.lbl_auto_status.config(text="")

    def _cancel_migration(self):
        if self.is_running:
            self._cancel_requested = True
            self.lbl_status.config(text="Cancelling migration...")

    def _start_migration(self, dry_run: bool):
        if self.is_running:
            return

        tps_path = self.var_tps_path.get().strip()
        if not tps_path or not Path(tps_path).is_file():
            messagebox.showerror("Invalid File", "Please select a valid CompuHost database (.tps, .ciqbackup, or .csv).")
            return

        if not self.target_db_path:
            messagebox.showerror("Invalid Target DB", "Target karaoke.db is not defined.")
            return

        if not dry_run:
            ans = messagebox.askyesno(
                "Confirm Full Migration",
                f"Are you sure you want to directly migrate tracks from:\n{tps_path}\n\nInto:\n{self.target_db_path}?",
            )
            if not ans:
                return

        for item in self.tree.get_children():
            self.tree.delete(item)

        self._records.clear()
        self.is_running = True
        self._cancel_requested = False

        self.btn_preview.config(state="disabled")
        self.btn_migrate.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.progress_bar["value"] = 0

        mode_text = "Previewing Migration (Dry Run)" if dry_run else "Importing to Database"
        self.lbl_status.config(text=f"Starting: {mode_text}...")

        self._worker_thread = threading.Thread(
            target=self._worker_run,
            args=(
                tps_path,
                self.target_db_path,
                self.var_remap_from.get().strip(),
                self.var_remap_to.get().strip(),
                self.var_skip_missing.get(),
                dry_run,
                self.var_enrich.get(),
            ),
            daemon=True,
        )
        self._worker_thread.start()

    def _worker_run(
        self,
        tps_path: str,
        db_path: str,
        remap_from: str,
        remap_to: str,
        skip_missing: bool,
        dry_run: bool,
        enable_enrich: bool,
    ):
        try:
            engine = TikiImportEngine(db_path=db_path, enable_enrichment=enable_enrich)

            def on_progress(idx: int, total: int, record: Dict[str, Any]):
                if self._cancel_requested:
                    raise StopIteration("Migration cancelled by user")
                self.win.after(0, self._on_item_processed, idx, total, record)

            engine.migrate_compuhost(
                compuhost_path=tps_path,
                remap_from=remap_from,
                remap_to=remap_to,
                skip_missing=skip_missing,
                dry_run=dry_run,
                progress_callback=on_progress,
            )

            self.win.after(0, self._on_worker_completed, None, dry_run)
        except StopIteration:
            self.win.after(0, self._on_worker_completed, "Migration cancelled.", dry_run)
        except Exception as e:
            self.win.after(0, self._on_worker_completed, str(e), dry_run)

    def _on_item_processed(self, idx: int, total: int, record: Dict[str, Any]):
        self._records.append(record)

        pct = (idx / total) * 100 if total > 0 else 0
        self.progress_bar["value"] = pct

        status_tag = record["status"].upper().replace("_", " ")
        year_str = str(record["release_year"]) if record["release_year"] else "-"
        exists_str = "YES" if record.get("file_exists") else "NO"

        item_id = self.tree.insert(
            "",
            "end",
            values=(
                status_tag,
                record["artist"],
                record["title"],
                year_str,
                record["genre"] or "-",
                record["media_type"].upper(),
                exists_str,
                record["path"],
            ),
        )
        self.tree.see(item_id)

        verified = sum(1 for r in self._records if r.get("file_exists"))
        missing = sum(1 for r in self._records if not r.get("file_exists"))
        enriched = sum(1 for r in self._records if "ENRICH" in r["status"].upper())
        indb = sum(1 for r in self._records if "ALREADY" in r["status"].upper() or "UPDATED" in r["status"].upper())

        self.lbl_status.config(text=f"Processing {idx} of {total}: {record['title']} ({record['artist']})")
        self.lbl_counts.config(text=f"Total: {len(self._records)}  •  Verified: {verified}  •  Missing: {missing}  •  Enriched: {enriched}  •  In DB: {indb}")

    def _on_worker_completed(self, error_msg: Optional[str], dry_run: bool):
        self.is_running = False
        self.btn_preview.config(state="normal")
        self.btn_migrate.config(state="normal")
        self.btn_cancel.config(state="disabled")

        if error_msg:
            self.lbl_status.config(text=f"Finished: {error_msg}")
            if error_msg != "Migration cancelled.":
                messagebox.showerror("Migration Error", f"An error occurred during migration:\n{error_msg}")
        else:
            total = len(self._records)
            verified = sum(1 for r in self._records if r.get("file_exists"))
            action = "Preview completed" if dry_run else "Full migration completed"
            self.lbl_status.config(text=f"✅ {action}! {verified} of {total} tracks verified on disk.")
            messagebox.showinfo("Done", f"{action}!\n{total} total records read from database.\n{verified} files verified on disk.")
