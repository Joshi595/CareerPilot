"""Run one AI step on the person's own AI plan, through that vendor's command-line tool.

Answer runs: the app sends the whole prompt on stdin, the AI gets no tools and must answer
with JSON matching a pydantic model. The app validates the answer and retries once.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pydantic import BaseModel, ValidationError

LIMIT = re.compile(r"usage limit|session limit|weekly limit|rate.?limit|limit reached|quota|too many requests", re.I)
WRITE_STEPS = {"Tailor", "Change", "Revision"}  # answer models (by class name) that write the CV and letter themselves
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class AIError(Exception):
    pass


class UsageLimit(AIError):
    """The person's plan limit is reached; the queue should wait for the reset."""


class Agent:
    name = exe = label = ""
    level = "standard"  # economy | standard | premium (Settings -> AI), mapped to a model where the CLI allows it
    answer_env: dict = {}  # extra environment for answer runs

    def path(self) -> str | None:
        return shutil.which(self.exe)

    def command(self, schema: dict, step: str = "") -> list[str]:
        raise NotImplementedError

    def extract(self, stdout: str) -> dict:
        return json.loads(stdout)

    def usage(self, stdout: str) -> dict:
        """Tokens and cost of one call, when the CLI reports them."""
        return {}

    def steps(self, stdout: str) -> list[dict]:
        """One entry per API call inside the run, when the CLI reports them."""
        return []

    def failed(self, stdout: str) -> str:
        """An answer that is an error report (the call still used the plan)."""
        return ""


    def call(self, args: list[str], prompt: str, timeout: int, step: str, key: str, env: dict | None = None, cwd: str | None = None,
             mode: str = "") -> str:
        """One CLI call, recorded in the ai_calls ledger whatever happens (a failed call uses plan limits too)."""
        start, out, err = time.time(), "", ""
        try:
            p = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                               creationflags=NO_WINDOW, env={**os.environ, **env} if env else None, cwd=cwd)
            out = p.stdout
            if p.returncode or not out.strip():
                err = (p.stderr or out).strip()[-500:] or "no answer"
                raise (UsageLimit if LIMIT.search(err) else AIError)(f"{self.label}: {err}")
            return out
        except subprocess.TimeoutExpired:
            err = f"no answer within {timeout} s"
            raise AIError(f"{self.label} did not answer within {timeout} s")
        finally:
            record(step, key, self, out, time.time() - start, err, prompt=prompt, mode=mode)

    def run(self, prompt: str, schema: dict, timeout: int = 600, step: str = "", key: str = "") -> dict:
        exe = self.path()
        if not exe:
            raise AIError(f"{self.label} is not installed")
        # an empty folder to start in: an AI that has tools (Codex, Antigravity) finds nothing of the person's around it
        with tempfile.TemporaryDirectory(prefix="jobgambit-ai-") as empty:
            out = self.call([exe, *self.command(schema, step)], prompt, timeout, step, key, self.answer_env, cwd=empty)
        try:
            return self.extract(out)
        except (ValueError, KeyError) as e:
            raise AIError(f"{self.label} answered something that is not JSON: {e}")


