// Shared helpers and the tab switcher. Each tab is an HTML file in /tabs/, its logic in a static/js file.
window.TOKEN = document.querySelector('meta[name="cp-token"]').content;  // a meta tag: the page allows no inline scripts
window.T = {};
window.t = (key, vars = {}) => {
  let s = key.split(".").reduce((o, k) => o?.[k], T) ?? key;
  for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
};

window.api = async (path, opts = {}) => {
  const headers = { "x-token": TOKEN, ...(opts.json !== undefined ? { "content-type": "application/json" } : {}) };
  const r = await fetch(path, { method: opts.method || (opts.json !== undefined || opts.body ? "POST" : "GET"), headers,
                                body: opts.json !== undefined ? JSON.stringify(opts.json) : opts.body, signal: opts.signal });
  // the key was refused (the app restarted): reload for the new one. Other 403s (a feature switched off) are errors.
  if (r.status === 403 && (await r.clone().json().catch(() => ({}))).error === "forbidden") { location.reload(); return new Promise(() => {}); }
  const isJson = (r.headers.get("content-type") || "").includes("json");
  const data = isJson ? await r.json() : await r.blob();
  if (!r.ok) throw new Error((isJson && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data.error))) || r.statusText);
  return data;
};

// "2026-10-18" or a full timestamp -> the reader's own date format
window.fmtDate = d => {
  if (!d) return "";
  const x = new Date(d);
  return isNaN(x) ? d : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", ...(d.length === 10 && { timeZone: "UTC" }) }).format(x);
};

window.download = (blob, name) => {
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: name });
  a.click();
  URL.revokeObjectURL(a.href);
};

const MAIN_TABS = ["board", "needs", "applications", "practice", "profile", "designs", "settings", "help"];
const NAV = [{ id: "find", tabs: ["board", "needs", "applications", "practice"] }, { id: "you", tabs: ["profile", "designs"] },
             { id: "end", tabs: ["settings", "help"] }];
window.ICONS = { board: "layout-list", needs: "bell", applications: "briefcase", practice: "message-square", profile: "user",
                 designs: "palette", settings: "settings", help: "life-buoy", setup: "sparkles" };
const parts = {};  // x-include cache: file -> Promise<html>
window.feature = name => !!Alpine.store("app").state?.features?.[name];  // features this computer has switched on

