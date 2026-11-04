"""
Tiki Karaoke — Native Desktop Importer Window
Lightweight, zero-bloat Tkinter GUI styled with the dark neon Tiki aesthetic.
Provides dry-run preview, CompuHost catalog integration, and real-time iTunes enrichment.
"""
import os
import sys
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any

HAS_GUI = False
_gui_import_error = ""

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    HAS_GUI = True
except Exception as e:
    HAS_GUI = False
    _gui_import_error = str(e)

if HAS_GUI:
    from importer.tiki_importer.config import get_default_db_path, get_default_library_path
    from importer.tiki_importer.engine import TikiImportEngine


class TikiImporterApp:
    def __init__(self, initial_folder: Optional[str] = None, initial_db: Optional[str] = None):
        if not HAS_GUI:
            raise RuntimeError(f"Tkinter GUI is unavailable: {_gui_import_error}")

        self.root = tk.Tk()
        self.root.title("🌴 Tiki Karaoke — Smart Library Importer")
        self.root.geometry("1020x720")
        self.root.minsize(860, 580)
        self.root.configure(bg="#0d0814")

        # Configuration variables
        self.var_db_path = tk.StringVar(value=initial_db or get_default_db_path())
        self.var_folder_path = tk.StringVar(value=initial_folder or get_default_library_path())
        self.var_compuhost_path = tk.StringVar(value="")
        self.var_enrich = tk.BooleanVar(value=True)
        self.var_recursive = tk.BooleanVar(value=True)

        # Worker state
        self.is_running = False
        self._cancel_requested = False
        self._worker_thread: Optional[threading.Thread] = None
        self._scanned_records: List[Dict[str, Any]] = []
        self._icon_ref = None

        # Set Window Icon
        self._set_app_icon()

        # Theme & Styling
        self._setup_styles()

        # Layout
        self._build_header()
        self._build_config_card()
        self._build_actions_card()
        self._build_stats_card()
        self._build_table_card()

    def _set_app_icon(self):
        try:
            base_dir = Path(__file__).resolve().parent.parent
            icon_ico = base_dir / "assets" / "tiki-icon.ico"
            icon_png = base_dir / "assets" / "tiki-icon.png"
            if sys.platform == "win32" and icon_ico.exists():
                self.root.iconbitmap(str(icon_ico))
            elif icon_png.exists():
                img = tk.PhotoImage(file=str(icon_png))
                self.root.iconphoto(False, img)
                self._icon_ref = img
        except Exception:
            pass

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Treeview styling for dark theme
        style.configure(
            "Tiki.Treeview",
            background="#130d1f",
            foreground="#cbd5e1",
            fieldbackground="#130d1f",
            borderwidth=0,
            rowheight=26,
            font=("Helvetica", 9),
        )
        style.configure(
            "Tiki.Treeview.Heading",
            background="#1a1325",
            foreground="#00f0ff",
            font=("Helvetica", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Tiki.Treeview",
            background=[("selected", "#2a1435")],
            foreground=[("selected", "#ff2a85")],
        )

        # Progressbar styling
        style.configure(
            "Tiki.Horizontal.TProgressbar",
            troughcolor="#130d1f",
            background="#00f0ff",
            borderwidth=0,
        )

    def _build_header(self):
        header = tk.Frame(self.root, bg="#0d0814")
        header.pack(fill="x", padx=20, pady=(14, 8))

        # Title & Subtitle Left
        left_box = tk.Frame(header, bg="#0d0814")
        left_box.pack(side="left", anchor="w")

        lbl_title = tk.Label(
            left_box,
            text="🌴 Tiki Karaoke Importer & Metadata Enricher",
            font=("Helvetica", 16, "bold"),
            fg="#00f0ff",
            bg="#0d0814",
        )
        lbl_title.pack(anchor="w")

        lbl_sub = tk.Label(
            left_box,
            text="Intelligent YouTube Noise Stripper  •  iTunes Music API Autocorrect  •  Zero Bloat",
            font=("Helvetica", 9),
            fg="#94a3b8",
            bg="#0d0814",
        )
        lbl_sub.pack(anchor="w", pady=(2, 0))

        # Dedicated Tools on Right Header
        right_btns = tk.Frame(header, bg="#0d0814")
        right_btns.pack(side="right", anchor="e")

        btn_cdg = tk.Button(
            right_btns,
            text="📀 CD+G Ripper...",
            font=("Helvetica", 9, "bold"),
            bg="#1a1424",
            fg="#00f0ff",
            activebackground="#2a1f3d",
            activeforeground="#00f0ff",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=6,
            command=self._open_cdg_ripper,
        )
        btn_cdg.pack(side="left", padx=(0, 6))

        btn_compuhost = tk.Button(
            right_btns,
            text="📦 Import from CompuHost...",
            font=("Helvetica", 9, "bold"),
            bg="#1a1424",
            fg="#ffd166",
            activebackground="#2a1f3d",
            activeforeground="#ffd166",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=6,
            command=self._open_compuhost_migrator,
        )
        btn_compuhost.pack(side="left")

    def _open_cdg_ripper(self):
        from .cdg_ripper_window import CdgRipperWindow
        CdgRipperWindow(self.root, target_tracks_dir=self.var_folder_path.get().strip())

    def _open_compuhost_migrator(self):
        from .compuhost_window import CompuHostMigrationWindow
        CompuHostMigrationWindow(self.root, target_db_path=self.var_db_path.get().strip())

    def _build_config_card(self):
        card = tk.Frame(self.root, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        card.pack(fill="x", padx=20, pady=6)

        inner = tk.Frame(card, bg="#130d1f")
        inner.pack(fill="x", padx=12, pady=10)

        # Grid config
        inner.columnconfigure(1, weight=1)

        # 1. Database Path
        tk.Label(inner, text="Karaoke DB:", font=("Helvetica", 9, "bold"), fg="#ffd166", bg="#130d1f").grid(row=0, column=0, sticky="w", pady=4)
        entry_db = tk.Entry(inner, textvariable=self.var_db_path, font=("Courier", 8), bg="#1a1325", fg="#cbd5e1", insertbackground="#00f0ff", relief="flat")
        entry_db.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=4, ipady=4)
        btn_db = tk.Button(inner, text="Browse DB...", font=("Helvetica", 8, "bold"), bg="#1a1325", fg="#00f0ff", relief="flat", cursor="hand2", command=self._browse_db)
        btn_db.grid(row=0, column=2, sticky="e", pady=4)

        # 2. Folder Path
        tk.Label(inner, text="Media Folder:", font=("Helvetica", 9, "bold"), fg="#00f0ff", bg="#130d1f").grid(row=1, column=0, sticky="w", pady=4)
        entry_f = tk.Entry(inner, textvariable=self.var_folder_path, font=("Courier", 8), bg="#1a1325", fg="#cbd5e1", insertbackground="#00f0ff", relief="flat")
        entry_f.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=4, ipady=4)
        btn_f = tk.Button(inner, text="Browse Folder...", font=("Helvetica", 8, "bold"), bg="#1a1325", fg="#00f0ff", relief="flat", cursor="hand2", command=self._browse_folder)
        btn_f.grid(row=1, column=2, sticky="e", pady=4)

        # Checkboxes row
        opts_frame = tk.Frame(card, bg="#130d1f")
        opts_frame.pack(fill="x", padx=12, pady=(0, 8))

        chk_enrich = tk.Checkbutton(
            opts_frame,
            text="✨ Web Metadata Enrichment (iTunes API lookup for Year, Genre, & Swapped Names)",
            variable=self.var_enrich,
            font=("Helvetica", 9),
            fg="#cbd5e1",
            bg="#130d1f",
            activebackground="#130d1f",
            activeforeground="#00f0ff",
            selectcolor="#1a1325",
        )
        chk_enrich.pack(side="left", padx=(0, 16))

        chk_rec = tk.Checkbutton(
            opts_frame,
            text="📂 Scan Subfolders Recursively",
            variable=self.var_recursive,
            font=("Helvetica", 9),
            fg="#cbd5e1",
            bg="#130d1f",
            activebackground="#130d1f",
            activeforeground="#00f0ff",
            selectcolor="#1a1325",
        )
        chk_rec.pack(side="left")

    def _build_actions_card(self):
        btn_frame = tk.Frame(self.root, bg="#0d0814")
        btn_frame.pack(fill="x", padx=20, pady=4)

        # 1. Preview Scan Button (Dry Run)
        self.btn_preview = tk.Button(
            btn_frame,
            text="🔍 Preview Scan (Dry Run — Safe)",
            font=("Helvetica", 10, "bold"),
            bg="#122436",
            fg="#00f0ff",
            activebackground="#00f0ff",
            activeforeground="#0d0814",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=8,
            command=lambda: self._start_import_process(dry_run=True),
        )
        self.btn_preview.pack(side="left", padx=(0, 10))

        # 2. Run Full Import Button
        self.btn_import = tk.Button(
            btn_frame,
            text="🚀 Run Full Import (Write to Database)",
            font=("Helvetica", 10, "bold"),
            bg="#2a1435",
            fg="#ff2a85",
            activebackground="#ff2a85",
            activeforeground="#ffffff",
            relief="flat",
            cursor="hand2",
            padx=16,
            pady=8,
            command=lambda: self._start_import_process(dry_run=False),
        )
        self.btn_import.pack(side="left", padx=(0, 10))

        # 3. Cancel Button
        self.btn_cancel = tk.Button(
            btn_frame,
            text="⏹ Cancel",
            font=("Helvetica", 9),
            bg="#1a1325",
            fg="#94a3b8",
            activebackground="#2a1b40",
            activeforeground="#ffffff",
            relief="flat",
            state="disabled",
            padx=12,
            pady=8,
            command=self._cancel_process,
        )
        self.btn_cancel.pack(side="left")

    def _build_stats_card(self):
        stats_frame = tk.Frame(self.root, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        stats_frame.pack(fill="x", padx=20, pady=6)

        inner = tk.Frame(stats_frame, bg="#130d1f")
        inner.pack(fill="x", padx=12, pady=6)

        # Status label
        self.lbl_status = tk.Label(
            inner,
            text="Ready. Select folder and click 'Preview Scan'.",
            font=("Helvetica", 9),
            fg="#94a3b8",
            bg="#130d1f",
        )
        self.lbl_status.pack(side="left")

        # Badges
        self.lbl_counts = tk.Label(
            inner,
            text="Total: 0  •  Enriched: 0  •  In DB: 0",
            font=("Helvetica", 9, "bold"),
            fg="#ffd166",
            bg="#130d1f",
        )
        self.lbl_counts.pack(side="right")

        # Progress bar
        self.progress_bar = ttk.Progressbar(
            stats_frame,
            style="Tiki.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
        )
        self.progress_bar.pack(fill="x", padx=12, pady=(0, 6))

    def _build_table_card(self):
        table_frame = tk.Frame(self.root, bg="#130d1f", highlightbackground="#2a1b40", highlightthickness=1)
        table_frame.pack(fill="both", expand=True, padx=20, pady=(4, 14))

        columns = ("status", "artist", "title", "year", "genre", "brand", "type", "filename")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            style="Tiki.Treeview",
            selectmode="extended",
        )

        self.tree.heading("status", text="Status")
        self.tree.heading("artist", text="Artist")
        self.tree.heading("title", text="Song Title")
        self.tree.heading("year", text="Year")
        self.tree.heading("genre", text="Genre")
        self.tree.heading("brand", text="Brand")
        self.tree.heading("type", text="Type")
        self.tree.heading("filename", text="Original File")

        self.tree.column("status", width=110, anchor="center")
        self.tree.column("artist", width=180, anchor="w")
        self.tree.column("title", width=220, anchor="w")
        self.tree.column("year", width=60, anchor="center")
        self.tree.column("genre", width=120, anchor="w")
        self.tree.column("brand", width=110, anchor="w")
        self.tree.column("type", width=60, anchor="center")
        self.tree.column("filename", width=220, anchor="w")

        # Scrollbar
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    # --- UI Event Handlers ---
    def _browse_db(self):
        path = filedialog.askopenfilename(
            title="Select Tiki Karaoke SQLite Database",
            filetypes=[("SQLite DB", "*.db"), ("All Files", "*.*")],
            initialdir=str(Path(self.var_db_path.get()).parent if self.var_db_path.get() else Path.home()),
        )
        if path:
            self.var_db_path.set(path)

    def _browse_folder(self):
        path = filedialog.askdirectory(
            title="Select Media Folder to Scan",
            initialdir=self.var_folder_path.get() or str(Path.home()),
        )
        if path:
            self.var_folder_path.set(path)

    def _browse_compuhost(self):
        path = filedialog.askopenfilename(
            title="Select CompuHost Database (.tps, .ciqbackup, .csv)",
            filetypes=[
                ("CompuHost Files (*.tps, *.ciqbackup, *.csv)", "*.tps;*.ciqbackup;*.csv"),
                ("TopSpeed Database (*.tps)", "*.tps"),
                ("CatalogIQ Backup (*.ciqbackup)", "*.ciqbackup"),
                ("CompuHost CSV (*.csv)", "*.csv"),
                ("All Files", "*.*"),
            ],
        )
        if path:
            self.var_compuhost_path.set(path)

    def _cancel_process(self):
        if self.is_running:
            self._cancel_requested = True
            self.lbl_status.config(text="Cancelling scan...")

    def _start_import_process(self, dry_run: bool):
        if self.is_running:
            return

        folder = self.var_folder_path.get().strip()
        db_path = self.var_db_path.get().strip()
        compuhost_path = self.var_compuhost_path.get().strip() or None

        if not folder or not Path(folder).is_dir():
            messagebox.showerror("Invalid Folder", "Please select a valid media folder to import.")
            return

        if not db_path:
            messagebox.showerror("Invalid Database", "Please provide a destination karaoke.db path.")
            return

        if not dry_run:
            ans = messagebox.askyesno(
                "Confirm Full Import",
                f"Are you sure you want to write imported tracks into:\n{db_path}?\n\nExisting records will be updated, not deleted.",
            )
            if not ans:
                return

        # Clear existing table rows
        for item in self.tree.get_children():
            self.tree.delete(item)

        self._scanned_records.clear()
        self.is_running = True
        self._cancel_requested = False

        self.btn_preview.config(state="disabled")
        self.btn_import.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.progress_bar["value"] = 0

        mode_text = "Previewing (Dry Run)" if dry_run else "Importing to Database"
        self.lbl_status.config(text=f"Initializing engine for {mode_text}...")
        self.lbl_counts.config(text="Scanned: 0  •  CompuHost: 0  •  Enriched: 0  •  In DB: 0")

        # Launch worker thread
        self._worker_thread = threading.Thread(
            target=self._worker_run,
            args=(folder, db_path, compuhost_path, dry_run, self.var_enrich.get(), self.var_recursive.get()),
            daemon=True,
        )
        self._worker_thread.start()

    def _worker_run(
        self,
        folder: str,
        db_path: str,
        compuhost_path: Optional[str],
        dry_run: bool,
        enable_enrich: bool,
        recursive: bool,
    ):
        try:
            engine = TikiImportEngine(
                db_path=db_path,
                compuhost_path=compuhost_path,
                enable_enrichment=enable_enrich,
            )

            def on_progress(idx: int, total: int, record: Dict[str, Any]):
                if self._cancel_requested:
                    raise StopIteration("Scan cancelled by user")
                self.root.after(0, self._on_item_processed, idx, total, record)

            engine.scan_and_import(
                folder_path=folder,
                dry_run=dry_run,
                recursive=recursive,
                progress_callback=on_progress,
            )

            self.root.after(0, self._on_worker_completed, None, dry_run)
        except StopIteration:
            self.root.after(0, self._on_worker_completed, "Scan cancelled.", dry_run)
        except Exception as e:
            self.root.after(0, self._on_worker_completed, str(e), dry_run)

    def _on_item_processed(self, idx: int, total: int, record: Dict[str, Any]):
        self._scanned_records.append(record)

        # Update progressbar
        pct = (idx / total) * 100 if total > 0 else 0
        self.progress_bar["value"] = pct

        status_tag = record["status"].upper().replace("_", " ")
        year_str = str(record["release_year"]) if record["release_year"] else "-"

        # Add row to Treeview
        item_id = self.tree.insert(
            "",
            "end",
            values=(
                status_tag,
                record["artist"],
                record["title"],
                year_str,
                record["genre"] or "-",
                record["brand"] or "-",
                record["media_type"].upper(),
                record["filename"],
            ),
        )

        # Auto-scroll to latest
        self.tree.see(item_id)

        # Update stats
        ch_count = sum(1 for r in self._scanned_records if "COMPUHOST" in r["status"].upper())
        enriched_count = sum(1 for r in self._scanned_records if "ENRICH" in r["status"].upper() or "SWAP" in r["status"].upper())
        indb_count = sum(1 for r in self._scanned_records if "ALREADY" in r["status"].upper() or "UPDATED" in r["status"].upper())
        self.lbl_status.config(text=f"Processed {idx} of {total}: {record['title']} ({record['artist']})")
        self.lbl_counts.config(text=f"Scanned: {len(self._scanned_records)}  •  CompuHost: {ch_count}  •  Enriched: {enriched_count}  •  In DB: {indb_count}")

    def _on_worker_completed(self, error_msg: Optional[str], dry_run: bool):
        self.is_running = False
        self.btn_preview.config(state="normal")
        self.btn_import.config(state="normal")
        self.btn_cancel.config(state="disabled")

        if error_msg:
            self.lbl_status.config(text=f"Finished: {error_msg}")
            if error_msg != "Scan cancelled.":
                messagebox.showerror("Import Error", f"An error occurred during import:\n{error_msg}")
        else:
            total = len(self._scanned_records)
            action = "Preview scan completed" if dry_run else "Full import completed"
            self.lbl_status.config(text=f"✅ {action}! {total} media tracks scanned.")
            messagebox.showinfo("Done", f"{action}!\n{total} media tracks processed successfully.")

    def run(self):
        """Starts the Tkinter UI event loop."""
        self.root.mainloop()
