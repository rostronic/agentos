// Tests for "[UX] Hash routing: give every screen a URL" (docs/ux/2026-07-01-
// dashboard-ux-walkthrough.md, confusion point #1, fix note):
//
//   "hash routing (#/tasks/04cb4446, #/board?project=x) - no server changes
//    needed, restores refresh/back/bookmark, and lets briefings + inbox items
//    emit real links."
//
// Today (pre-fix) the app has NO routing at all: `render(view, arg)` only
// ever swaps `#main`'s innerHTML - it never reads or writes `location.hash`,
// there is no `hashchange`/`popstate` listener, and the bottom of the script
// unconditionally boots with `render('home')` regardless of the URL. Every
// screen "lives at /" (confirmed via grep: no `location`, no `hashchange` in
// dashboard/index.html). These tests are written FIRST and are expected to
// FAIL against the current dashboard/index.html; they should pass once hash
// routing is implemented.
//
// Assumed contract (pinned here since none exists yet - an implementation
// must match this to make these tests the acceptance criteria they're meant
// to be):
//   render('home')                -> location.hash === '#/dashboard'
//   render('projects')            -> location.hash === '#/projects'
//   render('board')               -> location.hash === '#/board'
//   render('board', <projectId>)  -> location.hash === '#/board?project=<projectId>'
//   render('runs')                -> location.hash === '#/runs'
//   render('tokens')              -> location.hash === '#/tokens'
//   render('inbox')               -> location.hash === '#/inbox'
//   render('taskDetail', <id>)    -> location.hash === '#/tasks/<id>'
//   render('runDetail', <id>)     -> location.hash === '#/runs/<id>'
// ...and the reverse: on load (a hard refresh, or opening a bookmarked/
// shared URL), the app must read `location.hash` and render the matching
// view instead of always defaulting to Dashboard; and it must react to
// `hashchange`/`popstate` (how the Back/Forward buttons notify a page) by
// re-rendering the view the URL now points to. An unrecognized hash falls
// back to the Dashboard view rather than crashing.
//
// There is no bundler/JS-test-runner for this single-file dashboard, so (as
// in dashboard/tests/inbox.test.mjs) this harness loads the real <script>
// body into a Node `vm` context and exercises the actual `render()`/`views.*`
// functions - not a reimplementation of them. Unlike inbox.test.mjs, this
// file needs the bootstrap code at the bottom of the script (that's exactly
// what "refresh keeps place" and "bookmarkable" are properties of), so it
// runs the FULL script and mocks the browser APIs that bootstrap touches
// (`fetch`, `location`, `history`, `addEventListener`, `setInterval`) instead
// of truncating them away. Run with:
//
//   node --test dashboard/tests/hash-routing.test.mjs

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
const script = html.slice(scriptOpen + '<script>'.length, scriptClose);

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

// Fixtures for every endpoint any of the 6 hash-routed views (or their
// bootstrap-time siblings, refreshInboxBadge/refreshKillswitch) may call, so
// that whichever view a given hash boots into, the fetch mock never needs a
// per-test route just to avoid an unrelated "no mocked route" crash.
const DEFAULT_ROUTES = {
  '/api/stats': { total_runs: 3, by_status: { running: 1, done: 2, failed: 0 } },
  '/api/runs': [],
  '/api/projects': [],
  '/api/work-stats': { per_project: {} },
  '/api/tasks': [],
  '/api/tokens': {},
  '/api/inbox': [],
  '/api/killswitch': { paused: false },
};

/** Builds a fresh sandbox (fake window/document/fetch/location/history) and
 * runs the dashboard's FULL script - including its page-load bootstrap -
 * inside it, with `location.hash` preset to `initialHash` (simulating
 * opening the app at that URL, e.g. a hard refresh or a bookmarked link). */
