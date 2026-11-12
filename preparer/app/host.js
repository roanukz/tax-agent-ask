// The local review page's host side: an MCP Apps host (protocol 2026-01-26) for the preparer's review screen.
// preparer/local_review.py serves this file and relays the screen's tool calls to the preparer server;
// PRD-preparer-review.md section 6.5 says what it does and why. It never shows or logs the page token or a
// review token, keeps nothing in browser storage, and offers no text input.
"use strict";
(() => {
  const PROTOCOL = "2026-01-26";
  const MODES = ["inline", "fullscreen"];
  const TEARDOWN_MS = 3000;
  const MIN_HEIGHT = 120;
  const MAX_HEIGHT = 20000;
  const $ = id => document.getElementById(id);
  const ui = { progress: $("progress"), open: $("open"), close: $("close"), theme: $("theme"), note: $("note"), stage: $("stage"),
    where: $("where"), bar: document.querySelector(".bar") };

  let token = null;        // the page token, in this page's memory only
  let state = null;        // the last /api/state answer
  let frame = null;        // the one frame this page created
  let session = null;      // the open draft: {draftId, loads, inits, initialized, closing, sources, height}
  let mode = "inline";
  let themeChoice = "system";
  let stopped = false;     // this tab can't go on: the page stopped, or its link was refused or replaced
  let halted = "";         // what the bar says once it can't
  let claiming = false;
  let sentTheme = null;    // the theme the screen last heard
  let nextHostId = 1;
  const waiting = new Map(); // this page's own requests to the screen, by JSON-RPC id

  // The review server.

  class Refused extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(path, body) {
    const headers = { "content-type": "application/json" };
    if (token) headers["x-review-page-token"] = token;
    let r;
    try {
      r = await fetch(path, { method: "POST", headers, body: JSON.stringify(body || {}), cache: "no-store", credentials: "omit" });
    } catch (e) {
      halt("The page lost its connection to the review server. See the Terminal window that runs it.", "Stopped.");
      throw new Refused("no connection", 0);
    }
    let j = {};
    try { j = await r.json(); } catch (e) { j = {}; }
    const message = typeof j.error === "string" ? j.error : `The review server answered ${r.status}.`;
    const pageStopped = r.status === 503 || j.stopped === true;
    if (pageStopped || r.status === 401 || (path === "/api/claim" && !r.ok)) {
      halt(message, pageStopped ? "Stopped." : "This tab can't continue.");
      throw new Refused(message, r.status);
    }
    if (!r.ok) throw new Refused(message, r.status);
    return j;
  }

  function halt(message, label) {
    stopped = true;
    halted = label;
    token = null;
    if (frame) frame.remove();
    frame = null;
    session = null;
    waiting.clear();
    setMode("inline", false);
    say(message);
    render();
  }

  // What the page shows.

  function say(text) {
    ui.note.textContent = text || "";
    ui.note.hidden = !text;
  }

  function render() {
    if (stopped) {
      ui.progress.textContent = halted;
    } else if (!state) {
      ui.progress.textContent = claiming ? "Connecting..." : token ? "Could not load the drafts." : "Waiting for a link.";
    } else if (!state.total) {
      ui.progress.textContent = "No drafts to review.";
    } else {
      ui.progress.textContent = `Approved ${state.approved} of ${state.total}` + (state.next ? `. Next: ${state.next}.` : ". All approved.");
    }
    if (state) ui.where.textContent = state.practice ? "practice" : "local page";
    const next = state && state.next;
    const retry = !state && !!token && !claiming;  // the claim worked but the drafts didn't load
    ui.open.textContent = next ? `Open ${next}` : retry ? "Retry" : "Open";
    ui.open.disabled = stopped || !!session || !(next || retry);
    ui.close.disabled = stopped || !session || session.closing;
    ui.theme.textContent = `Theme: ${themeChoice}`;
    ui.theme.setAttribute("aria-label", `Theme: ${themeChoice}`);
  }

  async function refresh() {
    state = await api("/api/state");
    render();
  }

  // Theme and display mode.

  function theme() {
    if (themeChoice !== "system") return themeChoice;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme() {
    if (themeChoice === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", themeChoice);
  }

  function setMode(m, tell = true) {
    const changed = m !== mode;
    mode = m;
    document.body.dataset.mode = m;
    if (frame) frame.style.height = m === "inline" ? `${session ? session.height : 420}px` : "";
    if (changed && tell) notify("ui/notifications/host-context-changed", { displayMode: m });
  }

  // The screen hears the theme only when the one it shows would change.
  function tellTheme() {
    const t = theme();
    if (session && session.initialized && t !== sentTheme) {
      sentTheme = t;
      notify("ui/notifications/host-context-changed", { theme: t });
    }
  }

  function hostContext() {
    sentTheme = theme();
    const ctx = { theme: sentTheme, displayMode: mode, availableDisplayModes: MODES.slice(), platform: "web" };
    try { ctx.locale = navigator.language; ctx.timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { /* optional */ }
    return ctx;
  }

  function hostCapabilities() {
    const sandbox = { permissions: { ...state.sandbox.permissions } };
    if (state.sandbox.csp && Object.keys(state.sandbox.csp).length) sandbox.csp = { ...state.sandbox.csp };
    return { serverTools: {}, openLinks: {}, sandbox };
  }

  // Messages to the screen.

  function post(message) {
    if (frame && frame.contentWindow) frame.contentWindow.postMessage(message, "*");
  }
  function reply(id, result) { post({ jsonrpc: "2.0", id, result }); }
  function fail(id, code, message) { post({ jsonrpc: "2.0", id, error: { code, message } }); }
  function notify(method, params) { if (session && session.initialized) post({ jsonrpc: "2.0", method, params }); }

  function ask(method, params, ms) {
    const id = `host-${nextHostId++}`;
    return new Promise(resolve => {
      const timer = setTimeout(() => { waiting.delete(id); resolve(null); }, ms);
      waiting.set(id, m => { clearTimeout(timer); resolve(m); });
      post({ jsonrpc: "2.0", id, method, params });
    });
  }

  // Messages from the screen: only from the frame this page created, which has an opaque origin.

  window.addEventListener("message", e => {
    if (!frame || e.source !== frame.contentWindow || e.origin !== "null" || !session) return;
    const m = e.data;
    if (!m || typeof m !== "object" || m.jsonrpc !== "2.0") return;
    if (typeof m.method === "string") {
      if (session.closing) {
        if ("id" in m) fail(m.id, -32600, "The page is closing this draft.");
        return;
      }
      if ("id" in m) onRequest(m, session);
      else onNotification(m, session);
    } else if ("id" in m && waiting.has(m.id)) {
      const done = waiting.get(m.id);
      waiting.delete(m.id);
      done(m);
    }
  });

  function onRequest(m, s) {
    const p = m.params && typeof m.params === "object" && !Array.isArray(m.params) ? m.params : {};
    if (m.method === "ui/initialize") {
      s.inits += 1;
      if (s.inits > 1) {
        fail(m.id, -32600, "This screen already started; the host closed the draft.");
        closeDraft("The screen started again, so the page closed the draft. Use Open to open it again.", false);
        return;
      }
      reply(m.id, { protocolVersion: PROTOCOL, hostInfo: { ...state.host }, hostCapabilities: hostCapabilities(), hostContext: hostContext() });
      return;
    }
    if (m.method === "ping") { reply(m.id, {}); return; }
    if (!s.initialized) { fail(m.id, -32600, "The screen has not finished starting."); return; }
    switch (m.method) {
      case "tools/call": relay(m.id, p, s); return;
      case "ui/request-display-mode": {
        if (MODES.includes(p.mode)) setMode(p.mode);
        reply(m.id, { mode });
        return;
      }
      case "ui/open-link": openLink(m.id, p, s); return;
      default: fail(m.id, -32601, `This page does not offer ${String(m.method).slice(0, 80)}.`);
    }
  }

  function onNotification(m, s) {
    const p = m.params && typeof m.params === "object" ? m.params : {};
    switch (m.method) {
      case "ui/notifications/initialized":
        if (!s.initialized && s.inits === 1) { s.initialized = true; deliver(s); }
        return;
      case "ui/notifications/size-changed":
        if (typeof p.height === "number" && Number.isFinite(p.height)) {
          s.height = Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.ceil(p.height)));
          if (mode === "inline" && frame) frame.style.height = `${s.height}px`;
        }
        return;
      case "ui/notifications/request-teardown":
        closeDraft("The screen asked to close, so the page closed the draft.", true);
        return;
      default:
        return;
    }
  }

  // After initialized: the tool's input, then open_review on the server, then its whole result.
  async function deliver(s) {
    notify("ui/notifications/tool-input", { arguments: { draft_id: s.draftId } });
    let out;
    try { out = await api("/api/open", { draft_id: s.draftId }); }
    catch (e) {
      if (session !== s) return;
      notify("ui/notifications/tool-cancelled", { reason: "The page could not open the draft." });
      say(`${e.message} Use Close draft, then Open.`);
      return;
    }
    if (session !== s) return;
    s.sources = sourcesIn(out.result && out.result.structuredContent);
    notify("ui/notifications/tool-result", out.result);
  }

  function sourcesIn(value, found = new Set()) {
    if (Array.isArray(value)) value.forEach(v => sourcesIn(v, found));
    else if (value && typeof value === "object") {
      for (const [k, v] of Object.entries(value)) {
        if (k === "source_url" && typeof v === "string" && /^https?:\/\//i.test(v)) found.add(v);
        else sourcesIn(v, found);
      }
    }
    return found;
  }

  async function relay(id, p, s) {
    const args = p.arguments === undefined ? {} : p.arguments;
    if (typeof p.name !== "string" || !args || typeof args !== "object" || Array.isArray(args)) {
      fail(id, -32602, "Invalid tool call.");
      return;
    }
    let out;
    try { out = await api("/api/call", { name: p.name, arguments: args }); }
    catch (e) { if (session === s) fail(id, e.status === 409 ? -32602 : -32603, e.message); return; }
    if (session === s) reply(id, out.result);
    refresh().catch(() => {});  // a save or an approval changes the count in the bar
  }

  function openLink(id, p, s) {
    const url = typeof p.url === "string" ? p.url : "";
    const clicked = !!(navigator.userActivation && navigator.userActivation.isActive);
    if (!/^https?:\/\//i.test(url) || !s.sources.has(url) || !clicked) { reply(id, { isError: true }); return; }
    const w = window.open("", "_blank");
    if (!w) { reply(id, { isError: true }); return; }
    try { w.opener = null; } catch (e) { /* the new tab is already apart */ }
    w.location.href = url;
    reply(id, {});
  }

  // Opening and closing a draft.

  let opening = false;

  // Open asks the server first, so a stopped page or a changed order shows before any frame exists.
  async function openNext() {
    if (!state && token && !opening && !stopped) {
      opening = true;
      try { await refresh(); say(""); } catch (e) { if (!stopped) say(e.message); }
      opening = false;
      render();
      return;
    }
    if (opening || stopped || session || !state || !state.next) return;
    opening = true;
    ui.open.disabled = true;
    try { await refresh(); } catch (e) { opening = false; if (!stopped) say(e.message); render(); return; }
    opening = false;
    if (stopped || session || !state.next) { render(); return; }
    say("");
    const s = { draftId: state.next, loads: 0, inits: 0, initialized: false, closing: false, sources: new Set(), height: 420 };
    const f = document.createElement("iframe");
    f.setAttribute("sandbox", "allow-scripts");
    f.setAttribute("allow", "clipboard-write");
    f.setAttribute("referrerpolicy", "no-referrer");
    f.title = `Review screen for ${s.draftId}`;
    f.addEventListener("load", () => {
      if (session !== s) return;
      s.loads += 1;
      if (s.loads > 1) closeDraft("The screen reloaded, so the page closed the draft. Use Open to open it again.", false);
    });
    session = s;
    frame = f;
    setMode("inline", false);
    f.src = "/view";
    ui.stage.replaceChildren(f);
    render();
  }

  async function closeDraft(note, teardown = true) {
    const s = session;
    if (!s || s.closing) return;
    s.closing = true;
    render();
    if (teardown && s.initialized) await ask("ui/resource-teardown", {}, TEARDOWN_MS);
    if (session === s) {
      if (frame) frame.remove();
      frame = null;
      session = null;
      waiting.clear();
      setMode("inline", false);
    }
    say(note || "");
    render();
    try { await api("/api/close"); await refresh(); } catch (e) { if (!stopped) say(e.message); render(); }
  }

  // Wiring.

  ui.open.addEventListener("click", openNext);
  ui.close.addEventListener("click", () => closeDraft("", true));
  ui.theme.addEventListener("click", () => {
    themeChoice = { system: "light", light: "dark", dark: "system" }[themeChoice];
    applyTheme();
    render();
    tellTheme();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", tellTheme);
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--bar-h", `${Math.ceil(ui.bar.getBoundingClientRect().height)}px`);
  }).observe(ui.bar);

  async function start() {
    const code = new URLSearchParams(location.hash.slice(1)).get("c");
    history.replaceState(null, "", location.pathname);
    render();
    if (!code) {
      say("This page needs a fresh link. Press Enter in the Terminal window that runs the review page.");
      return;
    }
    claiming = true;
    render();
    try {
      token = (await api("/api/claim", { code })).token;
      claiming = false;
      await refresh();
    } catch (e) {
      claiming = false;
      if (!stopped) say(e.message);
      render();
    }
  }

  start();
})();
