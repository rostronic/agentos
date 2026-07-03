// Tests for Inbox quick win #4 (docs/ux/2026-07-01-dashboard-ux-walkthrough.md,
// "Quick wins" item 4 / confusion point #4):
//
//   "on each inbox item: link the title to the task, show age ('waiting 2d')"
//
// Today (pre-fix) the Inbox view renders the task id as inert text
// (`<span class="mono" ...>${shortId(i.task_id||'')}</span>`) with no click
// handler and no timestamp at all — confirmed via the walkthrough's
// accessibility-tree check ("the task id is displayed but not clickable").
// These tests are written FIRST and are expected to FAIL against the current
// dashboard/index.html; they should pass once the feature is implemented.
//
// There is no bundler/JS-test-runner for this single-file dashboard, so this
// harness loads the real <script> body into a Node `vm` context (mocking only
// `document`/`fetch`, i.e. the DOM/network boundary) and exercises the actual
// `views.inbox()` render function — not a reimplementation of it. Run with:
//
//   node --test dashboard/tests/inbox.test.mjs

import assert from 'node:assert/strict';
import { test } from 'node:test';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

const scriptOpen = html.indexOf('<script>');
const scriptClose = html.indexOf('</script>');
if (scriptOpen === -1 || scriptClose === -1) {
  throw new Error('Could not find <script>...</script> in dashboard/index.html');
}
const fullScript = html.slice(scriptOpen + '<script>'.length, scriptClose);

// Truncate before the bottom-of-file bootstrap (`render('home'); connectSSE();
// refreshInboxBadge(); refreshKillswitch(); setInterval(...)` and the nav
// click-binding loop). We only need the function/const *definitions*
// (views, render, get, escapeHtml, shortId, ...), not the live-app bootstrap.
const bootstrapMarker = "render('home');";
const bootstrapIdx = fullScript.lastIndexOf(bootstrapMarker);
if (bootstrapIdx === -1) {
  throw new Error('Expected bootstrap marker not found — dashboard/index.html structure changed');
}
const navBindMarker = "document.querySelectorAll('.nav a').forEach(a =>\n  a.addEventListener";
const navBindIdx = fullScript.lastIndexOf(navBindMarker, bootstrapIdx);
const script = fullScript.slice(0, navBindIdx === -1 ? bootstrapIdx : navBindIdx);

/** A minimal fake DOM element: just enough for the views' render() calls. */
function makeFakeElement() {
  return {
    innerHTML: '',
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {},
    querySelector() { return null; },
  };
}

/** Builds a fresh sandbox (fake window/document/fetch) and runs the
 * dashboard's view-layer definitions inside it. `routes` maps a URL path
 * (ignoring query string) to the JSON body `get()`/`fetch()` should resolve. */
