// Readout Globe page script. One ES module, no build step. MapLibre GL JS is vendored and loaded
// before this module by a deferred classic script, so it is read from window.maplibregl.
// Untrusted text from the API is only ever set with textContent, never parsed as HTML.

const maplibregl = window.maplibregl;

const params = new URLSearchParams(window.location.search);
const MOCK = params.get('mock') === '1';
// Test hooks for the fallback basemap: nobasemap=1 points the style at an invalid host (the load
// fails), nobasemap=timeout keeps the real style but gives it 1 ms (the load timer fires first).
const BASEMAP_TEST = params.get('nobasemap');
const MOCK_ASK_ERROR = MOCK ? params.get('askerror') : null;
// Mock answers: a places answer (geo_keys) by default, askmock=trials for a trials answer (nct_ids),
// askmock=none for an answer with neither.
const MOCK_ASK_MODE = MOCK ? params.get('askmock') : null;
const MOCK_ASK_FILE = MOCK_ASK_MODE === 'trials' || MOCK_ASK_MODE === 'none' ? 'ask-trials.json' : 'ask.json';

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';
const TEST_STYLE_URLS = { 1: 'https://basemap.invalid/styles/positron' };
const LAND_URL = new URL('vendor/natural-earth/ne_110m_land.json', document.baseURI).href;
const BASEMAP_TIMEOUT_MS = BASEMAP_TEST === 'timeout' ? 1 : 5000;
const TILE_TIMEOUT_MS = 9000;

const COLORS = { site: '#2a78d6', due: '#eb6834', answer: '#1baf7a', ring: '#ffffff', selected: '#1d1d1f' };
const WATER = '#d3dce3';
const LAND = '#f2f3f0';
const SKY = {
  'sky-color': '#eaf1f8',
  'horizon-color': '#ffffff',
  'fog-color': '#ffffff',
  'sky-horizon-blend': 0.6,
  'horizon-fog-blend': 0.6,
  'fog-ground-blend': 0.9,
  // A soft halo for depth. Kept low so it never tints the dots near the edge.
  'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.4, 4, 0.3, 6, 0],
};

const PHASE_ORDER = ['EARLY_PHASE1', 'PHASE1', 'PHASE1_PHASE2', 'PHASE2', 'PHASE2_PHASE3', 'PHASE3', 'PHASE4', 'NA', 'UNKNOWN'];
const GEO_KEY_RE = /^-?\d{1,2}\.\d{4},-?\d{1,3}\.\d{4}$/;
const NCT_RE = /^NCT\d{8}$/;
const MAX_NCT = 500;
const MAX_POINTS = 20000;
const PLAY_STEP_MS = 400;
const SPIN_DEG_PER_SEC = 2.6;
const ASK_CLIENT_TIMEOUT_MS = 90000;
const ROWS_PREVIEW = 8;
const EMPTY_FC = { type: 'FeatureCollection', features: [] };

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const phoneQuery = window.matchMedia('(max-width: 640px)');

const state = {
  meta: null,
  months: [],
  monthIndex: 0,
  allTime: true,
  phases: new Set(),
  statuses: new Set(),
  org: null,
  q: '',
  due: false,
  points: EMPTY_FC,
  answer: null,
  answerMode: null,
  answerNct: [],
  answerPoints: EMPTY_FC,
  card: null,
  playing: false,
};

// helpers

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const nf = new Intl.NumberFormat('en-US');
const nf2 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const fmt = (n) => nf.format(Number.isFinite(Number(n)) ? Number(n) : 0);
const plural = (n, one, many = `${one}s`) => (Number(n) === 1 ? one : many);
const MONTH_FMT = new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' });
const DATE_FMT = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });

function parseDate(value) {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(typeof value === 'string' ? value : '');
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, m[3] ? Number(m[3]) : 1));
  return Number.isNaN(d.getTime()) ? null : d;
}
const fmtDate = (value) => {
  const d = parseDate(value);
  return d ? DATE_FMT.format(d) : null;
};
const fmtMonth = (ym) => {
  const d = parseDate(ym);
  return d ? MONTH_FMT.format(d) : String(ym);
};

function compact(n) {
  const v = Number(n) || 0;
  if (v >= 10_000_000) return `${Math.round(v / 1_000_000)}M`;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  return fmt(v);
}

function monthList(min, max) {
  const a = /^(\d{4})-(\d{2})$/.exec(min || '');
  const b = /^(\d{4})-(\d{2})$/.exec(max || '');
  if (!a || !b) return [];
  const out = [];
  let y = Number(a[1]);
  let m = Number(a[2]);
  const endY = Number(b[1]);
  const endM = Number(b[2]);
  while ((y < endY || (y === endY && m <= endM)) && out.length < 1200) {
    out.push(`${y}-${String(m).padStart(2, '0')}`);
    m += 1;
    if (m > 12) { m = 1; y += 1; }
  }
  return out;
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
    }, { once: true });
  });
}

const isAbort = (err) => err?.name === 'AbortError';

function placeName(p) {
  return [p.city, p.state].filter(Boolean).join(', ') || p.country
    || (typeof p.geo_key === 'string' ? p.geo_key.replace(',', ', ') : 'Unknown place');
}

function coordsFromGeoKey(key) {
  const [lat, lon] = String(key).split(',').map(Number);
  return Number.isFinite(lat) && Number.isFinite(lon) ? [lon, lat] : null;
}

// A geo_key is "lat,lon" with four decimals, so a place can be drawn without another request.
function validGeoKey(key) {
  if (typeof key !== 'string' || !GEO_KEY_RE.test(key)) return false;
  const [lat, lon] = key.split(',').map(Number);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
}

// Answer columns are matched ignoring case, spaces and underscores.
const normColumn = (name) => String(name).toLowerCase().replace(/[\s_]/g, '');
const isGeoKeyColumn = (name) => normColumn(name) === 'geokey';

function trialUrl(nct, given) {
  if (NCT_RE.test(nct)) return `https://clinicaltrials.gov/study/${nct}`;
  if (typeof given === 'string' && given.startsWith('https://clinicaltrials.gov/')) return given;
  return null;
}

function statusLabel(code) {
  const hit = state.meta?.statuses?.find((s) => s.value === code);
  if (hit?.label) return hit.label;
  if (!code) return 'Unknown status';
  const words = String(code).toLowerCase().replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function phaseLabel(code, given) {
  if (given) return given;
  const hit = state.meta?.phases?.find((p) => p.value === code);
  return hit?.label || code || 'Unknown phase';
}

// API

class ApiError extends Error {
  constructor(status, kind, message, body = null) {
    super(message || kind);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
    this.body = body;
  }
}

function geoPath(key) {
  return GEO_KEY_RE.test(key) ? key : encodeURIComponent(key);
}

const realApi = {
  async request(path, { query, signal, method = 'GET', body } = {}) {
    const url = new URL(path, window.location.origin);
    if (query) url.search = query.toString();
    const init = { method, signal, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, init);
    } catch (err) {
      if (isAbort(err) || signal?.aborted) throw err.name === 'AbortError' ? err : signal.reason;
      throw new ApiError(0, 'network', 'Could not reach the server.');
    }
    let data = null;
    try {
      data = await res.json();
    } catch (err) {
      if (signal?.aborted) throw signal.reason ?? err;
    }
    if (!res.ok) {
      throw new ApiError(res.status, data?.kind || `http_${res.status}`, data?.error || res.statusText, data);
    }
    if (data === null || typeof data !== 'object') {
      throw new ApiError(res.status, 'bad_response', 'The server sent an unexpected response.');
    }
    return data;
  },
  meta(signal) { return this.request('/api/meta', { signal }); },
  points(query, signal) { return this.request('/api/points', { query, signal }); },
  pointTrials(key, query, signal) { return this.request(`/api/points/${geoPath(key)}/trials`, { query, signal }); },
  trial(nct, signal) { return this.request(`/api/trial/${encodeURIComponent(nct)}`, { signal }); },
  orgs(q, signal) { return this.request('/api/orgs', { query: new URLSearchParams({ q }), signal }); },
  ask(question, signal) { return this.request('/api/ask', { method: 'POST', body: { question }, signal }); },
};

// Mock mode: the same calls answered from static sample files in mock/. Filtering is approximate.
const mockCache = new Map();

function mockFile(name) {
  if (!mockCache.has(name)) {
    const promise = fetch(new URL(`mock/${name}`, document.baseURI)).then((res) => {
      if (!res.ok) throw new ApiError(res.status, `http_${res.status}`, `Sample file ${name} is missing.`);
      return res.json();
    });
    mockCache.set(name, promise);
    promise.catch(() => mockCache.delete(name));
  }
  return mockCache.get(name).then((data) => structuredClone(data));
}

function hash01(text) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967296;
}

const splitList = (value) => (value ? value.split(',').map((s) => s.trim()).filter(Boolean) : []);

const ACTIVITY = [[0, 0.42], [36, 0.55], [60, 0.5], [82, 0.36], [96, 0.2], [120, 0.05], [131, 0.02]];
function activityAt(index) {
  if (index <= ACTIVITY[0][0]) return ACTIVITY[0][1];
  for (let i = 1; i < ACTIVITY.length; i += 1) {
    const [x1, y1] = ACTIVITY[i];
    const [x0, y0] = ACTIVITY[i - 1];
    if (index <= x1) return y0 + ((y1 - y0) * (index - x0)) / (x1 - x0);
  }
  return ACTIVITY[ACTIVITY.length - 1][1];
}

const GROWING = new Set(['China', 'Korea, Republic of', 'Spain', 'Poland', 'Australia']);
const SHRINKING = new Set(['Russian Federation', 'Ukraine']);

