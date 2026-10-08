/* Keep Insight traffic on the server and never restart a camera on field change. */
(() => {
  const get = (id) => document.getElementById(id);
  let camera = '',
    state = null,
    loading = false,
    working = false,
    requesting = false,
    generation = 0;
  const status = (text) => {
    get('sourceStatus').textContent = text;
  };
  async function api(payload) {
    const response = await fetch('api/camera-settings', {
      cache: 'no-store',
      signal: AbortSignal.timeout(35000),
      ...(payload
        ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          }
        : {}),
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.error || 'Camera settings unavailable');
    return data;
  }
  function update() {
    const disabled = loading || working || !state;
    for (const panel of document.querySelectorAll('.zone-layout,.editor-footer'))
      panel.inert = working;
    get('zoneCamera').disabled = working;
    get('refreshFrame').disabled = working;
    for (const id of [
      'sourceVideo',
      'sourceSlot',
      'sourceChannel',
      'applySource',
      'stopSource',
      'refreshSources',
      'resetSourceZones',
    ])
      get(id).disabled = disabled;
    if (!state) return;
    const route = state.routes[camera];
    const source = state.sources.find((s) => s.index === route.source);
    const changedVideo = get('sourceVideo').value !== (route.video ?? source.file);
    get('resetZonesRow').hidden = !changedVideo;
    get('applySource').disabled =
      disabled || !get('sourceVideo').value || (changedVideo && !get('resetSourceZones').checked);
    const occupied = Object.entries(state.routes).find(
      ([n, r]) => n !== camera && r.channel === Number(get('sourceChannel').value),
    );
    get('sourceRouteHint').textContent =
      `Video: ${get('sourceVideo').value || 'none selected'}. src${get('sourceSlot').value} → Channel ${get('sourceChannel').value}. ` +
      (occupied
        ? `This swaps display positions with ${occupied[0]}; its analysis will also restart if running.`
        : 'Channels appear in numeric order on the Overview.');
  }
  async function load(name) {
    const token = ++generation;
    camera = name;
    get('streamAlias').value = window.cameraAlias(name);
    get('aliasStatus').textContent = '';
    state = null;
    loading = true;
    update();
    status('Connecting to Insight…');
    try {
      const result = await api();
      if (token !== generation) return;
      state = result;
      working = state.job.status === 'running';
      for (const option of get('zoneCamera').options) {
        const r = state.routes[option.value];
        if (r) option.textContent = `${window.cameraName(option.value)} · ${option.value} · Channel ${r.channel}`;
      }
      const route = state.routes[camera];
      if (!route) throw Error('Camera is no longer available');
      const current = state.sources.find((s) => s.index === route.source);
      get('sourceVideo').replaceChildren(
        new Option('Choose a video…', ''),
        ...state.videos.map((v) => new Option(v, v)),
      );
      get('sourceVideo').value = current.file;
      get('sourceSlot').replaceChildren(
        ...state.sources.map((s) => {
          const option = new Option(`src${s.index} · ${s.state}`, s.index);
          option.disabled = Object.entries(state.routes).some(
            ([n, r]) => n !== camera && r.source === s.index,
          );
          return option;
        }),
      );
      get('sourceSlot').value = route.source;
      get('sourceChannel').replaceChildren(
        ...state.channels.map((c) => new Option(`Channel ${c}`, c)),
      );
      get('sourceChannel').value = route.channel;
      get('resetSourceZones').checked = false;
      get('sourceSummary').textContent =
        `${current.file || 'No video'} · Source ${route.source} → Channel ${route.channel} · ${current.state}`;
      status('');
    } catch (error) {
      if (token === generation) {
        state = null;
        status(`${error.message}. Check the Insight connection, then select Refresh library to try again.`);
      }
    } finally {
      if (token === generation) {
        loading = false;
        update();
        get('refreshSources').disabled = working;
      }
    }
  }
  async function apply(action) {
    if (!state || working) return;
    if (window.zoneEditorHasChanges()) {
      status('Save or discard your zone edits before changing the camera.');
      return;
    }
    working = true;
    requesting = true;
    update();
    status('Requesting camera change…');
    const route = state.routes[camera];
    try {
      const job = await api({
        camera,
        revision: state.revision,
        action,
        source: action === 'stop' ? route.source : Number(get('sourceSlot').value),
        channel: action === 'stop' ? route.channel : Number(get('sourceChannel').value),
        video: get('sourceVideo').value,
        reset_zones: get('resetSourceZones').checked,
      });
      status(job.message);
    } catch (error) {
      working = false;
      update();
      status(`${error.message}. Check the Insight connection, then select Refresh library to try again.`);
    } finally {
      requesting = false;
    }
  }
  // Reuse the dashboard's lightweight status poll; no Insight request on each tick.
  let completed = '';
  window.addEventListener('camera-job-status', async (event) => {
    if (!camera || requesting) return;
    const job = event.detail;
    if (job.status === 'running') {
      working = true;
      update();
      status(job.message);
      return;
    }
    if (working && !loading && job.id && job.id !== completed) {
      completed = job.id;
      working = false;
      await load(camera);
      status(job.message);
      if (job.status === 'done')
        window.dispatchEvent(new CustomEvent('camera-routing-applied', { detail: { camera } }));
    }
  });
  window.addEventListener('camera-settings-opened', (event) => load(event.detail.camera));
  for (const id of ['sourceVideo', 'sourceSlot', 'sourceChannel', 'resetSourceZones'])
    get(id).onchange = update;
  get('refreshSources').onclick = () => load(camera);
  get('applySource').onclick = () => apply('apply');
  get('stopSource').onclick = () => apply('stop');
  get('saveAlias').onclick = async () => {
    const target = camera;
    get('saveAlias').disabled = true;
    get('aliasStatus').textContent = 'Saving…';
    try {
      const result = await api({action: 'alias', camera: target, alias: get('streamAlias').value});
      window.dispatchEvent(new CustomEvent('camera-alias-saved', {detail: result}));
      if (camera === target) {
        get('streamAlias').value = result.alias;
        get('aliasStatus').textContent = 'Name saved';
      }
    } catch (error) {
      if (camera === target) get('aliasStatus').textContent = error.message;
    } finally { get('saveAlias').disabled = false; }
  };
})();