function loadDashboard(routes) {
  const elements = new Map();
  const getElement = (sel) => {
    if (!elements.has(sel)) elements.set(sel, makeFakeElement());
    return elements.get(sel);
  };

  const sandbox = {
    console,
    fetch: async (url) => {
      const pathname = String(url).split('?')[0];
      if (!(pathname in routes)) {
        throw new Error(`inbox.test.mjs: no mocked route for ${url}`);
      }
      const body = routes[pathname];
      return { json: async () => body };
    },
    document: {
      querySelector: (sel) => getElement(sel),
      querySelectorAll: () => [],
      addEventListener() {},
    },
    localStorage: {
      _data: {},
      getItem(k) { return Object.prototype.hasOwnProperty.call(this._data, k) ? this._data[k] : null; },
      setItem(k, v) { this._data[k] = String(v); },
      removeItem(k) { delete this._data[k]; },
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(script, sandbox, { filename: 'dashboard-inline.js' });

  return {
    /** Renders a view (as the app's own `render(view)` does) and returns the
     * HTML written into '#main'. */
    async render(view, arg) {
      await sandbox.render(view, arg);
      return getElement('#main').innerHTML;
    },
  };
}

/** ISO-8601 UTC timestamp `daysAgo` days (and optional extra minutes) before
 * now, in the same shape the backend's `_now()` produces
 * (`datetime.now(timezone.utc).isoformat()`), e.g.
 * "2026-06-28T20:15:03.123456+00:00". */
function isoAgo({ days = 0, minutes = 0 } = {}) {
  const ms = Date.now() - (days * 24 * 60 * 60 * 1000) - (minutes * 60 * 1000);
  return new Date(ms).toISOString().replace('Z', '+00:00');
}

function inboxItem(overrides = {}) {
  return {
    id: 'item-1',
    kind: 'question',
    from_agent: 'developer',
    task_id: 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4',
    run_id: 'run-1',
    prompt: 'Should I use approach A or B?',
    options: [],
    status: 'open',
    created_at: isoAgo({ days: 0, minutes: 5 }),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Clickable title -> task detail
// ---------------------------------------------------------------------------

test('inbox item links to its task detail (calls render("taskDetail", <task_id>))', async () => {
  const item = inboxItem({ task_id: 'deadbeefcafef00dbaadf00ddeadbeef' });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  // The app has no URL routing yet (a separate, larger fix); its established
  // in-app navigation idiom is `render('taskDetail', <full task id>)` wired
  // to an onclick handler (see tasksTable() / the board card renderer).
  // The inbox item must use the SAME idiom with the FULL task id, not the
  // truncated 8-char display id — a truncated id is ambiguous and would
  // resolve to the wrong task via local_store.get_inbox_item()'s prefix match.
  const navPattern = /render\(\s*['"]taskDetail['"]\s*,\s*['"]deadbeefcafef00dbaadf00ddeadbeef['"]\s*\)/;
  assert.match(
    out, navPattern,
    `expected an onclick-style navigation call to the full task id, got: ${out}`
  );
});

test('inbox item navigation element is actually clickable (onclick attribute), not just text containing the id', async () => {
  const item = inboxItem({ task_id: 'cafebabe11112222333344445555' });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  // Guard against a false-positive implementation that merely prints the
  // task id as a string somewhere without wiring a click handler.
  const clickableWithId = /onclick=(['"])[^'"]*cafebabe11112222333344445555[^'"]*\1/;
  assert.match(
    out, clickableWithId,
    `expected the task id to sit inside a clickable (onclick=...) element, got: ${out}`
  );
});

test('each inbox item links to its OWN task, not the first/last task in the list', async () => {
  const items = [
    inboxItem({ id: 'item-1', task_id: 'task0000000000000000000000000001' }),
    inboxItem({ id: 'item-2', task_id: 'task0000000000000000000000000002' }),
    inboxItem({ id: 'item-3', task_id: 'task0000000000000000000000000003' }),
  ];
  const dash = loadDashboard({ '/api/inbox': items });
  const out = await dash.render('inbox');

  for (const id of ['task0000000000000000000000000001', 'task0000000000000000000000000002', 'task0000000000000000000000000003']) {
    const navPattern = new RegExp(`render\\(\\s*['"]taskDetail['"]\\s*,\\s*['"]${id}['"]\\s*\\)`);
    assert.match(out, navPattern, `expected a task-detail link for ${id}, got: ${out}`);
  }
});

test('an inbox item with no task_id does not render a broken/empty navigation call', async () => {
  const item = inboxItem({ task_id: null });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  assert.doesNotMatch(
    out, /render\(\s*['"]taskDetail['"]\s*,\s*['"]\s*['"]\s*\)/,
    `expected no render('taskDetail','') call when task_id is missing, got: ${out}`
  );
});

// ---------------------------------------------------------------------------
// Item age ("waiting Nd")
// ---------------------------------------------------------------------------

test('inbox item shows its age as "waiting 3d" for an item created 3 days ago', async () => {
  const item = inboxItem({ created_at: isoAgo({ days: 3 }) });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  assert.match(out, /waiting 3d\b/, `expected "waiting 3d" in the rendered item, got: ${out}`);
});

test('inbox item age uses singular-safe day formatting for a 1-day-old item ("waiting 1d")', async () => {
  const item = inboxItem({ created_at: isoAgo({ days: 1 }) });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  assert.match(out, /waiting 1d\b/, `expected "waiting 1d", got: ${out}`);
});

test('inbox item age scales correctly for a 10-day-old item ("waiting 10d"), not truncated to a single digit', async () => {
  const item = inboxItem({ created_at: isoAgo({ days: 10 }) });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  assert.match(out, /waiting 10d\b/, `expected "waiting 10d", got: ${out}`);
});

test('a sub-day-old inbox item (30 minutes) does not misreport its age as "waiting 0d"', async () => {
  const item = inboxItem({ created_at: isoAgo({ minutes: 30 }) });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  assert.doesNotMatch(
    out, /waiting 0d\b/,
    `a 30-minute-old item must not read as "waiting 0d" — needs sub-day granularity, got: ${out}`
  );
  // Some human-readable sub-day unit (minutes/hours) or an explicit
  // "just now"/"<1d" marker is expected instead of silently rounding to 0d.
  assert.match(
    out, /waiting (\d+m|\d+h|<\s*1d|just now)\b/i,
    `expected a sub-day age readout (minutes/hours/"<1d"/"just now"), got: ${out}`
  );
});

test('inbox item age is a rendered string, not the raw ISO created_at timestamp', async () => {
  const createdAt = isoAgo({ days: 3 });
  const item = inboxItem({ created_at: createdAt });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  // Regression guard: the fix must replace/format the timestamp, not just
  // print `created_at` verbatim next to the word "waiting".
  assert.doesNotMatch(
    out, new RegExp(createdAt.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
    `expected the raw ISO timestamp NOT to appear verbatim, got: ${out}`
  );
});

test('an inbox item with a missing created_at does not crash render or show "NaN"/"Invalid Date"', async () => {
  const item = inboxItem({ created_at: null });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  assert.doesNotMatch(out, /NaN|Invalid Date/, `got a broken age readout: ${out}`);
});

// ---------------------------------------------------------------------------
// Existing behavior must not regress
// ---------------------------------------------------------------------------

test('inbox still shows the empty state when there are no items', async () => {
  const dash = loadDashboard({ '/api/inbox': [] });
  const out = await dash.render('inbox');

  assert.match(out, /Inbox empty/i, `expected the empty state to still render, got: ${out}`);
});

test('inbox item still renders its prompt text, options, and Answer input alongside the new link/age', async () => {
  const item = inboxItem({
    prompt: 'Pick a deploy target',
    options: ['staging', 'prod'],
  });
  const dash = loadDashboard({ '/api/inbox': [item] });
  const out = await dash.render('inbox');

  assert.match(out, /Pick a deploy target/, `expected the prompt text to still render, got: ${out}`);
  assert.match(out, /staging/, `expected option buttons to still render, got: ${out}`);
  assert.match(out, /answerInboxField\((['"])item-1\1\)/, `expected the free-text Answer button to still render, got: ${out}`);
});
