"""JobQuill was called CareerPilot up to 0.12. The first start after the update moves that install over, so nothing is
lost: the data folder (and the database in it), the Documents folder of an install without the repo, and the saved
passwords. The old scheduled tasks go; the app makes them again under the new name at start (scheduler.ensure).
data.home() runs it before it makes the new folder, so no entry point can start on an empty one first.
Delete this file and its call in data.home once nobody runs 0.12 any more."""
import json
import re
import subprocess
import sys
from pathlib import Path

import keyring

from jobquill.core import data, scheduler, secrets

OLD = "CareerPilot"


def run():
    old, new = data.folder(OLD), data.folder("JobQuill")
    if data.instance() or not old.is_dir() or new.exists():  # a test or scratch home, a new install, or done already
        return
    docs = Path.home() / "Documents"
    try:  # either rename may run again on the next start: each one checks for itself
        if (docs / OLD).is_dir() and not (docs / "JobQuill").exists():
            (docs / OLD).rename(docs / "JobQuill")
        old.rename(new)
    except OSError:  # Windows: the old app still runs, or a file in there is open
        raise SystemExit("CareerPilot is still running. Quit it (or restart the computer), then start JobQuill again.")
    for f in new.glob("careerpilot.db*"):  # the database and its -wal / -shm
        f.rename(new / f.name.replace("careerpilot", "jobquill"))
    _passwords()
    _tasks()


def _passwords():
    try:
        for name in json.loads(keyring.get_password(OLD, secrets.INDEX) or "[]"):
            value = keyring.get_password(OLD, name)
            if value is not None:
                secrets.put(name, value)
                keyring.delete_password(OLD, name)
        keyring.delete_password(OLD, secrets.INDEX)
    except keyring.errors.KeyringError:  # no password store on this computer: nothing was saved there
        pass


def _tasks():
    """They would start a program that is gone after the update."""
    if sys.platform == "win32":
        for what in scheduler.JOBS.values():
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", f"{OLD} {what}"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    elif sys.platform == "darwin":
        for job in scheduler.JOBS:
            plist = Path.home() / "Library" / "LaunchAgents" / f"app.careerpilot.{job}.plist"
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
    else:
        lines = scheduler._crontab_lines()
        keep = [l for l in lines if not re.search(r"# careerpilot( mail)?$", l)]
        if keep != lines:
            scheduler._crontab(keep)
