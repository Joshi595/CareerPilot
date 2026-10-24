"""My applications: the job description in the drawer (for interviews), read from what was archived."""
import pytest
from fastapi.testclient import TestClient

from careerpilot.core.data import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    from careerpilot.app import server
    return TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})


def test_the_apply_page_asks_how_many_applied_once_a_day(client, monkeypatch):
    """One request when the Apply page opens a LinkedIn posting; the answer is kept for a day; LinkedIn switched off in
    Settings: never asked."""
    from datetime import date
    from careerpilot.core import sources
    from careerpilot.core import profile as pf
    asked = []
    monkeypatch.setattr(sources, "applicants",
                        lambda url: asked.append(url) or {"n": 39, "text": "39 applicants", "at": date.today().isoformat()})
    with db() as con:
        con.execute("insert into jobs(key, title, url, status, data) values ('k', 'Nurse', 'https://www.linkedin.com/jobs/view/123456', 'new', '{}')")
    assert client.post("/api/jobs/k/applicants").json()["applicants"]["n"] == 39
    client.post("/api/jobs/k/applicants")
    assert len(asked) == 1  # kept for the day
    prefs = pf.read("preferences")
    prefs["sources_off"] = ["linkedin"]
    pf.write("preferences", prefs)
    with db() as con:
        con.execute("update jobs set data='{}' where key='k'")
    assert client.post("/api/jobs/k/applicants").json()["applicants"] is None and len(asked) == 1


def test_the_drawer_shows_the_posting_it_was_written_for(client, tmp_path):
    (tmp_path / "applications" / "a1").mkdir(parents=True)
    (tmp_path / "applications" / "a1" / "posting.md").write_text("# Nurse at Acme\n- URL: https://acme.example/1\n\nNight shifts on ward 3.",
                                                                  encoding="utf-8")
    (tmp_path / "jobs" / "desc").mkdir(parents=True)
    (tmp_path / "jobs" / "desc" / "k2.txt").write_text("Data Engineer, Python and SQL.", encoding="utf-8")
    with db() as con:
        con.execute("insert into applications(id, company, role, status, folder) values (1, 'Acme', 'Nurse', 'interview', 'a1')")
        con.execute("insert into applications(id, company, role, status, job_key) values (2, 'Beta', 'Data Engineer', 'offer', 'k2')")
        con.execute("insert into applications(id, company, role, status) values (3, 'Gamma', 'Cook', 'interview')")
    assert client.get("/api/applications/1/posting").json()["text"] == "Night shifts on ward 3."  # the archive's own header left out
    assert client.get("/api/applications/2/posting").json()["text"] == "Data Engineer, Python and SQL."
    assert client.get("/api/applications/3/posting").json()["text"] == ""  # nothing archived: the drawer says so
    assert client.get("/api/applications/99/posting").status_code == 404


def test_the_posting_reads_as_text_not_markdown(client, tmp_path):
    (tmp_path / "applications" / "a1").mkdir(parents=True)
    (tmp_path / "applications" / "a1" / "posting.md").write_text(
        "# Nurse at Acme\n- URL: x\n\n## Your tasks\n- **Night** shifts\n### Profile\nC# and 3 # signs", encoding="utf-8")
    with db() as con:
        con.execute("insert into applications(id, company, role, status, folder) values (1, 'Acme', 'Nurse', 'interview', 'a1')")
    assert client.get("/api/applications/1/posting").json()["text"] == "Your tasks\n- Night shifts\nProfile\nC# and 3 # signs"
