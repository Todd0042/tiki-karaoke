#!/usr/bin/env python3
"""
Tiki Karaoke — Smart Library Importer
Command-line & Desktop GUI entry point.
"""
import sys
import argparse
import time
from pathlib import Path

# On Windows, attach to parent console if invoked from cmd/PowerShell
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.AttachConsole(-1)
    except Exception:
        pass

# Add repo root to python path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from importer.tiki_importer.config import get_default_db_path, get_default_library_path
from importer.tiki_importer.engine import TikiImportEngine
from importer.ui.app_window import HAS_GUI, TikiImporterApp


def run_cli_import(
    folder_path: str,
    db_path: str,
    compuhost_path: str = None,
    dry_run: bool = True,
    enable_enrich: bool = True,
    recursive: bool = True,
):
    print("=" * 75)
    print("🌴  TIKI KARAOKE — SMART LIBRARY IMPORTER (CLI)")
    print("=" * 75)
    print(f"📁 Media Folder   : {folder_path}")
    print(f"🗄️  Karaoke DB    : {db_path}")
    if compuhost_path:
        print(f"📦 CompuHost DB   : {compuhost_path}")
    print(f"✨ Web Enrichment : {'Enabled (iTunes API)' if enable_enrich else 'Disabled'}")
    print(f"⚙️  Operation Mode : {'PREVIEW (Dry Run — Safe)' if dry_run else '🚀 FULL IMPORT (Writing to DB)'}")
    print(f"🔄 Search Depth   : {'Recursive (all subfolders)' if recursive else 'Top-level folder only'}")
    print("=" * 75)

    if not Path(folder_path).is_dir():
        print(f"❌ Error: Folder '{folder_path}' does not exist or is not a directory.")
        return 1

    start_time = time.time()
    engine = TikiImportEngine(
        db_path=db_path,
        compuhost_path=compuhost_path,
        enable_enrichment=enable_enrich,
    )

    print("\nScanning media files...\n")
    print(f"{'STATUS':<15} | {'ARTIST':<25} | {'TITLE':<30} | {'YEAR':<5} | {'GENRE':<12}")
    print("-" * 96)

    def on_progress(idx: int, total: int, record: dict):
        status = record["status"].upper().replace("_", " ")[:14]
        artist = record["artist"][:24]
        title = record["title"][:29]
        year = str(record["release_year"]) if record["release_year"] else "-"
        genre = record["genre"][:11] if record["genre"] else "-"
        print(f"{status:<15} | {artist:<25} | {title:<30} | {year:<5} | {genre:<12}")

    results = engine.scan_and_import(
        folder_path=folder_path,
        dry_run=dry_run,
        recursive=recursive,
        progress_callback=on_progress,
    )

    elapsed = time.time() - start_time
    total = len(results)
    ch_count = sum(1 for r in results if "COMPUHOST" in r["status"].upper())
    enriched_count = sum(1 for r in results if "ENRICH" in r["status"].upper() or "SWAP" in r["status"].upper())
    indb_count = sum(1 for r in results if "ALREADY" in r["status"].upper() or "UPDATED" in r["status"].upper())

    print("=" * 96)
    print(f"✨ Scan Complete in {elapsed:.2f}s!")
    print(f"   • Total Files Processed : {total}")
    if compuhost_path:
        print(f"   • CompuHost Matched     : {ch_count}")
    print(f"   • Web Enriched (iTunes) : {enriched_count}")
    print(f"   • Existing in Database  : {indb_count}")
    if dry_run:
        print("\n💡 This was a PREVIEW SCAN. No records were written to karaoke.db.")
        print("   To import into the database, run without --dry-run or click 'Run Full Import' in GUI.")
    else:
        print(f"\n🎉 Successfully imported/updated tracks in: {db_path}")
    print("=" * 96)
    return 0


