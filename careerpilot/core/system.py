"""Small operating-system helpers: open a folder, open a terminal for a sign-in, back up, update."""
import io
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from careerpilot.core.data import home


def open_path(path: Path):
    if sys.platform == "win32":
        import os
        os.startfile(path)  # noqa: S606 (our own data folder)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def private(path: Path):
    """Only this user (and the system itself) may open the folder: other accounts on the computer can't read or change
    the documents in it. Best effort: a folder that can't be changed stays as it is."""
    try:
        if sys.platform == "win32":
            import os
            me = "\\".join(filter(None, [os.environ.get("USERDOMAIN"), os.environ["USERNAME"]]))
            grant = subprocess.run(["icacls", str(path), "/grant:r", f"{me}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],  # S-1-5-18: SYSTEM
                                   capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if grant.returncode == 0:  # inherited access goes only once this user's own is in place
                subprocess.run(["icacls", str(path), "/inheritance:r"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            path.chmod(0o700)
    except (OSError, KeyError):
        pass


def open_terminal(cmd: list[str]):
    """A visible terminal window running cmd, for vendor sign-ins that need the person to type or click."""
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/k", subprocess.list2cmdline(cmd)], creationflags=subprocess.CREATE_NEW_CONSOLE)
    elif sys.platform == "darwin":
        line = shlex.join(cmd).replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{line}"'])
    else:
        term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("konsole") or "xterm"
        subprocess.Popen([term, "-e", shlex.join(cmd)])


def backup() -> bytes:
    """Zip of the data folder: a consistent copy of the database, no browser sessions, no logs."""
    root = home()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z, tempfile.TemporaryDirectory() as tmp:
        if (root / "careerpilot.db").exists():
            snap = Path(tmp) / "careerpilot.db"
            with sqlite3.connect(root / "careerpilot.db") as con:
                con.execute("vacuum into ?", (str(snap),))
            z.write(snap, "careerpilot.db")
        for f in root.rglob("*"):
            rel = f.relative_to(root)
            if f.is_file() and rel.parts[0] not in ("browser", "runs", "uploads", "cache") and not f.name.startswith("careerpilot.db"):
                z.write(f, rel.as_posix())
    return buf.getvalue()


REPO = "Joshi595/CareerPilot"  # ponytail: GitHub release tags; PyPI (careerpilot==<version>) once the repo is public


def latest_tag() -> str:
    """The newest released version's tag (v1.2.3), from the repository's tags. Raises when it can't be read."""
    import re
    from careerpilot.core import sources
    tags = [t["name"] for t in sources.get_json(f"https://api.github.com/repos/{REPO}/tags?per_page=100")
            if re.fullmatch(r"v\d+(\.\d+)*", t.get("name", ""))]
    if not tags:
        raise RuntimeError("no released version found")
    return max(tags, key=lambda t: tuple(int(x) for x in t[1:].split(".")))


def update() -> str:
    """Install the newest released version: a tag, never whatever is on main (one bad push there would reach everyone
    on their next update). The data folder is separate, so nothing personal is touched. Restart afterwards."""
    from pathlib import Path as P
    uv = shutil.which("uv") or str(P.home() / ".local" / "bin" / ("uv.exe" if sys.platform == "win32" else "uv"))
    if not P(uv).exists():
        return "uv was not found. Run the installer again to update."
    try:
        tag = latest_tag()
    except Exception as e:  # offline, or the repository can't be read
        return f"Couldn't find the newest version ({e}). Nothing was changed."
    source = f"careerpilot @ https://github.com/{REPO}/archive/refs/tags/{tag}.zip"
    r = subprocess.run([uv, "tool", "install", "--force", "--reinstall", source], capture_output=True, text=True, encoding="utf-8")
    return ((r.stdout + r.stderr).strip()[-400:] or f"Updated to {tag[1:]}.") + " Close CareerPilot and start it again."
