"""The person's data folder and database. Never inside the code folder or a git repo."""
import hashlib
import os
import sqlite3
import sys
from pathlib import Path

# Each entry upgrades the database by one version (PRAGMA user_version). Append only, never edit.
MIGRATIONS = [
    """
    create table jobs (
        key text primary key, title text, company text, location text, country text,
        url text, source text, posted text, first_seen text, deadline text,
        type text, status text default 'new', score real, why text, note text, data text
    );
    create table applications (
        id integer primary key, job_key text, company text, role text, date_applied text,
        status text, method text, folder text, follow_up text, note text, data text
    );
    create table runs (
        id integer primary key, kind text, job_key text, agent text, status text,
        created text default current_timestamp, finished text, error text
    );
    """,
    """
    create table needs (
        id integer primary key, job_key text, kind text, message text, link text, question text,
        created text default current_timestamp, done integer default 0
    );
    """,
    "update jobs set status='expired' where status='filtered' and why like '[expired]%';",  # past the deadline: not less relevant
    """
    create table if not exists sites (kind text, ref text, company text, origin text, found text, last_scan text,
                        last_count integer default 0, fails integer default 0, error text,
                        off integer default 0, primary key (kind, ref));
    """,
    """
    create table if not exists ai_calls (id integer primary key, at text default current_timestamp, step text, job_key text, agent text,
                           model text, input integer, cached integer, output integer, cost_usd real, seconds real, error text);
    create index if not exists ai_calls_key on ai_calls(job_key);
    """,
    # zeros: scans in a row with no jobs on a site that had some; empty_since: the day that reached EMPTY_AFTER
    "alter table sites add column zeros integer default 0;",
    "alter table sites add column empty_since text;",
    # links that aren't web links (javascript:, file:…) came from other people's pages: never shown or opened
    "update jobs set url='' where coalesce(url, '') != '' and lower(url) not like 'http://%' and lower(url) not like 'https://%';",
    # Mail to check: replies the email check couldn't pin to one application or read for sure. Never dropped silently.
    """
    create table if not exists mail_items (id integer primary key, msg_id text unique, date text, frm text, subject text,
                           thread text, signal text, snippet text, app_id integer, candidates text, at text, done integer default 0);
    """,
    # mail_items becomes the log of every application mail: its text (read it in the app, any provider), the AI's
    # summary, auto=1 for what CareerPilot settled without asking and note for what it did (Needs you › Filed by itself)
    "alter table mail_items add column body text;",
    "alter table mail_items add column summary text;",
    "alter table mail_items add column auto integer default 0;",
    "alter table mail_items add column note text;",
    # Alerts are written first and sent after: one that didn't arrive is tried again, an interview alert is never lost
    """
    create table if not exists alerts_out (id integer primary key, at text default current_timestamp, event text,
                           title text, body text, key text, tries integer default 0, sent_at text, error text,
                           fallback integer default 0);
    """,
]


def home() -> Path:
    """Local app-data folder (not cloud-synced). CAREERPILOT_HOME overrides it, e.g. for tests."""
    if os.environ.get("CAREERPILOT_HOME"):
        path = Path(os.environ["CAREERPILOT_HOME"])
    elif sys.platform == "win32":
        path = Path(os.environ["LOCALAPPDATA"]) / "CareerPilot"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "CareerPilot"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "careerpilot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def instance() -> str:
    """Short id of a CAREERPILOT_HOME ("" for the normal install). A test or scratch home gets its own keyring
    service, scheduled task and browser port with it, so it never touches the person's real ones."""
    h = os.environ.get("CAREERPILOT_HOME")
    return hashlib.sha1(str(Path(h).resolve()).encode()).hexdigest()[:8] if h else ""


def db() -> sqlite3.Connection:
    con = sqlite3.connect(home() / "careerpilot.db")
    con.row_factory = sqlite3.Row
    con.execute("pragma journal_mode=wal")  # the scheduler and the app may write at the same time
    version = con.execute("pragma user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        with con:
            try:
                con.executescript(sql)
            except sqlite3.OperationalError as e:  # a column added again (a database set back to an older version)
                if "duplicate column" not in str(e):
                    raise
            con.execute(f"pragma user_version={i}")
    return con
