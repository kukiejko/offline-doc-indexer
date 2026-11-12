"""Server-side folder dialog: browsers cannot hand a real path to a web page."""
import subprocess
import sys

_SCRIPT = (
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
    "print(filedialog.askdirectory(parent=r, title='Select a document folder', mustexist=True))\n"
)


def pick_folder(timeout: int = 300):
    """Open a folder dialog on the machine running the app.

    Returns the chosen path, "" if the user cancelled, or None if no dialog
    could be shown (e.g. tkinter missing or no desktop).
    """
    try:
        out = subprocess.run([sys.executable, "-c", _SCRIPT], capture_output=True,
                             text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()
