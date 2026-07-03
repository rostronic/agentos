// Tests for the Mission Control dashboard's inline JS (dashboard/index.html).
//
// There is no bundler/JS-test-runner for this single-file dashboard, so this
// harness loads the real <script> body into a Node `vm` context (mocking only
// `document`/`fetch`, i.e. the DOM/network boundary) and exercises the actual
// `views.*` render functions — not a reimplementation of them. Run with:
//
//   node --test dashboard/tests/dashboard.test.mjs
//
// Covers UX quick wins #10 (docs/ux/2026-07-01-dashboard-ux-walkthrough.md):
// pluralization ("1 tasks") and the Pipelines empty state ("$AGENTOS_CRON_DIR").

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
// (views, render, get, escapeHtml, pill, ...), not the live-app bootstrap —
// running the bootstrap would race our own explicit render() calls below
// (both write to the same mocked '#main' element) and pull in EventSource/
// setInterval for no benefit to these tests.
// NB: "document.querySelectorAll('.nav a').forEach" also appears earlier,
// *inside* the render() function body — indexOf would find that one first and
// truncate mid-function. The bottom-of-file top-level `render('home');` call
// is unique and marks the start of the bootstrap block.
const bootstrapMarker = "render('home');";
const bootstrapIdx = fullScript.lastIndexOf(bootstrapMarker);
if (bootstrapIdx === -1) {
  throw new Error('Expected bootstrap marker not found — dashboard/index.html structure changed');
}
// Also drop the preceding nav click-binding + connectSSE() definition, which
// sit between the last real definition and this marker but aren't needed here.
const navBindMarker = "document.querySelectorAll('.nav a').forEach(a =>\n  a.addEventListener";
const navBindIdx = fullScript.lastIndexOf(navBindMarker, bootstrapIdx);
const script = fullScript.slice(0, navBindIdx === -1 ? bootstrapIdx : navBindIdx);

/** A minimal fake DOM element: just enough for the views' render() calls
 * (innerHTML assignment, classList.toggle, dataset). */
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
        throw new Error(`dashboard.test.mjs: no mocked route for ${url}`);
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

test('projects view pluralizes the task-count tag: "1 task", not "1 tasks"', async () => {
  const dash = loadDashboard({
    '/api/projects': [{ id: 'p1', name: 'Solo Project', slug: 'solo' }],
    '/api/work-stats': { per_project: { p1: 1 } },
  });
  const out = await dash.render('projects');

  assert.doesNotMatch(out, /\b1 tasks\b/, `expected singular "1 task", got: ${out}`);
  assert.match(out, /\b1 task\b(?!s)/, `expected the tag to read "1 task" somewhere, got: ${out}`);
});

test('projects view keeps plural counts plural: "0 tasks" and "3 tasks"', async () => {
  const dash = loadDashboard({
    '/api/projects': [
      { id: 'p0', name: 'Empty Project', slug: 'empty' },
      { id: 'p3', name: 'Busy Project', slug: 'busy' },
    ],
    '/api/work-stats': { per_project: { p3: 3 } }, // p0 deliberately absent -> defaults to 0
  });
  const out = await dash.render('projects');

  assert.match(out, /\b0 tasks\b/, `expected "0 tasks" to stay plural, got: ${out}`);
  assert.match(out, /\b3 tasks\b/, `expected "3 tasks" to stay plural, got: ${out}`);
});

test('pipelines empty state reads as a human sentence, not a raw $AGENTOS_CRON_DIR path', async () => {
  const dash = loadDashboard({
    '/api/pipelines': { jobs: [], summary: {} },
  });
  const out = await dash.render('pipelines');

  // The walkthrough's exact complaint: "Expected $AGENTOS_CRON_DIR/jobs.json"
  // leaking an internal env-var name into the empty-state row.
  assert.doesNotMatch(
    out, /\$AGENTOS_CRON_DIR/,
    `Pipelines empty state must not expose the raw $AGENTOS_CRON_DIR env var, got: ${out}`
  );
  // Must still communicate *something* actionable in its place (a human
  // sentence + setup hint, per the acceptance criteria) rather than going
  // silent.
  assert.match(
    out, /no (scheduled )?(cron )?jobs/i,
    `expected a human "no jobs" sentence in the empty state, got: ${out}`
  );
});
