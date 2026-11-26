/* Vail snow forecast map. Reads the static files written by `vailsnow forecast`. */
(() => {
  const RAMP = ['#86b6ef', '#659fe6', '#4689dc', '#2f74cb', '#2260b0', '#184c92', '#103a74', '#082853'];
  const DEPTH = ['#d9f0d3', '#b3dfb8', '#86c99b', '#5cb182', '#3a976b', '#237a55', '#125d40', '#06402b'];      // base depth: thin ... deep
  const DENSITY = ['#b5562a', '#dba27f', '#cfcdc6', '#86b6ef', '#2a78d6'];           // heavy ... blower
  const LAYERS = [['fallen', 'New snow'], ['settled', 'Settled'], ['density', 'Density']];
  const RANGES = [['low', 'Low end'], ['fallen', 'Likely'], ['high', 'High end']];
  const USGS = n => `https://basemap.nationalmap.gov/arcgis/rest/services/${n}/MapServer/tile/{z}/{y}/{x}`;
  const $ = id => document.getElementById(id);
  // Where the forecast files live: beside the page by default, or wherever the host page says (window.VAILSNOW_DATA).
  const DATA = (window.VAILSNOW_DATA || 'data').replace(/\/+$/, '');
  const qs = new URLSearchParams(location.search);
  const state = { run: qs.get('run'), win: '72', layer: 'fallen', range: 'fallen', frame: null, tab: 'zones', zone: null, point: null, newest: true, day: 0 };
  let meta, zones, why, grid, cellIndex, zoneGeo, markers = [], timer = null, score = null, alertInfo = null, outlook = null, ogrid = null, oIndex = null, depth = null, dgrid = null, dIndex = null, gaugeMarker = null;
  const HOME = [-106.355, 39.606];

  const half = v => Math.round(Math.max(v, 0) * 2) / 2, hf = v => String(half(v));          // snow to the half inch
  const rng = (lo, hi) => (half(lo) === half(hi) ? hf(lo) : `${hf(lo)}\u2013${hf(hi)}`);
  const fmt = v => (v >= 10 ? Math.round(v) : Math.round(v * 10) / 10).toString();
  const json = p => fetch(p, { cache: 'no-cache' }).then(r => { if (!r.ok) throw new Error(p); return r.json(); });
  const pad = n => String(n).padStart(2, '0');
  const optional = p => fetch(p, { cache: 'no-cache' }).then(r => (r.ok ? r.json() : null)).catch(() => null);      // files older forecasts do not have
  const day = (iso, opt) => new Date(iso + 'Z').toLocaleString('en-US', Object.assign({ timeZone: 'America/Denver' }, opt));

  const map = new maplibregl.Map({
    container: 'map', center: HOME, zoom: 12.2, maxZoom: 16, minZoom: 9, maxPitch: 70, attributionControl: { compact: true },
    style: {
      version: 8, glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
      sources: {
        relief: { type: 'raster', tiles: [USGS('USGSShadedReliefOnly')], tileSize: 256, maxzoom: 15, attribution: 'USGS' },
        topo: { type: 'raster', tiles: [USGS('USGSTopo')], tileSize: 256, maxzoom: 15, attribution: 'USGS' },
        sat: { type: 'raster', tiles: [USGS('USGSImageryOnly')], tileSize: 256, maxzoom: 15, attribution: 'USGS' },
        dem: { type: 'raster-dem', tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'], encoding: 'terrarium', tileSize: 256, maxzoom: 14,
          attribution: 'Terrain tiles: Mapzen, USGS and others' },
        snow: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } },
        zones: { type: 'geojson', data: 'static/zones.geojson' },
        lifts: { type: 'geojson', data: 'static/lifts.geojson' },
        trails: { type: 'geojson', data: 'static/trails.geojson' },
        pin: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } }
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': '#dfe4e6' } },
        { id: 'topo', type: 'raster', source: 'topo', paint: { 'raster-opacity': 0.55, 'raster-saturation': -0.6 } },
        { id: 'relief', type: 'raster', source: 'relief', paint: { 'raster-opacity': 0.5 } },
        { id: 'sat', type: 'raster', source: 'sat', layout: { visibility: 'none' } },
        { id: 'snow-fill', type: 'fill', source: 'snow', filter: ['==', ['get', 'kind'], 'band'], paint: { 'fill-opacity': 0.66, 'fill-antialias': false, 'fill-color': '#86b6ef' } },
        { id: 'snow-line', type: 'line', source: 'snow', filter: ['==', ['get', 'kind'], 'line'], paint: { 'line-color': '#ffffff', 'line-width': 0.9, 'line-opacity': 0.85 } },
        { id: 'trails', type: 'line', source: 'trails', minzoom: 12.5, paint: { 'line-color': '#0b1622', 'line-width': 0.6, 'line-opacity': 0.45 } },
        { id: 'zones-line', type: 'line', source: 'zones', paint: { 'line-color': '#0b1622', 'line-width': 1.2, 'line-dasharray': [3, 2], 'line-opacity': 0.75 } },
        { id: 'zones-on', type: 'line', source: 'zones', filter: ['==', ['get', 'id'], ''], paint: { 'line-color': '#ff7a2f', 'line-width': 3 } },
        { id: 'lifts-case', type: 'line', source: 'lifts', paint: { 'line-color': '#ffffff', 'line-width': 3, 'line-opacity': 0.8 } },
        { id: 'lifts', type: 'line', source: 'lifts', paint: { 'line-color': '#0b1622', 'line-width': 1.4 } },
        { id: 'snow-label', type: 'symbol', source: 'snow', filter: ['==', ['get', 'kind'], 'line'], minzoom: 11.5,
          layout: { 'symbol-placement': 'line', 'symbol-spacing': 260, 'text-field': ['concat', ['get', 'level'], ' in'], 'text-size': 11, 'text-font': ['Open Sans Semibold'] },
          paint: { 'text-color': '#0b1622', 'text-halo-color': '#ffffff', 'text-halo-width': 1.6 } },
        { id: 'pin', type: 'circle', source: 'pin', paint: { 'circle-radius': 7, 'circle-color': '#ff7a2f', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } }
      ]
    }
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'bottom-left');
  new ResizeObserver(() => map.resize()).observe($('map'));
  // The panel never waits for the map: numbers show even if the map is slow or WebGL is unavailable.
  const mapReady = new Promise(resolve => { if (map.isStyleLoaded()) resolve(); else map.once('style.load', resolve); });
  window.vailMap = map;
  // On a phone the panel covers the lower half, so frame the ski area in what is left above it.
  const frame = c => { if (innerWidth < 761) map.jumpTo({ center: c, zoom: 11.3, padding: { top: 56, bottom: Math.round(innerHeight * 0.46), left: 0, right: 0 } }); };
  mapReady.then(() => frame(HOME));

  /* ---------- controls ---------- */
  function buttons(el, items, key, after) {
    el.innerHTML = '';
    items.forEach(([value, label]) => {
      const b = document.createElement('button');
      b.textContent = label;
      b.dataset.value = value;
      b.setAttribute('aria-pressed', String(state[key] === value));
      b.onclick = () => { state[key] = value; [...el.children].forEach(c => c.setAttribute('aria-pressed', String(c === b))); after(); };
      el.appendChild(b);
    });
  }

  function windowLabel(h) {
    if (h === 'scrub') return 'Hour by hour';
    return { 24: 'Next 24 h', 48: '48 h', 72: '72 h storm total' }[h];
  }

  /* The 10-day view: one day at a time, or all ten added up. `state.day` is 1 to 10, or 0 for the total. */
  const baseOK = () => !!(depth && depth.zones && depth.zones.length && state.newest);
  const baseOn = () => state.layer === 'base';
  const tenOK = () => !!(outlook && outlook.map && state.newest && outlook.days.some(d => d.available));
  const snowIn = v => (v < 0.5 ? '0' : v < 0.95 ? '<1' : fmt(v));           // whole-number sense for snow: no 0.2 in
  const tenDay = () => (state.day && outlook ? outlook.days[state.day - 1] : null);
  /* The middle button names what the map shows: the range a day leads with, or the expected total for all ten. */
  const tenMiddle = () => { const d = tenDay(); return !d ? 'Expected' : d.lead_if_snow ? 'If it snows' : 'Likely'; };
  const tenRanges = () => [['low', 'Low end'], ['fallen', tenMiddle()], ['high', 'High end']];
  function tenZone(zid) {                           // [expected, low end, high end] inches of snow
    if (!state.day) return outlook.total.zones[zid];
    const d = outlook.days[state.day - 1];
    return [d.expected[zid], d.zones[zid][1], Math.max(d.zones[zid][2], d.expected[zid])];
  }
  const tenMid = zid => (state.day ? (outlook.days[state.day - 1].lead || outlook.days[state.day - 1].mid)[zid] : outlook.total.mid[zid]);      // the range a day leads with
  /* The figure a zone leads with: a likely range for new snow, a single number for anything else. */
  function lead(z) {
    if (baseOn() || state.frame != null || state.layer === 'density') return fmt(zoneValue(z)[0]);
    if (state.layer !== 'fallen' || state.range !== 'fallen') return hf(zoneValue(z)[0]);
    if (state.win === 'ten') return rng(...tenMid(z.id));
    const w = z.windows[win()];
    return w.mid ? rng(...w.mid) : hf(w.fallen[0]);
  }
  /* The morning total: new snow in the 24 hours before 5 am on a day, the time and window of a morning snow report.
     For day 1 that morning has happened, so it is what the gauge measured; for later days it is the forecast for the day before. */
  const pct = p => (p <= 5 ? 'under 5%' : p >= 95 ? 'over 95%' : p + '%');
  function morningLine(d, zid) {
    const wd = iso => day(iso, { weekday: 'short' }), when = `5 am ${wd(d.start)}`, tag = `<i>${when} &middot; morning total</i>`;
    const before = new Date(new Date(d.start + 'Z').getTime() - 864e5).toISOString().slice(0, 19), span = `5 am ${wd(before)} to 5 am ${wd(d.start)}`;
    const mt = d.morning;
    if (!mt) return '';
    if (mt.measured) {
      const m = mt.measured;
      if (m.snow_in == null) return '';
      const part = m.hours_reported < m.hours ? `, ${m.hours_reported} of ${m.hours} hours in` : '';
      const depth = m.depth_change_in == null ? '' : `, depth sensor ${m.depth_change_in > 0 ? '+' : ''}${m.depth_change_in} in`;
      return `<span class="m got">${tag}<b>${hf(m.snow_in)} in</b><em>${span} &middot; measured at the mid-mountain gauge${depth}${part}</em></span>`;
    }
    const r = mt.lead[zid], zero = half(r[1]) === 0;
    return `<span class="m">${tag}<b>${rng(...r)} in</b><em>${zero ? '' : mt.lead_if_snow ? 'if it snows, ' : 'likely, '}${pct(mt.chance)} chance &middot; ${span}</em></span>`;
  }
  const tenWhen = long => (state.day ? day(outlook.days[state.day - 1].start, long ? { weekday: 'long', month: 'short', day: 'numeric' } : { weekday: 'short', month: 'short', day: 'numeric' }) : 'next 10 days');

  function setDaybar() {
    const el = $('daybar');
    el.hidden = state.win !== 'ten';
    if (el.hidden) return;
    const items = [...(outlook.total ? [[0, 'All 10']] : []), ...outlook.days.filter(d => d.available).map(d => [d.day, `${day(d.start, { weekday: 'short' })} ${day(d.start, { day: 'numeric' })}`])];
    el.innerHTML = items.map(([n, label]) => `<button data-n="${n}" aria-pressed="${n === state.day}">${label}</button>`).join('');
    el.querySelectorAll('button').forEach(b => { b.onclick = () => enterTen(+b.dataset.n); });
  }

  function enterTen(n) {
    stop();
    const ok = outlook.days.filter(d => d.available).map(d => d.day);
    Object.assign(state, { frame: null, win: 'ten', layer: 'fallen', day: ok.includes(n) ? n : (outlook.total ? 0 : ok[0]) });
    $('scrub').hidden = true;
    setControls();
    refresh();
  }

  function setControls() {
    const wins = meta.windows.map(h => [String(h), windowLabel(h)]);
    if (tenOK()) wins.push(['ten', '10 day']);
    wins.push(['scrub', windowLabel('scrub')]);
    buttons($('windows'), wins, 'win', () => {
      if (state.win === 'ten') return enterTen(0);
      const scrubbing = state.win === 'scrub';
      $('scrub').hidden = !scrubbing;
      if (scrubbing) { state.layer = 'fallen'; state.range = 'fallen'; setControls(); frameTo(+$('slider').value); } else { stop(); state.frame = null; setControls(); refresh(); }
    });
    const leaveSpecial = () => { if (state.win === 'scrub' || state.win === 'ten') { state.win = '72'; $('scrub').hidden = true; stop(); } };
    buttons($('layers'), baseOK() ? [...LAYERS, ['base', 'Base depth']] : LAYERS, 'layer', () => { if (state.win === 'scrub' || state.layer !== 'fallen') leaveSpecial(); if (state.layer !== 'fallen') state.range = 'fallen'; setControls(); refresh(); });
    buttons($('range'), state.win === 'ten' ? tenRanges() : RANGES, 'range', () => { state.layer = 'fallen'; if (state.win === 'scrub') leaveSpecial(); setControls(); refresh(); });
    setDaybar();
  }

  /* ---------- data ---------- */
  const base = () => `${DATA}/${state.run}`;
  const win = () => (state.win === 'scrub' || state.win === 'ten' ? '72' : state.win);

  let opened = false;
  async function loadRun() {
    const runs = await json(DATA + '/runs.json');
    const current = runs.find(r => !r.replay) || runs[0];             // the newest real forecast: a replay never opens by default
    if (!state.run || !runs.some(r => r.name === state.run)) state.run = current.name;
    state.newest = current.name === state.run;
    state.latest = current.name;
    $('runs').innerHTML = runs.map(r => `<option value="${r.name}" ${r.name === state.run ? 'selected' : ''}>${r.replay ? 'Replay: ' : ''}${day(r.run, r.replay ? { month: 'short', day: 'numeric', year: 'numeric' } : { weekday: 'short', month: 'short', day: 'numeric' })}${r.name === current.name ? ' (latest)' : ''}</option>`).join('');
    $('runs').hidden = runs.length < 2;
    $('runs').onchange = e => { location.search = '?run=' + e.target.value; };
    [meta, zones, why, grid] = await Promise.all(['meta', 'zones', 'why', 'grid'].map(n => json(`${base()}/${n}.json`)));
    [score, alertInfo, outlook, depth] = await Promise.all([optional(DATA + '/scorecard.json'), optional(DATA + '/alert.json'), optional(DATA + '/outlook.json'), optional(DATA + '/depth.json')]);
    cellIndex = new Map(grid.cells.map((c, i) => [c, i]));
    if (baseOK()) optional(`${DATA}/depth/grid.json?u=${encodeURIComponent(depth.updated)}`).then(g => { if (g) { dgrid = g; dIndex = new Map(g.cells.map((c, i) => [c, i])); } });
    if (tenOK()) optional(`${DATA}/outlook/grid.json?u=${encodeURIComponent(outlook.updated)}`).then(g => { if (g) { ogrid = g; oIndex = new Map(g.cells.map((c, i) => [c, i])); if (state.point) showPoint(state.point); } });
    document.title = `${meta.resort} snow forecast`;
    const view = meta.view || {};
    if (view.center && (Math.abs(view.center[0] - HOME[0]) > 1e-4 || Math.abs(view.center[1] - HOME[1]) > 1e-4)) {
      mapReady.then(() => { map.jumpTo({ center: view.center, zoom: view.zoom || 12.2 }); frame(view.center); });
    }
    zoneGeo = await json('static/zones.geojson');
    if (tenOK() && !opened) tab('ten');                                // the 10-day list is the panel the page opens on
    opened = true;
    // The newest of everything on the page: the map is rebuilt once a day, the 10-day outlook and base depth four times.
    const stamps = [meta.built.replace(' ', 'T').replace('Z', ''), ...(state.newest ? [outlook && outlook.updated, depth && depth.updated] : [])].filter(Boolean).sort();
    const when = day(stamps[stamps.length - 1], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    $('issued').textContent = state.newest ? `Updated ${when} MT` : `${meta.replay ? 'Replay of' : 'Earlier forecast,'} ${day(meta.run, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}`;
    $('issued').title = `Map from the ${meta.issued_local} model run, built ${day(meta.built.replace(' ', 'T').replace('Z', ''), { weekday: 'short', hour: 'numeric', minute: '2-digit' })} MT`;
    $('slider').max = meta.frames.length - 1;
    $('slider').value = meta.frames.length - 1;
    setControls();
    renderWhy();
    renderTen();
    renderScore();
    refresh();
  }

  async function refresh() {
    const name = state.frame != null ? `frame_${String(state.frame).padStart(2, '0')}` : `${state.layer === 'fallen' ? state.range : state.layer}_${win()}`;
    const density = state.layer === 'density' && state.frame == null;
    const colors = baseOn() ? DEPTH : density ? DENSITY : RAMP;
    renderLegend(density);
    renderZones();
    renderHead();
    renderTen();
    if (state.point) showPoint(state.point);
    const ten = state.win === 'ten', wasBase = baseOn();
    showGauge();
    const data = await json(wasBase ? `${DATA}/depth/base.geojson?u=${encodeURIComponent(depth.updated)}` : ten ? `${DATA}/outlook/${state.day ? 'day_' + pad(state.day) : 'total'}_${state.range === 'fallen' ? (state.day && outlook.map_lead ? 'lead' : 'expected') : state.range}.geojson?u=${encodeURIComponent(outlook.updated)}`
      : `${base()}/contours/${name}.geojson`);
    if (ten !== (state.win === 'ten') || wasBase !== baseOn()) return;      // the view changed while the file was loading
    await mapReady;
    map.getSource('snow').setData(data);
    map.setPaintProperty('snow-fill', 'fill-color', ['match', ['get', 'band'], ...colors.flatMap((c, i) => [i, c]), '#000000']);
    map.setLayoutProperty('snow-line', 'visibility', density ? 'none' : 'visible');
    map.setLayoutProperty('snow-label', 'visibility', density ? 'none' : 'visible');
  }

  /* ---------- panel ---------- */
  const zoneValue = z => {
    if (baseOn()) return (depth.zones.find(q => q.id === z.id) || { depth_in: [0, 0, 0] }).depth_in;
    if (state.win === 'ten') { const [e, lo, hi] = tenZone(z.id); return state.range === 'low' ? [lo, lo, hi] : state.range === 'high' ? [hi, lo, hi] : [e, lo, hi]; }
    const w = z.windows[win()];
    if (state.frame != null) {                       // scrubbing: scale the 72-hour numbers by the share fallen so far
      const c = meta.hourly_curve_in, k = (c[state.frame] || 0) / (c[c.length - 1] || 1);
      return w.fallen.map(v => v * k);
    }
    if (state.layer === 'density') return [w.density, w.density, w.density];
    if (state.layer === 'fallen') { const f = w.fallen; return state.range === 'low' ? [f[1], f[1], f[2]] : state.range === 'high' ? [f[2], f[1], f[2]] : f; }
    return w[state.layer];
  };

  function renderHead() {
    if (baseOn()) {
      const mid = zones.find(z => z.id === (meta.view || {}).headline_zone) || zones[0], v = zoneValue(mid), g = depth.gauge;
      $('headNum').textContent = fmt(v[0]);
      $('headSub').innerHTML = `<b>${mid.name}</b>, estimated snow on the ground. Most of the zone is between ${fmt(v[1])} and ${fmt(v[2])} in.` +
        (g ? ` The mid-mountain sensor reads <b>${fmt(g.depth_in)} in</b>.` : '');
    } else if (state.win === 'ten') {
      const mid = zones.find(z => z.id === outlook.headline_zone) || zones[0], t = tenZone(mid.id), d = state.day ? outlook.days[state.day - 1] : null;
      $('headNum').textContent = lead(mid);
      const what = { low: 'low end for new snow', fallen: d && d.lead_if_snow ? 'new snow if it snows' : 'new snow likely', high: 'high end for new snow' }[state.range];
      $('headSub').innerHTML = (d ? morningLine(d, mid.id).replace('class="m', 'class="m head') : '') + `<b>${mid.name}</b>, ${what} ${d ? 'on ' + tenWhen(true) : 'over the next 10 days'}.${d ? ` ${d.chance['1in'] <= 5 ? 'Under 5' : d.chance['1in']}% chance of snow.` : ''} Expected ${hf(t[0])} in; 1 in 10 chance of more than ${hf(t[2])}.` +
        (d ? ` Snow quality: <b>${d.quality}</b>.${d.source === 'ensemble' ? ' From the ensemble, ' + d.lead_day + ' days out.' : ''}` : '') +
        (d && d.wind && d.wind.strength >= 0.2 ? ` Ridge wind from the ${d.wind.from} near ${d.wind.mph} mph: ${d.wind.strength >= 1 ? 'more' : 'a little more'} on slopes just over the ridges from it, less on the exposed side.` : d && d.wind ? ' Light ridge wind: little loading.' : '');
    } else {
      const best = [...zones].sort((a, b) => b.windows[win()].fallen[0] - a.windows[win()].fallen[0])[0];
      const mid = zones.find(z => z.id === (meta.view || {}).headline_zone) || best;
      const w = mid.windows[win()];
      const v = state.layer === 'density' ? w.fallen : zoneValue(mid);
      $('headNum').textContent = state.layer === 'density' ? hf(v[0]) : lead(mid);
      const what = { fallen: { low: 'low end for new snow', fallen: 'new snow likely', high: 'high end for new snow' }[state.range], settled: 'settled by the end', density: 'new snow' }[state.layer];
      const when = state.frame != null ? `by ${frameTime(state.frame)}` : { 24: 'in the next 24 hours', 48: 'in 48 hours', 72: 'in 72 hours' }[win()];
      const soFar = state.frame != null ? `, about ${fmt(v[1])} to ${fmt(v[2])} in` : '';
      const rangeText = `Most likely ${hf(w.fallen[0])} in; 1 in 10 chance of under ${hf(w.fallen[1])} or over ${hf(w.fallen[2])}`;
      $('headSub').innerHTML = `<b>${mid.name}</b>, ${what} ${when}. ${state.frame != null ? soFar.replace(', a', 'A') + '.' : rangeText + '.'} Snow quality: <b>${w.quality}</b>.`;
    }
    $('headNum').parentElement.classList.toggle('wide', $('headNum').textContent.length > 4);
    const flagged = meta.days.filter(d => d.available && d.upside_setup);
    const notes = [];
    const recent = alertInfo && state.newest && Date.now() - new Date(alertInfo.time + 'Z').getTime() < 18 * 36e5;
    if (!state.newest) notes.push(`This is ${meta.replay ? 'a replay of a past model run' : 'an earlier forecast'}, not the current one. <a href="?run=${state.latest}" style="color:inherit">Show the latest</a>.`);
    if (recent && alertInfo.fire) notes.push(`<b>Powder alert.</b> ${alertInfo.title}.`);
    if (flagged.length) notes.push(`Upside setup on day ${flagged.map(d => d.day).join(' and ')}: northwest flow, strong jet, moist ridge layer. Read the high end of the range.`);
    $('flag').hidden = !notes.length;
    $('flag').innerHTML = notes.join('<br>');
  }

  function renderZones() {
    const el = $('tab-zones');
    if (baseOn()) {
      const rows = zones.map(z => ({ z, v: zoneValue(z) })).sort((a, b) => b.v[0] - a.v[0]), top = Math.max(6, ...rows.map(r => r.v[2]));
      const others = depth.sensors.filter(x => !x.target).map(x => `${x.name} (${x.elev_ft.toLocaleString()} ft) ${fmt(x.depth_in)} in`).join(' &middot; ');
      const hc = depth.history_check;
      el.innerHTML = `<p class="hint" style="padding-bottom:4px">Estimated snow on the ground now: natural snowpack, not groomed or man-made base.</p>` + rows.map(({ z, v }) =>
        `<button class="card ${state.zone === z.id ? 'on' : ''}" data-id="${z.id}"><span class="name">${z.name}</span><span class="num">${fmt(v[0])}<small>in</small></span>
        <span class="sub">most of the zone ${fmt(v[1])} to ${fmt(v[2])} in</span>
        <span class="bar"><i style="left:${100 * v[1] / top}%;width:${100 * (v[2] - v[1]) / top}%"></i><b style="left:${100 * v[0] / top}%"></b></span></button>`).join('') +
        `<div class="why"><h3>Where it comes from</h3>
        <p><b>Measured:</b> ${depth.gauge ? `${depth.gauge.name} SNOTEL (${depth.gauge.elev_ft.toLocaleString()} ft) reads ${fmt(depth.gauge.depth_in)} in, as of ${day(depth.gauge.time, { weekday: 'short', hour: 'numeric' })}.` : 'no sensor reading.'} The estimate is made to match it there.</p>
        <p><b>Shape across the mountain:</b> NOAA's SNODAS snow model of ${day(depth.model_date + 'T12:00:00', { month: 'short', day: 'numeric' })} (1 km cells), with ${fmt(depth.inches_per_1000ft)} in more per 1,000 ft of height inside each cell.</p>
        ${hc ? `<p class="note">On ${hc.dates} past dates SNODAS missed this sensor by ${hc.gauge.typical_miss_in} in on average (${hc.gauge.typical_miss_pct}%), and six other sensors nearby by ${hc.other_sensors.typical_miss_in} in. It is given those sensors' readings, so between them it will be worse.</p>` : ''}
        <p class="note">Other sensors now: ${others}.</p>
        <p class="note">Not in it: the resort's own base measurement (its site does not allow automated reading), snowmaking, grooming, drifts, and sun on south faces at the scale of a run.</p></div>`;
      el.querySelectorAll('.card').forEach(c => { c.onclick = () => focusZone(c.dataset.id); });
      drawLabels();
      return;
    }
    if (state.win === 'ten') {
      const rows = zones.map(z => ({ z, v: zoneValue(z), t: tenZone(z.id) })).sort((a, b) => b.v[0] - a.v[0]);
      const top = Math.max(1, ...rows.map(r => r.t[2]));
      const far = !state.day || outlook.days[state.day - 1].source === 'ensemble';
      el.innerHTML = `<p class="hint" style="padding-bottom:4px">New snow, ${tenWhen(true)}. Pick a day above the map, or on the 10 day tab.</p>` + rows.map(({ z, v, t }) =>
        `<button class="card ${state.zone === z.id ? 'on' : ''}" data-id="${z.id}"><span class="name">${z.name}</span><span class="num">${lead(z)}<small>in</small></span>
        <span class="sub">${state.day && outlook.days[state.day - 1].lead_if_snow ? 'if it snows &middot; ' : ''}expected ${hf(t[0])} in &middot; 1 in 10 chance of more than ${hf(t[2])}</span>
        <span class="bar"><i style="left:${100 * t[1] / top}%;width:${100 * (t[2] - t[1]) / top}%"></i><b style="left:${100 * t[0] / top}%"></b></span></button>`).join('') +
        `<p class="hint">Bars show the 10th to 90th percentile range; the tick is the expected amount. ${far ? 'Past day 3 the mountain is told apart by the long-term pattern, elevation, the snow level and wind loading from the ensemble\'s average wind, which is less certain the further out it is.' : meta.notes.zones}</p>`;
      el.querySelectorAll('.card').forEach(c => { c.onclick = () => focusZone(c.dataset.id); });
      drawLabels();
      return;
    }
    const max = Math.max(1, ...zones.map(z => z.windows[win()].fallen[2]));
    const order = [...zones].sort((a, b) => zoneValue(b)[0] - zoneValue(a)[0]);
    const unit = state.layer === 'density' ? ':1' : 'in';
    el.innerHTML = order.map(z => {
      const w = z.windows[win()], v = zoneValue(z), f = w.fallen;
      const sub = state.layer === 'density' ? `${w.quality} snow, ${w.mid ? rng(...w.mid) : hf(f[0])} in likely`
        : state.layer === 'settled' ? `range ${hf(v[1])} to ${hf(v[2])} in &middot; ${w.quality}`
        : `most likely ${hf(f[0])} &middot; 1 in 10 over ${hf(f[2])} &middot; ${w.quality}`;
      return `<button class="card ${state.zone === z.id ? 'on' : ''}" data-id="${z.id}">
        <span class="name">${z.name}</span><span class="num">${lead(z)}<small>${unit}</small></span>
        <span class="sub">${sub}</span>
        <span class="bar"><i style="left:${100 * f[1] / max}%;width:${100 * (f[2] - f[1]) / max}%"></i><b style="left:${100 * f[0] / max}%"></b></span></button>`;
    }).join('') + `<p class="hint">The big figure is the likely range: the amount lands inside it a little over half the time. Bars show the wider 10th to 90th percentile range; the tick is the single most likely amount. Wind loading is included. ${meta.notes.zones}</p>`;
    el.querySelectorAll('.card').forEach(c => { c.onclick = () => focusZone(c.dataset.id); });
    drawLabels();
  }

  function centre(feature) {
    let best = null;
    const polys = feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates] : feature.geometry.coordinates;
    polys.forEach(p => { if (!best || p[0].length > best.length) best = p[0]; });
    const xs = best.map(c => c[0]), ys = best.map(c => c[1]);
    return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2, [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]]];
  }

  function showGauge() {                            // the one measured depth on the mountain, shown with the base-depth layer
    if (gaugeMarker) { gaugeMarker.remove(); gaugeMarker = null; }
    if (!baseOn() || !depth.gauge) return;
    const el = document.createElement('div');
    el.className = 'gauge';
    el.innerHTML = `${fmt(depth.gauge.depth_in)}"<small>measured</small>`;
    gaugeMarker = new maplibregl.Marker({ element: el }).setLngLat([depth.gauge.lon, depth.gauge.lat]).addTo(map);
  }

  function drawLabels() {
    markers.forEach(m => m.remove());
    markers = [];
    if (!zoneGeo) return;
    const unit = state.layer === 'density' ? ':1' : '"';
    zoneGeo.features.forEach(f => {
      const z = zones.find(q => q.id === f.properties.id);
      if (!z) return;
      const el = document.createElement('div');
      el.className = 'zlabel';
      el.innerHTML = `${lead(z)}${unit}<small>${z.name.split(' / ')[0]}</small>`;
      markers.push(new maplibregl.Marker({ element: el }).setLngLat(centre(f).slice(0, 2)).addTo(map));
    });
  }

  function focusZone(id) {
    state.zone = state.zone === id ? null : id;
    map.setFilter('zones-on', ['==', ['get', 'id'], state.zone || '']);
    const f = zoneGeo.features.find(q => q.properties.id === id);
    if (state.zone && f) map.fitBounds(centre(f)[2], { padding: { top: 120, bottom: innerWidth < 761 ? innerHeight * 0.5 : 60, left: 60, right: 60 }, maxZoom: 14.2 });
    renderZones();
    renderTen();
  }

  function renderWhy() {
    const el = $('tab-why');
    const days = meta.days.filter(d => d.available);
    const byDay = Object.fromEntries(why.points.map(p => [p.day, p.text]));
    const hz = zones.find(z => z.id === (meta.view || {}).headline_zone) || zones[0];
    const analogs = why.analogs.length ? `<h3>Closest past days to day ${why.analog_day}</h3>` + (why.resembles ? `<p>${why.resembles}</p>` : '') + why.analogs.map(a =>
      `<div class="analog"><span class="d">${day(a.date + 'T12:00:00', { month: 'short', day: 'numeric', year: 'numeric' })}</span>
       <span>${a.snow_in != null ? `About ${fmt(a.snow_in)} in of snow at mid-mountain` : `${a.water_in.toFixed(2)} in of water at mid-mountain`}${a.town_snow_in != null ? `, ${fmt(a.town_snow_in)} in in town` : ''} <span class="r">(${a.water_in.toFixed(2)} in of water)</span><br>
       <span class="r">${a.match ? a.match + ' match &middot; ' : ''}${a.regime}${a.regime.endsWith('flow') ? '' : ' flow'} from ${a.wind_from_deg}&deg;${a.storm && a.storm.days > 1 ? ` &middot; ${a.storm.label} storm, ${fmt(a.storm.water_in)} in of water` : ''}</span></span></div>`).join('') +
      `<p class="note" style="margin-top:8px">Past forecast days since 2018 with the most similar wind, temperature, moisture and forecast amount, and what the gauge then measured. ${meta.notes.analogs || ''}</p>` : '';
    const outlook = meta.outlook.length ? '<h3>Days 4 to 7 (outlook)</h3><div class="outlook">' + meta.outlook.map(o =>
      `<div><span class="note">${day(o.start, { weekday: 'short' })}</span><div class="n">${fmt(o.snow_in)}"</div><span class="note">&plusmn;${fmt(o.spread_in * 14)}</span></div>`).join('') +
      '</div><p class="note" style="margin-top:8px">Raw ensemble average at 14 to 1, not corrected and not verified. A sketch, not a forecast.</p>' : '';
    el.innerHTML = `<div class="why"><h2>${why.headline}</h2>` + days.map(d =>
      `<div class="day"><div class="when">${day(d.start, { weekday: 'long' })} 5 am to ${day(new Date(new Date(d.start + 'Z').getTime() + 864e5).toISOString().slice(0, 19), { weekday: 'short' })} 5 am</div>
       <p>${hz.days ? `<span class="amt">${hz.days[d.day - 1].length >= 5 ? rng(hz.days[d.day - 1][3], hz.days[d.day - 1][4]) : hf(hz.days[d.day - 1][0])} in of snow</span> likely at ${hz.name} (most likely ${hf(hz.days[d.day - 1][0])}; 1 in 10 chance of over ${hf(hz.days[d.day - 1][2])}). ` : ''}<span class="note">${d.water_in.q50.toFixed(2)} in of water at the gauge (${d.water_in.q10.toFixed(2)} to ${d.water_in.q90.toFixed(2)}) &middot; ${d.model}</span></p>
       <p>${byDay[d.day] || ''}</p></div>`).join('') + analogs + outlook + '</div>';
  }

  /* ---------- 10 day: three days from the high-resolution forecast, seven from the ensemble ---------- */
  function renderTen() {
    const el = $('tab-ten');
    if (!outlook || !outlook.days.some(d => d.available)) { el.innerHTML = '<p class="hint">No 10-day outlook has been built yet.</p>'; return; }
    const zid = outlook.zones.some(z => z.id === state.zone) ? state.zone : outlook.headline_zone;
    const zname = outlook.zones.find(z => z.id === zid).name;
    const days = outlook.days.filter(d => d.available);
    const onMap = n => state.win === 'ten' && state.day === n;
    const amounts = d => [d.expected[zid], d.zones[zid][1], Math.max(d.zones[zid][2], d.expected[zid])];          // expected, low end, high end
    const scale = d => (d.expected[outlook.headline_zone] > 0.05 ? d.expected[zid] / d.expected[outlook.headline_zone] : 1);
    // chart: bar to the likely amount, whisker over the 10th to 90th percentile range
    const W = 328, H = 150, left = 22, base = H - 22, top = 16, step = (W - left) / 10;
    const max = Math.max(4, ...days.map(d => amounts(d)[2]));
    const y = v => base - (base - top) * Math.min(v, max) / max, x = n => left + step * (n - 0.5);
    const grid = [0, max / 2, max].map(v => `<line x1="${left}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="#fff" stroke-opacity="${v ? 0.08 : 0.25}"/><text x="${left - 5}" y="${y(v) + 3.5}" fill="#93a4b5" font-size="10" text-anchor="end">${fmt(v)}</text>`).join('');
    const marks = days.map(d => {
      const [likely, low, high] = amounts(d), ens = d.source === 'ensemble', w = step * 0.52, bx = x(d.day) - w / 2, by = y(likely), bh = Math.max(base - by, 0);
      const bar = bh > 0.5 ? `<path d="M${bx},${base}V${by + Math.min(3, bh)}q0,-${Math.min(3, bh)} 3,-${Math.min(3, bh)}h${w - 6}q3,0 3,${Math.min(3, bh)}V${base}Z" fill="${ens ? '#86b6ef' : '#4689dc'}" fill-opacity="${ens ? 0.55 : 1}"/>` : `<rect x="${bx}" y="${base - 1.5}" width="${w}" height="1.5" fill="#86b6ef" fill-opacity=".6"/>`;
      return `<g data-day="${d.day}" class="${onMap(d.day) ? 'on' : ''}"><rect class="hit" x="${x(d.day) - step / 2}" y="0" width="${step}" height="${H}" rx="3"/>${bar}
        <line x1="${x(d.day)}" x2="${x(d.day)}" y1="${y(low)}" y2="${y(high)}" stroke="#eef3f8" stroke-width="1.5" stroke-opacity=".8"/><line x1="${x(d.day) - 4}" x2="${x(d.day) + 4}" y1="${y(high)}" y2="${y(high)}" stroke="#eef3f8" stroke-width="1.5" stroke-opacity=".8"/>
        <text x="${x(d.day)}" y="${H - 7}" fill="${onMap(d.day) ? '#eef3f8' : '#93a4b5'}" font-size="10.5" text-anchor="middle">${day(d.start, { weekday: 'short' }).slice(0, 2)}</text></g>`; }).join('');
    const firstEns = days.find(d => d.source === 'ensemble');
    const divider = firstEns && firstEns.day > 1 ? `<line x1="${x(firstEns.day) - step / 2}" x2="${x(firstEns.day) - step / 2}" y1="4" y2="${base}" stroke="#93a4b5" stroke-dasharray="2 3"/>` : '';
    const chart = `<svg class="ten" viewBox="0 0 ${W} ${H}" role="img" aria-label="New snow by day for ten days">${grid}${divider}${marks}</svg>
      <div class="src"><span>${firstEns && firstEns.day > 1 ? 'High-resolution forecast' : ''}</span><span>${firstEns ? 'Ensemble, days ' + firstEns.day + ' to 10' : ''}</span></div>`;
    const odds = p => (p <= 5 ? 'under 5%' : p >= 95 ? 'over 95%' : p + '%');       // the quantiles cannot resolve the far tails
    const spark = d => {
      const pts = [...(d.trail || []).map(t => t.expected_in), d.expected[outlook.headline_zone]].map(v => v * scale(d));
      if (pts.length < 3) return '';
      const hi = Math.max(1, ...pts), px = n => 2 + 52 * n / (pts.length - 1), py = v => 15 - 12 * v / hi;
      return `<svg width="58" height="18" aria-hidden="true"><path d="${pts.map((v, n) => `${n ? 'L' : 'M'}${px(n).toFixed(1)},${py(v).toFixed(1)}`).join('')}" fill="none" stroke="#93a4b5" stroke-width="1.5"/><circle cx="${px(pts.length - 1)}" cy="${py(pts[pts.length - 1])}" r="2.5" fill="#eef3f8"/></svg>`; };
    const move = d => {
      if (d.change_in == null) return '<span>first call</span>';
      const c = d.change_in * scale(d);
      return Math.abs(c) < 0.5 ? '<span>steady</span>' : `<span class="move">${c > 0 ? '&#9650; up' : '&#9660; down'} ${fmt(Math.abs(c))} in</span>`; };
    const rows = days.map(d => {
      const [likely, low, high] = amounts(d);
      const trust = d.source === 'ensemble' ? (d.skill_pct >= 15 ? 'fair guide' : d.skill_pct >= 5 ? 'weak guide' : 'little better than the average for the date') : 'checked forecast';
      return `<button class="tday ${onMap(d.day) ? 'on' : ''} ${firstEns && d.day === firstEns.day && d.day > 1 ? 'split' : ''}" data-day="${d.day}">
        ${morningLine(d, zid)}
        <span class="d">${day(d.start, { weekday: 'short' })}<small>${day(d.start, { month: 'short', day: 'numeric' })}</small></span>
        <span class="a"><b>${rng(...(d.lead || d.mid)[zid])} in</b> ${d.lead_if_snow ? 'if it snows' : 'of snow'} <span>(${odds(d.chance['1in'])} chance)</span><small>${day(d.start, { weekday: 'short' })}, midnight to midnight &middot; expected ${hf(likely)} in &middot; up to ${hf(high)} &middot; ${trust}${d.blend_snow_in != null && d.blend_snow_in >= 0.5 ? `<br>National Blend alone: ${hf(d.blend_snow_in)} in at the gauge` : ''}</small></span>
        <span class="c">${move(d)}${spark(d)}</span></button>`; }).join('');
    const all = outlook.total ? `<button class="tday total ${onMap(0) ? 'on' : ''}" data-day="0"><span class="a"><b>${rng(...outlook.total.mid[zid])} in</b> of snow over all 10 days <span>(expected ${hf(outlook.total.zones[zid][0])}, up to ${hf(outlook.total.zones[zid][2])})</span></span><span class="c">show total</span></button>` : '';
    const blocks = outlook.blocks.length ? '<div class="tiles">' + outlook.blocks.map(b => `<div><div class="n">${fmt(b.zones[zid][0])}"</div><div class="l">${b.name}: ${b.word}, range ${fmt(b.zones[zid][1])} to ${fmt(b.zones[zid][2])}</div></div>`).join('') + '</div>' : '';
    const sk = outlook.skill, lead = k => (sk[k] ? `${sk[k].skill_pct}%` : '');
    const hour = iso => day(iso, { weekday: 'short', hour: 'numeric', minute: '2-digit' });
    el.innerHTML = `<div class="why"><h2>${zname}, next 10 days</h2>
      <p class="note morn-note"><b>Morning total</b> is the number to check before skiing: new snow in the 24 hours up to 5 am, the time and window of a morning snow report. It spans two calendar days: most of the day before, plus the hours after midnight. It is this forecast's figure for the zone, not the resort's report. Today's is what the gauge measured.</p>
      <p class="note">Below it, inches of new snow each calendar day, midnight to midnight. A day with at least a 1 in 5 chance of snow shows the range to expect if it snows, with the chance beside it; below that it reads 0. "Up to" is the 1-in-10 high end. ${tenOK() ? 'Tap a day to see it on the map. ' : ''}Updated ${hour(outlook.updated)} from the ensemble run of ${hour(outlook.ensemble_run)}${outlook.blend_run ? ` and the National Blend of ${hour(outlook.blend_run)}` : ''}; new runs arrive four times a day, the next about ${hour(outlook.next_update)}.${state.zone ? '' : ' Pick a zone on the Zones tab to see it here.'}</p>
      ${chart}${blocks}<div class="key" style="margin-top:10px"><span><i class="t"></i>10th to 90th percentile range</span><span>change is since a day ago</span></div>${all}${rows}
      <h3>How far to trust it</h3>
      <p class="note">Tested on ${sk['4'] ? sk['4'].n : ''} held-out days per lead (winters ${outlook.skill_seasons || ''}). Improvement over simply quoting the average for the time of year: day 4 ${lead('4')}, day 5 ${lead('5')}, day 6 ${lead('6')}, day 7 ${lead('7')}, day 8 ${lead('8')}, day 9 ${lead('9')}, day 10 ${lead('10')}. "Expected" is the chance of snow times how much would fall, so the days add up to the total. The models forecast 5 am to 5 am, the way the gauge record they learned from is cut, and that is what was tested and what the Score tab grades: it is the morning total. Calendar days are re-cut from those by when the snow is forecast to fall (hour by hour for the first days, in six-hour steps after), and that re-cut has not been tested on its own. Past day 3 the forecast draws on the GEFS ensemble and the National Blend. It usually sits below the Blend's own figure on a snowy day: over six winters, when the Blend called for 0.30 to 0.45 in of water four to six days out, the gauge averaged 0.27 and nothing fell on almost a quarter of those days.</p>
      <p class="note">Days past the third have no temperature profile: snow level and density there are estimated from the 700 mb temperature alone, and zones differ only by the long-term pattern.</p></div>`;
    el.querySelectorAll('[data-day]').forEach(b => { b.onclick = () => { if (tenOK()) enterTen(+b.dataset.day); }; });
  }

  /* ---------- score: the running record, then what is measured and what is modelled ---------- */
  function renderScore() {
    const n = meta.notes;
    let card = '<h2>Scorecard</h2><p class="note">No forecasts have been scored yet.</p>';
    if (score && score.counts && score.counts.verified) {
      const L = score.leads['1'], sp = L.snow_possible || {}, c = score.counts;
      const shown = score.days.filter(d => d.obs != null && (d.q90 >= 0.05 || d.obs >= 0.05)).slice(0, 40);
      const max = Math.max(3, ...shown.map(d => Math.max(d.snow[2], d.obs_snow)));
      const pct = v => `${Math.min(100, 100 * v / max).toFixed(1)}%`;
      const rows = shown.map(d => `<div class="sday"><div class="top"><span><b>${day(d.start, { month: 'short', day: 'numeric' })}</b> &nbsp;forecast ${fmt(d.snow[1])} in <span class="note">(${fmt(d.snow[0])} to ${fmt(d.snow[2])})</span></span>
        <span class="o">measured ${fmt(d.obs_snow)} in${d.provisional ? '*' : ''}</span></div>
        <div class="bar"><i style="left:${pct(d.snow[0])};width:${pct(d.snow[2] - d.snow[0])}"></i><b style="left:${pct(d.snow[1])}"></b><u style="left:${pct(d.obs_snow)}"></u></div></div>`).join('');
      const storms = score.storms.length ? '<h3>Storms this season</h3>' + score.storms.map(s => {
        const end = new Date(new Date(s.start + 'Z').getTime() + s.days * 864e5).toISOString().slice(0, 19);
        return `<div class="storm"><b>${day(s.start, { month: 'short', day: 'numeric' })}${s.days > 1 ? ' to ' + day(end, { month: 'short', day: 'numeric' }) : ''}</b><br>
          Measured about ${fmt(s.observed_snow_in)} in of snow <span class="note">(${s.observed_in.toFixed(2)} in of water${s.depth_gain_in != null ? `; the depth sensor ended ${s.depth_gain_in >= 0 ? '+' : ''}${fmt(s.depth_gain_in)} in after settling` : ''})</span>.<br>
          <span class="note">Day-ahead forecasts added up: ${fmt(s.day_ahead_snow_in)} in (${fmt(s.day_ahead_snow_range[0])} to ${fmt(s.day_ahead_snow_range[1])}).</span></div>`; }).join('') : '';
      const depth = score.depth && score.depth.n ? `<p class="note">Settled depth at the sensor the next morning: typical miss ${score.depth.mae.toFixed(1)} in over ${score.depth.n} days snow was possible; ${score.depth.inside_10_90} of them inside the stated range. The sensor reads to the inch.</p>` : '';
      card = `<h2>${score.season} season, scored</h2>
        <p class="note">Every forecast is logged when it is built, then set against the ${score.site} gauge. Inches of new snow at mid-mountain for the 24 hours from 5 am. The gauge measures water; "measured" snow is that water at the snow-to-water ratio the forecast used that day. ${c.live} of ${c.logged} were logged on the day; the other ${c.replay} were rebuilt afterwards from the model runs as issued, by a model frozen before the season.</p>
        <div class="tiles"><div><div class="n">${L.snow_mae.toFixed(1)}"</div><div class="l">typical miss, inches of snow, over ${L.n} days</div></div>
          <div><div class="n">${L.snow_raw_mae != null ? L.snow_raw_mae.toFixed(1) + '"' : '--'}</div><div class="l">raw HRRR's miss on the same days</div></div>
          <div><div class="n">${sp.n ? `${sp.inside_10_90}/${sp.n}` : '--'}</div><div class="l">days inside the range (8 in 10 expected)</div></div></div>
        <p class="note">Season to date: ${fmt(L.snow_forecast_total_in)} in forecast day-ahead, about ${fmt(L.snow_observed_total_in)} in measured.</p>${depth}
        <h3>Day by day</h3><div class="key"><span><i class="r"></i>forecast range</span><span><i class="t"></i>likely</span><span><i class="d"></i>measured</span></div>${rows}
        ${shown.some(d => d.provisional) ? '<p class="note" style="margin-top:6px">* provisional: the gauge\'s checked daily total is not in yet.</p>' : ''}${storms}`;
    }
    const past = score && score.past_seasons && score.past_seasons.length ? `<h3>Earlier winters (hindcast)</h3><table class="past"><tr><th>Winter</th><th>Days</th><th>This model</th><th>Raw HRRR</th><th>Natl Blend</th></tr>` +
      score.past_seasons.map(p => `<tr><td>${p.season}</td><td>${p.n}</td><td>${p.snow_mae.toFixed(2)}</td><td>${p.snow_raw_mae.toFixed(2)}</td><td>${p.snow_nbm_mae != null ? p.snow_nbm_mae.toFixed(2) : '&ndash;'}</td></tr>`).join('') +
      '</table><p class="note" style="margin-top:6px">Typical miss in inches of snow at mid-mountain, day ahead. Each winter was forecast by a model that never saw it. All three are judged on the water they forecast, converted to snow the same way.</p>' : '';
    const lr = score && score.long_range, ho = lr && lr.held_out && Object.keys(lr.held_out).length ? lr.held_out : null;
    const far = ho ? `<h3>Days 4 to 10, by how far ahead</h3><table class="past"><tr><th>Day</th><th>This season</th><th>Past winters</th><th>Avg for date</th><th>Gain</th></tr>` +
      Object.keys(ho).map(k => { const now = lr.by_lead[k]; return `<tr><td>${k}</td><td>${now ? `${now.snow_mae.toFixed(2)} <span class="note">(${now.n})</span>` : '&ndash;'}</td><td>${ho[k].snow_mae.toFixed(2)}</td><td>${ho[k].snow_average_for_the_month_mae.toFixed(2)}</td><td>${ho[k].skill_pct}%</td></tr>`; }).join('') +
      `</table><p class="note" style="margin-top:6px">Typical miss in inches of snow at mid-mountain for one day, that many days ahead. "This season" counts every long-range forecast logged so far (days scored in brackets). "Past winters" is the same model on ${ho['4'].n} held-out days per lead; "Avg for date" is what quoting the monthly average would have missed by; "Gain" is the improvement in the full forecast range over that average.</p>` : '';
    const st = score && score.state && score.state.n ? score.state : null;
    const ground = st ? `<h3>Snow on the ground, hour by hour</h3><p class="note">A running estimate of what is on the ground across the mountain, stepped every hour, is published for a ski game. It is driven by this gauge and its depth sensor, so this is a check that it stays tied to them, not an independent test. At 5 am over ${st.n} days, total depth at the sensor was off by ${st.depth_mae_in.toFixed(1)} in on a typical day (${st.depth_bias_in >= 0 ? 'high' : 'low'} by ${Math.abs(st.depth_bias_in).toFixed(1)} on average)${st.snow_days ? `; on the ${st.snow_days.n} days with at least 0.2 in of water, the 24-hour change in depth missed by ${st.snow_days.change_mae_in.toFixed(1)} in` : ''}. Fresh snow by zone, what skiers have tracked out and what the wind has moved are modelled and measured by nothing public. The resort's own stakes are not used here: its site's terms do not allow republishing them, and no public sensor measures snow in Blue Sky Basin.</p>` : '';
    $('tab-score').innerHTML = `<div class="why">${card}${past}${far}${ground}<h2 style="margin-top:22px">What is measured and what is modelled</h2>
      <h3>Checked against instruments</h3><p>${n.water || ''}</p><p>${n.settled || ''}</p><p>${n.gusts || ''}</p>
      <h3>Known weakness</h3><p>${n.big_days || ''}</p>
      <h3>Modelled, not checked</h3><p>${n.zones}</p><p>${n.wind}</p><p>${n.sun}</p>
      <h3>Sources</h3><p class="note">Forecast models: NOAA HRRR, GFS and GEFS. Observations: USDA NRCS SNOTEL, NWS COOP, CoCoRaHS, and FAA/NWS airport stations through the Iowa Environmental Mesonet. Precipitation pattern: PRISM Climate Group, Oregon State University. Terrain: USGS 3DEP. Lifts and trails: &copy; OpenStreetMap contributors. Basemaps: USGS. Terrain tiles: Mapzen, hosted by AWS.</p>
      <p class="note"><b>Independent hobby project.</b> Not affiliated with, endorsed by or connected to Vail Resorts or Vail Mountain. Not an official forecast, not the resort's snow report, and not avalanche advice: for avalanche conditions see the Colorado Avalanche Information Center. Forecasts can be wrong; do not rely on this for safety or travel decisions. Built ${meta.built}.</p></div>`;
  }

  function renderLegend(density) {
    if (baseOn()) {
      $('legend').innerHTML = `<div class="t">Base depth, inches</div><div class="sw">${DEPTH.map(c => `<span style="background:${c}"></span>`).join('')}</div>
        <div class="lb">${depth.bins.labels.map(l => `<span>${l}</span>`).join('')}</div><div class="tag">Estimate: SNODAS tied to the SNOTEL sensor</div>`;
      return;
    }
    const colors = density ? DENSITY : RAMP;
    const labels = density ? meta.density.labels : meta.bins.labels;
    const tenTitle = `${{ low: 'Low end', fallen: { 'Expected': 'Expected snow', 'If it snows': 'Snow if it snows', 'Likely': 'Likely snow' }[tenMiddle()], high: 'High end' }[state.range]}, ${state.win === 'ten' ? tenWhen(false) : ''}, inches`;
    const title = state.win === 'ten' ? tenTitle : density ? 'Snow density' : { fallen: state.range === 'fallen' ? 'New snow, inches' : (state.range === 'low' ? 'Low end, inches' : 'High end, inches'), settled: 'Settled depth, inches' }[state.layer];
    $('legend').innerHTML = `<div class="t">${title}</div><div class="sw">${colors.map(c => `<span style="background:${c}"></span>`).join('')}</div>
      <div class="lb">${labels.map(l => `<span>${l.replace('trace-1', '<1')}</span>`).join('')}</div>` + (state.layer === 'fallen' && state.win !== 'ten' ? '<div class="tag">Includes wind loading, tuned to local reports</div>' : '') +
      (state.win === 'ten' ? '<div class="tag">Includes wind loading, tuned to local reports</div>' : '');
  }

  /* ---------- point forecast ---------- */
  function lookup(lng, lat) {
    const [a, b, c, d, e, f] = grid.fit;
    const col = Math.floor((a * lng + b * lat + c + 0.5) / grid.step), row = Math.floor((d * lng + e * lat + f + 0.5) / grid.step);
    if (col < 0 || row < 0 || col >= grid.cols || row >= grid.rows) return null;
    const i = cellIndex.get(row * grid.cols + col);
    return i === undefined ? null : i;
  }

  /* The ten days at a tapped spot: expected snow each day with its low and high end. */
  function pointDays(p) {
    if (!ogrid || !tenOK()) return null;
    const [a, b, c, d, e, f] = ogrid.fit;
    const col = Math.floor((a * p.lng + b * p.lat + c + 0.5) / ogrid.step), row = Math.floor((d * p.lng + e * p.lat + f + 0.5) / ogrid.step);
    const i = col < 0 || row < 0 || col >= ogrid.cols || row >= ogrid.rows ? undefined : oIndex.get(row * ogrid.cols + col);
    if (i === undefined) return null;
    const rows = outlook.days.filter(x => x.available).map(x => { const v = ogrid['d' + pad(x.day)][i] / ogrid.scale; const lead = x.lead_lo_ratio != null; return { d: x, e: v, lo: v * x.low_ratio, hi: Math.max(v * x.high_ratio, v), mlo: v * (lead ? x.lead_lo_ratio : x.mid_lo_ratio || 0), mhi: v * (lead ? x.lead_hi_ratio : x.mid_hi_ratio || 0) }; });
    const sum = rows.reduce((t, r) => t + r.e, 0), T = outlook.total;               // the expected amounts add up to the total
    return { rows, total: T ? { e: sum, lo: sum * T.low_ratio, hi: sum * T.high_ratio, mlo: sum * (T.mid_lo_ratio || 0), mhi: sum * (T.mid_hi_ratio || 0) } : null };
  }

  function pointTable(pd) {
    if (!pd) return '';
    const sel = { low: 1, fallen: 2, high: 3 }[state.range], ten = state.win === 'ten';
    const cells = r => [hf(r.lo), rng(r.mlo, r.mhi), hf(r.hi)].map((v, n) => `<td class="${n + 1 === sel ? 'sel' : ''}">${v}</td>`).join('');
    return `<h3>Day by day at this spot</h3><table class="past days"><tr><th>Day</th>${['Low end', 'Range', 'High end'].map((h, n) => `<th class="${n + 1 === sel ? 'sel' : ''}">${h}</th>`).join('')}</tr>` +
      pd.rows.map(r => `<tr data-day="${r.d.day}" class="${ten && state.day === r.d.day ? 'on' : ''}"><td>${day(r.d.start, { weekday: 'short', month: 'short', day: 'numeric' })}</td>${cells(r)}</tr>`).join('') +
      (pd.total ? `<tr data-day="0" class="sum ${ten && !state.day ? 'on' : ''}"><td>All 10 days</td>${cells(pd.total)}</tr>` : '') +
      `</table><p class="note" style="margin-top:6px">Inches of new snow. Tap a row to show that day on the map. On a day with at least a 1 in 5 chance of snow the range is what falls if it does snow; below that it reads 0. The low and high end are the 1-in-10 outcomes either side. Past day 3 the numbers come from the ensemble and the National Blend.</p>`;
  }

  function showPoint(p) {
    const i = lookup(p.lng, p.lat);
    const el = $('tab-point');
    mapReady.then(() => map.getSource('pin').setData({ type: 'FeatureCollection', features: i == null ? [] : [{ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lng, p.lat] }, properties: {} }] }));
    if (i == null) { el.innerHTML = '<p class="hint">That spot is outside the mapped ski terrain. Tap inside a zone.</p>'; return; }
    const h = win(), get = k => grid[`${k}_${h}`][i] / grid.scale;
    const zone = zones[grid.zone[i] - 1] ? zones.find((z, n) => n === grid.zone[i] - 1) : null;
    if (baseOn()) {
      const [a, b, c, d, e, f] = dgrid ? dgrid.fit : [];
      const col = dgrid ? Math.floor((a * p.lng + b * p.lat + c + 0.5) / dgrid.step) : -1, row = dgrid ? Math.floor((d * p.lng + e * p.lat + f + 0.5) / dgrid.step) : -1;
      const k = dgrid ? dIndex.get(row * dgrid.cols + col) : undefined;
      el.innerHTML = k === undefined ? '<p class="hint">No depth estimate for that spot.</p>' : `<div class="why"><div class="note">${zone ? zone.name : 'Ski area'} &middot; ${Math.round(grid.elev_ft[i] / 10) * 10} ft</div>
        <div class="pointhead">${fmt(dgrid.depth[k] / dgrid.scale)}<small> in on the ground (estimate)</small></div>
        <p class="note">Natural snowpack from NOAA's SNODAS model of ${day(depth.model_date + 'T12:00:00', { month: 'short', day: 'numeric' })}, tied to the mid-mountain SNOTEL sensor${depth.gauge ? ` (${fmt(depth.gauge.depth_in)} in)` : ''}. The model's cells are a kilometre wide: this is the area's depth adjusted for the spot's elevation, not a measurement at the spot.</p></div>`;
      return;
    }
    const pd = pointDays(p), wire = () => el.querySelectorAll('tr[data-day]').forEach(r => { r.onclick = () => enterTen(+r.dataset.day); });
    if (state.win === 'ten' && pd) {
      const r = state.day ? pd.rows.find(x => x.d.day === state.day) : pd.total, shown = { low: hf(r.lo), fallen: rng(r.mlo, r.mhi), high: hf(r.hi) }[state.range];
      el.innerHTML = `<div class="why"><div class="note">${zone ? zone.name : 'Ski area'} &middot; ${Math.round(grid.elev_ft[i] / 10) * 10} ft</div>
        <div class="pointhead">${shown}<small> in ${{ low: 'at the low end', fallen: r.d && r.d.lead_if_snow ? 'if it snows' : 'likely', high: 'at the high end' }[state.range]}</small></div>
        <div class="note">${state.day ? tenWhen(true) : 'Next 10 days'}: ${r.d ? (r.d.chance['1in'] <= 5 ? 'under 5' : r.d.chance['1in']) + '% chance of snow; ' : ''}expected ${hf(r.e)} in; 1 in 10 chance of more than ${hf(r.hi)}</div>${pointTable(pd)}</div>`;
      return wire();
    }
    const total72 = grid.fallen_72[i] / grid.scale, curve = meta.hourly_curve_in, end = curve[curve.length - 1] || 1;
    const series = curve.map(v => total72 * v / end);
    const W = 320, H = 120, top = Math.max(1, series[72]) * 1.08, x = n => 28 + (W - 36) * n / 72, y = v => H - 18 - (H - 30) * v / top;
    const path = series.map((v, n) => `${n ? 'L' : 'M'}${x(n).toFixed(1)},${y(v).toFixed(1)}`).join('');
    const ticks = [0, 24, 48, 72].map(n => `<line x1="${x(n)}" x2="${x(n)}" y1="${y(0)}" y2="${y(0) + 4}" stroke="#93a4b5"/><text x="${x(n)}" y="${H - 2}" fill="#93a4b5" font-size="10" text-anchor="middle">${n ? n + ' h' : 'now'}</text>`).join('');
    const mark = h === '72' ? '' : `<line x1="${x(+h)}" x2="${x(+h)}" y1="${y(0)}" y2="${y(top / 1.08)}" stroke="#ff7a2f" stroke-dasharray="3 3"/>`;
    el.innerHTML = `<div class="why"><div class="note">${zone ? zone.name : 'Ski area'} &middot; ${Math.round(grid.elev_ft[i] / 10) * 10} ft</div>
      <div class="pointhead">${grid['mid_lo_' + h] ? rng(get('mid_lo'), get('mid_hi')) : hf(get('fallen'))}<small> in likely</small></div>
      <div class="note">Most likely ${hf(get('fallen'))} in; 1 in 10 chance of under ${hf(get('low'))} or over ${hf(get('high'))}. ${windowLabel(+h)}, wind loading included.</div>
      <div class="kv"><span>Settled by the end</span><span>${hf(get('settled'))} in</span>
        <span>Snow to water</span><span>${fmt(get('density'))} to 1</span></div>
      <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Accumulation by hour">
        <line x1="28" x2="${W - 8}" y1="${y(0)}" y2="${y(0)}" stroke="#93a4b5" stroke-opacity=".5"/>
        <path d="${path}L${x(72)},${y(0)}L${x(0)},${y(0)}Z" fill="#86b6ef" fill-opacity=".22"/><path d="${path}" fill="none" stroke="#86b6ef" stroke-width="2"/>
        ${mark}${ticks}<text x="4" y="${y(series[72]) + 4}" fill="#eef3f8" font-size="11">${fmt(series[72])}"</text></svg>
      <p class="note">How the new snow builds up. The timing comes from HRRR at mid-mountain and is the same shape everywhere on the hill; day 3 is spread evenly.</p>${pointTable(pd)}</div>`;
    wire();
  }

  map.on('click', e => { state.point = e.lngLat; showPoint(e.lngLat); tab('point'); if (innerWidth < 761 && document.body.classList.contains('sheet-low')) sheet(''); });

  /* ---------- scrubber ---------- */
  function frameTime(h) { return day(new Date(new Date(meta.run + 'Z').getTime() + h * 36e5).toISOString().slice(0, 19), { weekday: 'short', hour: 'numeric' }); }
  function frameTo(n) { state.frame = meta.frames[n]; $('scrubLabel').textContent = `+${state.frame} h, ${frameTime(state.frame)}`; refresh(); }
  function stop() { clearInterval(timer); timer = null; $('play').innerHTML = '&#9654;'; }
  $('slider').oninput = e => { stop(); frameTo(+e.target.value); };
  $('play').onclick = () => {
    if (timer) return stop();
    $('play').innerHTML = '&#10073;&#10073;';
    if (+$('slider').value >= meta.frames.length - 1) $('slider').value = 0;
    frameTo(+$('slider').value);
    timer = setInterval(() => { const n = +$('slider').value + 1; if (n >= meta.frames.length) return stop(); $('slider').value = n; frameTo(n); }, 900);
  };

  /* ---------- tabs, sheet, map buttons ---------- */
  function tab(name) {
    state.tab = name;
    document.querySelectorAll('#tabs button').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === name)));
    document.querySelectorAll('.tab').forEach(s => { s.hidden = s.id !== 'tab-' + name; });
  }
  document.querySelectorAll('#tabs button').forEach(b => { b.onclick = () => tab(b.dataset.tab); });
  function sheet(size) { $('panel').className = size; document.body.classList.toggle('sheet-tall', size === 'tall'); document.body.classList.toggle('sheet-low', size === 'low'); }
  $('grip').onclick = () => sheet({ '': 'tall', tall: 'low', low: '' }[$('panel').className]);
  const toggle = (id, fn) => { $(id).onclick = () => { const on = $(id).getAttribute('aria-pressed') !== 'true'; $(id).setAttribute('aria-pressed', String(on)); fn(on); }; };
  toggle('basemap', on => {
    ['relief', 'topo'].forEach(l => map.setLayoutProperty(l, 'visibility', on ? 'none' : 'visible'));
    map.setLayoutProperty('sat', 'visibility', on ? 'visible' : 'none');
    map.setPaintProperty('snow-fill', 'fill-opacity', on ? 0.72 : 0.66);
    ['zones-line', 'trails'].forEach(l => map.setPaintProperty(l, 'line-color', on ? '#ffffff' : '#0b1622'));
  });
  toggle('three', on => { map.setTerrain(on ? { source: 'dem', exaggeration: 1.4 } : null); map.easeTo({ pitch: on ? 58 : 0, bearing: on ? -20 : 0, duration: 900 }); });
  toggle('overlay', on => ['trails', 'lifts', 'lifts-case'].forEach(l => map.setLayoutProperty(l, 'visibility', on ? 'visible' : 'none')));

  loadRun().catch(err => { $('issued').textContent = 'No forecast files found'; console.error(err); });
})();
