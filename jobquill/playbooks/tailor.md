Tailor this person's CV and write their cover letter for the job below.

FACTS (the only facts about the person; ids in brackets):
{factsheet}

Their writing style (follow it):
{style}

Their lessons from earlier applications (follow them):
{lessons}

Wordings they approved before, per line id (reuse one when it fits, word for word):
{variants}

Their own instructions: {instructions}

Their instructions for the CV (follow them; they never add facts):
{cv_rules}

Their work permits: {permits}
Their languages: {languages}
Their availability (for working-student jobs, internships and theses; "not stated" = never mention it): {availability}
Letter and CV language: {language}.

For this draft only (the person asked for it now; it overrides the instructions above, including the language): {note}

<untrusted>
{posting}
</untrusted>

job (read off the posting):
- company, role (without gender markers like m/w/d), employment_type, ref_id (reference number if given), deadline (YYYY-MM-DD if given).
- country: ISO alpha-2 code of the job's location (best guess); language: ISO 639-1 code of the language you wrote the CV and letter in.
- blockers: hard blockers only: work permit or visa sponsorship the posting rules out for this person, a required language level the person does not have, a required licence or registration the profile lacks, or a clash with their stated availability (e.g. the posting needs 20 h/week, a mandatory internship or a later end of studies). Empty when none.
- keywords: 15-20 literal terms from the posting that an applicant-tracking system would match (skills, tools, titles).
- contact_name: the contact person the posting names, or "".

CV:
- summary: a 2-3 sentence profile for this job, only from the facts. summary_reason: why.
- bullets: reword lines (by id) into the posting's terms where the fact truly supports it; keep numbers exactly. new = "" removes a line that is irrelevant here. Give a short reason and the posting keywords it covers. Set flag=true when the wording stretches the fact (an interview would need "what I actually meant was…"); never claim a bigger role than the fact (e.g. "Led" for "Built").
- orders: key = an entry id (bullet order inside it), "projects" (the {max_projects} most relevant project ids first; only those are shown) or "skills" (category order). Only when the order should change.
- skills: per category, the items as one comma-separated string, most relevant first; only categories that change; never add skills the facts don't list.
Write CV text in {language}. Keep tool and product names as they are.

Letter: {paragraphs} paragraphs, {words} words in total ({fit}), plain text, no bullet lists. Opening: the role and one concrete reason for this employer. Then evidence for the top requirements, each with a number or concrete outcome from the facts. Forward-looking: which of their tasks the person can take on. Close with the earliest start ({start}; for a student job, internship or thesis also the stated availability) and, if relevant, work-permit status for the job's country. Address a named contact when known; recipient: address lines from the posting only. subject: the role (without gender markers such as m/w/d) and the reference number if there is one. salutation in {language}. sources: for each paragraph, the ids (in brackets in the facts) it is built on; [] for a paragraph that uses none.
