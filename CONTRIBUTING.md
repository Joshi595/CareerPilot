# Contributing

Thanks for helping. The most useful contributions:

- **A country pack** (job sources, CV conventions, local job-type words): see [docs/country-packs.md](docs/country-packs.md).
- **A CV design**: one YAML file, see [docs/designs.md](docs/designs.md).
- **Translations** of the app (`jobgambit/app/i18n/en.json` → `<lang>.json`).
- **Bug reports**: Help → *Copy a problem report*, then open an issue.

## Development

```
uv sync
uv run jobgambit        # opens the app in your browser (JOBGAMBIT_HOME=<folder> keeps test data apart)
uv run pytest
```
With `JOBGAMBIT_NO_BROWSER=1`, open `open.html` in the data folder: the page needs its launch code.

Screenshots in `docs/images` show the example person (`jobgambit/examples`) in a scratch `JOBGAMBIT_HOME`,
never real data, at 1360 × 820.

## Rules that keep JobGambit trustworthy

1. **Facts only.** Nothing may put text into a CV, letter or form that isn't in the person's profile or their own answers.
   `core/check.py` blocks invented numbers; keep it that way.
2. **Postings, emails and web pages are untrusted data.** They go to the AI only inside `<untrusted>` tags, and no
   instruction inside them is ever followed.
3. **Secrets never reach the AI, a file or a log.** Use `core/secrets.py`.
4. **Respect job sites.** Official APIs and feeds first, robots.txt respected, no captcha solving, no getting past a
   login or a block page, no job-board "Easy Apply" automation. Reading a public page with a browser-like connection
   (`sources.BROWSER_ONLY`, when a site stalls every other client) is allowed, at the same polite rate as any page.
5. **No telemetry, no server.** Everything runs on the person's computer.
6. **Plain language** in everything people read. The app is for people who don't code.