function mockFilterPoints(all, meta, query) {
  const phases = splitList(query.get('phase'));
  const statuses = splitList(query.get('status'));
  const orgId = query.get('org_id');
  const q = (query.get('q') || '').toLowerCase();
  const activeOn = query.get('active_on');
  const dueWithin = Number(query.get('due_within')) || 0;
  const nct = splitList(query.get('nct'));
  const total = meta.trials || 1;
  const share = (list, field) => (list.length
    ? list.reduce((acc, v) => acc + (meta[field].find((x) => x.value === v)?.count || 0), 0) / total
    : 1);
  const phaseShare = share(phases, 'phases');
  const statusShare = share(statuses, 'statuses');
  const monthIdx = activeOn ? (Number(activeOn.slice(0, 4)) - 2020) * 12 + Number(activeOn.slice(5, 7)) - 1 : null;
  const allTrials = all.features.reduce((acc, f) => acc + f.properties.trials, 0) || 1;

  const features = [];
  for (const feature of all.features) {
    const p = feature.properties;
    const jitter = (salt) => 0.55 + 0.9 * hash01(p.geo_key + salt);
    let trials;
    let due;
    if (nct.length) {
      if (p.country !== 'Japan') continue;
      trials = Math.min(nct.length, 1 + Math.floor(hash01(p.geo_key + nct.join()) * 4));
      due = trials;
    } else {
      let factor = phaseShare * statusShare;
      if (phases.length) factor *= jitter(`p${phases.join()}`);
      if (statuses.length) factor *= jitter(`s${statuses.join()}`);
      if (orgId) {
        if (hash01(`${p.geo_key}o${orgId}`) < 0.45) continue;
        factor *= 0.06 * jitter(`o${orgId}`);
      }
      if (q) {
        if (hash01(`${p.geo_key}q${q}`) < 0.5) continue;
        factor *= 0.05 * jitter(`q${q}`);
      }
      if (monthIdx !== null) {
        let region = 1;
        if (GROWING.has(p.country)) region = 0.6 + 0.6 * Math.min(1, monthIdx / 82);
        if (SHRINKING.has(p.country)) region = monthIdx < 26 ? 1.25 : 0.45;
        factor *= activityAt(monthIdx) * region * (0.85 + 0.3 * hash01(p.geo_key + Math.floor(monthIdx / 12)));
      }
      trials = Math.round(p.trials * factor);
      due = Math.min(trials, Math.round(p.due * factor * (monthIdx !== null ? 1.8 : 1)));
      if (dueWithin) {
        trials = due;
      }
    }
    if (trials < 1) continue;
    features.push({ ...feature, properties: { ...p, trials, due } });
  }
  features.sort((a, b) => b.properties.trials - a.properties.trials);
  const sum = features.reduce((acc, f) => acc + f.properties.trials, 0);
  const distinct = nct.length
    ? Math.min(nct.length, sum)
    : Math.min(sum, Math.round((meta.trials_with_geo * sum) / allTrials));
  return {
    type: 'FeatureCollection',
    summary: { trials: distinct, sites: Math.round(sum * 1.18), points: features.length },
    features: features.slice(0, MAX_POINTS),
  };
}

const MOCK_ASK_ERRORS = {
  bad_question: [400, 'Questions must be 1 to 300 characters.'],
  unsafe_sql: [422, 'The generated SQL failed the safety check.'],
  busy: [429, 'Another question is running.'],
  llm_unavailable: [503, 'The language model is not available.'],
  timeout: [504, 'The query timed out after 20 seconds.'],
  query_failed: [500, 'Binder Error: column "site_city" not found.'],
};

const mockApi = {
  async meta(signal) {
    await sleep(60, signal);
    return mockFile('meta.json');
  },
  async points(query, signal) {
    await sleep(140, signal);
    const [all, meta] = await Promise.all([mockFile('points.json'), mockFile('meta.json')]);
    const hasFilters = [...query.keys()].length > 0;
    return hasFilters ? mockFilterPoints(all, meta, query) : all;
  },
  async pointTrials(key, query, signal) {
    await sleep(180, signal);
    const [sample, all, meta, answer] = await Promise.all([
      mockFile('point-trials.json'), mockFile('points.json'), mockFile('meta.json'), mockFile('ask-trials.json'),
    ]);
    const base = all.features.find((f) => f.properties.geo_key === key);
    if (!base) throw new ApiError(404, 'not_found', 'Unknown location.');
    const filtered = [...query.keys()].length ? mockFilterPoints(all, meta, query) : all;
    const point = filtered.features.find((f) => f.properties.geo_key === key);
    const total = point ? point.properties.trials : 0;
    const nct = splitList(query.get('nct'));
    let trials;
    if (nct.length) {
      trials = answer.rows.filter((r) => nct.includes(r[0])).slice(0, total).map((r) => ({
        nct_id: r[0], title: r[1], phase: r[3], phase_label: phaseLabel(r[3]), status: 'RECRUITING',
        lead_org_name: r[2], conditions: null, start_date: '2024-06-01', primary_completion_date: r[4],
        due: true, facilities: ['Example University Hospital'], url: `https://clinicaltrials.gov/study/${r[0]}`,
      }));
    } else {
      const phases = splitList(query.get('phase'));
      const statuses = splitList(query.get('status'));
      const q = (query.get('q') || '').toLowerCase();
      const activeOn = query.get('active_on');
      const start = activeOn ? `${activeOn}-01` : null;
      const end = activeOn ? `${activeOn}-31` : null;
      trials = sample.trials.filter((t) => (!phases.length || phases.includes(t.phase))
        && (!statuses.length || statuses.includes(t.status))
        && (!query.get('due_within') || t.due)
        && (!q || `${t.title} ${t.conditions}`.toLowerCase().includes(q))
        && (!activeOn || (t.start_date <= end && (!t.primary_completion_date || t.primary_completion_date >= start))));
      trials = trials.slice(0, Math.min(total, 100));
    }
    const p = base.properties;
    return { geo_key: key, city: p.city, state: p.state, country: p.country, total, trials };
  },
  async trial(nct, signal) {
    await sleep(140, signal);
    if (!NCT_RE.test(nct)) throw new ApiError(400, 'bad_filter', 'nct_id must look like NCT01234567.');
    const [detail, sample, answer] = await Promise.all([mockFile('trial.json'), mockFile('point-trials.json'), mockFile('ask-trials.json')]);
    const fromSample = sample.trials.find((t) => t.nct_id === nct);
    const fromAnswer = answer.rows.find((r) => r[0] === nct);
    const out = { ...detail, nct_id: nct, url: `https://clinicaltrials.gov/study/${nct}` };
    const drug = (title) => (/\b[A-Z]{3}-[A-Z0-9]+\b/.exec(title) || [null])[0];
    if (fromSample) {
      Object.assign(out, {
        title: fromSample.title, official_title: `${fromSample.title} (sample record)`, phase: fromSample.phase,
        phase_label: fromSample.phase_label, status: fromSample.status, lead_org_name: fromSample.lead_org_name,
        conditions: fromSample.conditions, interventions: drug(fromSample.title), start_date: fromSample.start_date,
        primary_completion_date: fromSample.primary_completion_date, due: fromSample.due,
        is_active: ['RECRUITING', 'NOT_YET_RECRUITING', 'ACTIVE_NOT_RECRUITING', 'ENROLLING_BY_INVITATION'].includes(fromSample.status),
        brief_summary: `Sample data. A made-up study: ${fromSample.title}.`,
      });
    } else if (fromAnswer) {
      Object.assign(out, {
        title: fromAnswer[1], official_title: `${fromAnswer[1]} (sample record)`, lead_org_name: fromAnswer[2],
        phase: fromAnswer[3], phase_label: phaseLabel(fromAnswer[3]), primary_completion_date: fromAnswer[4], due: true,
        interventions: drug(fromAnswer[1]), conditions: null, brief_summary: `Sample data. A made-up study: ${fromAnswer[1]}`,
      });
    }
    return out;
  },
  async orgs(q, signal) {
    await sleep(90, signal);
    const data = await mockFile('orgs.json');
    const needle = q.toLowerCase();
    return { orgs: data.orgs.filter((o) => o.org_name.toLowerCase().includes(needle)).slice(0, 10) };
  },
  async ask(question, signal) {
    await sleep(1800, signal);
    if (MOCK_ASK_ERROR && MOCK_ASK_ERRORS[MOCK_ASK_ERROR]) {
      const [status, message] = MOCK_ASK_ERRORS[MOCK_ASK_ERROR];
      const body = { error: message, kind: MOCK_ASK_ERROR };
      if (MOCK_ASK_ERROR === 'unsafe_sql') body.sql = "SELECT * FROM read_csv('/etc/passwd')";
      throw new ApiError(status, MOCK_ASK_ERROR, message, body);
    }
    const data = await mockFile(MOCK_ASK_FILE);
    if (MOCK_ASK_MODE === 'none') return { ...data, question, nct_ids: [], geo_keys: [] };
    return { ...data, question };
  },
};

const api = MOCK ? mockApi : realApi;

// map

let map = null;
let mapReady = false;
let basemap = 'remote';
let remoteStyleLoaded = false;
let basemapTileLoaded = false;
let basemapTileErrors = 0;
let styleTimer = 0;
let tileTimer = 0;
let hovered = null;
let selected = null;

const trialsNum = ['max', 1, ['to-number', ['get', 'trials'], 1]];
const sizeExpr = ['sqrt', trialsNum];

function radiusExpr() {
  return ['interpolate', ['linear'], ['zoom'],
    0, ['+', 1.4, ['*', 0.2, sizeExpr]],
    3, ['+', 2.2, ['*', 0.34, sizeExpr]],
    6, ['+', 3.2, ['*', 0.56, sizeExpr]],
    10, ['+', 4.2, ['*', 0.8, sizeExpr]]];
}

// Highlighted trial locations are sized by their trial count; highlighted places carry a 1 to 100 "size".
const answerNum = ['max', 1, ['to-number', ['coalesce', ['get', 'size'], ['get', 'trials']], 1]];
const answerSizeExpr = ['sqrt', answerNum];

function answerRadiusExpr(extra = 0) {
  return ['interpolate', ['linear'], ['zoom'],
    0, ['+', 4 + extra, ['*', 0.5, answerSizeExpr]],
    4, ['+', 5.5 + extra, ['*', 0.7, answerSizeExpr]],
    8, ['+', 7 + extra, ['*', 1, answerSizeExpr]]];
}

const isOn = (name) => ['boolean', ['feature-state', name], false];

function pointColorExpr() {
  return state.due ? ['case', ['>', ['to-number', ['get', 'due'], 0], 0], COLORS.due, COLORS.site] : COLORS.site;
}

function fallbackStyle() {
  return {
    version: 8,
    name: 'Readout offline basemap',
    projection: { type: 'globe' },
    sources: { land: { type: 'geojson', data: LAND_URL } },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': WATER } },
      { id: 'land', type: 'fill', source: 'land', paint: { 'fill-color': LAND, 'fill-antialias': true } },
      { id: 'coastline', type: 'line', source: 'land', paint: { 'line-color': '#b6c1cb', 'line-width': 0.6 } },
    ],
  };
}

function initialZoom() {
  const pad = cameraPadding();
  const w = window.innerWidth - pad.left - pad.right;
  const h = window.innerHeight - pad.top - pad.bottom;
  const radius = Math.max(80, Math.min(w, h)) * 0.46;
  return Math.min(2.6, Math.max(0.1, Math.log2(radius / 81.5)));
}

function showMapMessage(text) {
  const box = $('map-fallback');
  box.textContent = text;
  box.hidden = false;
}