def run_cli_compuhost_migration(
    compuhost_path: str,
    db_path: str,
    remap_from: str = "",
    remap_to: str = "",
    skip_missing: bool = True,
    dry_run: bool = True,
    enable_enrich: bool = True,
):
    print("=" * 85)
    print("🌴  TIKI KARAOKE — COMPUHOST DIRECT MIGRATION WIZARD (CLI)")
    print("=" * 85)
    print(f"📦 CompuHost DB   : {compuhost_path}")
    print(f"🗄️  Karaoke DB    : {db_path}")
    if remap_from and remap_to:
        print(f"🔄 Path Remap     : '{remap_from}' -> '{remap_to}'")
    print(f"✨ Web Enrichment : {'Enabled (iTunes API)' if enable_enrich else 'Disabled'}")
    print(f"⚙️  Operation Mode : {'PREVIEW (Dry Run — Safe)' if dry_run else '🚀 FULL MIGRATION (Writing to DB)'}")
    print("=" * 85)

    start_time = time.time()
    engine = TikiImportEngine(db_path=db_path, enable_enrichment=enable_enrich)

    print("\nReading CompuHost database and verifying disk files...\n")
    print(f"{'STATUS':<15} | {'ARTIST':<24} | {'TITLE':<28} | {'YEAR':<5} | {'DISK':<5}")
    print("-" * 85)

    def on_progress(idx: int, total: int, r: dict):
        status = r["status"].upper().replace("_", " ")[:14]
        artist = r["artist"][:23]
        title = r["title"][:27]
        year = str(r["release_year"]) if r["release_year"] else "-"
        exists = "YES" if r.get("file_exists") else "NO"
        print(f"{status:<15} | {artist:<24} | {title:<28} | {year:<5} | {exists:<5}")

    results = engine.migrate_compuhost(
        compuhost_path=compuhost_path,
        remap_from=remap_from,
        remap_to=remap_to,
        skip_missing=skip_missing,
        dry_run=dry_run,
        progress_callback=on_progress,
    )

    elapsed = time.time() - start_time
    total = len(results)
    verified = sum(1 for r in results if r.get("file_exists"))
    missing = sum(1 for r in results if not r.get("file_exists"))
    enriched = sum(1 for r in results if "ENRICH" in r["status"].upper())
    indb = sum(1 for r in results if "ALREADY" in r["status"].upper() or "UPDATED" in r["status"].upper())

    print("=" * 85)
    print(f"✨ Migration Complete in {elapsed:.2f}s!")
    print(f"   • Total Records in DB  : {total}")
    print(f"   • Verified on Disk     : {verified}")
    print(f"   • Missing on Disk      : {missing}")
    print(f"   • Web Enriched (iTunes): {enriched}")
    print(f"   • Existing in Database : {indb}")
    if dry_run:
        print("\n💡 This was a PREVIEW SCAN. No records were written to karaoke.db.")
        print("   To commit to the database, run with --import.")
    else:
        print(f"\n🎉 Successfully migrated tracks into: {db_path}")
    print("=" * 85)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Tiki Karaoke Smart Importer & Metadata Enricher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-f", "--folder", type=str, default=None, help="Path to media directory to scan")
    parser.add_argument("-d", "--db", type=str, default=None, help="Path to Tiki Karaoke karaoke.db")
    parser.add_argument("-c", "--compuhost", type=str, default=None, help="Path to CompuHost SongList.tps, .ciqbackup, or .csv")
    parser.add_argument(
        "--migrate-compuhost",
        dest="migrate_compuhost_path",
        type=str,
        default=None,
        help="Directly migrate from CompuHost SongList.tps or .ciqbackup without folder scanning",
    )
    parser.add_argument("--remap-from", type=str, default="", help="Old path prefix to replace (e.g. C:\\Karaoke)")
    parser.add_argument("--remap-to", type=str, default="", help="New path prefix (e.g. D:\\Karaoke)")
    parser.add_argument("--skip-missing", action="store_true", default=True, help="Skip tracks not found on disk")
    parser.add_argument("--cdg-ripper", "--rip-cdg", dest="cdg_ripper", action="store_true", help="Launch the dedicated CD+G Optical Disc Ripper window")
    parser.add_argument("--dry-run", action="store_true", default=None, help="Run preview scan without writing to DB")
    parser.add_argument("--import", dest="do_import", action="store_true", help="Perform real database import")
    parser.add_argument("--no-enrich", action="store_true", help="Disable web metadata enrichment")
    parser.add_argument("--flat", action="store_true", help="Do not scan subfolders recursively")
    parser.add_argument("--cli", action="store_true", help="Force command-line interface mode")

    args = parser.parse_args()

    default_folder = args.folder or get_default_library_path()
    default_db = args.db or get_default_db_path()

    # Direct CD+G Ripper dedicated window mode
    if args.cdg_ripper:
        from importer.ui.cdg_ripper_window import CdgRipperWindow
        app = CdgRipperWindow(target_tracks_dir=default_folder)
        app.win.mainloop()
        return

    # Direct CompuHost migration CLI mode
    if args.migrate_compuhost_path:
        dry_run_mode = not args.do_import
        run_cli_compuhost_migration(
            compuhost_path=args.migrate_compuhost_path,
            db_path=default_db,
            remap_from=args.remap_from,
            remap_to=args.remap_to,
            skip_missing=args.skip_missing,
            dry_run=dry_run_mode,
            enable_enrich=not args.no_enrich,
        )
        return

    # Determine whether to run GUI or CLI
    force_cli = args.cli or args.do_import or (args.dry_run is not None)

    if not force_cli and HAS_GUI:
        try:
            app = TikiImporterApp(initial_folder=default_folder, initial_db=default_db)
            if args.compuhost:
                app.var_compuhost_path.set(args.compuhost)
            if args.no_enrich:
                app.var_enrich.set(False)
            if args.flat:
                app.var_recursive.set(False)
            app.run()
            return
        except Exception as e:
            print(f"[Notice] Could not start desktop GUI ({e}), falling back to CLI.\n")

    # CLI fallback or requested CLI mode
    dry_run_mode = not args.do_import
    run_cli_import(
        folder_path=default_folder,
        db_path=default_db,
        compuhost_path=args.compuhost,
        dry_run=dry_run_mode,
        enable_enrich=not args.no_enrich,
        recursive=not args.flat,
    )


if __name__ == "__main__":
    main()
