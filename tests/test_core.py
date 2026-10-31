import json
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from jobgambit.core import ai, data, prompts


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
    assert "--system-prompt" in c.command({}) and "stream-json" in c.command({}) and "--verbose" in c.command({})
    assert c.command({}, "Tailor")[-2:] == ["--model", "opus"]  # standard: the documents themselves on Opus,
    assert c.command({}, "Review")[-2:] == ["--model", "sonnet"]  # every other step on Sonnet, the CLI's default never
    from jobgambit.core import apply
    assert {apply.Tailor.__name__, apply.Change.__name__, apply.Revision.__name__} <= ai.WRITE_STEPS  # a rename can't move them
    c.level = "premium"
    assert c.command({}, "Tailor")[-2:] == ["--model", "opus"]
    c.level = "economy"
    assert c.command({}, "Review")[-2:] == ["--model", "haiku"]
    out = json.dumps({"modelUsage": {"claude-haiku-5-5": {"inputTokens": 900, "costUSD": 0.001},
                                     "claude-opus-5-5": {"inputTokens": 100, "outputTokens": 5, "costUSD": 0.05}}})
    u = c.usage(out)
    assert {k: u[k] for k in ("model", "input", "cached", "output", "cost_usd")} == {
        "model": "claude-opus-5-5", "input": 100, "cached": 0, "output": 5, "cost_usd": 0.051}


def stream(cut=False):
    """A stream-json run: two API calls (the first arrives as two lines with one id), a tool result in between."""
    use = lambda i, cw, cr, o, th: {"input_tokens": i, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
                                    "output_tokens": o, "output_tokens_details": {"thinking_tokens": th}}
    first = {"id": "m1", "model": "claude-sonnet-5-5", "usage": use(10, 3000, 0, 50, 20)}
    rows = [{"type": "system", "subtype": "init"},
            {"type": "assistant", "message": {**first, "content": [{"type": "text", "text": "opening"}]}},
            {"type": "assistant", "message": {**first, "content": [{"type": "tool_use", "id": "t1", "name": "mcp__pw__browser_navigate"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 1200}]}},
            {"type": "assistant", "message": {"id": "m2", "model": "claude-sonnet-5-5", "usage": use(5, 400, 3000, 30, 10),
                                              "content": [{"type": "text", "text": "done"}]}}]
    if not cut:
        rows.append({"type": "result", "is_error": False, "num_turns": 2, "duration_api_ms": 4200, "structured_output": {"ok": True},
                     "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 15, "cacheCreationInputTokens": 3400,
                                                          "cacheReadInputTokens": 3000, "outputTokens": 80, "costUSD": 0.019},
                                    "claude-haiku-5-5": {"inputTokens": 300, "outputTokens": 10, "costUSD": 0.001}}})
    return "\n".join(json.dumps(r) for r in rows)


def test_stream_answer_extracts_structured_output():
    assert ai.Claude().extract(stream()) == {"ok": True}


def test_steps_dedupe_by_message_id():
    s = ai.Claude().steps(stream())
    assert len(s) == 2 and s[0]["tools"] == ["mcp__pw__browser_navigate"]
    assert (s[0]["result_chars"], s[1]["result_chars"]) == (0, 1200)  # the second call read the tool's 1,200 chars
    assert sum(x["thinking"] for x in s) == 30 and s[1]["cached"] == 3000


def last_call():
    with data.db() as con:
        r = dict(con.execute("select * from ai_calls order by id desc").fetchone())
        return r, [dict(x) for x in con.execute("select * from ai_steps where call_id=? order by n", (r["id"],))]