function initMap() {
  if (!maplibregl) {
    showMapMessage('The 3D globe could not load. The summary, filters and questions still work.');
    return;
  }
  const styleUrl = TEST_STYLE_URLS[BASEMAP_TEST] || STYLE_URL;
  try {
    map = new maplibregl.Map({
      container: 'map',
      style: styleUrl,
      center: [-38, 22],
      zoom: initialZoom(),
      attributionControl: false,
      dragRotate: false,
      pitchWithRotate: false,
      touchPitch: false,
      fadeDuration: 150,
    });
  } catch {
    map = null;
    showMapMessage('This browser could not start the 3D globe (WebGL is off). The summary, filters and questions still work.');
    return;
  }
  map.keyboard.disableRotation();
  map.touchZoomRotate.disableRotation();
  map.addControl(new maplibregl.NavigationControl({ showCompass: false, visualizePitch: false }), 'bottom-right');
  placeNavControl();
  map.getCanvas().setAttribute('aria-label', 'Globe of trial site locations. Arrow keys pan, plus and minus zoom.');
  map.jumpTo({ padding: cameraPadding() });

  map.on('style.load', onStyleLoad);
  map.on('error', onMapError);
  map.on('data', (e) => {
    if (e.dataType === 'source' && e.tile && e.sourceId !== 'points' && e.sourceId !== 'answer') basemapTileLoaded = true;
  });
  styleTimer = window.setTimeout(() => {
    if (!remoteStyleLoaded) useFallbackBasemap('timeout');
  }, BASEMAP_TIMEOUT_MS);

  wireMapPointer();
  startSpin();
}

// One-line English names keep the light basemap quiet under the data.
const LABEL_EXPR = ['coalesce', ['get', 'name:en'], ['get', 'name_en'], ['get', 'name:latin'], ['get', 'name']];

function tuneBasemap() {
  if (map.getLayer('water')) map.setPaintProperty('water', 'fill-color', WATER);
  for (const layer of map.getStyle().layers) {
    if (layer.type !== 'symbol') continue;
    const field = JSON.stringify(layer.layout?.['text-field'] ?? '');
    if (field.includes('name')) map.setLayoutProperty(layer.id, 'text-field', LABEL_EXPR);
  }
}

function onStyleLoad() {
  if (basemap === 'remote') {
    remoteStyleLoaded = true;
    window.clearTimeout(styleTimer);
    try {
      tuneBasemap();
    } catch (err) {
      console.warn('Basemap tuning skipped', err);
    }
    tileTimer = window.setTimeout(() => {
      if (basemap === 'remote' && !basemapTileLoaded) useFallbackBasemap('no tiles');
    }, TILE_TIMEOUT_MS);
  }
  map.setProjection({ type: 'globe' });
  try {
    map.setSky(SKY);
  } catch (err) {
    console.warn('Sky not applied', err);
  }
  installLayers();
}

function onMapError(e) {
  if (basemap !== 'remote') {
    if (!e?.sourceId) console.warn('Map error', e?.error?.message || e);
    return;
  }
  const sourceId = e?.sourceId;
  if (!remoteStyleLoaded && !sourceId) {
    useFallbackBasemap('style failed');
    return;
  }
  if (sourceId && sourceId !== 'points' && sourceId !== 'answer') {
    basemapTileErrors += 1;
    if (!basemapTileLoaded && basemapTileErrors >= 3) useFallbackBasemap('tiles failed');
  }
}

function useFallbackBasemap(reason) {
  if (!map || basemap === 'fallback') return;
  basemap = 'fallback';
  window.clearTimeout(styleTimer);
  window.clearTimeout(tileTimer);
  mapReady = false;
  hovered = null;
  map.setStyle(fallbackStyle(), { diff: false });
  $('footer-basemap').hidden = true;
  $('footer-fallback').hidden = false;
  console.info(`OpenFreeMap basemap unavailable (${reason}); showing the offline Natural Earth basemap.`);
}

function installLayers() {
  if (!map.getSource('points')) map.addSource('points', { type: 'geojson', data: state.points, promoteId: 'geo_key' });
  if (!map.getSource('answer')) map.addSource('answer', { type: 'geojson', data: state.answerPoints, promoteId: 'geo_key' });
  // Data sits under the basemap labels so place names stay readable.
  const beforeId = map.getStyle().layers.find((l) => l.type === 'symbol')?.id;
  if (!map.getLayer('points')) {
    map.addLayer({
      id: 'points',
      type: 'circle',
      source: 'points',
      layout: { 'circle-sort-key': ['-', 0, trialsNum] },
      paint: {
        'circle-radius': radiusExpr(),
        'circle-color': pointColorExpr(),
        'circle-opacity': 0.82,
        'circle-stroke-color': ['case', isOn('selected'), COLORS.selected, COLORS.ring],
        'circle-stroke-width': ['case', isOn('selected'), 2.4, isOn('hover'), 2, 0.8],
        'circle-stroke-opacity': 0.95,
      },
    }, beforeId);
  }
  if (!map.getLayer('answer-halo')) {
    map.addLayer({
      id: 'answer-halo',
      type: 'circle',
      source: 'answer',
      paint: {
        'circle-radius': answerRadiusExpr(7),
        'circle-color': COLORS.answer,
        'circle-opacity': 0.18,
        'circle-blur': 0.5,
      },
    }, beforeId);
  }
  if (!map.getLayer('answer')) {
    map.addLayer({
      id: 'answer',
      type: 'circle',
      source: 'answer',
      layout: { 'circle-sort-key': ['-', 0, answerNum] },
      paint: {
        'circle-radius': answerRadiusExpr(),
        'circle-color': COLORS.answer,
        'circle-opacity': 0.95,
        'circle-stroke-color': ['case', isOn('selected'), COLORS.selected, COLORS.ring],
        'circle-stroke-width': ['case', isOn('selected'), 2.4, isOn('hover'), 2.2, 1.4],
      },
    }, beforeId);
  }
  mapReady = true;
  applyPointStyle();
  if (selected) map.setFeatureState(selected, { selected: true });
}

function applyPointStyle() {
  const answerOn = state.answerPoints.features.length > 0;
  $('legend-due').hidden = !state.due;
  $('legend-answer').hidden = !answerOn;
  if (!mapReady) return;
  map.setPaintProperty('points', 'circle-color', pointColorExpr());
  map.setPaintProperty('points', 'circle-opacity', answerOn ? 0.3 : 0.82);
  map.setPaintProperty('points', 'circle-stroke-opacity', answerOn ? 0.45 : 0.95);
}

function setSourceData(id, data) {
  if (!mapReady) return;
  map.getSource(id)?.setData(data);
}

function setSelected(id, source = 'points') {
  if (map && mapReady && selected) map.setFeatureState(selected, { selected: false });
  selected = id ? { source, id } : null;
  if (map && mapReady && selected) map.setFeatureState(selected, { selected: true });
}

function topFeatureAt(point) {
  const layers = ['answer', 'points'].filter((id) => map.getLayer(id));
  if (!layers.length) return null;
  const box = [[point.x - 4, point.y - 4], [point.x + 4, point.y + 4]];
  return map.queryRenderedFeatures(box, { layers })[0] || null;
}

function clearHover() {
  if (hovered && mapReady) map.setFeatureState(hovered, { hover: false });
  hovered = null;
  if (map) map.getCanvas().style.cursor = '';
  $('tooltip').hidden = true;
  spinHoverPause = false;
}

function wireMapPointer() {
  map.on('mousemove', (e) => {
    if (!mapReady) return;
    const feature = topFeatureAt(e.point);
    if (!feature) {
      if (hovered) clearHover();
      return;
    }
    const id = feature.properties.geo_key;
    if (!hovered || hovered.id !== id || hovered.source !== feature.source) {
      if (hovered) map.setFeatureState(hovered, { hover: false });
      hovered = { source: feature.source, id };
      map.setFeatureState(hovered, { hover: true });
    }
    map.getCanvas().style.cursor = 'pointer';
    spinHoverPause = true;
    showTooltip(feature, e.originalEvent);
  });
  map.getCanvas().addEventListener('mouseleave', clearHover);
  map.on('movestart', (e) => {
    if (!e.spin) $('tooltip').hidden = true;
  });
  map.on('click', (e) => {
    if (!mapReady) return;
    const feature = topFeatureAt(e.point);
    if (!feature) return;
    const coords = feature.geometry?.coordinates;
    openLocation(feature.properties, {
      scope: feature.source === 'answer' ? answerScope() : 'filters',
      coords: Array.isArray(coords) ? [coords[0], coords[1]] : null,
      fly: true,
    });
  });
}

function showTooltip(feature, ev) {
  const p = feature.properties;
  const tip = $('tooltip');
  const inAnswer = feature.source === 'answer';
  let stats;
  if (p.kind === 'place') {
    // A place from a places answer: show the answer's own number for it when there is one.
    const metric = Number(p.metric);
    stats = el('div', { class: 'tt-stats' }, el('span', {}, el('span', { class: 'dot dot-answer' }),
      p.metric_label && Number.isFinite(metric) && p.metric !== null ? `${p.metric_label}: ${nf2.format(metric)}` : 'In the answer'));
  } else {
    stats = el('div', { class: 'tt-stats' },
      el('span', {}, el('span', { class: `dot ${inAnswer ? 'dot-answer' : 'dot-site'}` }),
        `${fmt(p.trials)} ${inAnswer ? 'in answer' : plural(p.trials, 'trial')}`),
      el('span', {}, el('span', { class: 'dot dot-due' }), `${fmt(p.due)} due`));
  }
  tip.replaceChildren(
    el('div', { class: 'tt-place', text: placeName(p) }),
    el('div', { class: 'tt-country', text: p.city || p.state ? (p.country || '') : '' }),
    stats,
  );
  tip.hidden = false;
  const pad = 14;
  const { innerWidth: vw, innerHeight: vh } = window;
  const rect = tip.getBoundingClientRect();
  let x = ev.clientX + pad;
  let y = ev.clientY + pad;
  if (x + rect.width > vw - 8) x = ev.clientX - rect.width - pad;
  if (y + rect.height > vh - 8) y = ev.clientY - rect.height - pad;
  tip.style.transform = `translate(${Math.max(8, Math.round(x))}px, ${Math.max(8, Math.round(y))}px)`;
}

