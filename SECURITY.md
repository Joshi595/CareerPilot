# Security

## Supported versions

Only the latest release gets security fixes. Help › *Update* installs it (always a tagged release, never unreleased
code).

## Reporting a problem

Please don't open a public issue for a security problem. Report it privately on GitHub: the repository's
**Security** tab → **Report a vulnerability**. Say what you found and how to repeat it. You'll get an answer within a
week, and a fix goes into the next release.

In scope: anything that lets a website, a job posting, an email, another program or **another account on the same
computer** read or change your CareerPilot data, your passwords or your files, or act in your name (send email,
apply, delete). Out of scope: problems that need someone who is already signed in to *your* computer account.

## What CareerPilot defends against

| Who | What they could try | What stops them |
|---|---|---|
| A website you visit | Call the app's local address from your browser | Every request needs a key that only CareerPilot's own page has; requests under another host name are refused (DNS rebinding) |
| Another program, or another account on the same computer | Open `127.0.0.1` and take over the app | The page opens only with a launch code stored in your private data folder (see below) |
| Another account on the same computer | Read your certificates and PDFs | The data folder and the *Current application* folder are readable by your account only |
| A job posting or an email | Give the AI instructions, or get it to copy your files into a letter | Untrusted text is fenced as data; Claude runs with no tools; documents with password- or key-shaped text fail their checks; you see everything before it's sent |
| A link in a posting | Make CareerPilot fetch something on your computer or network | Only public `http`/`https` addresses, checked again after every redirect |
| A compromised update | Reach every user at once | Updates install a tagged release only; CI actions are pinned to exact versions and dependencies are audited |

## How it works

- **Local only.** The app is a web server on `127.0.0.1`. Each start makes a new key that only CareerPilot's own page
  knows; every API request without it is refused.
- **Launch code.** `127.0.0.1` is shared by every account on the computer, so the page itself is protected too: it is
  served only with a launch code, which CareerPilot keeps in your data folder (`instance.json`, readable by you
  only). On start it opens a small file there (`open.html`) that carries the code, so the code never shows up in a
  command line another account could see. On Linux the browser gets the address itself, because snap browsers (Ubuntu's
  Firefox) can't open files in hidden folders: there, other accounts can see the code in the process list while the
  browser runs. Opening the bare address only says to use the Start icon. *Delete
  everything* removes the code; the next start makes a new one. (If you point `CAREERPILOT_HOME` at a folder of your
  own on Windows, that folder is only as private as you made it.)
- **The page runs only its own code.** A Content Security Policy allows no inline scripts and no `javascript:` links,
  and the page can't be shown inside another website. Text from postings and emails is always shown as text, never as
  HTML. Links from postings are kept only if they are web links.
- **Passwords stay in your computer's password store** (Windows Credential Manager, macOS Keychain, Linux Secret
  Service), never in a file and never sent to the AI. Email connections check the server's certificate.
- **Desktop notifications** pass the mail subject to the system as data, never as part of a script.

## The AI apps and what they can reach

CareerPilot works through the AI app you already have. What each can reach on your computer:

| AI | Tools | Can it read your files? |
|---|---|---|
| Claude (Claude Code) | None: tools are switched off for every call | No |
| ChatGPT (Codex), beta | Codex's own read-only sandbox, started in an empty temporary folder | Yes, files your account can read. Anything it copied out would have to pass through a CV or letter, and documents containing password- or key-shaped text fail their checks |
| Google (Antigravity), beta | Not tested yet | Assume yes |

If that matters to you, use Claude.

See [docs/privacy.md](docs/privacy.md) for what data goes where.