document.addEventListener("alpine:init", () => {
  Alpine.store("app", {
    tab: "", sub: "", dirty: false, n: {}, state: null, draft: null, setupStep: 0, setupPhase: "", applyKey: "", practiceApp: "",
    setupMode() { return this.state && !this.state.preferences.setup_done; },
    tabs() { return this.setupMode() ? ["setup", "help"] : MAIN_TABS; },
    groups() { return this.setupMode() ? [{ id: "setup", tabs: ["setup"] }, { id: "end", tabs: ["help"] }] : NAV; },
    on(k) { return this.tab === k || (k === "board" && this.tab === "apply"); },  // Apply belongs to the Board
    async refresh() { this.state = await api("/api/state"); },
    async counts() { try { this.n = await api("/api/counts"); } catch {} },
    async show(tab, sub = "") {
      if (this.dirty && !confirm(t("unsaved"))) return;
      this.dirty = false;
      this.tab = tab;
      this.sub = sub;
      history.replaceState(null, "", "#" + tab + (sub ? "/" + sub : ""));
      document.getElementById("menu")?.close();
      this.counts();
      const view = document.getElementById("view");
      const r = await fetch(`/tabs/${tab}.html`, { cache: "no-cache" });  // after an update: the new page, never a stale copy
      view.innerHTML = r.ok ? await r.text() : `<div class="card muted">${t("soon")}</div>`;
    },
  });
  Alpine.store("toast", {
    msg: "", kind: "", timer: 0, undo: null,
    show(msg, kind = "ok", undo = null) {  // undo: a function, offered as an Undo button while the toast shows
      Object.assign(this, { msg, kind, undo });
      clearTimeout(this.timer); this.timer = setTimeout(() => (this.msg = ""), undo ? 6000 : 4000);
    },
  });
  // x-include="sites.html": loads /tabs/parts/sites.html into the element (cached); Alpine initialises it on insert
  Alpine.directive("include", (el, { expression }) => {
    (parts[expression] ||= fetch(`/tabs/parts/${expression}`, { cache: "no-cache" }).then(r => r.text())).then(html => { el.innerHTML = html; });
  });
  // A "- rule" line added to CV or letter instructions: above a "## Banned phrases" list, never into it (that list is enforced).
  window.addRuleLine = (text, line) => {
    const i = text.indexOf("## Banned phrases"), add = "- " + line;
    if (i < 0) return (text.trim() ? text.trimEnd() + "\n" : "") + add;
    return (text.slice(0, i).trim() ? text.slice(0, i).trimEnd() + "\n" : "") + add + "\n\n" + text.slice(i);
  };
  // Ask CareerPilot (parts/ask.html, on Settings and Profile): one sentence -> proposed changes in up to five areas,
  // each with a checkbox; Apply saves the checked ones, then the open page reloads with the new values.
  // Inside <div data-writing="cvField,letterField">: the writing box. It only touches the CV and letter instructions and the
  // writing settings, builds on the editors' unsaved text, and puts its result into the editors (saved with their Save).
  Alpine.data("askBox", () => ({
    q: "", asking: false, applying: false, answer: null, err: "", use: {}, writing: null,
    init() { const w = this.$el.closest("[data-writing]")?.dataset.writing; this.writing = w ? w.split(",") : null; },
    async ask() {
      if (!this.q.trim() || this.asking) return;
      this.asking = true; this.err = ""; this.answer = null;
      try {
        const [cv, letter] = this.writing || [];
        this.answer = await api("/api/settings/ask", { json: { text: this.q, ...(this.writing
          ? { areas: "writing", current: { cv_rules: this[cv], style: this[letter] } } : {}) } });
        this.use = Object.fromEntries(this.parts().map(p => [p.id, true]));
      } catch (e) { this.err = e.message; }
      this.asking = false;
    },
    label(key) { const s = t("settings.keys." + key); return s === "settings.keys." + key ? key : s; },
    fmt(v) {
      if (v === "" || v == null || (Array.isArray(v) && !v.length)) return t("settings.ask.empty");
      if (typeof v === "boolean") return v ? t("settings.ask.on") : t("settings.ask.off");
      if (Array.isArray(v)) return v.map(x => typeof x === "object" ? x.code || JSON.stringify(x) : x).join(", ");
      if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k}: ${Array.isArray(x) ? x.join(", ") : x}`).join(" · ");
      return String(v);
    },
    lines(c) {  // a text's change as its added and removed lines
      const a = new Set(c.old.split("\n").map(s => s.trim())), b = new Set(c.new.split("\n").map(s => s.trim()));
      return [...[...a].filter(l => l && !b.has(l)).map(l => ({ old: l })), ...[...b].filter(l => l && !a.has(l)).map(l => ({ new: l }))];
    },
    parts() {
      const r = this.answer;
      if (!r) return [];
      return [
        r.changes?.length && { id: "settings", lines: r.changes.map(c => ({ label: this.label(c.key), old: this.fmt(c.old), new: this.fmt(c.new) })) },
        r.cv_rules && { id: "cv_rules", lines: this.lines(r.cv_rules) },
        r.letter_rules && { id: "letter_rules", lines: this.lines(r.letter_rules) },
        r.answers?.length && { id: "answers", lines: r.answers.map(a => ({ label: a.question, old: a.old, new: a.new })) },
        r.cv && { id: "cv", lines: r.cv.changes.map(c => ({ new: c })) },
      ].filter(Boolean);
    },
    async apply() {
      const r = this.answer, on = id => this.use[id] && this.parts().some(p => p.id === id);
      this.applying = true; this.err = "";
      if (this.writing) {  // into the editors: the person sees the new text and saves it with the page
        try {
          if (on("settings")) await api("/api/preferences", { method: "PATCH", json: Object.fromEntries(r.changes.map(c => [c.key, c.new])) });
          const [cv, letter] = this.writing;
          if (on("cv_rules")) this[cv] = r.cv_rules.new;
          if (on("letter_rules")) this[letter] = r.letter_rules.new;
          this.answer = null; this.q = "";
          Alpine.store("toast").show(t("ask.writing_applied"));
        } catch (e) { this.err = e.message; }
        this.applying = false;
        return;
      }
      try {
        if (on("settings")) {
          await api("/api/preferences", { method: "PATCH", json: Object.fromEntries(r.changes.map(c => [c.key, c.new])) });
          const theme = r.changes.find(c => c.key === "theme");
          if (theme) applyTheme(theme.new);
        }
        if (on("cv_rules")) await api("/api/data/cv_rules", { method: "PUT", json: { text: r.cv_rules.new } });
        if (on("letter_rules")) await api("/api/data/style", { method: "PUT", json: { text: r.letter_rules.new } });
        if (on("answers")) await api("/api/answers", { method: "PATCH", json: Object.fromEntries(r.answers.map(a => [a.question, a.new])) });
        if (on("cv")) await api("/api/data/profile", { method: "PUT", json: r.cv.profile });
        this.answer = null; this.q = "";
        Alpine.store("toast").show(t("settings.ask.applied"));
        const app = Alpine.store("app");
        await app.refresh();
        app.dirty = false;
        app.show(app.tab, app.sub);  // the open page shows the new values
      } catch (e) { this.err = e.message; }
      this.applying = false;
    },
  }));

  Alpine.data("help", () => ({
    h: null, results: {}, note: "", err: "",
    async init() { this.err = ""; try { this.h = await api("/api/health"); } catch (e) { this.err = e.message; } },
    async test(name) {
      this.results[name] = t("health.testing");
      try { const r = await api(`/api/ai/test/${name}`, { method: "POST" });
            this.results[name] = r.ok ? "✓ " + t("health.works") : "✗ " + t("health.failed") + ": " + r.error; }
      catch (e) { this.results[name] = "✗ " + t("health.failed") + ": " + e.message; }
    },
    signIn(name) { api(`/api/ai/sign-in/${name}`, { method: "POST" }); },
    openFolder() { api("/api/open-folder", { method: "POST" }); },
    async backup() { download(await api("/api/backup"), "careerpilot-backup.zip"); },
    async update() {
      this.note = t("working");
      try { this.note = (await api("/api/update", { method: "POST" })).message; } catch (e) { this.note = e.message; }
    },
    async exportJson() {
      const data = await api("/api/export/json-resume");
      download(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), "resume.json");
    },
    async report() {
      const r = await api("/api/problem-report");
      await navigator.clipboard.writeText(r.text);
      this.note = t("health.report_copied");
    },
    async quit() { await api("/api/quit", { method: "POST" }); document.body.innerHTML = `<main><div class="card">${t("quit_done")}</div></main>`; },
  }));
});

addEventListener("beforeunload", e => { if (window.Alpine && Alpine.store("app").dirty) e.preventDefault(); });

// Texts first, then Alpine: labels are rendered once, so they must exist when Alpine starts. Alpine also waits for
// DOMContentLoaded: every page script must have registered its alpine:init (stores like "saved") before Alpine runs.
const strings = fetch("/i18n/en.json", { cache: "no-cache" }).then(r => r.json());
document.addEventListener("DOMContentLoaded", async () => {
  T = await strings;
  document.head.append(Object.assign(document.createElement("script"), { src: "/static/vendor/alpine.min.js" }));
});

document.addEventListener("alpine:initialized", async () => {
  const app = Alpine.store("app");
  await app.refresh();
  applyTheme(app.state.preferences.theme);
  const [want, sub] = location.hash.slice(1).split("/");
  app.show(app.tabs().includes(want) ? want : app.tabs()[0], app.tabs().includes(want) ? sub : "");
  setInterval(() => app.counts(), 60000);
  const ping = () => api("/api/ping", { method: "POST" }).catch(() => {});
  ping();
  setInterval(ping, 30000);  // the app stops itself a few minutes after the last tab closes
});