class Claude(Agent):
    name, exe, label = "claude", "claude", "Claude"
    # An answer run is one turn on a prompt never sent again: a cache write would cost twice the plain input
    # (measured 2026-10-21: $0.060 cached vs $0.031 uncached for the same 7k-token prompt).
    answer_env = {"DISABLE_PROMPT_CACHING": "1"}

    def path(self):
        # ponytail: falls back to the VS Code extension's bundled binary (Joshi's PC); real users install the CLI.
        found = shutil.which(self.exe) or sorted(glob.glob(os.path.expanduser(
            "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude*")))
        return found if isinstance(found, str) else (found[-1] if found else None)

    def command(self, schema, step=""):
        # No tools, no MCP, none of the person's own settings, a one-line system prompt (the playbook carries the
        # instructions): one lean model call. Standard writes the documents on Opus (it wrote better CVs and letters in a
        # blind comparison) and runs every other step on Sonnet (half the price); premium is Opus throughout.
        model = {"economy": "haiku", "premium": "opus"}.get(self.level) or ("opus" if step in WRITE_STEPS else "sonnet")
        return ["-p", "--output-format", "stream-json", "--verbose", "--json-schema", json.dumps(schema), "--tools", "",
                "--system-prompt", "Answer only with JSON matching the given schema.",
                "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence", "--model", model]

    def extract(self, stdout):
        out = result(stdout)
        if out.get("is_error"):
            raise (UsageLimit if LIMIT.search(str(out.get("result"))) else AIError)(str(out.get("result")))
        return out["structured_output"]

    def failed(self, stdout):
        out = result(stdout)
        return str(out.get("result"))[:500] if out.get("is_error") else ""

    def usage(self, stdout):
        r, steps = result(stdout), self.steps(stdout)
        models = r.get("modelUsage") or {}
        if not models and not steps:
            return {}
        sums = {k: sum(s[k] for s in steps) for k in ("fresh", "cache_write", "cached", "output", "thinking")}
        if models:  # the CLI's totals, of the model that answered (the costliest); its own Haiku pass only adds to the cost
            name, main = max(models.items(), key=lambda m: m[1].get("costUSD", 0))
            sums.update(fresh=main.get("inputTokens", 0), cache_write=main.get("cacheCreationInputTokens", 0),
                        cached=main.get("cacheReadInputTokens", 0), output=main.get("outputTokens", 0))
            cost = round(sum(m.get("costUSD", 0) for m in models.values()), 4)
        else:  # a run cut short: what its finished API calls reported
            name, cost = steps[-1]["model"], 0
        return {"model": name, "input": sums["fresh"] + sums["cache_write"] + sums["cached"], **sums, "cost_usd": cost,
                "turns": r.get("num_turns") or len(steps), "api_seconds": round((r.get("duration_api_ms") or 0) / 1000, 1),
                "models": {m: {"in": v.get("inputTokens", 0), "cache_write": v.get("cacheCreationInputTokens", 0),
                               "cached": v.get("cacheReadInputTokens", 0), "out": v.get("outputTokens", 0),
                               "cost": v.get("costUSD", 0)} for m, v in models.items()}}

    def steps(self, stdout):
        """One entry per API call of a stream-json run: its tokens, the tools it called, and result_chars, the size of
        the tool results it read (what a page snapshot cost)."""
        out, by_id, pending = [], {}, 0
        for r in lines(stdout):
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}
            blocks = [b for b in msg.get("content") or [] if isinstance(b, dict)] if isinstance(msg.get("content"), list) else []
            if r.get("type") == "assistant" and msg.get("usage"):
                tools = [b.get("name", "") for b in blocks if b.get("type") == "tool_use"]
                if msg.get("id") in by_id:  # one API answer can arrive as several lines, each with the same id and usage
                    by_id[msg["id"]]["tools"] += tools
                    continue
                u = msg["usage"]
                s = {"n": len(out) + 1, "model": msg.get("model", ""), "tools": tools, "result_chars": pending,
                     "fresh": u.get("input_tokens") or 0, "cache_write": u.get("cache_creation_input_tokens") or 0,
                     "cached": u.get("cache_read_input_tokens") or 0, "output": u.get("output_tokens") or 0,
                     "thinking": (u.get("output_tokens_details") or {}).get("thinking_tokens") or 0}
                if msg.get("id"):
                    by_id[msg["id"]] = s
                out.append(s)
                pending = 0
            elif r.get("type") == "user":
                for b in blocks:
                    if b.get("type") == "tool_result":
                        c = b.get("content")
                        pending += len(c) if isinstance(c, str) else sum(len(x.get("text") or "") for x in c or [] if isinstance(x, dict))
        return out



def _pairs(s: dict) -> bool:
    """A free-form dict (dict[str, X]): an object with no properties."""
    return s.get("type") == "object" and "properties" not in s


def strict(schema: dict) -> dict:
    """The schema as OpenAI's strict mode wants it (Codex): every object closed with every property required (optional
    ones nullable), a free-form dict as a list of {key, value} pairs, no defaults. loosen() turns the answer back."""
    def walk(s):
        if not isinstance(s, dict):
            return s
        if "$ref" in s:
            return {"$ref": s["$ref"]}  # no sibling keywords next to a $ref
        # "title" / "default" are dropped as keywords, never as field names (a custom section's title)
        s = {k: {p: walk(x) for p, x in v.items()} if k == "properties" else walk(v) if isinstance(v, dict)
             else [walk(x) for x in v] if k == "anyOf" else v for k, v in s.items() if k not in ("default", "title")}
        if _pairs(s):
            value = s["additionalProperties"] if isinstance(s.get("additionalProperties"), dict) else {"type": "string"}
            return {"type": "array", **({"description": s["description"]} if "description" in s else {}),
                    "items": {"type": "object", "properties": {"key": {"type": "string"}, "value": value},
                              "required": ["key", "value"], "additionalProperties": False}}
        if s.get("type") == "object":
            req = set(s.get("required", []))
            s["properties"] = {k: v if k in req else {"anyOf": [v, {"type": "null"}]} for k, v in s["properties"].items()}
            s["required"], s["additionalProperties"] = list(s["properties"]), False
        return s
    out = walk(schema)
    if "$defs" in schema:
        out["$defs"] = {k: walk(v) for k, v in schema["$defs"].items()}
    return out


