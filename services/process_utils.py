import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


def spawn_detached_process(cmd: Sequence[str], work_dir: Optional[Path] = None) -> subprocess.Popen:
    """Launch an external desktop tool detached from the server/hub process."""
    cwd = str(work_dir) if work_dir else None
    if sys.platform == "win32":
        flags = 0x00000008 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        return subprocess.Popen(list(cmd), cwd=cwd, creationflags=flags)
    return subprocess.Popen(list(cmd), cwd=cwd, start_new_session=True)