// Camera padding keeps the globe centered in the space the panels leave free.
function cameraPadding() {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const pad = { top: 20, right: 20, bottom: 20, left: 20 };
  const rect = (id) => {
    const node = $(id);
    return node && !node.hidden ? node.getBoundingClientRect() : null;
  };
  // While an answer or error shows under the question box, keep the camera below the whole panel.
  const askOpen = !$('ask-result').hidden || !$('ask-error').hidden;
  const askBox = rect(askOpen ? 'ask' : 'ask-form');
  if (askBox) pad.top = Math.round(askBox.bottom + 16);
  const card = rect('card');
  if (phoneQuery.matches) {
    const sheet = rect('sheet');
    if (sheet) pad.bottom = Math.round(vh - sheet.top + 12);
    if (card) pad.bottom = Math.max(pad.bottom, Math.round(vh - card.top + 12));
  } else {
    const filters = rect('filters');
    if (filters) pad.left = Math.round(filters.right + 16);
    const time = rect('timebar');
    if (time) pad.bottom = Math.round(vh - time.top + 12);
    if (card) pad.right = Math.round(vw - card.left + 16);
  }
  const spareX = vw - pad.left - pad.right;
  if (spareX < 160) {
    const cut = (160 - spareX) / 2;
    pad.left = Math.max(0, pad.left - cut);
    pad.right = Math.max(0, pad.right - cut);
  }
  const spareY = vh - pad.top - pad.bottom;
  if (spareY < 160) {
    const cut = (160 - spareY) / 2;
    pad.top = Math.max(0, pad.top - cut);
    pad.bottom = Math.max(0, pad.bottom - cut);
  }
  return pad;
}

function syncPadding() {
  if (!map) return;
  map.easeTo({ padding: cameraPadding(), duration: reduceMotion.matches ? 0 : 450, essential: false });
}

function flyToCoords(coords, minZoom = 3.4) {
  if (!map || !coords) return;
  stopSpin(true);
  map.flyTo({
    center: coords,
    zoom: Math.max(map.getZoom(), minZoom),
    padding: cameraPadding(),
    duration: 1600,
    essential: false,
  });
}

function fitToFeatures(features) {
  if (!map || !features.length) return;
  stopSpin(true);
  const coords = features.map((f) => f.geometry.coordinates);
  const padding = cameraPadding();
  if (coords.length === 1) {
    map.flyTo({ center: coords[0], zoom: 5, padding, duration: 1800, essential: false });
    return;
  }
  const lats = coords.map((c) => c[1]);
  let lons = coords.map((c) => c[0]);
  let minLon = Math.min(...lons);
  let maxLon = Math.max(...lons);
  if (maxLon - minLon > 180) {
    const shifted = lons.map((l) => (l < 0 ? l + 360 : l));
    const sMin = Math.min(...shifted);
    const sMax = Math.max(...shifted);
    if (sMax - sMin < maxLon - minLon) {
      lons = shifted;
      minLon = sMin;
      maxLon = sMax;
    }
  }
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  if (maxLon - minLon > 200 || maxLat - minLat > 110) {
    const center = [lons.reduce((a, b) => a + b, 0) / lons.length, lats.reduce((a, b) => a + b, 0) / lats.length];
    if (center[0] > 180) center[0] -= 360;
    map.flyTo({ center, zoom: initialZoom(), padding, duration: 1800, essential: false });
    return;
  }
  // On the globe, MapLibre fits the bounds inside the padding option while the camera's own padding moves
  // the center, so the two must not be mixed. Measure with no camera padding and the panels as the fit
  // padding, then fly there while easing the camera padding to zero.
  const zero = { top: 0, right: 0, bottom: 0, left: 0 };
  const margin = 36;
  const fitPadding = {
    top: padding.top + margin, right: padding.right + margin,
    bottom: padding.bottom + margin, left: padding.left + margin,
  };
  const before = map.getPadding();
  map.jumpTo({ padding: zero });
  const camera = map.cameraForBounds([[minLon, minLat], [maxLon, maxLat]], { padding: fitPadding, maxZoom: 6 });
  map.jumpTo({ padding: before });
  if (!camera) {
    map.flyTo({ center: [(minLon + maxLon) / 2, (minLat + maxLat) / 2], zoom: 3, padding, duration: 1800, essential: false });
    return;
  }
  map.flyTo({ center: camera.center, zoom: camera.zoom, padding: zero, duration: 1800, essential: false });
}

// Gentle auto-rotate until the first interaction. Off when reduced motion is on.
let spinOn = false;
let spinRaf = 0;
let spinLast = 0;
let spinHoverPause = false;
let interacted = false;

function startSpin() {
  if (!map || interacted || reduceMotion.matches || spinOn) return;
  spinOn = true;
  spinLast = 0;
  spinRaf = requestAnimationFrame(spinFrame);
}

function stopSpin(forGood = false) {
  if (forGood) interacted = true;
  spinOn = false;
  cancelAnimationFrame(spinRaf);
}

function spinFrame(ts) {
  if (!spinOn) return;
  if (spinLast && !spinHoverPause && mapReady) {
    const dt = Math.min(ts - spinLast, 100);
    const center = map.getCenter();
    let lng = center.lng - (SPIN_DEG_PER_SEC * dt) / 1000;
    if (lng < -180) lng += 360;
    map.jumpTo({ center: [lng, center.lat] }, { spin: true });
  }
  spinLast = ts;
  spinRaf = requestAnimationFrame(spinFrame);
}

function onFirstInteraction() {
  if (!interacted) stopSpin(true);
}

// points and summary

let pointsCtrl = null;
let pointsTimer = 0;
let liveTimer = 0;

function filterParams() {
  const query = new URLSearchParams();
  if (state.phases.size) query.set('phase', [...state.phases].join(','));
  if (state.statuses.size) query.set('status', [...state.statuses].join(','));
  if (state.org) query.set('org_id', String(state.org.org_id));
  if (state.q.length >= 2) query.set('q', state.q);
  if (!state.allTime && state.months.length) query.set('active_on', state.months[state.monthIndex]);
  if (state.due) query.set('due_within', '12');
  return query;
}

function activeFilterCount() {
  return (state.phases.size ? 1 : 0) + (state.statuses.size ? 1 : 0) + (state.org ? 1 : 0)
    + (state.q.length >= 2 ? 1 : 0) + (state.due ? 1 : 0) + (state.allTime ? 0 : 1);
}

function schedulePoints(delay = 250) {
  window.clearTimeout(pointsTimer);
  pointsTimer = window.setTimeout(() => { loadPoints(); }, delay);
}

function validFeature(f) {
  const c = f?.geometry?.coordinates;
  return f?.type === 'Feature' && f.geometry.type === 'Point' && Array.isArray(c)
    && Number.isFinite(c[0]) && Number.isFinite(c[1]) && typeof f.properties?.geo_key === 'string';
}

async function loadPoints() {
  if (!state.meta) return;
  window.clearTimeout(pointsTimer);
  pointsCtrl?.abort();
  const ctrl = new AbortController();
  pointsCtrl = ctrl;
  $('summary').classList.add('is-loading');
  try {
    const fc = await api.points(filterParams(), ctrl.signal);
    if (ctrl.signal.aborted) return;
    applyPoints(fc);
    $('points-error').hidden = true;
  } catch (err) {
    if (isAbort(err) || ctrl.signal.aborted) return;
    showPointsError(err);
  } finally {
    if (pointsCtrl === ctrl) {
      pointsCtrl = null;
      $('summary').classList.remove('is-loading');
    }
  }
  updateFilterChrome();
  if (state.card?.kind === 'location' && usesFilters(state.card.scope)) scheduleCardRefresh();
}

function applyPoints(fc) {
  const features = Array.isArray(fc?.features) ? fc.features.filter(validFeature) : [];
  state.points = { type: 'FeatureCollection', features };
  setSourceData('points', state.points);
  const s = fc?.summary || {};
  setStat('sum-trials', s.trials);
  setStat('sum-sites', s.sites);
  setStat('sum-points', s.points ?? features.length);
  const filtered = activeFilterCount() > 0;
  let note = filtered
    ? `Matching your filters, out of ${fmt(state.meta.trials_with_geo)} trials with mapped sites`
    : `All ${fmt(state.meta.trials_with_geo)} trials with mapped sites`;
  if (features.length >= MAX_POINTS && Number(s.points) > features.length) {
    note += `. Showing the ${fmt(features.length)} busiest locations`;
  }
  $('summary-note').textContent = note;
  renderTopList(features.slice(0, 5));
  if (!state.playing) {
    window.clearTimeout(liveTimer);
    liveTimer = window.setTimeout(() => {
      $('live').textContent = `${fmt(s.trials)} ${plural(s.trials, 'trial')} at ${fmt(s.points ?? features.length)} ${plural(s.points ?? features.length, 'location')}`;
    }, 700);
  }
}

function setStat(id, value) {
  const node = $(id);
  node.textContent = compact(value);
  node.title = fmt(value);
}

function showPointsError(err) {
  const box = $('points-error');
  if (err?.kind === 'bad_filter') box.textContent = `A filter was not accepted: ${err.message}`;
  else if (err?.status === 0 || err?.kind === 'network') box.textContent = 'Could not reach the server. The globe shows the last results.';
  else box.textContent = `Could not load trial sites (${err?.status || 'error'}). The globe shows the last results.`;
  box.hidden = false;
}

function renderTopList(features) {
  const list = $('top-list');
  if (!features.length) {
    list.replaceChildren(el('li', { class: 'top-empty', text: 'No locations match these filters.' }));
    return;
  }
  list.replaceChildren(...features.map((f, i) => {
    const p = f.properties;
    const btn = el('button', {
      type: 'button',
      class: 'top-btn',
      'aria-label': `${placeName(p)}, ${p.country}: ${fmt(p.trials)} ${plural(p.trials, 'trial')}. Show details.`,
    },
    el('span', { class: 'top-rank', text: String(i + 1) }),
    el('span', { class: 'top-place' }, p.city || placeName(p), el('span', { class: 'top-country', text: ` ${p.country || ''}` })),
    el('span', { class: 'top-n', text: fmt(p.trials) }));
    btn.addEventListener('click', () => {
      openLocation(p, { scope: 'filters', coords: f.geometry.coordinates.slice(0, 2), fly: true, focus: true });
    });
    return el('li', {}, btn);
  }));
}

// filter panel