def loosen(value, schema: dict, defs: dict | None = None):
    """A strict() answer back in the original schema's shape: pairs -> dict, null optional fields left out."""
    defs = schema.get("$defs", {}) if defs is None else defs
    if "$ref" in schema:
        schema = defs[schema["$ref"].split("/")[-1]]
    if "anyOf" in schema and value is not None:
        kind = {dict: ("object",), list: ("array", "object")}.get(type(value), ())
        schema = next((b for b in schema["anyOf"] if "$ref" in b or b.get("type") in kind), {})
        return loosen(value, schema, defs) if schema else value
    if _pairs(schema) and isinstance(value, list):
        sub = schema.get("additionalProperties") if isinstance(schema.get("additionalProperties"), dict) else {}
        return {p["key"]: loosen(p["value"], sub, defs) for p in value}
    if schema.get("type") == "object" and isinstance(value, dict):
        req, props = set(schema.get("required", [])), schema.get("properties", {})
        return {k: loosen(v, props.get(k, {}), defs) for k, v in value.items() if v is not None or k in req}
    if schema.get("type") == "array" and isinstance(value, list):
        return [loosen(v, schema.get("items", {}), defs) for v in value]
    return value


class Codex(Agent):
    """Unverified: not installed on the dev machine yet (see docs/spike.md). Codex answers in OpenAI's strict mode,
    which refuses optional fields and free-form dicts: the schema goes through strict() and the answer through loosen()."""
    name, exe, label = "codex", "codex", "ChatGPT (Codex)"

    def command(self, schema, step=""):
        # One file per schema, named by its content: reused across calls, so files don't pile up. In the data folder,
        # not the shared temp folder, where another user of the computer could put a file of that name first.
        from jobgambit.core.data import home
        text = json.dumps(schema, sort_keys=True)
        f = home() / "cache" / f"schema-{hashlib.sha1(text.encode()).hexdigest()[:12]}.json"
        if not f.exists():
            f.parent.mkdir(exist_ok=True)
            f.write_text(text, encoding="utf-8")
        return ["exec", "--output-schema", str(f), "--sandbox", "read-only", "--skip-git-repo-check", "-"]

    def run(self, prompt, schema, timeout=600, step="", key=""):
        return loosen(super().run(prompt, strict(schema), timeout, step, key), schema)


class Antigravity(Agent):
    """Unverified. Reported to print nothing without a terminal: may need a pseudo-terminal."""
    name, exe, label = "antigravity", "agy", "Google (Antigravity)"

    def command(self, schema, step=""):
        return ["-p", "--output-format", "json", "--json-schema", json.dumps(schema)]

    def extract(self, stdout):
        return json.loads(stdout)["structured_output"]


AGENTS = {a.name: a for a in (Claude(), Codex(), Antigravity())}


def installed() -> dict[str, bool]:
    return {name: bool(a.path()) for name, a in AGENTS.items()}


def chosen(name: str = "", level: str = "") -> Agent:
    """The person's chosen AI (and cost level from their settings), else the first one installed."""
    if not level:
        from jobgambit.core import profile as pf
        level = pf.read("preferences").get("cost_level", "standard")
    for a in ([AGENTS[name]] if name in AGENTS else []) + list(AGENTS.values()):
        if a.path():
            a.level = level
            return a
    raise AIError("No AI is installed. Open Help to set one up.")


def answer(prompt: str, model: type[BaseModel], agent: Agent, timeout: int = 600, key: str = "") -> BaseModel:
    """key: the job this call is for, so the ledger can add up each application (a retry is a second call)."""
    schema = model.model_json_schema()
    for attempt in range(2):
        try:
            return model.model_validate(agent.run(prompt, schema, timeout, model.__name__, key))
        except ValidationError as e:
            if attempt:
                raise AIError(f"{agent.label} gave an answer in the wrong shape twice: {e}")
            prompt += f"\n\nYour previous answer did not match the required JSON schema:\n{e}\nAnswer again."