def test_record_writes_detail_parts_and_log():
    old = data.home() / "logs" / "ai" / "2000-01-01"
    old.mkdir(parents=True)
    prompts.build("rules", facts="abc")
    ai.record("Submit", "k", ai.Claude(), stream(), 3.0, prompt="the prompt", mode="submit")
    r, steps = last_call()
    assert (r["turns"], r["fresh"], r["cache_write"], r["cached"], r["output"], r["thinking"]) == (2, 15, 3400, 3000, 80, 30)
    assert r["input"] == 6415 and r["api_seconds"] == 4.2 and r["prompt_chars"] == 10
    assert json.loads(r["parts"])["facts"] == 3 and "playbook" in json.loads(r["parts"])
    assert (r["mode"], r["outcome"]) == ("submit", "ok") and "claude-haiku-5-5" in json.loads(r["models"])
    assert [s["tools"] for s in steps] == ["mcp__pw__browser_navigate", ""] and steps[1]["result_chars"] == 1200
    log = data.home() / r["log"]
    assert log.read_text(encoding="utf-8") == stream() and (log.parent / f"{r['id']}.prompt.txt").read_text(encoding="utf-8") == "the prompt"
    assert not old.exists()  # run logs are kept 30 days
    ai.record("Review", "k", ai.Claude(), stream(), 1.0)
    assert last_call()[0]["parts"] in (None, "")  # one prompt's parts never stick to the next call


def test_record_without_result_line_keeps_partial_usage():
    ai.record("Submit", "k", ai.Claude(), stream(cut=True), 3.0, error="no answer within 1800 s")
    r, steps = last_call()
    assert r["error"] and (r["output"], r["cached"], r["thinking"], r["turns"]) == (80, 3000, 30, 2) and len(steps) == 2