function renderMeta(meta) {
  $('brand-sub').textContent = `Where ${fmt(meta.trials)} industry trials run`;
  $('footer-asof').textContent = meta.as_of ? `, as of ${fmtDate(meta.as_of) || meta.as_of}` : '';
  const fetched = fmtDate(meta.sites_fetched_at);
  $('footer-sites').textContent = fetched ? `Site locations fetched ${fetched}` : 'Site locations from ClinicalTrials.gov';

  const phases = [...(meta.phases || [])].sort((a, b) => {
    const ia = PHASE_ORDER.indexOf(a.value);
    const ib = PHASE_ORDER.indexOf(b.value);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  $('phase-chips').replaceChildren(...phases.map((p) => chip(p, state.phases, 'phase')));

  const statuses = [...(meta.statuses || [])].sort((a, b) => (Number(b.active) - Number(a.active)) || (b.count - a.count));
  const chips = [];
  statuses.forEach((s, i) => {
    if (i > 0 && statuses[i - 1].active && !s.active) chips.push(el('span', { class: 'chip-divider', 'aria-hidden': 'true' }));
    chips.push(chip(s, state.statuses, 'status'));
  });
  $('status-chips').replaceChildren(...chips);
  $('status-active').hidden = !statuses.some((s) => s.active);

  setupTime(meta);
}

function chip(item, set, kind) {
  const node = el('button', {
    type: 'button',
    class: 'chip',
    'aria-pressed': 'false',
    'data-value': item.value,
    'aria-label': `${item.label}, ${fmt(item.count)} ${plural(item.count, 'trial')}`,
  }, item.label, el('span', { class: 'chip-n', 'aria-hidden': 'true', text: compactCount(item.count) }));
  node.addEventListener('click', () => {
    if (set.has(item.value)) set.delete(item.value);
    else set.add(item.value);
    node.setAttribute('aria-pressed', String(set.has(item.value)));
    filtersChanged(220);
  });
  node.dataset.kind = kind;
  return node;
}

function compactCount(n) {
  const v = Number(n) || 0;
  if (v >= 10000) return `${Math.round(v / 1000)}k`;
  if (v >= 1000) return `${(v / 1000).toFixed(1).replace(/\.0$/, '')}k`;
  return fmt(v);
}

function syncChips() {
  for (const node of document.querySelectorAll('.chip[data-kind]')) {
    const set = node.dataset.kind === 'phase' ? state.phases : state.statuses;
    node.setAttribute('aria-pressed', String(set.has(node.dataset.value)));
  }
}

function filtersChanged(delay = 250) {
  updateFilterChrome();
  schedulePoints(delay);
}

function updateFilterChrome() {
  const n = activeFilterCount();
  $('reset').hidden = n === 0;
  const expanded = $('sheet').classList.contains('is-expanded');
  $('sheet-toggle-text').textContent = expanded ? 'Hide filters' : (n ? `Show filters, ${n} on` : 'Show filters');
}

function wireStatusShortcut() {
  $('status-active').addEventListener('click', () => {
    const active = (state.meta?.statuses || []).filter((s) => s.active).map((s) => s.value);
    const already = active.length === state.statuses.size && active.every((v) => state.statuses.has(v));
    state.statuses = already ? new Set() : new Set(active);
    syncChips();
    filtersChanged(150);
  });
}

// Lead sponsor combobox.
let orgCtrl = null;
let orgTimer = 0;
let orgOptions = [];
let orgActive = -1;

function wireSponsor() {
  const input = $('sponsor-input');
  const list = $('sponsor-list');

  const close = () => {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
    orgActive = -1;
  };
  const setActive = (i) => {
    orgActive = i;
    [...list.children].forEach((li, j) => li.setAttribute('aria-selected', String(j === i)));
    const li = list.children[i];
    if (li?.id) {
      input.setAttribute('aria-activedescendant', li.id);
      li.scrollIntoView({ block: 'nearest' });
    } else input.removeAttribute('aria-activedescendant');
  };
  const choose = (org) => {
    state.org = { org_id: org.org_id, org_name: org.org_name };
    input.value = '';
    close();
    renderChosenSponsor();
    filtersChanged(0);
  };
  const render = (orgs, q) => {
    orgOptions = orgs;
    if (!orgs.length) {
      list.replaceChildren(el('li', { class: 'listbox-empty', role: 'option', 'aria-disabled': 'true', text: `No sponsors match "${q}"` }));
    } else {
      list.replaceChildren(...orgs.map((o, i) => {
        const li = el('li', { id: `org-opt-${i}`, role: 'option', 'aria-selected': 'false' },
          el('span', { text: o.org_name }),
          el('span', { class: 'opt-n', text: `${fmt(o.n_trials)} ${plural(o.n_trials, 'trial')}` }));
        li.addEventListener('mousedown', (e) => e.preventDefault());
        li.addEventListener('click', () => choose(o));
        return li;
      }));
    }
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    orgActive = -1;
  };

  input.addEventListener('input', () => {
    const q = input.value.trim();
    window.clearTimeout(orgTimer);
    orgCtrl?.abort();
    if (q.length < 2) {
      close();
      return;
    }
    orgTimer = window.setTimeout(async () => {
      const ctrl = new AbortController();
      orgCtrl = ctrl;
      try {
        const data = await api.orgs(q.slice(0, 60), ctrl.signal);
        if (ctrl.signal.aborted || input.value.trim() !== q) return;
        render(Array.isArray(data?.orgs) ? data.orgs : [], q);
      } catch (err) {
        if (isAbort(err) || ctrl.signal.aborted) return;
        list.replaceChildren(el('li', { class: 'listbox-empty', role: 'option', 'aria-disabled': 'true', text: 'Could not load sponsors' }));
        list.hidden = false;
        input.setAttribute('aria-expanded', 'true');
      }
    }, 200);
  });

  input.addEventListener('keydown', (e) => {
    const open = !list.hidden && orgOptions.length > 0;
    if (e.key === 'ArrowDown' && open) {
      e.preventDefault();
      setActive(Math.min(orgOptions.length - 1, orgActive + 1));
    } else if (e.key === 'ArrowUp' && open) {
      e.preventDefault();
      setActive(Math.max(0, orgActive - 1));
    } else if (e.key === 'Enter' && open) {
      e.preventDefault();
      choose(orgOptions[Math.max(0, orgActive)]);
    } else if (e.key === 'Escape' && !list.hidden) {
      e.preventDefault();
      close();
    }
  });
  input.addEventListener('blur', () => window.setTimeout(close, 120));

  $('sponsor-remove').addEventListener('click', () => {
    state.org = null;
    renderChosenSponsor();
    filtersChanged(0);
    input.focus();
  });
}

function renderChosenSponsor() {
  const chosen = $('sponsor-chosen');
  chosen.hidden = !state.org;
  $('sponsor-chosen-name').textContent = state.org ? state.org.org_name : '';
  $('sponsor-input').placeholder = state.org ? 'Change sponsor' : 'Search sponsors';
}

function wireSearch() {
  const input = $('q-input');
  const hint = $('q-hint');
  input.addEventListener('input', () => {
    const value = input.value.trim().slice(0, 80);
    const effective = value.length >= 2 ? value : '';
    hint.textContent = value.length === 1 ? 'Type at least 2 characters' : 'Matches titles, conditions and interventions';
    if (effective === state.q) return;
    state.q = effective;
    filtersChanged(350);
  });
}

function wireDue() {
  const sw = $('due-switch');
  sw.addEventListener('click', () => {
    state.due = !state.due;
    sw.setAttribute('aria-checked', String(state.due));
    applyPointStyle();
    filtersChanged(0);
  });
}

function wireReset() {
  $('reset').addEventListener('click', () => {
    state.phases = new Set();
    state.statuses = new Set();
    state.org = null;
    state.q = '';
    state.due = false;
    $('q-input').value = '';
    $('due-switch').setAttribute('aria-checked', 'false');
    syncChips();
    renderChosenSponsor();
    stopPlaying();
    setAllTime(true, { load: false });
    applyPointStyle();
    filtersChanged(0);
    // The button hides itself, so hand focus to the first filter.
    document.querySelector('#phase-chips .chip')?.focus();
  });
}

// time slider

let playToken = 0;

function setupTime(meta) {
  state.months = monthList(meta.months?.min, meta.months?.max);
  const slider = $('month');
  const last = Math.max(0, state.months.length - 1);
  slider.max = String(last);
  const asOfMonth = (meta.as_of || '').slice(0, 7);
  let start = state.months.indexOf(asOfMonth);
  if (start < 0) start = last;
  state.monthIndex = start;
  slider.value = String(start);
  if (state.months.length) {
    $('time-range').textContent = `${fmtMonth(state.months[0])} to ${fmtMonth(state.months[last])}`;
  }
  const ticks = $('time-ticks');
  const years = [];
  state.months.forEach((m, i) => { if (m.endsWith('-01')) years.push([m.slice(0, 4), i]); });
  const step = years.length > 8 ? 2 : 1;
  ticks.replaceChildren(...years.filter((_, i) => i % step === 0).map(([year, i]) => {
    const tick = el('span', { class: 'tick', text: year });
    tick.style.left = `${last ? (i / last) * 100 : 0}%`;
    return tick;
  }));
  updateTimeUi();
  $('timebar').hidden = state.months.length === 0;
}

function updateTimeUi() {
  const slider = $('month');
  const last = Math.max(1, state.months.length - 1);
  const pct = (state.monthIndex / last) * 100;
  slider.style.setProperty('--pct', `${pct}%`);
  slider.value = String(state.monthIndex);
  const month = state.months[state.monthIndex];
  slider.setAttribute('aria-valuetext', month ? `${fmtMonth(month)}${state.allTime ? ', not applied' : ''}` : '');
  $('time-label').textContent = state.allTime || !month ? 'All time' : `Active in ${fmtMonth(month)}`;
  $('alltime').setAttribute('aria-pressed', String(state.allTime));
  $('timebar').classList.toggle('is-all', state.allTime);
}

function applyMonth(index) {
  state.monthIndex = Math.max(0, Math.min(state.months.length - 1, index));
  state.allTime = false;
  updateTimeUi();
}

function setAllTime(on, { load = true } = {}) {
  state.allTime = on;
  updateTimeUi();
  if (load) filtersChanged(0);
}

function wireTime() {
  const slider = $('month');
  slider.addEventListener('input', () => {
    if (state.playing) stopPlaying();
    applyMonth(Number(slider.value));
    filtersChanged(140);
  });
  $('alltime').addEventListener('click', () => {
    if (state.playing) stopPlaying();
    setAllTime(!state.allTime);
  });
  $('play').addEventListener('click', togglePlay);
  syncPlayAvailability();
}

function syncPlayAvailability() {
  const btn = $('play');
  const off = reduceMotion.matches;
  btn.disabled = off;
  btn.title = off ? 'Play is off because your system asks for reduced motion. Use the slider or arrow keys.' : '';
  if (off) stopPlaying();
}

function togglePlay() {
  if (state.playing) {
    stopPlaying();
    return;
  }
  if (reduceMotion.matches || !state.months.length) return;
  state.playing = true;
  $('timebar').classList.add('is-playing');
  $('play').setAttribute('aria-label', 'Pause');
  playLoop();
}

function stopPlaying() {
  if (!state.playing) return;
  state.playing = false;
  playToken += 1;
  $('timebar').classList.remove('is-playing');
  $('play').setAttribute('aria-label', 'Play through the months');
  updateFilterChrome();
  if (state.card?.kind === 'location' && usesFilters(state.card.scope)) scheduleCardRefresh();
}

async function playLoop() {
  playToken += 1;
  const token = playToken;
  const last = state.months.length - 1;
  applyMonth(state.allTime || state.monthIndex >= last ? 0 : state.monthIndex);
  while (state.playing && token === playToken) {
    const t0 = performance.now();
    await loadPoints();
    const wait = PLAY_STEP_MS - (performance.now() - t0);
    if (wait > 0) await sleep(wait);
    if (!state.playing || token !== playToken) break;
    if (state.monthIndex >= last) {
      stopPlaying();
      break;
    }
    applyMonth(state.monthIndex + 1);
  }
}

// question box

let askCtrl = null;
let askTicker = 0;
let askRowsExpanded = false;
let answerPointsCtrl = null;

const ASK_MESSAGES = {
  bad_question: 'Ask a question between 1 and 300 characters.',
  unsafe_sql: 'The SQL failed the safety check, so it was not run. Try rephrasing.',
  busy: 'Another question is running. Try again in a few seconds.',
  llm_unavailable: 'The AI is offline right now. The globe and filters still work.',
  timeout: 'The query hit the 20 second limit. Try a narrower question.',
  query_failed: 'The query failed to run. Try rephrasing the question.',
  network: 'Could not reach the server. Check that it is running.',
  client_timeout: 'No answer after 90 seconds. Try again.',
};

function wireAsk() {
  const form = $('ask-form');
  const input = $('ask-input');
  const button = $('ask-button');
  const syncButton = () => { button.disabled = askCtrl !== null || input.value.trim().length === 0; };
  input.addEventListener('input', syncButton);
  syncButton();
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question || askCtrl) return;
    ask(question.slice(0, 300));
  });
  $('ask-clear').addEventListener('click', () => {
    clearAnswer();
    input.value = '';
    syncButton();
    input.focus();
  });
  $('ask-more').addEventListener('click', () => {
    askRowsExpanded = !askRowsExpanded;
    if (state.answer) renderAnswerTable(state.answer);
  });
  $('ask-copy').addEventListener('click', async () => {
    const code = $('ask-sql-code').textContent;
    const btn = $('ask-copy');
    try {
      await navigator.clipboard.writeText(code);
      btn.textContent = 'Copied';
    } catch {
      const range = document.createRange();
      range.selectNodeContents($('ask-sql-code'));
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      btn.textContent = 'Selected, press Cmd+C';
    }
    window.setTimeout(() => { btn.textContent = 'Copy SQL'; }, 1800);
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && input.value && !askCtrl) {
      e.preventDefault();
      input.value = '';
      syncButton();
    }
  });
}

