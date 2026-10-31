"""JobGambit was called JobQuill in 0.13–0.16 and CareerPilot up to 0.12. The first start after the update moves that
install over, so nothing is lost: the data folder (and the database in it), the Documents folder of an install without
the repo, and the saved passwords. The old scheduled tasks go; the app makes them again under the new name at start
(scheduler.ensure). data.home() runs it before it makes the new folder, so no entry point can start on an empty one first.
Delete this file and its call in data.home once nobody runs 0.16 or older any more."""
import json
import re
import subprocess
import sys
from pathlib import Path

import keyring

from jobgambit.core import data, scheduler, secrets

OLD = ("JobQuill", "CareerPilot")  # newest first: a computer that has both moves the newer one


def run():
    new = data.folder("JobGambit")
    if data.instance() or new.exists():  # a test or scratch home, or done already
        return
    old = next((o for o in OLD if data.folder(o).is_dir()), None)
    if not old:  # a new install
        return
    docs = Path.home() / "Documents"
    try:  # either rename may run again on the next start: each one checks for itself
        if (docs / old).is_dir() and not (docs / "JobGambit").exists():
            (docs / old).rename(docs / "JobGambit")
        data.folder(old).rename(new)
    except OSError:  # Windows: the old app still runs, or a file in there is open
        raise SystemExit(f"{old} is still running. Quit it (or restart the computer), then start JobGambit again.")
    for f in new.glob(f"{old.lower()}.db*"):  # the database and its -wal / -shm
        f.rename(new / f.name.replace(old.lower(), "jobgambit"))
    _passwords(old)
    _tasks(old)


def _passwords(old: str):
    try:
        for name in json.loads(keyring.get_password(old, secrets.INDEX) or "[]"):
            value = keyring.get_password(old, name)
            if value is not None:
                secrets.put(name, value)
                keyring.delete_password(old, name)
        keyring.delete_password(old, secrets.INDEX)
    except keyring.errors.KeyringError:  # no password store on this computer, or nothing was saved there
        pass


def _tasks(old: str):
    """They would start a program that is gone after the update."""
    if sys.platform == "win32":
        for what in scheduler.JOBS.values():
            subprocess.run(["schtasks", "/Delete", "/F", "/TN", f"{old} {what}"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    elif sys.platform == "darwin":
        for job in scheduler.JOBS:
            plist = Path.home() / "Library" / "LaunchAgents" / f"app.{old.lower()}.{job}.plist"
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
    else:
        lines = scheduler._crontab_lines()
        keep = [l for l in lines if not re.search(rf"# {old.lower()}( mail)?$", l)]
        if keep != lines:
            scheduler._crontab(keep)
