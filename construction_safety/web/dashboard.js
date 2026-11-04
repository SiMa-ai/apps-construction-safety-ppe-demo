const $ = (id) => document.getElementById(id);
let data = { cameras: [], incidents: [] },
  visible = [];
const cards = new Map();
window.cameraAlias = name => data.cameras.find(c => c.source_id === name)?.alias || '';
window.cameraName = name => window.cameraAlias(name) || name;
window.addEventListener('camera-alias-saved', event => {
  const camera = data.cameras.find(c => c.source_id === event.detail.camera);
  if (camera) camera.alias = event.detail.alias;
  renderCameras(); renderEvents();
  for (const option of $('zoneCamera').options) {
    const c = data.cameras.find(c => c.source_id === option.value);
    if (c) option.textContent = `${window.cameraName(c.source_id)} · ${c.source_id} · Channel ${c.routing?.channel ?? c.channel}`;
  }
});
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

function metricBar(label, value, max, unit, p95) {
  const row = el('div', undefined, 'metric-row');
  const valid = Number.isFinite(value);
  const text = valid ? `${value.toFixed(1)} ${unit}` : 'Unavailable';
  const heading = el('div', undefined, 'metric-heading');
  heading.append(el('span', label), el('b', text));
  const track = el('div', undefined, 'meter-track');
  if (valid) {
    const meter = el('meter');
    meter.min = 0; meter.max = max; meter.value = value;
    meter.setAttribute('aria-label', label);
    meter.setAttribute('aria-valuetext', `${text}${Number.isFinite(p95) ? `; p95 ${p95.toFixed(1)} ${unit}` : ''}`);
    track.append(meter);
    if (Number.isFinite(p95)) {
      const marker = el('span', undefined, 'p95-marker');
      marker.style.insetInlineStart = `${Math.min(100, p95 / max * 100)}%`;
      marker.setAttribute('aria-hidden', 'true');
      track.append(marker);
      heading.append(el('span', `p95 ${p95.toFixed(1)}`, 'p95-value'));
    }
  } else track.append(el('span', 'No measurement yet', 'meter-unavailable'));
  row.append(heading, track);
  return row;
}

function card(name) {
  const n = el('article', undefined, 'camera');
  const head = el('div', undefined, 'camera-head');
  const title = el('h2');
  const badge = el('span', undefined, 'badge');
  const actions = el('div', undefined, 'camera-actions');
  const settings = el('button', undefined, 'channel-settings');
  settings.type = 'button';
  settings.title = 'Video source and safety zones for ' + name;
  settings.setAttribute('aria-label', 'Settings for ' + name);
  settings.innerHTML =
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 3-.6 2.4-2 .9-2.2-.7-2 3.4 1.7 1.7v2.6L2.2 15l2 3.4 2.2-.7 2 .9L9 21h4l.6-2.4 2-.9 2.2.7 2-3.4-1.7-1.7v-2.6L19.8 9l-2-3.4-2.2.7-2-.9L13 3z"/><circle cx="11" cy="12" r="3"/></svg>';
  settings.onclick = () =>
    window.dispatchEvent(new CustomEvent('open-camera-settings', { detail: { camera: name } }));
  actions.append(badge, settings);
  head.append(title, actions);
  const img = el('video');
  img.autoplay = true;
  img.muted = true;
  img.playsInline = true;
  img.setAttribute('aria-label', name + ' annotated live video');
  const videoFrame = el('div', undefined, 'video-frame');
  const expand = el('button', undefined, 'expand-camera');
  expand.setAttribute('aria-label', 'Expand ' + name);
  expand.title = 'Expand camera';
  expand.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg>';
  expand.onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await videoFrame.requestFullscreen();
    } catch (_) { expand.title = 'Fullscreen unavailable in this browser'; }
  };
  const videoMessage = el('div', undefined, 'video-message');
  videoFrame.append(img, videoMessage, expand);
  const stats = el('div', undefined, 'stats');
  const note = el('details', undefined, 'session-details');
  note.append(el('summary', 'Session & audit'));
  const sessionText = el('p');
  const audit = el('a', 'Download incident audit (JSONL)');
  audit.href = 'api/events.jsonl?camera=' + encodeURIComponent(name);
  note.append(sessionText, audit);
  const review = el('div', undefined, 'review-state');
  const performance = el('details', undefined, 'performance');
  performance.append(el('summary', 'Camera details'));
  const metrics = el('div', undefined, 'performance-values');
  const stageDetails = el('details', undefined, 'timing-details');
  stageDetails.append(el('summary', 'All timings & system usage'));
  const stageValues = el('div');
  stageDetails.append(stageValues);
  const download = el('a', 'Download performance history (JSONL)');
  download.href = 'api/performance.jsonl?camera=' + encodeURIComponent(name);
  stageDetails.append(download);
  performance.append(metrics, stageDetails, review, note);
  n.append(head, videoFrame, stats, performance);
  $('cameras').append(n);
  const c = {
    n,
    title,
    badge,
    img,
    videoMessage,
    stats,
    note,
    review,
    metrics,
    stageValues,
    sessionText,
    live: false,
    previewProblem: true,
  };
  cards.set(name, c);
  c.player = new CameraVideo(img, name, (message) => {
    c.previewProblem = message !== 'LIVE';
    c.videoStatus = message;
    c.badge.textContent = c.live ? message : 'Offline';
    updateVideoMessage(c);
  });
  return c;
}

