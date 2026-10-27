# AI adapters

JobQuill runs the person's own AI through that vendor's command-line program (`jobquill/core/ai.py`).
The runs:

- **Answer runs** (almost everything): the whole prompt on stdin, no tools, JSON that matches a schema
  (generated from pydantic models). The app validates the answer and retries once.

| | Claude Code | Codex CLI | Antigravity CLI |
|---|---|---|---|
| Answer runs | `claude -p --json-schema … --tools ""` (tested) | `codex exec --output-schema … --sandbox read-only -` (untested) | `agy -p --output-format json --json-schema …` (untested) |
| Cost levels | economy = haiku, standard = opus for the CV and letter and sonnet for the rest, premium = opus | default model | default model |

To add an adapter: a subclass of `Agent` with `exe`, `command(schema)` and `extract(stdout)`; add it to `AGENTS`; test
it with Help → *Test*. Rules: use the vendor's own program as the person installed and signed into it; never bundle it;
never route requests through someone else's account.