function setAskBusy(busy) {
  const input = $('ask-input');
  const button = $('ask-button');
  $('ask-progress').hidden = !busy;
  input.readOnly = busy;
  button.disabled = busy || input.value.trim().length === 0;
  button.textContent = busy ? 'Asking' : 'Ask';
  $('ask').setAttribute('aria-busy', String(busy));
  window.clearInterval(askTicker);
  if (busy) {
    const t0 = Date.now();
    const text = $('ask-progress-text');
    const tick = () => {
      const s = Math.floor((Date.now() - t0) / 1000);
      text.textContent = `Writing and running the SQL, ${s} s. Answers take 5 to 20 seconds.`;
    };
    tick();
    askTicker = window.setInterval(tick, 1000);
  }
}

async function ask(question) {
  const ctrl = new AbortController();
  askCtrl = ctrl;
  let timedOut = false;
  const timer = window.setTimeout(() => { timedOut = true; ctrl.abort(); }, ASK_CLIENT_TIMEOUT_MS);
  $('ask-error').hidden = true;
  setAskBusy(true);
  try {
    const answer = await api.ask(question, ctrl.signal);
    showAnswer(answer);
  } catch (err) {
    if (isAbort(err) && !timedOut) return;
    showAskError(timedOut ? new ApiError(0, 'client_timeout', '') : err);
  } finally {
    window.clearTimeout(timer);
    askCtrl = null;
    setAskBusy(false);
  }
}

function showAskError(err) {
  const box = $('ask-error');
  const kind = err?.kind || (err?.status === 0 ? 'network' : 'unknown');
  box.textContent = ASK_MESSAGES[kind] || `Something went wrong (${err?.status || 'error'}). Try again.`;
  box.classList.toggle('is-info', kind === 'llm_unavailable' || kind === 'busy');
  box.hidden = false;
  const rejected = kind === 'unsafe_sql' && typeof err?.body?.sql === 'string' ? err.body.sql : null;
  if (rejected) {
    // The rejected SQL replaces the previous answer, so its highlight goes too.
    state.answer = null;
    state.answerMode = null;
    state.answerNct = [];
    clearHighlight();
    $('ask-count').textContent = 'Not run';
    $('ask-meta').textContent = '';
    $('ask-note').textContent = '';
    $('ask-table').replaceChildren();
    $('ask-more').hidden = true;
    $('ask-sql-code').textContent = rejected;
    $('ask-sql').querySelector('summary').textContent = 'Rejected SQL';
    $('ask-sql').hidden = false;
    $('ask-sql').open = true;
    $('ask-result').hidden = false;
  } else if (!state.answer) {
    $('ask-result').hidden = true;
  }
}

function showAnswer(answer) {
  const rows = Array.isArray(answer?.rows) ? answer.rows.slice(0, 50) : [];
  const columns = Array.isArray(answer?.columns) ? answer.columns.map(String) : [];
  const rowCount = Number.isFinite(answer?.row_count) ? answer.row_count : rows.length;
  state.answer = { ...answer, rows, columns, row_count: rowCount };
  askRowsExpanded = false;
  $('ask-error').hidden = true;
  $('ask-count').textContent = `${fmt(rowCount)} ${plural(rowCount, 'row')}`;
  const bits = [];
  if (rows.length < rowCount) bits.push(`first ${fmt(rows.length)} shown`);
  if (Number.isFinite(answer?.seconds)) bits.push(`${answer.seconds.toFixed(1)} s`);
  if (answer?.cached) bits.push('cached');
  $('ask-meta').textContent = bits.length ? bits.join(' · ') : '';
  renderAnswerTable(state.answer);
  $('ask-sql-code').textContent = typeof answer?.sql === 'string' ? answer.sql : '';
  $('ask-sql').querySelector('summary').textContent = answer?.model ? `Generated SQL (model ${answer.model})` : 'Generated SQL';
  $('ask-sql').open = false;
  $('ask-sql').hidden = !answer?.sql;
  $('ask-result').hidden = false;

  const ids = Array.isArray(answer?.nct_ids)
    ? [...new Set(answer.nct_ids.filter((id) => typeof id === 'string' && NCT_RE.test(id)))].slice(0, MAX_NCT)
    : [];
  const places = Array.isArray(answer?.geo_keys)
    ? [...new Set(answer.geo_keys.filter(validGeoKey))].slice(0, MAX_NCT)
    : [];
  // A card opened from the previous answer no longer applies.
  if (state.card?.kind === 'location' && isAnswerScope(state.card.scope)) closeCard();
  state.answerNct = ids;
  // Places first, then the sites of the answer's trials, as the API describes.
  if (places.length) {
    state.answerMode = 'places';
    highlightPlaces(places, state.answer);
  } else if (ids.length) {
    state.answerMode = 'trials';
    setAnswerNote('Finding these trials on the globe');
    highlightAnswer(ids);
  } else {
    state.answerMode = null;
    clearHighlight();
    const hasTrials = columns.includes('nct_id');
    const hasPlaces = columns.some(isGeoKeyColumn);
    let note = 'This answer has no nct_id or geo_key column, so nothing is highlighted on the globe.';
    if (hasPlaces && !hasTrials) note = 'No places to show on the globe.';
    else if (hasTrials || rowCount === 0) note = 'No trials to show on the globe.';
    setAnswerNote(note);
  }
  $('live').textContent = `Answer ready: ${fmt(rowCount)} ${plural(rowCount, 'row')}.`;
}

// Places in a places answer: names and the answer's own number per place, read from the rows on hand.
const NOT_A_METRIC = new Set(['geokey', 'lat', 'lon', 'lng', 'latitude', 'longitude', 'zip', 'zipcode', 'postalcode', 'nctid', 'siteorder']);
const PLACE_NAME_COLUMNS = ['city', 'cityname', 'place', 'placename', 'location', 'site', 'sitename', 'facility', 'name'];

function answerPlaceDetails(answer) {
  const { columns, rows } = answer;
  const names = columns.map(normColumn);
  const keyIdx = columns.findIndex(isGeoKeyColumn);
  const find = (options) => names.findIndex((n) => options.includes(n));
  const cityIdx = find(['city', 'cityname']);
  const stateIdx = find(['state', 'statename', 'province', 'region']);
  const countryIdx = find(['country', 'countryname']);
  const isNumberColumn = (i) => rows.length > 0
    && rows.every((r) => Array.isArray(r) && (r[i] === null || (typeof r[i] === 'number' && Number.isFinite(r[i]))))
    && rows.some((r) => typeof r[i] === 'number');
  const metricIdx = names.findIndex((n, i) => i !== keyIdx && !NOT_A_METRIC.has(n) && !n.endsWith('id')
    && !n.includes('year') && isNumberColumn(i));
  const byKey = new Map();
  if (keyIdx >= 0) {
    for (const row of rows) {
      if (!Array.isArray(row) || !validGeoKey(row[keyIdx]) || byKey.has(row[keyIdx])) continue;
      const text = (i) => (i >= 0 && typeof row[i] === 'string' && row[i].trim() ? row[i].trim() : null);
      byKey.set(row[keyIdx], {
        city: text(cityIdx),
        state: text(stateIdx),
        country: text(countryIdx),
        metric: metricIdx >= 0 && typeof row[metricIdx] === 'number' ? row[metricIdx] : null,
      });
    }
  }
  return { byKey, metricLabel: metricIdx >= 0 ? prettyColumn(columns[metricIdx]) : null };
}