function updateVideoMessage(view) {
  view.videoMessage.hidden = view.live && !view.previewProblem;
  view.videoMessage.textContent = !view.live ? 'Camera offline · check settings'
    : 'Connecting to live video…';
  if (view.live && /RETRY|BLOCKED|UNAVAILABLE|UNSUPPORTED/.test(view.videoStatus || '')) {
    view.videoMessage.textContent = 'Video unavailable · check the camera connection in settings';
  }
}

function renderCameras() {
  const names = data.cameras.map((c) => c.source_id);
  const filter = $('cameraFilter');
  if (JSON.stringify([...filter.options].slice(1).map((o) => o.value)) !== JSON.stringify(names)) {
    const selected = filter.value;
    filter.replaceChildren(new Option('All cameras', ''), ...names.map((n) => new Option(n, n)));
    filter.value = names.includes(selected) ? selected : '';
  }
  const ordered = [...data.cameras].sort((a, b) => (a.routing?.channel ?? a.channel ?? 0) - (b.routing?.channel ?? b.channel ?? 0));
  for (const option of filter.options) if (option.value) option.textContent = window.cameraName(option.value);
  const viewNames = ['', ...ordered.map(c => c.source_id)];
  if (JSON.stringify([...$('cameraViews').children].map(b => b.dataset.camera)) !== JSON.stringify(viewNames)) {
    $('cameraViews').replaceChildren(...viewNames.map(name => {
      const button = el('button', name || 'All cameras');
      button.dataset.camera = name;
      button.onclick = () => { filter.value = name; renderCameras(); renderEvents(); };
      return button;
    }));
  }
  for (const button of $('cameraViews').children) {
    button.setAttribute('aria-pressed', String(button.dataset.camera === filter.value));
    button.textContent = button.dataset.camera ? window.cameraName(button.dataset.camera) : 'All cameras';
  }
  $('cameraCount').textContent = `${data.cameras.filter(c => c.status === 'running' && !c.stale).length} / ${data.cameras.length} cameras online`;
  $('cameras').classList.toggle('single-view', !!filter.value || ordered.length === 1);
  for (const [name, view] of cards) {
    if (!names.includes(name)) { view.player.close(); view.n.remove(); cards.delete(name); }
  }
  for (const camera of ordered) {
    const view = cards.get(camera.source_id) || card(camera.source_id);
    const position = ordered.indexOf(camera);
    if ($('cameras').children[position] !== view.n) $('cameras').insertBefore(view.n, $('cameras').children[position] || null);
    view.live = camera.status === 'running' && !camera.stale;
    view.n.hidden = !!filter.value && filter.value !== camera.source_id;
    view.player.update(camera, view.n.hidden || document.hidden || $('monitorPanel').hidden);
    view.title.textContent = camera.alias ||
      'Channel ' +
      (camera.routing?.channel ?? camera.channel ?? '—') +
      ' · src' +
      (camera.routing?.source ?? camera.source_id.slice(3));
    view.title.title = `${camera.source_id} · Channel ${camera.routing?.channel ?? camera.channel}`;
    view.badge.textContent = view.live
      ? view.previewProblem
        ? view.videoStatus || 'CONNECTING'
        : 'LIVE'
      : 'OFFLINE';
    view.badge.className = 'badge' + (view.live ? '' : ' ppe');
    view.n.dataset.alert = !view.live ? 'offline' : camera.occupancy?.red_zone_in > 0 ? 'danger' : camera.ppe_missing_now > 0 ? 'ppe' : 'neutral';
    updateVideoMessage(view);
    view.stats.replaceChildren();
    for (const [label, value, cls] of [
      ['People', camera.occupancy?.workers_total, ''],
      ['In danger', camera.occupancy?.red_zone_in, 'danger'],
      ['PPE alerts', camera.ppe_missing_now, 'ppe'],
      ['Limited view', (camera.occupancy?.workers_partial ?? 0) + (camera.occupancy?.workers_uncertain ?? 0), ''],
    ]) {
      const stat = el('div', undefined, 'stat ' + (value > 0 ? cls : ''));
      stat.append(el('b', value ?? '—'), el('span', label, 'muted'));
      if (label === 'People') stat.title = 'People detected now, including limited views. Not a lifetime count of unique individuals.';
      if (label === 'Limited view') {
        const partial = camera.occupancy?.workers_partial ?? 0;
        const unclear = camera.occupancy?.workers_uncertain ?? 0;
        const detail = `${partial} partial · ${unclear} uncertain`;
        stat.title = `${detail}. Excluded from incident logging.`;
        if (value > 0) stat.append(el('small', detail, 'muted'));
      }
      if (label === 'In danger' && camera.occupancy?.zone_uncertain > 0) {
        stat.append(el('small', `${camera.occupancy.zone_uncertain} zone uncertain`, 'muted'));
      }
      view.stats.append(stat);
    }
    const review = camera.review || {};
    view.review.replaceChildren();
    if (review.mode === 'demo_once') {
      view.review.append(
        el(
          'span',
          review.complete
            ? `${review.incident_count ?? 0} findings · review complete · replay logging paused`
            : `Collecting this pass · ${Math.min(review.elapsed_s, review.collection_seconds).toFixed(0)} / ${Math.ceil(review.collection_seconds)} s`,
        ),
      );
      const progress = el('progress');
      progress.max = review.collection_seconds;
      progress.value = Math.min(review.elapsed_s, review.collection_seconds);
      progress.setAttribute('aria-label', 'Incident collection progress');
      if (!review.complete) view.review.append(progress);
    } else view.review.textContent = 'Continuous monitoring';
    const perf = camera.performance;
    const fmt = (v, unit) => Number.isFinite(v) ? `${v.toFixed(1)} ${unit}` : '—';
    view.metrics.replaceChildren();
    view.stageValues.replaceChildren();
    if (!perf) view.metrics.append(el('p', 'Performance measurements unavailable for this session.'));
    else {
      view.stageValues.append(el('p', perf.warmup_complete
        ? `${perf.measured_frames} measured frames · ${view.live ? 'Live' : 'Last recorded'} measurements`
        : `Warming up · first ${perf.warmup_frames} frames excluded`));
      const fpsMax = Math.max(30, Math.ceil(Math.max(...data.cameras.map(c => c.performance?.processing_fps || 0), ...[...cards.values()].map(c => c.player.playbackFps || 0)) / 30) * 30);
      const fpsGroup = el('div', undefined, 'meter-group');
      fpsGroup.append(el('p', `Frame rate · 0–${fpsMax} FPS`, 'meter-scale'));
      fpsGroup.append(metricBar('Processing', perf.processing_fps, fpsMax, 'FPS'), metricBar('Playback', view.player.playbackFps, fpsMax, 'FPS'));
      const stages = [['people_detector', 'People model'], ['ppe_detector', 'PPE model'], ['render_preview', 'Rendering']];
      // All camera latency bars use the same scale, including the p95 marker.
      const maxTiming = Math.max(50, ...data.cameras.flatMap(c => stages.map(([k]) => c.performance?.stages?.[k]?.p95_ms || 0)));
      const timingMax = Math.ceil(maxTiming / 50) * 50;
      const latency = el('div', undefined, 'meter-group');
      latency.append(el('p', `Call time · 0–${timingMax} ms`, 'meter-scale'));
      for (const [key, label] of stages) {
        const stage = perf.stages?.[key];
        latency.append(metricBar(label, stage?.mean_ms, timingMax, 'ms', stage?.p95_ms));
      }
      view.metrics.append(fpsGroup, latency);
      view.stageValues.append(el('p', 'Bars: mean · marker: p95 (95% of measured calls are at or below this time). PPE includes sampled calls only.', 'meter-caption'));
      const rows = [
        ['Processing throughput', fmt(perf.processing_fps, 'FPS')],
        ['Browser playback', fmt(view.player.playbackFps, 'FPS')],
      ];
      for (const [key, label] of [
        ['people_detector', 'People detector'], ['ppe_detector', 'PPE detector'],
        ['detector_total', 'Combined detector per frame'], ['analytics', 'Tracking / analytics'],
        ['render_preview', 'Annotations / resize'], ['output_submit', 'Output submission'],
        ['frame_work', 'Total frame work'],
        ['checkpoint', 'Status / config polling'], ['publication', 'Background file writes'], ['control_poll', 'Control polling'],
        ['source_wait', 'Waiting for input'],
      ]) {
        const stage = perf.stages?.[key];
        if (stage) rows.push([label, `${fmt(stage.mean_ms, 'ms')} mean · ${fmt(stage.p95_ms, 'ms')} p95`]);
      }
      rows.push(['Process CPU / RAM', `${fmt(perf.process_cpu_percent, '%')} / ${fmt(perf.process_rss_mb, 'MiB')}`]);
      const list = el('dl');
      for (const [label, value] of rows) list.append(el('dt', label), el('dd', value));
      view.stageValues.append(list, el('p', 'Detector wall time includes preprocessing and decoding. CPU: one core = 100%. Playback FPS is browser-local; end-to-end latency is not measured.', 'muted'));
    }
    view.sessionText.textContent = `Session ${camera.session_id || '—'} · Track IDs are local to this session.`;
    view.img.style.opacity = view.live ? '' : '.4';
  }
  $('modelNote').textContent =
    'Model predictions require review; neutral boxes do not certify PPE compliance.';
  $('newReview').disabled = !data.cameras.length || data.camera_job?.status === 'running';
}

