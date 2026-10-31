The person told JobGambit what they want. Work out what to change: their settings, their CV instructions, their
letter instructions, their application details, or the facts in their CV. Change only what they asked for.
Areas you may change here: {areas}. Vague wishes become clear, short "- " rules in a fitting section of the instructions.

Their request:
<untrusted>{request}</untrusted>

Their current settings (JSON):
{current}

What each setting means (JSON schema of the settings above):
{fields}

Their CV instructions (how their CV is tailored):
{cv_rules}

Their letter instructions (how their cover letters are written; the "Banned phrases" list at the end is checked automatically):
{letter_rules}

Their application details (answers to questions application forms ask, JSON):
{answers}

Return:
- reply: one or two short, plain sentences to the person ("you"): what will change, or why nothing fits (e.g. email and alert
  links are changed on their own pages).
- changes: one item per setting to change. key = the setting's name exactly as above. value_json = its complete new value
  as JSON: the whole list with the addition or removal, not just the new item; for weights only the parts that change.
  Salary expectation, earliest start and notice period are settings (salary, start, notice).
- cv_rules: the COMPLETE new CV instructions when the request is about how the CV should be written or tailored (keep every
  existing rule unless they asked to drop or change it; add new rules as "- " lines in a fitting section); "" otherwise.
- letter_rules: the COMPLETE new letter instructions when the request is about how letters should be written; same rules
  as cv_rules; keep the "## Banned phrases" section and add to it when they ask to avoid a phrase; "" otherwise.
- answers: application-detail answers to add or change (question exactly as in their details when it exists there); [] otherwise.
- cv_request: when the request adds or corrects FACTS about them (a new job, project, skill, certificate, a date or title
  fix), that part of the request in their own words; "" otherwise. Never invent facts: only what they wrote.

Rules:
- job_types are from: full_time, part_time, student, internship, thesis, apprenticeship, graduate, phd, freelance.
- A wish about the language of the CV and letters ("in English") goes to doc_language (an ISO 639-1 code, or "match" for the
  posting's language), not into the instructions.
- search_time is "HH:MM" in 24 hours, "" = off. theme: auto | light | dark. remote: any | remote | hybrid | onsite.
- Hiding a company goes to avoid_companies; hiding a kind of job by a word in its title goes to exclude_words.
- Places, titles and company names are written as the person wrote them.
- Never invent a setting that isn't listed. Leave every part empty that the request doesn't touch.