def test_db_migrates(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    con = data.db()
    assert con.execute("pragma user_version").fetchone()[0] == len(data.MIGRATIONS)
    con.execute("insert into jobs(key, title) values ('a', 'Nurse')")
    assert data.db().execute("select count(*) from jobs").fetchone()[0] == 0  # uncommitted insert not visible


def test_server_needs_token_and_local_host(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    from jobgambit.app import server
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
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    from jobgambit.app import server
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
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    from jobgambit.app import server
    code = server.launch_code()
    assert code == server.launch_code() and len(code) >= 24
    page = server.write_opener(8791)
    assert f"http://127.0.0.1:8791/?k={code}" in page.read_text(encoding="utf-8") and page.parent == tmp_path
    monkeypatch.setattr(server, "alive", lambda port: port == 8791)
    assert server.running_port() == 8791
    monkeypatch.setattr(server, "alive", lambda port: False)
    assert server.running_port() is None


def test_another_jobgambit_on_the_old_port_is_not_ours(tmp_path, monkeypatch):
    """The port in instance.json may now belong to another JobGambit (another account, a test home) or a squatter:
    only one that proves it knows this folder's launch code is this one. The code is never sent to the port."""
    import io
    import urllib.parse
    import urllib.request
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    from jobgambit.app import server
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
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    from jobgambit.app import server
    monkeypatch.setattr(server.sys, "platform", "linux")
    assert server.opener(8791).startswith("http://127.0.0.1:8791/?k=")
    monkeypatch.setattr(server.sys, "platform", "win32")
    assert server.opener(8791).startswith("file:")


def _old_install(tmp_path, monkeypatch, store=None):
    """A computer whose app-data, Documents and password store live under tmp_path; no real tasks are touched."""
    import keyring
    from jobgambit.core import moved
    monkeypatch.delenv("JOBGAMBIT_HOME")
    monkeypatch.setattr(data, "folder", lambda name: tmp_path / "appdata" / name)
    monkeypatch.setattr(moved.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(moved, "_tasks", lambda old: None)
    store = {} if store is None else store
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    def delete(s, n):  # like the real store: deleting what isn't there raises
        if store.pop((s, n), None) is None:
            raise keyring.errors.PasswordDeleteError(n)
    monkeypatch.setattr(keyring, "delete_password", delete)
    return moved, store


@pytest.mark.parametrize("old, db", [("CareerPilot", "careerpilot.db"), ("JobQuill", "jobquill.db")])
def test_moved_from_old_name(tmp_path, monkeypatch, old, db):
    """The first start after a rename takes the old install's data, documents and passwords along, once."""
    moved, store = _old_install(tmp_path, monkeypatch, {(old, "__index__"): '["email_password"]', (old, "email_password"): "pw-12345"})
    (tmp_path / "appdata" / old).mkdir(parents=True)
    (tmp_path / "appdata" / old / db).write_text("db")
    (tmp_path / "Documents" / old).mkdir(parents=True)
    assert data.home() == tmp_path / "appdata" / "JobGambit"  # whatever asks for the folder first
    assert (tmp_path / "appdata" / "JobGambit" / "jobgambit.db").read_text() == "db"
    assert not (tmp_path / "appdata" / old).exists() and (tmp_path / "Documents" / "JobGambit").is_dir()
    assert store == {("JobGambit", "__index__"): '["email_password"]', ("JobGambit", "email_password"): "pw-12345"}
    (tmp_path / "appdata" / old).mkdir()  # a later old folder is left alone
    moved.run()
    assert (tmp_path / "appdata" / old).is_dir()


def test_moved_prefers_newest_old_name(tmp_path, monkeypatch):
    """A computer that still has a CareerPilot folder next to its JobQuill one: the JobQuill data is the current one."""
    _old_install(tmp_path, monkeypatch)
    for old, value in (("JobQuill", "new"), ("CareerPilot", "old")):
        (tmp_path / "appdata" / old).mkdir(parents=True)
        (tmp_path / "appdata" / old / f"{old.lower()}.db").write_text(value)
    assert data.home() == tmp_path / "appdata" / "JobGambit"
    assert (tmp_path / "appdata" / "JobGambit" / "jobgambit.db").read_text() == "new"
    assert (tmp_path / "appdata" / "CareerPilot" / "careerpilot.db").read_text() == "old"


def test_moved_skips_instance_home(tmp_path, monkeypatch):
    """A test or scratch home (JOBGAMBIT_HOME) never moves the computer's real install."""
    moved, _ = _old_install(tmp_path, monkeypatch)
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path / "scratch"))
    (tmp_path / "appdata" / "JobQuill").mkdir(parents=True)
    moved.run()
    assert (tmp_path / "appdata" / "JobQuill").is_dir() and not (tmp_path / "appdata" / "JobGambit").exists()




def test_tmp_home_isolated(tmp_path, monkeypatch):
    """A test or scratch home never reads or writes the real keyring service, task or browser port."""
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    import keyring
    from jobgambit.core import scheduler, secrets
    store, used = {}, set()
    monkeypatch.setattr(keyring, "get_password", lambda s, n: used.add(s) or store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: used.add(s) or store.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, n: used.add(s) or store.pop((s, n), None))
    secrets.put("email_password", "pw-12345")
    secrets.get("email_password"), secrets.names(), secrets.redact("x"), secrets.delete("email_password"), secrets.delete_all()
    assert used == {f"JobGambit-{data.instance()}"} and len(data.instance()) == 8
    assert scheduler.name().endswith(data.instance()) and str(tmp_path.resolve()) in scheduler.command()
    assert scheduler.name("mail").startswith("JobGambit email check") and "mail" in scheduler.command("mail")


def test_counts(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    from jobgambit.app import server
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
    import jobgambit
    for m in pkgutil.walk_packages(jobgambit.__path__, "jobgambit."):
        if ".countries." not in m.name:
            importlib.import_module(m.name)

    def models(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from models(sub)
    found = {m for m in models(BaseModel) if m.__module__.startswith("jobgambit")}
    assert len(found) > 20
    for m in found:
        _strict_ok(ai.strict(m.model_json_schema()), m.__name__)

    from jobgambit.core.profile import Profile
    schema = Profile.model_json_schema()
    defs = ai.strict(schema)["$defs"]  # a field named "title" is not the schema's title annotation: it stays
    assert "title" in defs["Section"]["required"] and "title" in defs["Entry"]["properties"]
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


def test_backup_leaves_out_the_run_logs():
    import io
    import zipfile
    from jobgambit.core import system
    (data.home() / "logs" / "ai" / "2026-10-26").mkdir(parents=True)
    (data.home() / "logs" / "ai" / "2026-10-26" / "1.jsonl").write_text("{}", encoding="utf-8")
    (data.home() / "notes.txt").write_text("kept", encoding="utf-8")
    names = zipfile.ZipFile(io.BytesIO(system.backup())).namelist()
    assert "notes.txt" in names and not any(n.startswith("logs/") for n in names)