function alertLabel(event) {
  if (event.type === 'danger_zone_entry') return 'Danger-zone entry';
  if (event.type === 'ppe_missing') return `Missing ${event.item || 'PPE'}`;
  return 'Safety alert';
}

function showIncident(event) {
  $('detailTitle').textContent = alertLabel(event);
  $('detailTitle').className = event.type === 'danger_zone_entry' ? 'danger' : 'ppe';
  $('detailCamera').textContent = window.cameraName(event.source_id);
  $('evidence').hidden = true;
  $('evidenceStatus').hidden = false;
  $('evidenceStatus').textContent = 'Loading evidence…';
  $('evidence').onload = () => { $('evidence').hidden = false; $('evidenceStatus').hidden = true; };
  $('evidence').onerror = () => { $('evidence').hidden = true; $('evidenceStatus').textContent = 'Image unavailable. Incident details and JSON evidence are still available below.'; };
  $('evidence').src =
    'api/snapshot?camera=' +
    encodeURIComponent(event.source_id) +
    '&session=' +
    event.session_id +
    '&id=' +
    event.incident_id;
  const facts = el('dl', undefined, 'incident-facts');
  for (const [label, value] of [
    ['Location', event.zone || 'Unspecified location'],
    ['Track IDs', (event.person_ids || [event.person_id]).filter(Boolean).join(', ') || 'Unavailable'],
    ['First observed', new Date(event.timestamp_utc).toLocaleString()],
    ['Observations', String(event.observation_count || 1)],
  ]) {
    const fact = el('div');
    fact.append(el('dt', label), el('dd', value));
    facts.append(fact);
  }
  $('incidentSummary').replaceChildren(facts, el('p', 'IDs identify tracks, not verified unique people. Image shows the first observation.', 'muted'));
  $('record').textContent = JSON.stringify(event, null, 2);
  $('detail').showModal();
}