# ---------------------------------------------------------------- the ledger: every AI call, kept to see where tokens go
BATCH = ""  # a batch of AI calls in progress (its start time) tags them
# ponytail: one module value, as one batch goes at a time (under its own lock); per-thread if batches ever overlap
LOG_DAYS = 30  # run logs (the prompt and the CLI's whole output) are kept this long, on this computer only


def lines(out: str) -> list[dict]:
    """The CLI's output as JSON objects: stream-json gives one per line, json one in all."""
    rows = []
    for line in (out or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def result(out: str) -> dict:
    """The run's final report (answer, totals per model, turns); {} when the run was cut short."""
    return next((r for r in reversed(lines(out)) if r.get("type") in ("result", None)), {})


def record(step, key, agent, out, seconds, error="", prompt="", mode="", outcome="", batch=""):
    """agent: the Agent (or its name, then without token numbers); out: the CLI's output; prompt: what was sent.
    The prompt's part sizes come from the last prompts.build() in this thread."""
    from jobgambit.core import prompts
    parts, prompts.LAST.parts = getattr(prompts.LAST, "parts", None), None  # one prompt's parts never stick to the next
    try:
        from jobgambit.core.data import db
        real = out and not isinstance(agent, str)
        u, steps = (agent.usage(out), agent.steps(out)) if real else ({}, [])
        error = error or (agent.failed(out) if real else "")
        answer = result(out).get("structured_output") if real else None
        outcome = outcome or str((answer or {}).get("status") or "" if isinstance(answer, dict) else "") or ("error" if error else "ok")
        with db() as con:
            call = con.execute(
                "insert into ai_calls(step, job_key, agent, model, input, cached, output, cost_usd, seconds, error, fresh, cache_write,"
                " thinking, turns, api_seconds, prompt_chars, parts, models, mode, outcome, batch) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (step, key, agent if isinstance(agent, str) else agent.name, u.get("model", ""), u.get("input", 0),
                 u.get("cached", 0), u.get("output", 0), u.get("cost_usd", 0), round(seconds, 1), error, u.get("fresh", 0),
                 u.get("cache_write", 0), u.get("thinking", 0), u.get("turns", 0), u.get("api_seconds", 0), len(prompt or ""),
                 json.dumps(parts) if parts else None, json.dumps(u["models"]) if u.get("models") else None, mode, outcome,
                 batch or BATCH)).lastrowid
            con.executemany("insert into ai_steps(call_id, n, tools, result_chars, fresh, cache_write, cached, output, thinking)"
                            " values (?,?,?,?,?,?,?,?,?)", [(call, s["n"], ",".join(s["tools"]), s["result_chars"], s["fresh"],
                                                            s["cache_write"], s["cached"], s["output"], s["thinking"]) for s in steps])
            if out or prompt:
                con.execute("update ai_calls set log=? where id=?", (write_log(call, prompt, out), call))
    except Exception:
        pass  # the ledger is a bonus: it never breaks an AI step


def write_log(call: int, prompt: str, out: str) -> str:
    """<data>/logs/ai/<day>/<call>.prompt.txt and .jsonl: what was sent and all the CLI said, to work out later where
    tokens went. Kept LOG_DAYS days; never sent anywhere. Returns the log's path in the data folder ('' if not written)."""
    try:
        from jobgambit.core import secrets
        from jobgambit.core.data import home
        root = home() / "logs" / "ai"
        day = root / date.today().isoformat()
        day.mkdir(parents=True, exist_ok=True)
        (day / f"{call}.prompt.txt").write_text(secrets.redact(prompt or ""), encoding="utf-8", newline="")
        (day / f"{call}.jsonl").write_text(secrets.redact(out or ""), encoding="utf-8", newline="")
        cutoff = (date.today() - timedelta(days=LOG_DAYS)).isoformat()
        for d in root.iterdir():
            if d.is_dir() and d.name < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        return f"logs/ai/{day.name}/{call}.jsonl"
    except Exception:
        return ""


def calls(key: str) -> list[dict]:
    """One application's AI calls, oldest first."""
    from jobgambit.core.data import db
    with db() as con:
        return [dict(r) for r in con.execute("select * from ai_calls where job_key=? order by id", (key,))]
