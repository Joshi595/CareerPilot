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
from pydantic import BaseModel, ValidationError

LIMIT = re.compile(r"usage limit|session limit|weekly limit|rate.?limit|limit reached|quota|too many requests", re.I)
CHECK_STEPS = {"Review"}  # answer models (by class name) that only check a draft: a cheaper model is enough
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

    def failed(self, stdout: str) -> str:
        """An answer that is an error report (the call still used the plan)."""
        return ""


    def call(self, args: list[str], prompt: str, timeout: int, step: str, key: str, env: dict | None = None, cwd: str | None = None) -> str:
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
            record(step, key, self, out, time.time() - start, err)

    def run(self, prompt: str, schema: dict, timeout: int = 600, step: str = "", key: str = "") -> dict:
        exe = self.path()
        if not exe:
            raise AIError(f"{self.label} is not installed")
        # an empty folder to start in: an AI that has tools (Codex, Antigravity) finds nothing of the person's around it
        with tempfile.TemporaryDirectory(prefix="jobquill-ai-") as empty:
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
        # instructions): one lean model call. A check of a draft (CHECK_STEPS) runs on Sonnet at the standard level.
        model = {"economy": "haiku", "premium": "opus"}.get(self.level) or ("sonnet" if step in CHECK_STEPS else None)
        return ["-p", "--output-format", "json", "--json-schema", json.dumps(schema), "--tools", "",
                "--system-prompt", "Answer only with JSON matching the given schema.",
                "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence", *(["--model", model] if model else [])]

    def extract(self, stdout):
        out = json.loads(stdout)
        if out.get("is_error"):
            raise (UsageLimit if LIMIT.search(str(out.get("result"))) else AIError)(str(out.get("result")))
        return out["structured_output"]

    def failed(self, stdout):
        try:
            out = json.loads(stdout)
            return str(out.get("result"))[:500] if out.get("is_error") else ""
        except ValueError:
            return ""

    def usage(self, stdout):
        try:
            models = json.loads(stdout).get("modelUsage") or {}
        except ValueError:
            return {}
        # Tokens of the model that answered (the costliest); the CLI's own Haiku pass over the prompt only adds to the cost.
        name, main = max(models.items(), key=lambda m: m[1].get("costUSD", 0), default=("", {}))
        tok = lambda *ks: sum(main.get(k, 0) for k in ks)
        return {"model": name, "input": tok("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"),
                "cached": tok("cacheReadInputTokens"), "output": tok("outputTokens"),
                "cost_usd": round(sum(m.get("costUSD", 0) for m in models.values()), 4)}



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
        s = {k: walk(v) if isinstance(v, dict) else [walk(x) for x in v] if k == "anyOf" else v
             for k, v in s.items() if k not in ("default", "title")}
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
        from jobquill.core.data import home
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
        from jobquill.core import profile as pf
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
def record(step, key, agent, out, seconds, error=""):
    """agent: the Agent (or its name, then without token numbers); out: the CLI's output."""
    try:
        from jobquill.core.data import db
        u = agent.usage(out) if out and not isinstance(agent, str) else {}
        error = error or (agent.failed(out) if out and not isinstance(agent, str) else "")
        with db() as con:
            con.execute("insert into ai_calls(step, job_key, agent, model, input, cached, output, cost_usd, seconds, error)"
                        " values (?,?,?,?,?,?,?,?,?,?)",
                        (step, key, agent if isinstance(agent, str) else agent.name, u.get("model", ""), u.get("input", 0),
                         u.get("cached", 0), u.get("output", 0), u.get("cost_usd", 0), round(seconds, 1), error))
    except Exception:
        pass  # the ledger is a bonus: it never breaks an AI step


def calls(key: str) -> list[dict]:
    """One application's AI calls, oldest first."""
    from jobquill.core.data import db
    with db() as con:
        return [dict(r) for r in con.execute("select * from ai_calls where job_key=? order by id", (key,))]