let lastEventView = '';
function renderEvents() {
  const signature = JSON.stringify([
    data.incidents,
    data.cameras.map(c => [c.source_id, c.alias]),
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
      (!search || [e.pid, e.person_id, ...(e.person_ids || [])].join(' ').toLowerCase().includes(search)),
  );
  $('events').replaceChildren();
  $('findingCount').textContent = `(${visible.length})`;
  const activeFilters = ['cameraFilter', 'typeFilter', 'pidFilter'].filter(id => $(id).value).length;
  $('filterSummary').textContent = activeFilters ? `Filter alerts · ${activeFilters} active` : 'Filter alerts';
  $('empty').hidden = visible.length > 0;
  $('empty').textContent = data.incidents.length ? 'No matching findings. Clear filters to see this review.' : 'No findings recorded yet. Live monitoring will add confirmed observations here.';
  for (const event of visible) {
    const isDanger = event.type === 'danger_zone_entry';
    const row = el('li', undefined, 'alert-item');
    const button = el('button', undefined, 'alert-entry');
    const thumb = el('img', undefined, 'alert-thumb');
    thumb.src = 'api/snapshot?' + new URLSearchParams({camera: event.source_id, session: event.session_id, id: event.incident_id});
    thumb.alt = '';
    thumb.loading = 'lazy';
    thumb.onerror = () => { thumb.hidden = true; };
    const finding = el('span', undefined, 'alert-copy');
    finding.append(el('strong', alertLabel(event), isDanger ? 'danger' : 'ppe'), el('span', event.zone || 'Unspecified location', 'finding-location'));
    const date = new Date(event.timestamp_utc);
    const time = el('span', `${window.cameraName(event.source_id)} · ${new Intl.DateTimeFormat(undefined, {hour: '2-digit', minute: '2-digit'}).format(date)}`, 'alert-meta');
    time.title = date.toLocaleString();
    finding.append(time);
    button.setAttribute('aria-label', `View evidence for ${alertLabel(event).toLowerCase()} on ${window.cameraName(event.source_id)}`);
    button.onclick = () => showIncident(event);
    button.append(finding, thumb);
    row.append(button);
    $('events').append(row);
  }
}

$('newReview').onclick = () => {
  window.dispatchEvent(new CustomEvent('open-camera-settings', {
    detail: {camera: $('cameraFilter').value || data.cameras[0]?.source_id},
  }));
};

let statusFailures = 0;
async function refresh() {
  let delay = 1000;
  try {
    const { r, body } = await timedFetch('api/state', { cache: 'no-store' }, 4000);
    if (!r.ok) throw Error(r.status);
    const fresh = JSON.parse(await body.text());
    data = fresh;
    statusFailures = 0;
    window.dispatchEvent(
      new CustomEvent('camera-job-status', { detail: fresh.camera_job || { status: 'idle' } }),
    );
    renderCameras(); renderEvents();
    $('connection').textContent = 'Connected';
    $('connection').className = '';
  } catch (error) {
    statusFailures++;
    delay = Math.min(4000, 1000 * statusFailures);
    $('connection').textContent =
      statusFailures < 3
        ? 'Status delayed — retrying…'
        : 'Reconnecting · displayed counts may be stale';
    $('connection').className = statusFailures < 3 ? '' : 'bad';
    $('newReview').disabled = true;
  } finally {
    setTimeout(refresh, delay);
  }
}

for (const id of ['cameraFilter', 'typeFilter', 'pidFilter'])
  $(id).addEventListener('input', () => { if (id === 'cameraFilter') renderCameras(); renderEvents(); });
$('clearFilters').onclick = () => { for (const id of ['cameraFilter', 'typeFilter', 'pidFilter']) $(id).value = ''; renderCameras(); renderEvents(); };
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

window.addEventListener('pagehide', () => {
  for (const view of cards.values()) view.player.close();
});