async function loadDashboard({ routes = {}, initialHash = '' } = {}) {
  const allRoutes = { ...DEFAULT_ROUTES, ...routes };
  const elements = new Map();
  const getElement = (sel) => {
    if (!elements.has(sel)) elements.set(sel, makeFakeElement());
    return elements.get(sel);
  };

  const calledUrls = [];
  const listeners = { hashchange: [], popstate: [] };
  const locationObj = { hash: initialHash };

  const sandbox = {
    console,
    setInterval: () => 0,
    clearInterval: () => {},
    fetch: async (url) => {
      calledUrls.push(String(url));
      const pathname = String(url).split('?')[0];
      if (!(pathname in allRoutes)) {
        throw new Error(`hash-routing.test.mjs: no mocked route for ${url}`);
      }
      const body = allRoutes[pathname];
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
    location: locationObj,
    history: {
      pushState(state, title, url) { applyUrlToLocation(url); },
      replaceState(state, title, url) { applyUrlToLocation(url); },
    },
    addEventListener(type, handler) {
      if (!listeners[type]) listeners[type] = [];
      listeners[type].push(handler);
    },
    removeEventListener() {},
  };
  function applyUrlToLocation(url) {
    if (url == null) return;
    const s = String(url);
    const hi = s.indexOf('#');
    locationObj.hash = hi === -1 ? '' : s.slice(hi);
  }
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(script, sandbox, { filename: 'dashboard-inline.js' });

  // The bootstrap's initial render() call (however the fix triggers it) is
  // fired-and-not-awaited at the bottom of the script, exactly as a real
  // <script> tag can't top-level-await either. Flush the microtask queue
  // (a macrotask boundary drains ALL pending microtasks first) so its
  // chain of `await get(...)` calls has settled before we inspect #main.
  await new Promise((resolve) => setTimeout(resolve, 0));

  if (typeof sandbox.render !== 'function') {
    throw new Error('hash-routing.test.mjs: expected window.render to be defined after loading the script');
  }
  const renderCalls = [];
  const originalRender = sandbox.render;
  // Top-level `function render(){}` in a vm *script* (non-module) context
  // aliases the sandbox global object, so reassigning `sandbox.render` here
  // also redirects the bare `render(...)` identifier the script's own
  // hashchange/click handlers close over - this spy sees ALL render calls
  // from this point on, including ones triggered internally by the router.
  sandbox.render = async (...args) => {
    renderCalls.push(args);
    return originalRender(...args);
  };

  return {
    async render(view, arg) {
      await sandbox.render(view, arg);
      return getElement('#main').innerHTML;
    },
    main() { return getElement('#main').innerHTML; },
    getHash() { return locationObj.hash; },
    get calledUrls() { return calledUrls; },
    get renderCalls() { return renderCalls; },
    hashListenerCount() { return listeners.hashchange.length + listeners.popstate.length; },
    /** Simulate the browser having already navigated (Back/Forward button,
     * or a typed/bookmarked URL) to `newHash`, then notify the app the way
     * a real browser would - via hashchange (and popstate, in case the
     * implementation listens for that instead/as well). */
    async navigateTo(newHash) {
      locationObj.hash = newHash;
      for (const fn of listeners.hashchange) await fn({ newURL: newHash, oldURL: undefined });
      for (const fn of listeners.popstate) await fn({});
    },
  };
}

// ---------------------------------------------------------------------------
// 1. Hash-based routes exist for Dashboard/Projects/Board/Runs/Tokens/Inbox
//    (navigating via render() must produce a real, distinct URL for each)
// ---------------------------------------------------------------------------

const NAV_VIEWS = [
  { view: 'home', hash: '#/dashboard', label: 'Dashboard' },
  { view: 'projects', hash: '#/projects', label: 'Projects' },
  { view: 'board', hash: '#/board', label: 'Board' },
  { view: 'runs', hash: '#/runs', label: 'Runs' },
  { view: 'tokens', hash: '#/tokens', label: 'Tokens' },
  { view: 'inbox', hash: '#/inbox', label: 'Inbox' },
];

for (const { view, hash, label } of NAV_VIEWS) {
  test(`render('${view}') (${label}) sets location.hash to '${hash}'`, async () => {
    const dash = await loadDashboard();
    await dash.render(view);
    assert.equal(
      dash.getHash(), hash,
      `expected navigating to ${label} to produce the URL ${hash}, got location.hash = ${JSON.stringify(dash.getHash())}`
    );
  });
}

test('each of the 6 top-level views gets its OWN distinct hash (no two views collapse to the same URL)', async () => {
  const dash = await loadDashboard();
  const seen = new Map();
  for (const { view } of NAV_VIEWS) {
    await dash.render(view);
    const hash = dash.getHash();
    assert.ok(
      !seen.has(hash),
      `views '${seen.get(hash)}' and '${view}' both produced the hash ${hash} - each screen needs its own URL`
    );
    seen.set(hash, view);
  }
});

// ---------------------------------------------------------------------------
// 2. Per-task / per-run deep links, and board's project filter round-trips
//    into the URL as a query param (per the walkthrough's own example)
// ---------------------------------------------------------------------------

test(`render('taskDetail', id) sets location.hash to '#/tasks/<id>'`, async () => {
  const routes = { '/api/tasks/04cb4446': { id: '04cb4446', title: 'Fix the gate message', status: 'blocked', priority: 'high' } };
  const dash = await loadDashboard({ routes });
  await dash.render('taskDetail', '04cb4446');
  assert.equal(dash.getHash(), '#/tasks/04cb4446');
});

test(`render('runDetail', id) sets location.hash to '#/runs/<id>'`, async () => {
  const routes = { '/api/runs/run-42': { id: 'run-42', agent: 'developer', status: 'done', model: 'claude', cost_usd: 0, started_at: '2026-07-01T00:00:00+00:00', events: [] } };
  const dash = await loadDashboard({ routes });
  await dash.render('runDetail', 'run-42');
  assert.equal(dash.getHash(), '#/runs/run-42');
});

test(`render('board', projectId) encodes the project filter into the hash as a query param, per the doc's own "#/board?project=x" example`, async () => {
  const dash = await loadDashboard();
  await dash.render('board', 'proj-9');
  assert.equal(
    dash.getHash(), '#/board?project=proj-9',
    `expected the project filter to round-trip into the URL, got: ${dash.getHash()}`
  );
});

// ---------------------------------------------------------------------------
// 3. Refresh keeps place: booting (loading the script) with a given hash
//    already in the URL must render THAT view, not default to Dashboard
// ---------------------------------------------------------------------------

const BOOT_CASES = [
  { hash: '#/dashboard', headingRe: /<h1>Mission Control<\/h1>/, label: 'Dashboard' },
  { hash: '#/projects', headingRe: /<h1>Projects<\/h1>/, label: 'Projects' },
  { hash: '#/board', headingRe: /<h1>Board<\/h1>/, label: 'Board' },
  { hash: '#/runs', headingRe: /<h1>Runs<\/h1>/, label: 'Runs' },
  { hash: '#/tokens', headingRe: /<h1>Token usage<\/h1>/, label: 'Tokens' },
  { hash: '#/inbox', headingRe: /<h1>Inbox/, label: 'Inbox' },
];

for (const { hash, headingRe, label } of BOOT_CASES) {
  test(`a fresh load (refresh) with location.hash = '${hash}' boots straight into ${label}, not Dashboard`, async () => {
    const dash = await loadDashboard({ initialHash: hash });
    assert.match(
      dash.main(), headingRe,
      `expected booting at ${hash} to render ${label}, got #main: ${dash.main()}`
    );
  });
}

test('a fresh load with location.hash = "#/tasks/<id>" boots straight into that task detail (refresh keeps place on a deep link)', async () => {
  const routes = { '/api/tasks/deadbeef': { id: 'deadbeef', title: 'Wire up the auth flow', status: 'in_progress', priority: 'medium', assignee: 'developer', created_by: 'human', depends_on: [] } };
  const dash = await loadDashboard({ initialHash: '#/tasks/deadbeef', routes });
  assert.match(dash.main(), /Wire up the auth flow/, `expected the task detail view for deadbeef, got: ${dash.main()}`);
});

test('a fresh load with location.hash = "#/runs/<id>" boots straight into that run detail (refresh keeps place on a deep link)', async () => {
  const routes = { '/api/runs/run-99': { id: 'run-99', agent: 'qa', status: 'failed', model: 'claude-sonnet', cost_usd: 0.12, started_at: '2026-07-01T04:22:33+00:00', events: [], error: 'developer gate failed' } };
  const dash = await loadDashboard({ initialHash: '#/runs/run-99', routes });
  assert.match(dash.main(), /run-99/, `expected the run detail view for run-99, got: ${dash.main()}`);
});

test('a fresh load with location.hash = "#/board?project=proj-9" applies that project filter (fetches tasks scoped to it)', async () => {
  const dash = await loadDashboard({ initialHash: '#/board?project=proj-9' });
  assert.ok(
    dash.calledUrls.some((u) => u.startsWith('/api/tasks') && u.includes('project_id=proj-9')),
    `expected the board's project filter to be applied from the URL on load, calledUrls: ${JSON.stringify(dash.calledUrls)}`
  );
});

test('a fresh load with no hash at all still defaults to Dashboard (existing default behavior preserved)', async () => {
  const dash = await loadDashboard({ initialHash: '' });
  assert.match(dash.main(), /<h1>Mission Control<\/h1>/, `expected the no-hash default to still be Dashboard, got: ${dash.main()}`);
});

// ---------------------------------------------------------------------------
// 4. Bookmarkable / shareable: a hash captured from one session, handed to a
//    brand-new session (a new tab, a teammate's browser, a briefing link),
//    reproduces the exact same screen
// ---------------------------------------------------------------------------

test('a task deep link is bookmarkable: the hash captured from render() reproduces the same task in a brand-new session', async () => {
  const routes = { '/api/tasks/task-777': { id: 'task-777', title: 'Deeper audit dimensions', status: 'ready', priority: 'high', assignee: 'developer', created_by: 'human', depends_on: [] } };
  const dash1 = await loadDashboard({ routes });
  await dash1.render('taskDetail', 'task-777');
  const hash = dash1.getHash();
  assert.equal(hash, '#/tasks/task-777');

  // Simulate a teammate opening a briefing/inbox link that points at this
  // URL - i.e. a totally fresh page load whose only input is the hash.
  const dash2 = await loadDashboard({ routes, initialHash: hash });
  assert.match(
    dash2.main(), /Deeper audit dimensions/,
    `expected the bookmarked/shared URL to boot straight into the same task, got: ${dash2.main()}`
  );
});

test('a run deep link is bookmarkable: the hash captured from render() reproduces the same run in a brand-new session', async () => {
  const routes = { '/api/runs/run-share-1': { id: 'run-share-1', agent: 'execute-sprint', status: 'running', model: 'claude-opus', cost_usd: 0, started_at: '2026-06-18T00:00:00+00:00', events: [] } };
  const dash1 = await loadDashboard({ routes });
  await dash1.render('runDetail', 'run-share-1');
  const hash = dash1.getHash();
  assert.equal(hash, '#/runs/run-share-1');

  const dash2 = await loadDashboard({ routes, initialHash: hash });
  assert.match(
    dash2.main(), /run-share-1/,
    `expected the bookmarked/shared URL to boot straight into the same run, got: ${dash2.main()}`
  );
});

// ---------------------------------------------------------------------------
// 5. Back button works: the app must listen for hashchange/popstate (how a
//    browser notifies a page that Back/Forward changed the URL) and
//    re-render to match, without needing render() called on it directly
// ---------------------------------------------------------------------------

test('the app registers a hashchange/popstate listener so it can react to Back/Forward', async () => {
  const dash = await loadDashboard();
  assert.ok(
    dash.hashListenerCount() > 0,
    'expected at least one hashchange or popstate listener to be registered at load time'
  );
});

test('pressing Back after Dashboard -> Board -> Inbox returns to Board, then Dashboard, via hashchange (not a render() call)', async () => {
  const dash = await loadDashboard({ initialHash: '#/dashboard' });
  await dash.render('board');
  assert.equal(dash.getHash(), '#/board');
  await dash.render('inbox');
  assert.equal(dash.getHash(), '#/inbox');

  // The browser Back button does NOT call render() - it changes
  // location.hash/history state and fires hashchange/popstate. The app must
  // react to that notification on its own.
  await dash.navigateTo('#/board');
  assert.match(dash.main(), /<h1>Board<\/h1>/, `expected Back to re-render Board, got: ${dash.main()}`);

  await dash.navigateTo('#/dashboard');
  assert.match(dash.main(), /<h1>Mission Control<\/h1>/, `expected Back again to reach Dashboard, got: ${dash.main()}`);
});

test('a hashchange to a task deep link renders that exact task (Back/Forward into a bookmarked task, not a stale view)', async () => {
  const routes = { '/api/tasks/task-9': { id: 'task-9', title: 'Fix the thing', status: 'ready', priority: 'low', depends_on: [] } };
  const dash = await loadDashboard({ initialHash: '#/dashboard', routes });
  await dash.navigateTo('#/tasks/task-9');
  assert.match(dash.main(), /Fix the thing/, `expected the hashchange handler to render task-9's detail, got: ${dash.main()}`);
});

// ---------------------------------------------------------------------------
// 6. Fallback / regression guards
// ---------------------------------------------------------------------------

test('booting with an unrecognized hash falls back to Dashboard instead of crashing or showing a blank screen', async () => {
  const dash = await loadDashboard({ initialHash: '#/not-a-real-route/xyz' });
  assert.doesNotMatch(dash.main(), /Could not reach the API/, `expected a graceful fallback, not the fetch-error screen, got: ${dash.main()}`);
  assert.match(dash.main(), /<h1>Mission Control<\/h1>/, `expected an unknown route to fall back to Dashboard, got: ${dash.main()}`);
});

test('navigating between views twice in a row leaves the hash matching the LATEST view, not a stale one', async () => {
  const dash = await loadDashboard();
  await dash.render('inbox');
  await dash.render('projects');
  assert.equal(dash.getHash(), '#/projects', `expected the hash to track the most recent render(), got: ${dash.getHash()}`);
});
