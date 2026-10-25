# Third-party notices

JobQuill (MIT) uses, adapts or was inspired by the following. Their licences apply to the parts named.

## Ideas and adapted code (MIT)

- **career-ops** (santifer and contributors, MIT): liveness check, reach-claim and scope checks, the "should I apply"
  evaluation, ATS scanner endpoints, application-form rules. "career-ops" is a trademark of its owners; JobQuill is
  not affiliated with it.
- **ai-job-search** (Mads Lorentzen, MIT): reviewer pass, requirement coverage, layout checks, setup interview flow.
- **JobFinderOS** (Matthew Price, MIT): career-neutral onboarding, target-company watchlist.
- **rendercv** (Sina Atalay and contributors, MIT): design ideas for the CV themes.

## Libraries (installed as dependencies)

| Library | Licence |
|---|---|
| FastAPI, Starlette, Uvicorn, pydantic | MIT / BSD-3-Clause |
| typst (Python bindings to the Typst compiler) | Apache-2.0 |
| python-jobspy | MIT |
| trafilatura | Apache-2.0 |
| ruamel.yaml | MIT |
| Babel (CLDR data) | BSD-3-Clause (data: Unicode licence) |
| dateparser | BSD-3-Clause |
| rapidfuzz | MIT |
| langdetect | Apache-2.0 |
| geonamescache (GeoNames data) | MIT (data: CC BY 4.0, GeoNames.org) |
| keyring | MIT |
| Apprise | BSD-2-Clause |
| pypdf | BSD-3-Clause |
| Alpine.js (bundled in `jobquill/app/static/vendor`) | MIT |

## Icons (bundled in `jobquill/app/static/icons.svg`)

- **Lucide** v0.460.0 (https://lucide.dev), ISC License:

  > Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT). All other copyright
  > (c) for Lucide are held by Lucide Contributors 2022.
  >
  > Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby
  > granted, provided that the above copyright notice and this permission notice appear in all copies.
  >
  > THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL
  > IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
  > INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
  > AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
  > PERFORMANCE OF THIS SOFTWARE.

## Fonts (bundled in `jobquill/templates/fonts`)

- **Fira Sans** (Mozilla Foundation, Carrois Corporate): SIL Open Font License 1.1, see `OFL-FiraSans.txt`.
- **Lato** (Łukasz Dziedzic): SIL Open Font License 1.1, see `OFL-Lato.txt`.
- Libertinus Serif and New Computer Modern come with the Typst compiler (OFL / GUST Font License).

Job data shown in the app belongs to the job sites and employers it comes from.