// Places answers are drawn straight from their "lat,lon" keys, so no extra request is made.
function highlightPlaces(keys, answer) {
  answerPointsCtrl?.abort();
  const { byKey, metricLabel } = answerPlaceDetails(answer);
  const known = new Map(state.points.features.map((f) => [f.properties.geo_key, f.properties]));
  const values = [...byKey.values()].map((d) => d.metric).filter((v) => Number.isFinite(v) && v > 0);
  const maxValue = values.length ? Math.max(...values) : 0;
  const features = [];
  for (const key of keys) {
    const coords = coordsFromGeoKey(key);
    if (!coords) continue;
    const row = byKey.get(key) || {};
    const base = known.get(key) || {};
    const metric = Number.isFinite(row.metric) ? row.metric : null;
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: coords },
      properties: {
        geo_key: key,
        kind: 'place',
        city: row.city ?? base.city ?? null,
        state: row.state ?? base.state ?? null,
        country: row.country ?? base.country ?? null,
        // Sized by the answer's number when it has one, scaled to 1 to 100 so no circle grows huge.
        size: maxValue > 0 && metric !== null && metric > 0 ? 1 + (99 * metric) / maxValue : 1,
        metric,
        metric_label: metric !== null ? metricLabel : null,
      },
    });
  }
  state.answerPoints = { type: 'FeatureCollection', features };
  setSourceData('answer', state.answerPoints);
  applyPointStyle();
  if (!features.length) {
    setAnswerNote('No places to show on the globe.');
    return;
  }
  setAnswerNote(`${fmt(features.length)} ${plural(features.length, 'place')} highlighted on the globe.`, true);
  fitToFeatures(features);
}

function openAnswerPlace(key) {
  const feature = state.answerPoints.features.find((f) => f.properties.geo_key === key);
  openLocation(feature ? feature.properties : { geo_key: key, kind: 'place' }, {
    scope: answerScope(),
    coords: coordsFromGeoKey(key),
    fly: true,
    focus: true,
  });
}

function setAnswerNote(text, withDot = false) {
  const note = $('ask-note');
  note.replaceChildren();
  if (withDot) note.append(el('span', { class: 'dot dot-answer', 'aria-hidden': 'true' }));
  note.append(text);
}

function prettyColumn(name) {
  if (name === 'nct_id') return 'NCT ID';
  const words = name.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function renderAnswerTable(answer) {
  const table = $('ask-table');
  const wrap = table.closest('.table-wrap');
  const { columns, rows } = answer;
  // geo_key is for placing points, not for reading, so it never shows as a column.
  const visible = columns.map((_, i) => i).filter((i) => !isGeoKeyColumn(columns[i]));
  if (!visible.length) {
    table.replaceChildren();
    wrap.hidden = true;
    $('ask-more').hidden = true;
    return;
  }
  wrap.hidden = false;
  // In a places answer the place name opens that place's card, which also works from the keyboard.
  const keyIdx = columns.findIndex(isGeoKeyColumn);
  const placeIdx = keyIdx >= 0 ? placeNameColumn(answer, visible) : -1;
  const shown = askRowsExpanded ? rows : rows.slice(0, ROWS_PREVIEW);
  const head = el('thead', {}, el('tr', {}, ...visible.map((i) => el('th', { scope: 'col', text: prettyColumn(columns[i]) }))));
  const body = el('tbody', {}, ...shown.map((row) => el('tr', {}, ...visible.map((i) => {
    const value = Array.isArray(row) ? row[i] : undefined;
    const key = Array.isArray(row) ? row[keyIdx] : undefined;
    if (i === placeIdx && validGeoKey(key) && value !== null && value !== undefined) return placeCell(value, key);
    return cell(columns[i], value);
  }))));
  if (!rows.length) {
    body.append(el('tr', {}, el('td', { colspan: String(visible.length), class: 'cell-null', text: 'No rows' })));
  }
  table.replaceChildren(head, body);
  const more = $('ask-more');
  more.hidden = rows.length <= ROWS_PREVIEW;
  more.textContent = askRowsExpanded ? 'Show fewer rows' : `Show all ${fmt(rows.length)} rows`;
}

function placeNameColumn(answer, visible) {
  const named = visible.find((i) => PLACE_NAME_COLUMNS.includes(normColumn(answer.columns[i])));
  if (named !== undefined) return named;
  const texty = visible.find((i) => normColumn(answer.columns[i]) !== 'nctid'
    && answer.rows.some((r) => Array.isArray(r) && typeof r[i] === 'string'));
  return texty === undefined ? -1 : texty;
}

function placeCell(value, key) {
  const text = typeof value === 'string' ? value : String(value);
  const btn = el('button', { type: 'button', class: 'place-btn', text, title: `Show ${text} on the globe` });
  btn.addEventListener('click', () => openAnswerPlace(key));
  return el('td', { class: 'cell-text' }, btn);
}

function cell(column, value) {
  if (value === null || value === undefined) return el('td', { class: 'cell-null', text: 'null' });
  if (column === 'nct_id' && typeof value === 'string' && NCT_RE.test(value)) {
    const btn = el('button', { type: 'button', class: 'nct-btn', text: value, 'aria-label': `Show trial ${value}` });
    btn.addEventListener('click', () => openTrial(value));
    return el('td', {}, btn);
  }
  if (typeof value === 'number') {
    const yearish = /year/i.test(column) && Number.isInteger(value) && value > 1800 && value < 2200;
    return el('td', { class: 'cell-num', text: yearish ? String(value) : nf2.format(value) });
  }
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  return el('td', { class: 'cell-text', text, title: text.length > 32 ? text : null });
}

async function highlightAnswer(ids) {
  answerPointsCtrl?.abort();
  const ctrl = new AbortController();
  answerPointsCtrl = ctrl;
  try {
    const fc = await api.points(new URLSearchParams({ nct: ids.join(',') }), ctrl.signal);
    if (ctrl.signal.aborted) return;
    const features = Array.isArray(fc?.features) ? fc.features.filter(validFeature) : [];
    state.answerPoints = { type: 'FeatureCollection', features };
    setSourceData('answer', state.answerPoints);
    applyPointStyle();
    if (features.length) {
      const trials = Number(fc?.summary?.trials) || ids.length;
      setAnswerNote(`${fmt(features.length)} ${plural(features.length, 'location')} for ${fmt(trials)} ${plural(trials, 'trial')} highlighted on the globe.`, true);
      fitToFeatures(features);
    } else {
      setAnswerNote('None of these trials list site coordinates, so nothing is highlighted.');
    }
  } catch (err) {
    if (isAbort(err) || ctrl.signal.aborted) return;
    setAnswerNote('Could not load the locations for these trials.');
  } finally {
    if (answerPointsCtrl === ctrl) answerPointsCtrl = null;
  }
}

function clearHighlight() {
  answerPointsCtrl?.abort();
  state.answerPoints = EMPTY_FC;
  setSourceData('answer', EMPTY_FC);
  applyPointStyle();
  if (state.card?.kind === 'location' && isAnswerScope(state.card.scope)) closeCard();
}

function clearAnswer() {
  askCtrl?.abort();
  state.answer = null;
  state.answerMode = null;
  state.answerNct = [];
  clearHighlight();
  $('ask-result').hidden = true;
  $('ask-error').hidden = true;
  $('ask-table').replaceChildren();
  $('ask-sql-code').textContent = '';
  syncPadding();
}

// detail card

let cardCtrl = null;
let cardOpener = null;
let cardRefreshTimer = 0;

// Card scopes: 'filters' lists a location's trials under the current filters, 'answer' lists only the
// answer's trials there, and 'answer-place' is a place from a places answer, listed under the filters.
const isAnswerScope = (scope) => scope === 'answer' || scope === 'answer-place';
const usesFilters = (scope) => scope === 'filters' || scope === 'answer-place';

function answerScope() {
  return state.answerMode === 'places' && !state.answerNct.length ? 'answer-place' : 'answer';
}

function showCard({ focus = false } = {}) {
  const card = $('card');
  const wasHidden = card.hidden;
  card.hidden = false;
  if (wasHidden && !phoneQuery.matches) syncPadding();
  if (focus) $('card-title').focus();
}

function rememberOpener() {
  const active = document.activeElement;
  if (active && active !== document.body && !$('card').contains(active)) cardOpener = active;
}

function setCardHead(kicker, title, sub) {
  $('card-kicker').textContent = kicker;
  $('card-title').textContent = title;
  $('card-sub').textContent = sub || '';
  $('card-sub').hidden = !sub;
}

function openLocation(props, { scope = 'filters', coords = null, fly = false, focus = false } = {}) {
  rememberOpener();
  const p = { ...props };
  state.card = { kind: 'location', props: p, scope };
  setSelected(p.geo_key, isAnswerScope(scope) ? 'answer' : 'points');
  setCardHead(isAnswerScope(scope) ? 'In the answer' : 'Trial location', p.city || placeName(p), [p.state, p.country].filter(Boolean).join(', '));
  $('card-count').textContent = '';
  $('card-body').replaceChildren(el('p', { class: 'loading-line', text: 'Loading trials' }));
  showCard({ focus });
  loadCardTrials();
  if (fly) flyToCoords(coords || coordsFromGeoKey(p.geo_key));
}

function scheduleCardRefresh() {
  window.clearTimeout(cardRefreshTimer);
  if (state.playing) return;
  cardRefreshTimer = window.setTimeout(loadCardTrials, 300);
}

async function loadCardTrials() {
  const c = state.card;
  if (!c || c.kind !== 'location') return;
  cardCtrl?.abort();
  const ctrl = new AbortController();
  cardCtrl = ctrl;
  const body = $('card-body');
  body.setAttribute('aria-busy', 'true');
  const query = c.scope === 'answer' ? new URLSearchParams({ nct: state.answerNct.join(',') }) : filterParams();
  try {
    const data = await api.pointTrials(c.props.geo_key, query, ctrl.signal);
    if (ctrl.signal.aborted || state.card !== c) return;
    renderLocationTrials(data, c);
  } catch (err) {
    if (isAbort(err) || ctrl.signal.aborted || state.card !== c) return;
    $('card-count').textContent = '';
    body.replaceChildren(el('p', {
      class: 'card-empty',
      text: err?.status === 404 ? 'This location is not in the data any more.' : 'Could not load the trials here. Try again in a moment.',
    }));
  } finally {
    if (cardCtrl === ctrl) {
      cardCtrl = null;
      body.setAttribute('aria-busy', 'false');
    }
  }
}

function renderLocationTrials(data, c) {
  const p = c.props;
  // Under filters, the due count comes from the current points; a location that dropped out has none.
  const live = state.points.features.find((f) => f.properties.geo_key === p.geo_key)?.properties;
  setCardHead(isAnswerScope(c.scope) ? 'In the answer' : 'Trial location', data.city || p.city || placeName(p),
    [data.state ?? p.state, data.country ?? p.country].filter(Boolean).join(', '));
  const total = Number(data.total) || 0;
  const trials = Array.isArray(data.trials) ? data.trials : [];
  // A highlighted place carries no due count of its own, so count the due trials listed for it.
  let due = live?.due ?? 0;
  if (c.scope === 'answer') due = p.kind === 'place' ? trials.filter((t) => t.due).length : p.due;
  const label = c.scope === 'answer' ? 'from the answer' : (activeFilterCount() ? 'matching' : 'in all');
  let count = `${fmt(total)} ${plural(total, 'trial')} ${label}`;
  if (c.scope !== 'answer' && activeFilterCount() === 0) count = `${fmt(total)} ${plural(total, 'trial')} here`;
  if (Number(due) > 0) count += ` · ${fmt(due)} due within 12 months`;
  $('card-count').textContent = count;
  const body = $('card-body');
  if (!trials.length) {
    body.replaceChildren(el('p', { class: 'card-empty', text: 'No trials here match the current filters.' }));
    return;
  }
  const list = el('ol', { class: 'trials' }, ...trials.map(trialItem));
  const note = total > trials.length
    ? `Showing the first ${fmt(trials.length)} of ${fmt(total)}, earliest primary completion first.`
    : 'Earliest primary completion first.';
  body.replaceChildren(list, el('p', { class: 'card-note', text: note }));
  body.scrollTop = 0;
}

function trialTags(t) {
  return el('div', { class: 'trial-tags' },
    el('span', { class: 'tag', text: phaseLabel(t.phase, t.phase_label) }),
    el('span', { class: 'tag tag-status', text: statusLabel(t.status) }),
    t.due ? el('span', { class: 'badge badge-due', title: 'Active, with primary completion within 12 months of the data snapshot' },
      el('span', { class: 'dot dot-due', 'aria-hidden': 'true' }), 'Readout due') : null);
}

function ctgovLink(nct, url) {
  const href = trialUrl(nct, url);
  if (!href) return el('span', { class: 'muted', text: nct || '' });
  return el('a', { href, target: '_blank', rel: 'noopener noreferrer' },
    `${nct} on ClinicalTrials.gov`, el('span', { class: 'sr-only', text: ' (opens in a new tab)' }));
}

function trialItem(t) {
  const more = el('div', { class: 'trial-more', hidden: true });
  const moreBtn = el('button', { type: 'button', class: 'btn-link', 'aria-expanded': 'false', text: 'More details' });
  moreBtn.addEventListener('click', async () => {
    const open = moreBtn.getAttribute('aria-expanded') === 'true';
    moreBtn.setAttribute('aria-expanded', String(!open));
    moreBtn.textContent = open ? 'More details' : 'Fewer details';
    more.hidden = open;
    if (!open && !more.dataset.loaded) {
      more.replaceChildren(el('p', { class: 'muted', text: 'Loading' }));
      try {
        const detail = await api.trial(t.nct_id);
        more.replaceChildren(...trialDetailNodes(detail, { compact: true }));
        more.dataset.loaded = '1';
      } catch {
        more.replaceChildren(el('p', { class: 'muted', text: 'Could not load the details.' }));
      }
    }
  });
  const facilities = Array.isArray(t.facilities) ? t.facilities.filter(Boolean) : [];
  return el('li', { class: 'trial' },
    trialTags(t),
    el('p', { class: 'trial-title', text: t.title || t.nct_id }),
    el('dl', { class: 'kv' },
      el('dt', { text: 'Lead sponsor' }), el('dd', { text: t.lead_org_name || 'Not given' }),
      el('dt', { text: 'Primary completion' }), el('dd', { text: fmtDate(t.primary_completion_date) || 'Not given' }),
      facilities.length ? el('dt', { text: facilities.length === 1 ? 'Site' : 'Sites' }) : null,
      facilities.length ? el('dd', { text: facilities.join('; ') }) : null),
    el('div', { class: 'trial-links' }, ctgovLink(t.nct_id, t.url), NCT_RE.test(t.nct_id || '') ? moreBtn : null),
    more);
}

function trialDetailNodes(d, { compact: small = false } = {}) {
  const nodes = [];
  const kv = el('dl', { class: 'kv' });
  const add = (k, v) => {
    if (v === null || v === undefined || v === '') return;
    kv.append(el('dt', { text: k }), el('dd', { text: String(v) }));
  };
  if (!small) {
    add('Lead sponsor', d.lead_org_name);
    add('Primary completion', fmtDate(d.primary_completion_date) || 'Not given');
  }
  add('Start', fmtDate(d.start_date));
  if (Number.isFinite(d.enrollment_count)) add('Enrollment', fmt(d.enrollment_count));
  if (Number.isFinite(d.n_sites)) {
    const countries = Number.isFinite(d.n_countries) ? ` in ${fmt(d.n_countries)} ${plural(d.n_countries, 'country', 'countries')}` : '';
    add('Sites', `${fmt(d.n_sites)}${countries}`);
  }
  add('Conditions', d.conditions);
  add('Interventions', d.interventions);
  if (Array.isArray(d.countries) && d.countries.length) {
    const shown = d.countries.slice(0, 8).join(', ');
    add('Countries', d.countries.length > 8 ? `${shown} and ${d.countries.length - 8} more` : shown);
  }
  nodes.push(kv);
  if (d.official_title && d.official_title !== d.title && !small) nodes.push(el('p', { class: 'muted', text: d.official_title }));
  if (d.brief_summary) nodes.push(el('p', { class: 'trial-summary', text: d.brief_summary.length > 900 ? `${d.brief_summary.slice(0, 900)}...` : d.brief_summary }));
  return nodes;
}

async function openTrial(nct) {
  if (!NCT_RE.test(nct)) return;
  rememberOpener();
  const c = { kind: 'trial', nct };
  state.card = c;
  setSelected(null);
  setCardHead('Trial', nct, '');
  $('card-count').textContent = '';
  $('card-body').replaceChildren(el('p', { class: 'loading-line', text: 'Loading the trial' }));
  showCard({ focus: true });
  cardCtrl?.abort();
  const ctrl = new AbortController();
  cardCtrl = ctrl;
  try {
    const d = await api.trial(nct, ctrl.signal);
    if (ctrl.signal.aborted || state.card !== c) return;
    setCardHead(nct, d.title || nct, d.lead_org_name || '');
    const body = $('card-body');
    body.replaceChildren(
      el('div', { class: 'trial' },
        trialTags(d),
        ...trialDetailNodes(d),
        el('div', { class: 'trial-links' }, ctgovLink(d.nct_id || nct, d.url))),
    );
  } catch (err) {
    if (isAbort(err) || ctrl.signal.aborted || state.card !== c) return;
    $('card-body').replaceChildren(el('p', {
      class: 'card-empty',
      text: err?.status === 404 ? 'This trial is not in the data.' : 'Could not load the trial. Try again in a moment.',
    }));
  } finally {
    if (cardCtrl === ctrl) cardCtrl = null;
  }
}

function closeCard() {
  const card = $('card');
  if (card.hidden) return;
  cardCtrl?.abort();
  window.clearTimeout(cardRefreshTimer);
  state.card = null;
  setSelected(null);
  card.hidden = true;
  if (!phoneQuery.matches) syncPadding();
  if (cardOpener && document.contains(cardOpener) && !cardOpener.closest('[hidden]')) cardOpener.focus();
  cardOpener = null;
}

// page wiring

function wireSheet() {
  const sheet = $('sheet');
  const toggle = $('sheet-toggle');
  toggle.addEventListener('click', () => {
    const expanded = !sheet.classList.contains('is-expanded');
    sheet.classList.toggle('is-expanded', expanded);
    toggle.setAttribute('aria-expanded', String(expanded));
    updateFilterChrome();
    if (!expanded) sheet.scrollTop = 0;
  });
}

function wireGlobalKeys() {
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    if (!$('card').hidden) {
      closeCard();
      e.preventDefault();
    } else if ($('sheet').classList.contains('is-expanded')) {
      $('sheet-toggle').click();
      $('sheet-toggle').focus();
      e.preventDefault();
    }
  });
  $('card-close').addEventListener('click', closeCard);
  for (const type of ['pointerdown', 'wheel', 'keydown', 'touchstart']) {
    document.addEventListener(type, onFirstInteraction, { capture: true, passive: true });
  }
}

