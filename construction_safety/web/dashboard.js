const $ = (id) => document.getElementById(id);
let data = { cameras: [], incidents: [] },
  visible = [];
const cards = new Map();
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
async function timedFetch(url, options = {}, timeout = 4000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const r = await fetch(url, { ...options, signal: controller.signal });
    const body = await r.blob();
    return { r, body };
  } finally {
    clearTimeout(timer);
  }
}
async function previewLoop(name, c) {
  if (!c.n.isConnected) return;
  if (document.hidden || document.getElementById('monitorPanel').hidden || !c.live) {
    setTimeout(() => previewLoop(name, c), 1000);
    return;
  }
  const start = performance.now();
  let delay = 100;
  try {
    const { r, body } = await timedFetch(
      'api/preview?camera=' + encodeURIComponent(name),
      { cache: 'no-store', headers: c.etag ? { 'If-None-Match': c.etag } : {} },
      3000,
    );
    if (r.status !== 304 && !r.ok) throw Error(r.status);
    const stamp = Number(r.headers.get('X-Frame-Time'));
    if (stamp && Date.now() / 1000 - stamp > 5) throw Error('stale preview');
    if (r.status !== 304) {
      const url = URL.createObjectURL(body);
      const next = new Image();
      next.src = url;
      try {
        await next.decode();
      } catch (e) {
        URL.revokeObjectURL(url);
        throw e;
      }
      const old = c.objectURL;
      c.img.src = url;
      c.objectURL = url;
      c.etag = r.headers.get('ETag');
      if (old) URL.revokeObjectURL(old);
    }
    c.img.style.opacity = '1';
    c.previewProblem = false;
  } catch (e) {
    c.previewProblem = true;
    c.badge.textContent = 'PREVIEW RETRY';
    c.badge.className = 'badge ppe';
    c.img.style.opacity = '.65';
    delay = 1000;
  }
  setTimeout(() => previewLoop(name, c), Math.max(100, delay - (performance.now() - start)));
}

function card(name) {
  const n = el('article', undefined, 'camera');
  const head = el('div', undefined, 'camera-head');
  const title = el('h2');
  const badge = el('span', undefined, 'badge');
  const actions = el('div', undefined, 'camera-actions');
  const settings = el('button', undefined, 'channel-settings');
  settings.type = 'button';
  settings.title = 'Edit zones for ' + name;
  settings.setAttribute('aria-label', 'Settings for ' + name);
  settings.innerHTML =
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 3-.6 2.4-2 .9-2.2-.7-2 3.4 1.7 1.7v2.6L2.2 15l2 3.4 2.2-.7 2 .9L9 21h4l.6-2.4 2-.9 2.2.7 2-3.4-1.7-1.7v-2.6L19.8 9l-2-3.4-2.2.7-2-.9L13 3z"/><circle cx="11" cy="12" r="3"/></svg>';
  settings.onclick = () =>
    window.dispatchEvent(new CustomEvent('open-camera-settings', { detail: { camera: name } }));
  actions.append(badge, settings);
  head.append(title, actions);
  const img = el('img');
  img.alt = name + ' annotated camera preview';
  const stats = el('div', undefined, 'stats');
  const note = el('div', undefined, 'stats muted');
  const review = el('div', undefined, 'review-state');
  n.append(head, img, stats, review, note);
  $('cameras').append(n);
  const c = {
    n,
    title,
    badge,
    img,
    stats,
    note,
    review,
    live: false,
    etag: null,
    objectURL: null,
    previewProblem: false,
  };
  cards.set(name, c);
  setTimeout(() => previewLoop(name, c), 0);
  return c;
}

function renderCameras() {
  const names = data.cameras.map((c) => c.source_id);
  const filter = $('cameraFilter');
  if (JSON.stringify([...filter.options].slice(1).map((o) => o.value)) !== JSON.stringify(names)) {
    const selected = filter.value;
    filter.replaceChildren(new Option('All cameras', ''), ...names.map((n) => new Option(n, n)));
    filter.value = names.includes(selected) ? selected : '';
  }
  for (const camera of data.cameras) {
    const view = cards.get(camera.source_id) || card(camera.source_id);
    view.n.style.order = camera.routing?.channel ?? camera.channel ?? 0;
    view.live = camera.status === 'running' && !camera.stale;
    view.title.textContent =
      'Channel ' +
      (camera.routing?.channel ?? camera.channel ?? '—') +
      ' · src' +
      (camera.routing?.source ?? camera.source_id.slice(3));
    view.badge.textContent = view.live
      ? view.previewProblem
        ? 'PREVIEW RETRY'
        : 'LIVE'
      : 'OFFLINE';
    view.badge.className = 'badge' + (view.live ? '' : ' danger');
    view.stats.replaceChildren();
    for (const [label, value, cls] of [
      ['People now', camera.occupancy?.workers_total, ''],
      ['In danger now', camera.occupancy?.red_zone_in, 'danger'],
      ['PPE alerts now', camera.ppe_missing_now, 'ppe'],
      ['Review findings', camera.review?.incident_count, ''],
    ]) {
      const stat = el('div', undefined, 'stat ' + cls);
      stat.append(el('b', value ?? '—'), el('span', label, 'muted'));
      view.stats.append(stat);
    }
    const review = camera.review || {};
    view.review.replaceChildren();
    if (review.mode === 'demo_once') {
      view.review.append(
        el(
          'span',
          review.complete
            ? 'Review complete · replay logging paused'
            : `Collecting this pass · ${Math.min(review.elapsed_s, review.collection_seconds).toFixed(0)} / ${Math.ceil(review.collection_seconds)} s`,
        ),
      );
      const progress = el('progress');
      progress.max = review.collection_seconds;
      progress.value = Math.min(review.elapsed_s, review.collection_seconds);
      progress.setAttribute('aria-label', 'Incident collection progress');
      view.review.append(progress);
    } else view.review.textContent = 'Continuous monitoring';
    view.note.replaceChildren();
    const details = el('details');
    details.append(el('summary', 'Session details'));
    details.append(
      el(
        'p',
        `Session ${camera.session_id || '—'} · ${Math.round(camera.mean_inference_ms || 0)} ms inference`,
      ),
    );
    const audit = el('a', 'Download JSONL audit');
    audit.href = 'api/events.jsonl?camera=' + encodeURIComponent(camera.source_id);
    details.append(audit);
    view.note.append(details);
    if (!view.live) view.img.style.opacity = '.4';
  }
  $('modelNote').textContent =
    'Model predictions require review; neutral boxes do not certify PPE compliance.';
  $('newReview').disabled =
    resetPending ||
    !data.cameras.every(
      (c) => c.status === 'running' && !c.stale && c.review?.mode === 'demo_once',
    );
}

