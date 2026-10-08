/* Camera-scoped polygon editing. Coordinates remain normalized at every canvas size. */
(() => {
  const get = (id) => document.getElementById(id);
  const canvas = get('zoneCanvas'),
    ctx = canvas.getContext('2d');
  const colors = ['#5cdb88', '#ffdf62', '#ffa353', '#ff5565'];
  let zones = [],
    selected = -1,
    draft = null,
    image = null,
    revision = '',
    camera = '',
    dirty = false,
    drag = -1,
    busy = false,
    savedZones = '[]';
  const status = (text) => {
    get('zoneStatus').textContent = text;
  };
  const current = () => draft || zones[selected];
  const changed = () => {
    dirty = JSON.stringify(zones) !== savedZones;
    status(
      draft
        ? 'Add corners around the area, then finish your polygon.'
        : 'Changes are ready to save.',
    );
    draw();
  };

  function feedback() {
    const pending = dirty || !!draft;
    get('editState').textContent = busy
      ? 'Working…'
      : pending
        ? 'Unsaved changes'
        : 'All changes saved';
    get('editState').className = 'save-state' + (pending ? ' dirty' : '');
    get('saveHint').textContent = draft
      ? 'Finish drawing before you save'
      : dirty
        ? 'Ready to apply your changes?'
        : 'Your saved zones are active';
    get('canvasMode').textContent = draft
      ? 'Drawing a new zone'
      : current()
        ? 'Editing · ' + current().name
        : 'Choose an area to monitor';
    get('pointCount').textContent = current()
      ? current().points.length + ' corners'
      : 'Add your first zone';
    get('canvasHint').textContent = draft
      ? 'Click to place corners. At least 3 corners are required.'
      : 'Drag a numbered corner or use Corner coordinates.';
    get('saveZones').disabled = !!draft || busy || !camera || !dirty;
    get('reloadZones').disabled = busy || (!dirty && !draft);
  }

  function draw() {
    feedback();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (image) ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
    [...zones, ...(draft ? [draft] : [])].forEach((zone, index) => {
      if (!zone.points.length) return;
      const active = draft ? zone === draft : index === selected;
      ctx.beginPath();
      zone.points.forEach(([x, y], n) =>
        n
          ? ctx.lineTo(x * canvas.width, y * canvas.height)
          : ctx.moveTo(x * canvas.width, y * canvas.height),
      );
      if (zone !== draft) ctx.closePath();
      ctx.strokeStyle = colors[zone.level];
      ctx.lineWidth = active ? 4 : 2;
      ctx.fillStyle = colors[zone.level] + '25';
      ctx.fill();
      ctx.stroke();
      if (active)
        zone.points.forEach(([x, y], n) => {
          ctx.beginPath();
          ctx.arc(x * canvas.width, y * canvas.height, 7, 0, Math.PI * 2);
          ctx.fillStyle = colors[zone.level];
          ctx.fill();
          ctx.strokeStyle = '#10151d';
          ctx.lineWidth = 2;
          ctx.stroke();
          ctx.font = 'bold 14px system-ui';
          ctx.fillStyle = '#fff';
          ctx.fillText(String(n + 1), x * canvas.width + 9, y * canvas.height - 9);
        });
      const [x, y] = zone.points[0];
      ctx.font = 'bold 16px system-ui';
      ctx.lineWidth = 4;
      ctx.strokeStyle = '#10151d';
      ctx.fillStyle = colors[zone.level];
      ctx.strokeText(zone.name, x * canvas.width + 8, Math.max(20, y * canvas.height - 16));
      ctx.fillText(zone.name, x * canvas.width + 8, Math.max(20, y * canvas.height - 16));
    });
  }

  function fields() {
    const zone = current();
    for (const id of ['zoneCamera', 'refreshFrame', 'reloadZones', 'zoneList'])
      get(id).disabled = busy;
    get('zoneName').value = zone?.name || '';
    get('zoneLevel').value = String(zone?.level ?? 3);
    get('zoneRestricted').checked = zone?.restricted ?? true;
    for (const id of ['zoneName', 'zoneLevel', 'zoneRestricted', 'undoPoint'])
      get(id).disabled = !zone || busy;
    get('finishZone').disabled = !draft || draft.points.length < 3 || busy;
    get('cancelPolygon').disabled = !draft || busy;
    get('deleteZone').disabled = !!draft || selected < 0 || busy;
    get('addZone').disabled = !!draft || !image || busy;
    get('undoPoint').hidden = !draft;
    get('finishZone').hidden = !draft;
    get('cancelPolygon').hidden = !draft;
    get('deleteZone').hidden = !!draft || selected < 0;
    for (const button of get('zoneCards').querySelectorAll('button'))
      button.disabled = busy || !!draft;
    cornerFields();
    feedback();
  }

  function list() {
    get('zoneList').replaceChildren(...zones.map((z, i) => new Option(z.name, i)));
    get('zoneList').value = String(selected);
    get('zoneCount').textContent = zones.length;
    get('zoneCountLabel').textContent = `${zones.length} ${zones.length === 1 ? 'zone' : 'zones'}`;
    get('zoneCards').replaceChildren();
    zones.forEach((zone, index) => {
      const button = document.createElement('button');
      button.className = 'zone-card';
      button.setAttribute('aria-pressed', String(index === selected && !draft));
      const dot = document.createElement('span');
      dot.className = 'zone-dot';
      dot.style.background = colors[zone.level];
      const label = document.createElement('span');
      label.className = 'zone-label';
      label.textContent = zone.name + ' · ' + ['Permitted', 'Caution', 'Warning', 'Danger'][zone.level];
      button.title = zone.name + ' · ' + zone.points.length + ' corners';
      button.append(dot, label);
      button.onclick = () => {
        if (!draft) {
          selected = index;
          list();
          status('Drag a numbered corner to adjust this zone.');
        }
      };
      get('zoneCards').append(button);
    });
    if (!zones.length) {
      const empty = document.createElement('p');
      empty.className = 'muted';
      empty.textContent = 'No zones yet. Add a zone to mark an area.';
      get('zoneCards').append(empty);
    }
    fields();
    draw();
  }

  async function refreshImage() {
    const response = await fetch('api/editor-frame?camera=' + encodeURIComponent(camera), {
      cache: 'no-store',
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok)
      throw Error('No camera image available. Start the camera, then Refresh image.');
    const url = URL.createObjectURL(await response.blob());
    const next = new Image();
    next.src = url;
    try {
      await next.decode();
    } finally {
      URL.revokeObjectURL(url);
    }
    image = next;
    canvas.width = next.naturalWidth;
    canvas.height = next.naturalHeight;
    draw();
    fields();
  }

  async function load(name) {
    busy = true;
    fields();
    try {
      const response = await fetch('api/zones?camera=' + encodeURIComponent(name), {
        cache: 'no-store',
        signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) throw Error('Could not load camera zones');
      const result = await response.json();
      camera = name;
      revision = result.revision;
      zones = result.zones;
      savedZones = JSON.stringify(zones);
      selected = zones.length ? 0 : -1;
      draft = null;
      dirty = false;
      image = null;
      list();
      await refreshImage();
      status('Select a zone to drag its corners, or choose Add zone.');
    } catch (error) {
      status(`${error.message}. Check the connection and try again; unsaved edits remain in this editor.`);
    } finally {
      busy = false;
      list();
    }
  }

  async function openSettings(name) {
    if (busy) return;
    if (
      name &&
      camera &&
      name !== camera &&
      (dirty || draft) &&
      !confirm('Discard unsaved zone edits and switch camera?')
    )
      return;
    if (!get('settingsPanel').open) {
      showSection('video');
      get('settingsPanel').showModal();
      document.body.classList.add('settings-open');
    }
    if (!camera || (name && name !== camera)) {
      busy = true;
      fields();
      try {
        const response = await fetch('api/state', { signal: AbortSignal.timeout(5000) });
        if (!response.ok) throw Error('Could not load cameras');
        const data = await response.json();
        get('zoneCamera').replaceChildren(
          ...data.cameras.map(
            (c) =>
              new Option(
                `${c.source_id} · Channel ${c.routing?.channel ?? c.channel ?? '—'}`,
                c.source_id,
              ),
          ),
        );
        if (name) get('zoneCamera').value = name;
        if (!get('zoneCamera').value) throw Error('Camera is no longer available');
        await load(get('zoneCamera').value);
      } catch (error) {
        status(`${error.message}. Check the connection and try again; unsaved edits remain in this editor.`);
      } finally {
        busy = false;
        fields();
      }
    }
    window.dispatchEvent(new CustomEvent('camera-settings-opened', { detail: { camera } }));
    get('zoneCamera').focus();
  }
  window.zoneEditorHasChanges = () => dirty || !!draft;
  window.addEventListener('camera-routing-applied', (event) => {
    if (event.detail.camera === camera) load(camera);
  });
  window.addEventListener('open-camera-settings', (event) => openSettings(event.detail.camera));
  get('monitorTab').onclick = () => {
    get('overviewHeading').focus();
  };
  function showSection(section) {
    const isVideo = section === 'video';
    get('cameraRoutingPanel').hidden = !isVideo;
    get('cameraZonesPanel').hidden = isVideo;
    get('videoSettingsTab').setAttribute('aria-pressed', String(isVideo));
    get('zoneSettingsTab').setAttribute('aria-pressed', String(!isVideo));
    get('settingsPanel').classList.toggle('editing-zones', !isVideo);
  }
  get('videoSettingsTab').onclick = () => showSection('video');
  get('zoneSettingsTab').onclick = () => showSection('zones');
  function closeSettings() {
    if (busy) return;
    if ((dirty || draft) && !confirm('Discard unsaved zone edits and close settings?')) return;
    if (dirty || draft) {
      zones = JSON.parse(savedZones);
      dirty = false;
      draft = null;
      selected = zones.length ? 0 : -1;
      list();
    }
    get('settingsPanel').close();
    document.body.classList.remove('settings-open');
  }
  get('closeSettings').onclick = closeSettings;
  get('settingsPanel').addEventListener('cancel', event => {
    event.preventDefault();
    if (draft) { get('cancelPolygon').click(); return; }
    closeSettings();
  });
  get('zoneCamera').onchange = () => {
    if ((dirty || draft) && !confirm('Discard unsaved zone edits and switch camera?')) {
      get('zoneCamera').value = camera;
      return;
    }
    load(get('zoneCamera').value).then(() =>
      window.dispatchEvent(new CustomEvent('camera-settings-opened', { detail: { camera } })),
    );
  };
  get('refreshFrame').onclick = () => refreshImage().catch((error) => status(error.message));
  get('reloadZones').onclick = () => {
    if ((!dirty && !draft) || confirm('Discard unsaved zone edits?')) load(camera);
  };
  get('zoneList').onchange = () => {
    if (draft) {
      status('Finish or cancel the new polygon first.');
      get('zoneList').value = String(selected);
      return;
    }
    selected = Number(get('zoneList').value);
    fields();
    draw();
  };
  get('addZone').onclick = () => {
    let number = zones.length + 1;
    while (zones.some((z) => z.name === `Zone ${number}`)) number++;
    draft = { name: `Zone ${number}`, level: 3, restricted: true, points: [] };
    list();
    canvas.focus();
    status('Place corners on the image or use Corner coordinates, then Finish polygon.');
  };
  get('finishZone').onclick = () => {
    if (!draft || draft.points.length < 3) return;
    zones.push(draft);
    selected = zones.length - 1;
    draft = null;
    changed();
    list();
  };
  get('cancelPolygon').onclick = () => {
    draft = null;
    list();
    status(dirty ? 'Unsaved changes' : 'New polygon cancelled');
  };
  get('deleteZone').onclick = () => {
    const zone = zones[selected];
    if (zone && confirm(`Delete “${zone.name}”? The zone will remain active until you save.`)) {
      zones.splice(selected, 1);
      selected = zones.length ? 0 : -1;
      changed();
      list();
    }
  };
  get('undoPoint').onclick = () => {
    if (current()?.points.length) {
      current().points.pop();
      changed();
      fields();
    }
  };
  get('zoneName').oninput = () => {
    if (current()) {
      current().name = get('zoneName').value;
      changed();
    }
  };
  get('zoneName').onchange = list;
  get('zoneLevel').onchange = () => {
    if (current()) {
      current().level = Number(get('zoneLevel').value);
      current().restricted = current().level === 3;
      changed();
      fields();
    }
  };
  get('zoneRestricted').onchange = () => {
    if (current()) {
      current().restricted = get('zoneRestricted').checked;
      changed();
    }
  };

  function cornerFields() {
    const zone = current(), select = get('cornerSelect');
    const previous = Number(select.value) || 0;
    select.replaceChildren(...(zone?.points || []).map((_, i) => new Option(`Corner ${i + 1}`, i)));
    select.value = String(Math.min(previous, Math.max(0, (zone?.points.length || 1) - 1)));
    select.disabled = busy || !zone?.points.length;
    get('applyCorner').disabled = select.disabled;
    get('addCorner').disabled = busy || !zone || zone.points.length >= 32;
    get('cornerX').disabled = get('cornerY').disabled = busy || !zone;
    cornerValues();
  }
  function cornerValues() {
    const p = current()?.points[Number(get('cornerSelect').value)];
    if (p) {
      get('cornerX').value = (p[0] * 100).toFixed(1);
      get('cornerY').value = (p[1] * 100).toFixed(1);
    }
  }
  get('cornerSelect').onchange = cornerValues;
  function setCorner(add) {
    const zone = current();
    if (busy || !zone) return;
    for (const id of ['cornerX', 'cornerY']) {
      const input = get(id);
      input.required = true;
      if (!input.reportValidity()) return;
    }
    const point = [Number(get('cornerX').value) / 100, Number(get('cornerY').value) / 100];
    if (add) {
      if (zone.points.length >= 32) return;
      zone.points.push(point);
    } else zone.points[Number(get('cornerSelect').value)] = point;
    changed();
    fields();
    status('Corner updated. Save and apply zones when finished.');
  }
  get('applyCorner').onclick = () => setCorner(false);
  get('addCorner').onclick = () => setCorner(true);

  function point(event) {
    const rect = canvas.getBoundingClientRect();
    return [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ];
  }
  canvas.onpointerdown = (event) => {
    if (busy || !image) return;
    const p = point(event),
      zone = current();
    if (draft) {
      if (draft.points.length >= 32) {
        status('Maximum 32 corners per polygon.');
        return;
      }
      draft.points.push(p);
      changed();
      fields();
      return;
    }
    if (!zone) return;
    const rect = canvas.getBoundingClientRect();
    drag = zone.points.findIndex(
      ([x, y]) => Math.hypot((x - p[0]) * rect.width, (y - p[1]) * rect.height) < 14,
    );
    if (drag >= 0) canvas.setPointerCapture(event.pointerId);
  };
  canvas.onpointermove = (event) => {
    if (drag >= 0 && current()) {
      current().points[drag] = point(event);
      changed();
    }
  };
  canvas.onpointerup = canvas.onpointercancel = () => {
    drag = -1;
    cornerFields();
  };
  get('saveZones').onclick = async () => {
    if (draft) return;
    busy = true;
    fields();
    try {
      const response = await fetch('api/zones', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera, revision, zones }),
        signal: AbortSignal.timeout(5000),
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Could not save zones');
      revision = result.revision;
      dirty = false;
      savedZones = JSON.stringify(zones);
      status('Saved. Waiting for the running camera to apply the layout…');
      let applied = false;
      for (let n = 0; n < 8; n++) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        const r = await fetch('api/state', {
          cache: 'no-store',
          signal: AbortSignal.timeout(3000),
        });
        const s = (await r.json()).cameras.find((c) => c.source_id === camera);
        if (s?.zones_error) throw Error('Saved, but camera rejected zones: ' + s.zones_error);
        if (s?.zones_revision === revision && !s.stale && s.status === 'running') {
          applied = true;
          break;
        }
      }
      status(
        applied
          ? 'Saved and applied live. Start a new review to collect findings with these zones.'
          : 'Saved to configuration. The layout will apply when this camera is running.',
      );
    } catch (error) {
      status(`${error.message}. Check the connection and try again; unsaved edits remain in this editor.`);
    } finally {
      busy = false;
      fields();
    }
  };
  canvas.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && draft) {
      event.preventDefault();
      get('cancelPolygon').click();
    }
    if (event.key === 'Enter' && draft && draft.points.length >= 3) {
      event.preventDefault();
      get('finishZone').click();
    }
    if ((event.key === 'Backspace' || event.key === 'Delete') && draft) {
      event.preventDefault();
      get('undoPoint').click();
    }
  });
  window.addEventListener('beforeunload', (event) => {
    if (dirty || draft) {
      event.preventDefault();
      event.returnValue = '';
    }
  });
  fields();
})();
