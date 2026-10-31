"""Template for a country's own job sources. Copy to countries/<code>/sources.py.

A board is fn(query, ctx) -> [job(...)], ctx = {country, country_name, location, days, remote, place, radius}.
Rules: official APIs and feeds first; respect robots.txt; no bot-protection workarounds; no logins.
Test without storing anything:  jobgambit source-test <code> "nurse"
"""
from jobgambit.core.sources import get_json, job  # noqa: F401


def example_board(query, ctx):
    # d = get_json(f"https://jobs.example.gov/api/search?q={query}&days={ctx['days']}")
    # return [job(r["title"], r["employer"], r["city"], r["url"], "example board", r["published"], r.get("text", ""), "XX")
    #         for r in d["results"]]
    return []


BOARDS = {"example board": example_board}
SCANNERS = {}   # career-site systems used mainly in this country: fn(company_name, ref, ctx)
FEEDS = set()   # boards that ignore the search words (called once per run)