function showIncident(event) {
  $('detailTitle').textContent =
    event.source_id +
    ' · ' +
    (event.type === 'danger_zone_entry' ? 'Danger-zone finding' : 'PPE finding');
  $('evidence').src =
    'api/snapshot?camera=' +
    encodeURIComponent(event.source_id) +
    '&session=' +
    event.session_id +
    '&id=' +
    event.incident_id;
  $('incidentSummary').textContent =
    `${event.zone} · ${(event.person_ids || [event.person_id]).join(', ')} · ${event.observation_count || 1} confirmed observations. Image shows the first observation.`;
  $('record').textContent = JSON.stringify(event, null, 2);
  $('detail').showModal();
}

let lastEventView = '';
function renderEvents() {
  const signature = JSON.stringify([
    data.incidents,
    $('cameraFilter').value,
    $('typeFilter').value,
    $('pidFilter').value,
  ]);
  if (signature === lastEventView) return;
  lastEventView = signature;
  const search = $('pidFilter').value.toLowerCase();
  visible = data.incidents.filter(
    (e) =>
      (!$('cameraFilter').value || e.source_id === $('cameraFilter').value) &&
      (!$('typeFilter').value || e.type === $('typeFilter').value) &&
      (!search || [e.pid, ...(e.person_ids || [])].join(' ').toLowerCase().includes(search)),
  );
  $('events').replaceChildren();
  $('findingCount').textContent = `(${visible.length})`;
  $('empty').hidden = visible.length > 0;
  $('empty').textContent = 'No matching findings in this review.';
  for (const event of visible) {
    const row = el('tr');
    row.append(
      el('td', event.timestamp_utc.replace('T', ' ').slice(0, 19)),
      el('td', event.source_id),
    );
    const people = el('td');
    const ids = event.person_ids || [event.person_id];
    people.append(
      el('code', ids.slice(0, 3).join(', ') + (ids.length > 3 ? ` +${ids.length - 3}` : '')),
      el('div', `${event.observation_count || 1} observations`, 'muted'),
    );
    people.title = ids.join(', ');
    row.append(people);
    const type = el('td');
    type.append(
      el(
        'span',
        event.type === 'danger_zone_entry' ? 'DANGER ZONE' : 'MISSING PPE',
        'badge ' + (event.type === 'danger_zone_entry' ? 'danger' : 'ppe'),
      ),
    );
    row.append(type, el('td', event.zone + (event.item ? ' · ' + event.item : '')));
    const evidence = el('td');
    const button = el('button', 'Review');
    button.onclick = () => showIncident(event);
    evidence.append(button);
    row.append(evidence);
    $('events').append(row);
  }
}

let resetPending = false;
$('newReview').onclick = async () => {
  resetPending = true;
  $('newReview').disabled = true;
  try {
    const { r, body } = await timedFetch('api/review/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cameras: data.cameras.map((c) => c.source_id) }),
    });
    const result = JSON.parse(await body.text());
    if (!r.ok) throw Error(result.error || 'Reset failed');
    $('reviewMessage').textContent = 'New review requested. Previous findings have been preserved.';
  } catch (error) {
    $('reviewMessage').textContent = error.message;
  } finally {
    resetPending = false;
  }
};

let statusFailures = 0;
async function refresh() {
  let delay = 1000;
  try {
    const { r, body } = await timedFetch('api/state', { cache: 'no-store' }, 4000);
    if (!r.ok) throw Error(r.status);
    data = JSON.parse(await body.text());
    statusFailures = 0;
    window.dispatchEvent(
      new CustomEvent('camera-job-status', { detail: data.camera_job || { status: 'idle' } }),
    );
    renderCameras();
    renderEvents();
    $('connection').textContent = 'Connected · ' + new Date().toLocaleTimeString();
    $('connection').className = '';
  } catch (error) {
    statusFailures++;
    delay = Math.min(4000, 1000 * statusFailures);
    $('connection').textContent =
      statusFailures < 3
        ? 'Status delayed — retrying…'
        : 'Reconnecting · displayed counts may be stale';
    $('connection').className = statusFailures < 3 ? '' : 'bad';
  } finally {
    setTimeout(refresh, delay);
  }
}

for (const id of ['cameraFilter', 'typeFilter', 'pidFilter'])
  $(id).addEventListener('input', renderEvents);
$('closeDetail').onclick = () => $('detail').close();
$('export').onclick = () => {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(visible, null, 2)], { type: 'application/json' }),
  );
  const link = el('a');
  link.href = url;
  link.download = 'site-review-findings.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
refresh();