function wireMediaQueries() {
  const onMotion = () => {
    syncPlayAvailability();
    if (reduceMotion.matches) stopSpin();
  };
  reduceMotion.addEventListener('change', onMotion);
  let resizeTimer = 0;
  window.addEventListener('resize', () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      syncAskPlaceholder();
      placeNavControl();
      if (!map) return;
      // Until someone takes the controls, keep the whole globe framed for the new size.
      if (interacted) map.jumpTo({ padding: cameraPadding() });
      else map.jumpTo({ padding: cameraPadding(), zoom: initialZoom() });
    }, 150);
  });
}

function syncAskPlaceholder() {
  $('ask-input').placeholder = phoneQuery.matches
    ? 'Ask about the trials'
    : 'Try: phase 3 trials in Japan finishing next year';
}

// Keep the time slider and zoom buttons clear of the attribution footer, whose height depends on its text.
const mediumQuery = window.matchMedia('(max-width: 1199px) and (min-width: 641px)');

function placeNavControl() {
  const timebar = $('timebar');
  if (mediumQuery.matches) {
    const footerTop = $('footer').getBoundingClientRect().top;
    timebar.style.bottom = `${Math.round(window.innerHeight - footerTop + 8)}px`;
  } else {
    timebar.style.bottom = '';
  }
  const corner = document.querySelector('.maplibregl-ctrl-bottom-right');
  if (!corner) return;
  if (phoneQuery.matches) {
    corner.style.bottom = '';
    return;
  }
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  let bottom = 16;
  for (const id of ['footer', 'timebar']) {
    const node = $(id);
    if (!node || node.hidden) continue;
    const r = node.getBoundingClientRect();
    if (r.right > vw - 60 && r.height > 0) bottom = Math.max(bottom, Math.round(vh - r.top + 6));
  }
  corner.style.bottom = `${bottom}px`;
  // The detail card ends above the zoom buttons.
  const navTop = corner.getBoundingClientRect().top;
  document.documentElement.style.setProperty('--bottom-reserve', `${Math.round(vh - navTop + 10)}px`);
}

async function init() {
  if (MOCK) {
    $('sample-badge').hidden = false;
    $('footer-sample').hidden = false;
    document.title = 'Readout Globe (sample data)';
  }
  for (const id of ['sum-trials', 'sum-sites', 'sum-points']) $(id).textContent = '...';
  syncAskPlaceholder();
  wireAsk();
  wireStatusShortcut();
  wireSponsor();
  wireSearch();
  wireDue();
  wireReset();
  wireTime();
  wireSheet();
  wireGlobalKeys();
  wireMediaQueries();
  initMap();

  try {
    state.meta = await api.meta();
  } catch (err) {
    $('summary-note').textContent = 'No data yet';
    showPointsError(err);
    return;
  }
  renderMeta(state.meta);
  updateFilterChrome();
  placeNavControl();
  loadPoints();
}

init();
