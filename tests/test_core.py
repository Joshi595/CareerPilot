import json
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from careerpilot.core import ai, data


class Ping(BaseModel):
    ok: bool


class FakeCLI(ai.Agent):
    """Answers with the queued outputs, one per call: (stdout, stderr, exit code)."""
    name = label = "fake"

    def __init__(self, *outputs):
        self.outputs = list(outputs)

    def path(self):
        return sys.executable

    def command(self, schema, step=""):
        out, err, code = self.outputs.pop(0)
        return ["-c", f"import sys; sys.stdout.write({out!r}); sys.stderr.write({err!r}); sys.exit({code})"]


def test_answer_valid():
    assert ai.answer("p", Ping, FakeCLI(('{"ok": true}', "", 0))).ok is True


def test_answer_retries_once_then_gives_up():
    assert ai.answer("p", Ping, FakeCLI(('{"ok": "maybe"}', "", 0), ('{"ok": false}', "", 0))).ok is False
    with pytest.raises(ai.AIError):
        ai.answer("p", Ping, FakeCLI(('{}', "", 0), ('{}', "", 0)))


def test_usage_limit_and_errors():
    with pytest.raises(ai.UsageLimit):
        ai.answer("p", Ping, FakeCLI(("", "Claude usage limit reached, resets 5pm", 1)))
    with pytest.raises(ai.AIError):
        ai.answer("p", Ping, FakeCLI(("not json", "", 0)))


def test_claude_extract():
    out = json.dumps({"is_error": False, "structured_output": {"ok": True}})
    assert ai.Claude().extract(out) == {"ok": True}


def test_claude_lean_call_and_main_model_tokens():
    c = ai.Claude()
    assert "--system-prompt" in c.command({}) and "--model" not in c.command({}, "Tailor")
    assert c.command({}, "Review")[-2:] == ["--model", "sonnet"]
    c.level = "premium"
    assert c.command({}, "Review")[-2:] == ["--model", "opus"]
    out = json.dumps({"modelUsage": {"claude-haiku-5-5": {"inputTokens": 900, "costUSD": 0.001},
                                     "claude-opus-5-5": {"inputTokens": 100, "outputTokens": 5, "costUSD": 0.05}}})
    assert c.usage(out) == {"model": "claude-opus-5-5", "input": 100, "cached": 0, "output": 5, "cost_usd": 0.051}


