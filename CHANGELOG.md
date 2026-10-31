# Changelog

## 0.16.2 (2026-10-31)

- pypdf 6.21.4: fixes seven known security issues in reading PDF files (CVE-2026-119203 to -119209).

## 0.16.1 (2026-10-31)

- JobQuill is now **JobGambit**: the old name belongs to another product. The first start moves your data folder,
  documents and saved passwords over; the scheduled tasks are made again under the new name.
- Coming from 0.13–0.14: Help › Update can't install the renamed app. Run the installer once more (README), it removes
  the old JobQuill copy and keeps your data.

## 0.13.0 (2026-10-24)

- **CareerPilot is now JobQuill.** Thousands of projects on GitHub share the old name. The command is now `jobquill`,
  and the data folder and environment variables are named after JobQuill too (`JOBQUILL_HOME`).
- Updating from CareerPilot: run the installer again (the update button of 0.12 can't install the renamed app). It
  removes the old copy, and the first start moves your data folder, saved passwords and documents over. The old
  scheduled tasks are replaced by new ones.

## 0.12.4 (2026-10-24)

- Ready to be public: test examples use fictional employers, and the docs say exactly what a failed document check
  does (it is shown before you send; it doesn't stop you).

## 0.12.3 (2026-10-24)

- A version tag no longer tries to publish to PyPI (it failed, as no PyPI project is set up). Publishing waits for the
  repository variable PUBLISH_PYPI = true, set when the project goes public.

## 0.12.2 (2026-10-24)

- Updated: fastapi 0.156.8 (the local server) and httpx2 2.16.0 (used by the tests); the test machines' actions/checkout
  7.0.1 and setup-uv 10.3.1, still pinned to exact commits.

## 0.12.1 (2026-10-24)

- Review cards read as whole sentences: the new line in full with the added words marked, the original below it
  with the removed words struck through (the Documents step's changes too).
- A computer without a password store (Linux without a Secret Service) no longer breaks Needs you: it reads as
  nothing saved.
- Tests no longer need an AI app installed (they failed on CI).

## 0.12.0 (2026-10-24)

**Never miss an interview**
- New **Needs you** page: interviews coming up, mails to act on (each with a short summary and the mail itself), mails
  whose application isn't clear (every candidate with its reasons), and what CareerPilot filed by itself.
- Mails are linked by the job number in their text, not only by the role's words; receipts are filed by themselves.
- Interview, test, offer and "action needed" alerts are always on, are kept and sent again until they arrive, and
  also show on your desktop. Reminders the evening before, that morning and three hours before.
- English and numeric interview times are read ("October 26, 2026 at 2:00 PM", "26/10/2026 14:00").
- The hourly email check no longer depends on the daily search, repairs itself when its task is missing, and warns
  you when it stopped running.
- Optional AI for mail sorts the mails the rules can't.

**Applying**
- Review cards mark exactly which words changed, with a tag for the kind of change, a summary and filters.
- My applications: cleaner rows and drawer; the job description is shown for interviews.
- How many people already applied, where LinkedIn shows it publicly.

**Interview prep**
- Practice opens on your next interview: a brief on the job, STAR stories from your CV, a drill on every line you
  sent, and a mock interview with a timer and a summary.

**Search**
- Every search word now comes round within each board's look-back window; Settings › Search shows the coverage.
- Postings filtered on their title alone are judged again once their text is read; an optional AI second look.
- With several places in one country, the boards search the whole country and the distance decides; with one place,
  your radius is passed to the boards.
- Google Jobs is left out while the scraper gets no results.

**Security**
- The page opens only with a launch code, so other accounts on the same computer can't use it; one CareerPilot per
  data folder.
- Install and Update use tagged releases only. CI actions are pinned, dependencies audited.
- Prompts can't pull untrusted text out of its fence; documents with password- or key-shaped text fail their checks.
- The Current application folder is readable by your account only. Problem reports leave out your name, email,
  phone and home folder.

## 0.11.0 and earlier

See the commit history.
