"""Interview prep: the brief (with STAR stories only from real facts), what was sent (no AI), the defend-your-CV drill,
the meeting link from the invite mail."""
import json

import pytest
from fastapi.testclient import TestClient

from careerpilot.core import ai, practice
from careerpilot.core import profile as pf
from careerpilot.core.data import db

PROFILE = {"basics": {"name": "Alex Example"}, "experience": [
    {"id": "e1", "title": "Backend Developer", "org": "Example Logistics", "start": "2021-01", "end": "present",
     "bullets": [{"id": "b1", "text": "Built a shipment tracking API handling 2 million requests per day."},
                 {"id": "b2", "text": "Cut batch run time from 6 hours to 40 minutes."}]}]}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    pf.write("profile", PROFILE)
    folder = tmp_path / "applications" / "a1"
    folder.mkdir(parents=True)
    (folder / "posting.md").write_text("# Data Engineer at Acme\n\nYou run our Python data pipelines.", encoding="utf-8")
    (folder / "accepted.json").write_text(json.dumps({"b1": {"text": "Built a production shipment API (2 million requests/day)."},
                                                      "order_projects": {"text": "1. A", "order": ["p1"]}}), encoding="utf-8")
    (folder / "letter.json").write_text(json.dumps({"paragraphs": ["Dear Ms Weber,", "", "I build data pipelines."]}), encoding="utf-8")
    (folder / "fit.json").write_text(json.dumps({"blockers": ["German C1 asked"], "keywords": ["Airflow"]}), encoding="utf-8")
    with db() as con:
        con.execute("insert into applications(id, company, role, status, folder, data) values (1, 'Acme', 'Data Engineer', 'interview', 'a1', ?)",
                    (json.dumps({"interview_at": "2099-10-28T10:00"}),))
        con.execute("insert into mail_items(msg_id, app_id, signal, subject, body, done) values ('<i>', 1, 'interview', 'Invitation',"
                    " 'Join here: https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc See you', 0)")
    monkeypatch.setattr(practice.apply, "agent", lambda: None)
    return 1


def test_what_you_sent_needs_no_ai(app):
    assert practice.sent(app) == {"cv": ["Built a production shipment API (2 million requests/day)."],
                                  "letter": ["Dear Ms Weber,", "I build data pipelines."]}


def test_the_brief_keeps_only_stories_built_on_real_facts(app, monkeypatch):
    asked = []

    def answer(prompt, model, agent, timeout=600, key=""):
        asked.append(prompt)
        story = lambda facts: {"requirement": "Python pipelines", "title": "Faster batch jobs", "situation": "s", "task": "t",
                               "action": "a", "result": "r", "facts": facts}
        return model(role="Runs Acme's data pipelines.", wants=["Python", "Airflow"], gaps=[{"gap": "German C1", "answer": "B2, improving"}],
                     ask=["What does a first week look like?"], stories=[story(["b2"]), story(["[b1]", "e1"]), story(["b9"]), story([])])
    monkeypatch.setattr(ai, "answer", answer)
    out = practice.brief(app)
    assert [s["facts"] for s in out["stories"]] == [["b2"], ["[b1]", "e1"]]  # an unknown id or none: dropped
    assert "<untrusted>" in asked[0] and "You run our Python data pipelines." in asked[0] and "German C1 asked" in asked[0]
    assert "Built a production shipment API" in asked[0]  # what was sent, so the answers stay consistent with it
    assert practice.brief(app) == out and len(asked) == 1  # kept: no second run until Refresh


def test_the_drill_asks_about_what_was_sent(app, monkeypatch):
    seen = []

    def answer(prompt, model, agent, timeout=600, key=""):
        seen.append(prompt)
        return model(questions=[{"question": "Walk me through the 2 million requests.", "kind": "cv", "why": "w", "facts": "b1"}])
    monkeypatch.setattr(ai, "answer", answer)
    qs = practice.defend(app)
    assert qs[0]["kind"] == "defend" and "I build data pipelines." in seen[0]


def test_the_prep_page_gets_its_meeting_link_and_time(app):
    from careerpilot.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    r = c.get("/api/practice/1").json()
    assert r["prep"]["link"] == "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc"
    assert r["prep"]["interview_at"] == "2099-10-28T10:00" and r["prep"]["brief"] is None and r["prep"]["sent"]["letter"]
    assert c.get("/api/practice/general").json().get("prep") is None
