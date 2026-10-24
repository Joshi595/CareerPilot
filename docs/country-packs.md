# Adding your country

Every country already works with the worldwide sources (LinkedIn, Indeed, Glassdoor, Google Jobs, remote-job sites,
company career sites). A **country pack** makes it better: national job boards, the local CV conventions, local
job-type words, and language rules.

1. Copy `careerpilot/countries/_template/` to `careerpilot/countries/<code>/` (two-letter ISO code, lower case, e.g. `fr`).
2. Fill in `pack.yaml`: languages, CV conventions (photo, date of birth, paper size, length, letter format), local
   job-type words and language requirements.
   - `job_types`: the words that mark a title as a working-student job, internship, thesis… (`werkstudent`, `praktikum`).
   - `search_words`: the words searched in front of a person's titles for each job type they chose (`Werkstudent`,
     `Praktikum`, `Masterarbeit`). Without them, a student in your country only gets the English ones.
   - `career_sites` with `types: [student, internship, thesis]`: a student-job board scanned only for people who look
     for one of those types.
3. Optional: add the country's own job sources in `sources.py`. Each source is a small function that returns postings.
   A source that filters job types itself (like Germany's Arbeitsagentur with its offer types) lists them in `TYPED`
   and reads `ctx["kind"]`; it then gets the person's plain words instead of "Werkstudent …" queries.
4. Test without storing anything: `uv run careerpilot source-test <code> "nurse"`.
5. Open a pull request. Say which sources you added and why they're allowed (see the rules below).

## Source rules

- Official APIs and feeds first (national employment agencies often have one), then a company career site's public data.
- Respect `robots.txt`. No logins, no bot-protection workarounds, no captcha solving, no proxies.
- Keep request rates low: one person's search, not a crawler.
- Never collect personal data about other people.

Ideas for next packs (free public sources): Canada (Job Bank), Sweden (Platsbanken), Belgium (VDAB), Singapore
(MyCareersFuture), Mexico (OCC), Vietnam (ITviec), India (Naukri is already worldwide), the UK (Find a job).
career-ops' provider documentation lists many more with notes on what's allowed.