def test_db_migrates(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    con = data.db()
    assert con.execute("pragma user_version").fetchone()[0] == len(data.MIGRATIONS)
    con.execute("insert into jobs(key, title) values ('a', 'Nurse')")
    assert data.db().execute("select count(*) from jobs").fetchone()[0] == 0  # uncommitted insert not visible


def test_server_needs_token_and_local_host(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    from careerpilot.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1")
    # anyone on this computer can reach 127.0.0.1 (other user accounts too): the page holds the key only with the
    # launch code, which lives in this user's own data folder
    assert server.TOKEN not in c.get("/").text and server.TOKEN not in c.get("/?k=guess").text
    assert server.TOKEN in c.get(f"/?k={server.launch_code()}").text
    assert c.get("/api/health").status_code == 403
    assert c.get("/api/health", headers={"x-token": server.TOKEN}).status_code == 200
    evil = TestClient(server.app, base_url="http://evil.example")
    assert evil.get("/api/health", headers={"x-token": server.TOKEN}).status_code == 400


def test_pages_run_only_their_own_scripts(tmp_path, monkeypatch):
    """No inline scripts, no javascript: links, never inside another site's frame; links opened from the app carry no
    address (or ?t= key) with them."""
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    from careerpilot.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1")
    page = c.get(f"/?k={server.launch_code()}")
    assert "script-src 'self' 'unsafe-eval';" in page.headers["content-security-policy"] and page.headers["x-frame-options"] == "DENY"
    assert "<script>" not in page.text and f'content="{server.TOKEN}"' in page.text
    api = c.get("/api/health", headers={"x-token": server.TOKEN})
    assert api.headers["x-content-type-options"] == "nosniff" and api.headers["referrer-policy"] == "no-referrer"
    assert "content-security-policy" not in api.headers


def test_launch_code_is_kept_and_a_second_start_opens_the_running_app(tmp_path, monkeypatch):
    """The code survives a restart (an open tab reloads fine); the browser opens a file holding it, never a command line
    with it; a second start finds the running app instead of starting another server on the same data."""
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    from careerpilot.app import server
    code = server.launch_code()
    assert code == server.launch_code() and len(code) >= 24
    page = server.write_opener(8791)
    assert f"http://127.0.0.1:8791/?k={code}" in page.read_text(encoding="utf-8") and page.parent == tmp_path
    monkeypatch.setattr(server, "alive", lambda port: port == 8791)
    assert server.running_port() == 8791
    monkeypatch.setattr(server, "alive", lambda port: False)
    assert server.running_port() is None


def test_another_careerpilot_on_the_old_port_is_not_ours(tmp_path, monkeypatch):
    """The port in instance.json may now belong to another CareerPilot (another account, a test home) or a squatter:
    only one that proves it knows this folder's launch code is this one. The code is never sent to the port."""
    import io
    import urllib.parse
    import urllib.request
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    from careerpilot.app import server
    code, asked = server.launch_code(), []

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def ports(req, timeout=0):
        asked.append(req.full_url + str(req.data))
        nonce = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)["n"][0]
        ours = ":8791/" in req.full_url
        return Answer(json.dumps({"ok": True, **({"proof": server.proof(nonce)} if ours else {"proof": "guess"})}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", ports)
    assert server.alive(8791) and not server.alive(8792)
    assert not any(code in a for a in asked)


def test_linux_browsers_get_the_address_not_a_hidden_file(tmp_path, monkeypatch):
    """Snap browsers (Ubuntu's Firefox) can't open files in hidden folders like ~/.local/share."""
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    from careerpilot.app import server
    monkeypatch.setattr(server.sys, "platform", "linux")
    assert server.opener(8791).startswith("http://127.0.0.1:8791/?k=")
    monkeypatch.setattr(server.sys, "platform", "win32")
    assert server.opener(8791).startswith("file:")


def test_tmp_home_isolated(tmp_path, monkeypatch):
    """A test or scratch home never reads or writes the real keyring service, task or browser port."""
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    import keyring
    from careerpilot.core import scheduler, secrets
    store, used = {}, set()
    monkeypatch.setattr(keyring, "get_password", lambda s, n: used.add(s) or store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: used.add(s) or store.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, n: used.add(s) or store.pop((s, n), None))
    secrets.put("email_password", "pw-12345")
    secrets.get("email_password"), secrets.names(), secrets.redact("x"), secrets.delete("email_password"), secrets.delete_all()
    assert used == {f"CareerPilot-{data.instance()}"} and len(data.instance()) == 8
    assert scheduler.name().endswith(data.instance()) and str(tmp_path.resolve()) in scheduler.command()
    assert scheduler.name("mail").startswith("CareerPilot email check") and "mail" in scheduler.command("mail")


def test_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    from careerpilot.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    want = {"board": 0, "less": 0, "applications": 0, "needs": 0}
    assert c.get("/api/counts").json() == want
    with data.db() as con:  # an open mail counts; a filed one doesn't
        con.execute("insert into mail_items(msg_id, subject, signal, done) values ('<a>', 'Interview', 'interview', 0)")
        con.execute("insert into mail_items(msg_id, subject, signal, done, auto) values ('<b>', 'Receipt', 'acknowledgement', 1, 1)")
    assert c.get("/api/counts").json()["needs"] == 1


def _strict_ok(s, path="$"):
    """OpenAI strict mode's rules: closed objects, every property required, no free-form dicts, no defaults."""
    if isinstance(s, dict):
        assert "default" not in s, path
        if "$ref" in s:
            assert list(s) == ["$ref"], path
        if s.get("type") == "object":
            assert "properties" in s and s.get("additionalProperties") is False, path
            assert s["required"] == list(s["properties"]), path
        for k, v in s.items():
            _strict_ok(v, f"{path}.{k}")
    elif isinstance(s, list):
        for i, v in enumerate(s):
            _strict_ok(v, f"{path}[{i}]")


def test_codex_schemas_are_strict_and_answers_come_back():
    import importlib
    import pkgutil
    import careerpilot
    for m in pkgutil.walk_packages(careerpilot.__path__, "careerpilot."):
        if ".countries." not in m.name:
            importlib.import_module(m.name)

    def models(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from models(sub)
    found = {m for m in models(BaseModel) if m.__module__.startswith("careerpilot")}
    assert len(found) > 20
    for m in found:
        _strict_ok(ai.strict(m.model_json_schema()), m.__name__)

    from careerpilot.core.profile import Profile
    schema = Profile.model_json_schema()
    answer = {"basics": {"name": "Ana", "headline": None, "email": None, "phone": None, "location": None, "address": None, "dob": None,
                         "nationality": None, "links": [{"key": "github", "value": "gh/ana"}], "photo": None},
              "summaries": [{"key": "ml", "value": "ML engineer"}], "skills": [{"key": "Languages", "value": ["Python"]}],
              "experience": [{"id": "e1", "title": "Dev", "org": None, "unit": None, "location": None, "start": None, "end": None,
                              "link": None, "bullets": [{"id": "b1", "text": "Built X", "tags": None}]}],
              **{k: None for k in ("projects", "education", "languages", "certifications", "awards", "publications",
                                   "volunteering", "courses", "interests", "custom")}}
    p = Profile.model_validate(ai.loosen(answer, schema))
    assert p.basics.links == {"github": "gh/ana"} and p.skills == {"Languages": ["Python"]} and p.summaries == {"ml": "ML engineer"}
    assert p.experience[0].org == "" and p.experience[0].bullets[0].tags == [] and p.projects == []
