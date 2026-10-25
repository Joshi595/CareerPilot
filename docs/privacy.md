# Privacy: what goes where

JobQuill runs on your computer. There is no JobQuill server, no account with us, and no tracking.

| Your data | Where it is kept | Who sees it |
|---|---|---|
| Profile, preferences, form answers, writing style | Your data folder (Help → *Open folder*) | The AI company you chose, when a step needs it |
| Jobs, applications, PDFs | Your data folder | Only you; the employers you apply to get the PDFs you send |
| Email app password and other passwords, alert links (incl. a Telegram bot key) | Your computer's password store (Windows Credential Manager, macOS Keychain, Linux Secret Service), never a file | Only JobQuill on your computer. **Never the AI.** |

**The data folder** is your computer's local app-data folder (on Windows `%LOCALAPPDATA%\JobQuill`). It is not inside
the program folder and not synced to OneDrive or iCloud.

**The AI** gets, for each step, only what that step needs: your profile facts, the job posting, your writing style.
It never sees a password. Claude runs with no tools at all (it can't read files or run anything on your computer).
ChatGPT (Codex) runs in Codex's own read-only sandbox, which can still read files; Google (Antigravity) is not tested
yet. Documents that contain password- or key-shaped text fail their checks, so a file the AI read doesn't leave in a
PDF unnoticed (details in [SECURITY.md](../SECURITY.md)). A job posting could try to give the AI instructions; JobQuill marks it as data, and you see the documents before
you send them.
The *Ask JobQuill* box (Settings and Profile) sends the AI your sentence, your current settings (except email),
your CV and letter instructions and your application details; when you ask to change facts in your CV,
also your CV. Passwords and alert links are never among them. Nothing changes until you click Apply.

**Email**: every hour, JobQuill downloads the mail that arrived since its last check (starting from when you
connected it), keeps the mails about your applications (replies from companies you applied to, job portals) and
forgets the rest. Of those it keeps the sender, subject and up to 4,000 characters of text, so *Needs you* can show
them. Use a separate address just for job applications if you prefer it never sees private mail. Use an app password
(you can revoke it any time in your email account).

**AI for mail** (Settings › Email, on by default): a mail about an application that the rules can't sort goes to the
AI, marked as untrusted, with a short list of your applications to that company (company, role, date, job number), at
most 10 mails per check. The AI says what kind of mail it is and which application it is about; you still decide.

**AI second look** (Settings › Search, on by default): after each search, the titles, companies and places of the
postings filtered on their title alone go to the AI (at most 400 lines), to find the ones that fit you after all.

**How many applied**: for your best new matches and the job you open, JobQuill reads LinkedIn's public job page,
without logging in. Nothing about you is sent.

**Alerts and notifications**: interview invitations, tests, offers and "action needed" mails go to the alert service
you chose (ntfy, Telegram, Slack…: the subject, the company and the interview time) and appear as a notification on
your computer (switch that off in Settings › Alerts). The alert service sees what it delivers, under its own terms.

**Your upload folder** ("Current application" in the JobQuill folder; `Documents\JobQuill` when JobQuill is
installed as a program) holds the documents you add (certificates, references…) and the CV and letter of the application
you have open. It is never committed to git. If your Documents folder is synced to OneDrive or iCloud, so is this folder.

**Delete everything**: Settings → *Delete everything* removes your data folder, the CV and letter JobQuill put in your
upload folder, all saved passwords and the daily search. The documents you added yourself stay in the upload folder;
delete them there if you want them gone too.

This is a description of how the software works, not legal advice.
