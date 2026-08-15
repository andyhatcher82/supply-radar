/* Supply Radar front end.
   Vanilla JS, no build step, no framework. One file, six views. */

const S = { snap: null, regions: null, terms: null, selectedTerms: [],
            view: 'overview', map: null, layer: null,
            mode: 'circle', shape: null, estimate: null, decisions: {},
            permitted: true, permitMsg: '',
            adminCfg: null, adminCode: '' };

/* Ray casting. Used only to keep the UI honest before a request is sent —
   the API re-checks every sweep, because a browser gate is a courtesy. */
function pointInRing(lat, lng, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [yi, xi] = ring[i], [yj, xj] = ring[j];
    if ((xi > lng) !== (xj > lng) &&
        lat < (yj - yi) * (lng - xi) / (xj - xi) + yi) inside = !inside;
  }
  return inside;
}

function regionFor(lat, lng) {
  for (const r of (S.regions?.enabled || [])) {
    if (pointInRing(lat, lng, r.polygon)) return r;
  }
  return null;
}

/* A shape is permitted only if it sits ENTIRELY inside one enabled market.
   Partial overlap would spend budget on a market nobody has opened, and the
   operators found could not be actioned by anyone. */
function checkShape(shape) {
  if (!shape) return { ok: false, msg: '' };
  let points;
  if (shape.kind === 'circle') {
    points = [];
    const dLat = shape.radius_km / 111.32;
    const dLng = shape.radius_km / (111.32 * Math.cos(shape.lat * Math.PI / 180));
    for (let a = 0; a < 360; a += 15) {
      const r = a * Math.PI / 180;
      points.push([shape.lat + dLat * Math.sin(r), shape.lng + dLng * Math.cos(r)]);
    }
    points.push([shape.lat, shape.lng]);
  } else {
    points = shape.points;
  }
  const regions = (S.regions?.enabled || []);
  for (const r of regions) {
    if (points.every(p => pointInRing(p[0], p[1], r.polygon))) {
      return { ok: true, msg: `Inside ${r.name}.`, region: r };
    }
  }
  const touching = regions.find(r => points.some(p => pointInRing(p[0], p[1], r.polygon)));
  if (touching) {
    return { ok: false, msg: `This area extends beyond ${touching.name}. Searches must sit entirely inside an enabled market.` };
  }
  return { ok: false, msg: `Outside every enabled market. Currently enabled: ${regions.map(r => r.name).join(', ') || 'none'}.` };
}

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (n) => (n * 100).toFixed(1) + '%';
const n3 = (n) => Number(n).toFixed(3);
const gbp = (n) => '£' + Number(n).toFixed(2);

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* noop */ }
    throw new Error(detail);
  }
  return res.json();
}

function bar(value, cls) {
  const w = Math.max(0, Math.min(100, value * 100));
  return `<div class="bar ${cls || ''}"><i style="width:${w}%"></i></div>`;
}

/* ------------------------------------------------------------- overview */

function viewOverview() {
  const s = S.snap, c = s.counts, m = s.metrics.matching;
  const gaps = s.category_gaps.slice(0, 7);

  return `
  <div class="grid g4">
    ${stat('Places discovered', c.places_discovered, 'Real Google Places results')}
    ${stat('Experience operators', c.operators, `${c.not_relevant} filtered out as not relevant`)}
    ${stat('Genuinely net-new', c.net_new, `of the ${c.operators} operators`, 'good')}
    ${stat('Sent to a human', pct(m.review_rate), `${pct(m.automation_rate)} decided automatically`)}
  </div>

  <p class="section-title">The funnel, one denominator throughout</p>
  <div class="card">
    <div class="scroll"><table>
      <thead><tr><th>Step</th><th class="num">Records</th><th>What happened</th></tr></thead>
      <tbody>
        <tr><td>Discovered</td><td class="num">${c.places_discovered}</td>
          <td style="color:var(--muted)">Everything Google Places returned for the search terms</td></tr>
        <tr><td>Not an experience operator</td><td class="num">−${c.not_relevant}</td>
          <td style="color:var(--muted)">Judged on what they <em>sell</em>, not what they are</td></tr>
        <tr style="border-top:2px solid var(--line)"><td><strong>Experience operators</strong></td>
          <td class="num"><strong>${c.operators}</strong></td>
          <td style="color:var(--muted)">Everything below is a subset of this</td></tr>
        <tr><td>Already a Viator supplier</td><td class="num">${c.already_on_file}</td>
          <td style="color:var(--muted)">Matched to the supplier list</td></tr>
        <tr><td>Needs a human decision</td><td class="num">${c.needs_review}</td>
          <td style="color:var(--muted)">Too close to call automatically</td></tr>
        <tr><td><strong>Net-new leads</strong></td><td class="num"><strong>${c.net_new}</strong></td>
          <td style="color:var(--muted)">Operators Viator does not have</td></tr>
      </tbody>
    </table></div>
    <div class="note">Classification runs <em>before</em> matching, so the matcher never
      compares a car park against the supplier list and every figure here shares one
      denominator: ${c.already_on_file} + ${c.needs_review} + ${c.net_new} = ${c.operators}.</div>

    <div class="note"><strong>The filter is about what a business sells, not what type
      Google says it is.</strong> A museum running guided tours or workshops is an
      experience operator; one that only sells admission at the door is an attraction. A
      restaurant selling a cooking class or a scheduled tasting is an operator; one where
      you turn up and eat is not. Real decisions from this run:
      <em>"Meštrović Gallery — selling admission to view exhibits, with no scheduled
      guided activity"</em> and <em>"Restaurant Krug — no evidence of scheduled tastings
      or classes, just dining"</em>. Every one of those reasons is shown to the reviewer
      so they can overrule it.</div>
  </div>

  <p class="section-title">What the pipeline does</p>
  <div class="card">
    <div class="scroll"><table>
      <thead><tr><th>Stage</th><th>What it decides</th><th class="num">Cost</th><th>Who decides</th></tr></thead>
      <tbody>
        <tr><td>Discover</td><td>Which operators exist in the area</td><td class="num">£0.76</td><td>Google Places, adaptive cell subdivision</td></tr>
        <tr><td>Classify</td><td>Is this an experience operator at all</td><td class="num">£0.29</td><td>Rules first, model only for the ambiguous 64%</td></tr>
        <tr><td>Match</td><td>Are they already a Viator supplier</td><td class="num">£0.00</td><td>Deterministic keys, then fuzzy, then a human</td></tr>
        <tr><td>Enrich</td><td>Can they actually transact</td><td class="num">£1.53</td><td>Their own website, read by the model</td></tr>
        <tr><td>Score</td><td>Which leads are worth Sales time</td><td class="num">£0.00</td><td>Three separate axes, evidence shown</td></tr>
      </tbody>
    </table></div>
    <div class="note">Costs are measured from the real Split run, not modelled.
      Total <strong>${gbp(s.economics.per_destination.total_gbp)}</strong> per destination
      against an assumed <strong>${gbp(s.economics.versus_manual.manual_cost_gbp)}</strong> of manual research.</div>
  </div>

  <p class="section-title">Where the opportunity actually is <span class="synthetic">demand data synthetic</span></p>
  <div class="card">
    <p class="hint">Discovery found the most boat-tour operators. Gap fit says they are the
      least valuable, because that category is already saturated. This is the judgement a
      generic lead-generation tool cannot make.</p>
    <div class="scroll"><table>
      <thead><tr><th>Category</th><th class="num">Operators found</th><th class="num">Gap fit</th><th style="width:34%">Unmet demand</th><th>Evidence</th></tr></thead>
      <tbody>${gaps.map(g => `
        <tr>
          <td>${esc(g.category.replace(/_/g, ' '))}</td>
          <td class="num">${g.operators_found}</td>
          <td class="num">${n3(g.gap_fit)}</td>
          <td>${bar(g.gap_fit, 'g')}</td>
          <td style="color:var(--dim);font-size:12.5px">${esc(g.evidence.split(': ')[1] || g.evidence)}</td>
        </tr>`).join('')}
      </tbody>
    </table></div>
  </div>`;
}

function stat(k, v, note, cls) {
  return `<div class="card stat"><div class="k">${esc(k)}</div>
    <div class="v ${cls || ''}">${esc(v)}</div>
    ${note ? `<div class="n">${esc(note)}</div>` : ''}</div>`;
}

/* ------------------------------------------------------------- discover */

function viewDiscover() {
  return `
  <div class="discover-wrap">
    <div>
      <div class="card">
        <h2>Draw a search area</h2>
        <p class="hint">This runs a genuinely live sweep against Google Places. It costs
          real money, so it tells you what it will cost first.</p>

        <div class="modes">
          <button data-mode="circle" class="${S.mode === 'circle' ? 'active' : ''}">Point &amp; radius</button>
          <button data-mode="polygon" class="${S.mode === 'polygon' ? 'active' : ''}">Draw a shape</button>
        </div>

        <div id="modehelp" class="note" style="margin-top:0"></div>

        <label class="field" style="margin-top:14px">
          <span>Radius (km)</span>
          <input type="number" id="radius" value="4" min="1" max="25" step="0.5">
        </label>
        <label class="field">
          <span>Cell size (km half-side)</span>
          <input type="number" id="cell" value="3" min="1" max="10" step="0.5">
        </label>
        <label class="field">
          <span>Search terms (pick up to ${S.terms?.max_selectable || 3})</span>
          <div id="terms" class="termlist">${termOptions()}</div>
        </label>
        <label class="field">
          <span>Access code</span>
          <input type="password" id="code" placeholder="required to spend money">
        </label>

        <div style="display:flex;gap:8px">
          <button class="btn ghost" id="btnEstimate" style="flex:1">Estimate cost</button>
          <button class="btn" id="btnRun" style="flex:1" disabled>Run sweep</button>
        </div>

        <div class="estimate" id="estimate"></div>
      </div>

      <div class="card" style="margin-top:14px">
        <h2>Permitted markets</h2>
        <p class="hint">Searching is restricted to markets the business has opened.
          Everywhere else is greyed out on the map.</p>
        ${(S.regions?.enabled || []).map(r => `
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
            <span class="pill good">open</span><strong>${esc(r.name)}</strong>
          </div>
          ${r.note ? `<p style="color:var(--dim);font-size:12.5px;margin:0 0 10px">${esc(r.note)}</p>` : ''}`).join('')}
        ${(S.regions?.disabled || []).map(r => `
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
            <span class="pill grey">closed</span><span style="color:var(--muted)">${esc(r.name)}</span>
          </div>
          ${r.note ? `<p style="color:var(--dim);font-size:12.5px;margin:0 0 10px">${esc(r.note)}</p>` : ''}`).join('')}
        <div class="note">Opening a market is a change to one config file, not a code
          change. That is what "scales to hundreds of destinations" has to mean in
          practice. The API re-checks every request, so the map is a courtesy rather
          than the boundary.</div>
      </div>
    </div>
    <div>
      <div id="map"></div>
      <div id="runout"></div>
    </div>
  </div>`;
}

function termOptions() {
  const terms = S.terms?.terms || [];
  const byCat = {};
  terms.forEach(t => (byCat[t.category] = byCat[t.category] || []).push(t));
  return Object.entries(byCat).map(([cat, ts]) => `
    <div class="termgroup">
      <div class="termcat">${esc(cat.replace(/_/g, ' '))}</div>
      ${ts.map(t => `
        <label class="term ${S.selectedTerms.includes(t.term) ? 'on' : ''}"
               title="${esc(t.note || '')}">
          <input type="checkbox" value="${esc(t.term)}"
            ${S.selectedTerms.includes(t.term) ? 'checked' : ''}>
          ${esc(t.term)}
        </label>`).join('')}
    </div>`).join('');
}

const MODE_HELP = {
  circle: 'Click the map to place a centre, then set a radius. Simple, and it matches how the Places API actually works.',
  polygon: 'Use the toolbar on the map to trace a shape. Slower, but on a coastline it avoids spending API calls on open sea.',
};

function initMap() {
  const map = L.map('map', { zoomControl: true }).setView([43.5081, 16.4402], 12);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors',
  }).addTo(map);
  S.map = map;

  /* Grey out everywhere the business has not opened. One outer ring covering
     the world, with each enabled market punched out as a hole. */
  const enabled = S.regions?.enabled || [];
  if (enabled.length) {
    const world = [[-89, -179], [89, -179], [89, 179], [-89, 179]];
    L.polygon([world, ...enabled.map(r => r.polygon)], {
      color: '#2a3038', weight: 0, fillColor: '#080a0d', fillOpacity: 0.72,
      interactive: false,
    }).addTo(map);
    enabled.forEach(r => {
      L.polygon(r.polygon, {
        color: '#4fd1c5', weight: 1.5, fill: false, dashArray: '4 4',
        interactive: false,
      }).addTo(map).bindTooltip(`${r.name} — open for search`, { sticky: true });
    });
  }

  S.layer = L.layerGroup().addTo(map);

  map.on('click', (e) => { if (S.mode === 'circle') setCircle(e.latlng); });

  map.pm.addControls({
    position: 'topright', drawCircle: false, drawMarker: false,
    drawCircleMarker: false, drawPolyline: false, drawText: false,
    drawRectangle: true, drawPolygon: true, editMode: true,
    dragMode: false, cutPolygon: false, rotateMode: false,
  });
  map.on('pm:create', (e) => {
    S.layer.clearLayers();
    S.shape = { kind: 'polygon', points: e.layer.getLatLngs()[0].map(p => [p.lat, p.lng]) };
    e.layer.setStyle({ color: '#4fd1c5', weight: 2, fillOpacity: 0.08 });
    S.layer.addLayer(e.layer);
    map.pm.disableDraw();
    clearEstimate();
  });

  applyMode();
  setCircle(L.latLng(43.5081, 16.4402));
}

function applyMode() {
  $('#modehelp').textContent = MODE_HELP[S.mode];
  const ctl = document.querySelector('.leaflet-pm-toolbar');
  if (ctl) ctl.style.display = S.mode === 'polygon' ? '' : 'none';
  $('#radius').closest('label').style.display = S.mode === 'circle' ? '' : 'none';
}

function setCircle(latlng) {
  const radius = parseFloat($('#radius').value) || 4;
  S.layer.clearLayers();
  S.shape = { kind: 'circle', lat: latlng.lat, lng: latlng.lng, radius_km: radius };
  L.circle(latlng, {
    radius: radius * 1000, color: '#4fd1c5', weight: 2, fillOpacity: 0.08,
  }).addTo(S.layer);
  clearEstimate();
}

function clearEstimate() {
  S.estimate = null;
  $('#btnRun').disabled = true;
  const check = checkShape(S.shape);
  S.permitted = check.ok;
  S.permitMsg = check.msg;
  const btn = $('#btnEstimate');
  if (btn) btn.disabled = !!S.shape && !check.ok;
  $('#estimate').innerHTML = (S.shape && !check.ok)
    ? `<div class="note warn"><strong>Outside a permitted market.</strong><br>${esc(check.msg)}</div>`
    : (S.shape && check.msg ? `<div class="note">${esc(check.msg)} Estimate the cost to continue.</div>` : '');
}

function requestBody() {
  const queries = S.selectedTerms.slice();
  const cell = parseFloat($('#cell').value) || 3;
  if (!S.shape) return null;
  if (S.shape.kind === 'circle') {
    return { shape: 'circle', center_lat: S.shape.lat, center_lng: S.shape.lng,
             radius_km: S.shape.radius_km, cell_km: cell, queries };
  }
  return { shape: 'polygon', points: S.shape.points, cell_km: cell, queries };
}

async function doEstimate() {
  const body = requestBody();
  if (!body) { $('#estimate').innerHTML = '<div class="note warn">Place a point or draw a shape first.</div>'; return; }
  $('#estimate').innerHTML = '<div class="note">Estimating…</div>';
  try {
    const e = await api('/api/estimate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    S.estimate = e;
    $('#estimate').innerHTML = `
      <div class="row"><span>Area</span><span>${e.area_km2} km²</span></div>
      <div class="row"><span>Cells</span><span>${e.cells}</span></div>
      <div class="row"><span>Search terms</span><span>${e.queries_per_cell}</span></div>
      <div class="row"><span>API calls</span><span>${e.estimated_calls}</span></div>
      <div class="row"><span>Estimated cost</span><span>${gbp(e.estimated_gbp)}</span></div>
      <div class="row"><span>Estimated time</span><span>${e.estimated_seconds}s</span></div>
      <div class="note ${e.within_live_run_limit ? '' : 'warn'}">${esc(e.message)}</div>`;
    $('#btnRun').disabled = !e.within_live_run_limit;
  } catch (err) {
    $('#estimate').innerHTML = `<div class="note warn">${esc(err.message)}</div>`;
  }
}

async function doRun() {
  const body = requestBody();
  const code = $('#code').value.trim();
  $('#btnRun').disabled = true;
  $('#runout').innerHTML = '<div class="card" style="margin-top:16px">Running a live sweep… discovery, then classification, then scoring.</div>';
  try {
    const r = await api('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Access-Code': code },
      body: JSON.stringify(body),
    });
    renderRun(r);
  } catch (err) {
    $('#runout').innerHTML = `<div class="card" style="margin-top:16px"><div class="note warn">${esc(err.message)}</div></div>`;
  }
  $('#btnRun').disabled = false;
}

function renderRun(r) {
  const d = r.discovery, c = r.classification;
  r.leads.forEach(lead => {
    L.circleMarker([lead.lat, lead.lng], {
      radius: 5, color: '#4fd1c5', fillColor: '#4fd1c5', fillOpacity: 0.85, weight: 1,
    }).bindPopup(`<strong>${esc(lead.name)}</strong><br>${esc(lead.category || '')}<br>score ${n3(lead.composite)}`)
      .addTo(S.layer);
  });

  $('#runout').innerHTML = `
    <div class="card" style="margin-top:16px">
      <h2>Live run complete <span class="pill good">${r.elapsed_seconds}s</span></h2>
      <div class="grid g4" style="margin-top:14px">
        ${stat('Places found', d.places, `${d.cells_queried} cells, ${d.api_calls} API calls`)}
        ${stat('Cells subdivided', d.cells_subdivided, `${d.truncated_cells} hit the result cap`)}
        ${stat('Operators', r.leads.length, c ? `${pct(c.model_share)} needed the model` : '')}
        ${stat('Cost', gbp(r.cost.gbp), 'this run, measured')}
      </div>
      ${d.unresolved_cells ? `<div class="note warn">${d.unresolved_cells} cells were still returning a
        full page at maximum depth. Coverage is incomplete there, and the tool says so rather
        than reporting a clean result.</div>` : ''}
      ${r.caveats.map(c => `<div class="note">${esc(c)}</div>`).join('')}
      <p class="section-title">Top leads from this sweep</p>
      <div class="scroll"><table>
        <thead><tr><th>Operator</th><th>Category</th><th class="num">Rating</th><th class="num">Score</th><th>Band</th></tr></thead>
        <tbody>${r.leads.slice(0, 15).map(l => `
          <tr><td>${esc(l.name)}</td><td style="color:var(--muted)">${esc((l.category || '—').replace(/_/g, ' '))}</td>
          <td class="num">${l.rating ?? '—'}</td><td class="num">${n3(l.composite)}</td>
          <td><span class="pill ${l.band}">${l.band}</span></td></tr>`).join('')}
        </tbody>
      </table></div>
    </div>`;
}

/* ---------------------------------------------------------------- leads */

const BANDS = {
  A: ['Contact first', 'Highest on the weighted composite. Open the lead to see which axes carried it — an operator can reach A on quality and readiness alone, even where the category has no supply gap.'],
  B: ['Worth contacting', 'Solid on at least one axis with a visible caveat on another. Read the evidence before calling.'],
  C: ['Park for now', 'Thin evidence, or the category is already well served and adding supply mostly cannibalises it.'],
};

function viewLeads() {
  const leads = S.snap.leads;
  const counts = { A: 0, B: 0, C: 0 };
  leads.forEach(l => counts[l.band]++);

  return `
  <div class="card">
    <h2>Qualified leads <span class="pill grey">${leads.length}</span></h2>
    <p class="hint">Ranked by composite score. Click any lead for the full evidence trail
      behind all three axes. The composite is a sort order, not a decision.</p>

    <div class="grid g3" style="margin-top:6px">
      ${['A', 'B', 'C'].map(b => `
        <div style="display:flex;gap:10px;align-items:flex-start">
          <span class="pill ${b}" style="margin-top:2px">${b}</span>
          <div>
            <div style="font-weight:600">${BANDS[b][0]} <span style="color:var(--dim);font-weight:400">— ${counts[b]} leads</span></div>
            <div style="color:var(--muted);font-size:12.5px">${BANDS[b][1]}</div>
          </div>
        </div>`).join('')}
    </div>

    <div class="note">Bands come from the composite of the three axes, weighted
      35% quality / 35% readiness / 30% gap fit. The cut-offs are recalibrated per
      destination, because a saturated category scores 0.00 on gap fit and that caps
      what any operator there can reach.</div>

    ${counts.A === 0 ? `<div class="note warn"><strong>No A-band leads here, and that is
      the model working rather than failing.</strong> Every operator found sits in a
      category the demand model says is already well served. Good businesses in a
      saturated market: worth knowing about, not worth prioritising over an under-served
      category elsewhere.</div>` : `<div class="note">Every lead here scores 0.00 on gap
      fit, because the categories found in this destination are already well served. The
      A-band leads earned their place on quality and readiness alone — strong operators
      in a competitive market, rather than an unmet need.</div>`}
  </div>
  <div style="margin-top:14px">${leads.map(leadRow).join('')}</div>`;
}

function leadRow(l, i) {
  const ax = (name, axis, cls) => `
    <div class="axis"><div class="k">${name}</div>
      <div class="v">${n3(axis.score)}</div>${bar(axis.score, cls)}</div>`;
  return `
  <div class="lead" data-lead="${i}">
    <div class="lead-head">
      <div>
        <div class="lead-name">${esc(l.name)}
          <span class="pill ${l.band}" title="${esc(BANDS[l.band][0])}: ${esc(BANDS[l.band][1])}">${l.band}</span></div>
        <div class="lead-meta">${l.category
            ? esc(l.category.replace(/_/g, ' ')) +
              (l.category_source === 'search term'
                ? '<span title="No classifier call was needed for this operator, so the category comes from the search term that found them" style="color:var(--dim)"> (from search term)</span>'
                : '')
            : '<span style="color:var(--dim)">category not determined</span>'}
          ${l.website ? ` &middot; ${esc(l.website.replace(/^https?:\/\//, '').slice(0, 44))}` : ' &middot; no website'}
          ${l.extract ? ` &middot; ${esc(l.extract.booking.replace(/_/g, ' '))}` : ''}</div>
      </div>
      <div class="axes">
        ${ax('Quality', l.quality, 'q')}
        ${ax('Readiness', l.readiness, 'r')}
        ${ax('Gap fit', l.gap_fit, 'g')}
        <div class="axis"><div class="k">Composite</div>
          <div class="v" style="font-size:16px">${n3(l.composite)}</div></div>
      </div>
    </div>
    <div class="lead-body">
      <div class="grid g3" style="margin-top:14px">
        ${axisCard('Quality', l.quality)}
        ${axisCard('Readiness', l.readiness)}
        ${axisCard('Gap fit', l.gap_fit)}
      </div>
    </div>
  </div>`;
}

function axisCard(title, axis) {
  return `<div>
    <p class="section-title" style="margin:0 0 8px">${title} — ${n3(axis.score)}</p>
    <div class="evidence">${axis.components.map(c => `
      <div class="ev">
        <div class="n">${esc(c.name)}</div>
        <div class="v">${n3(c.value)}</div>
        <div class="d">${esc(c.evidence)}</div>
      </div>`).join('')}</div>
    ${axis.note ? `<div class="note">${esc(axis.note)}</div>` : ''}
  </div>`;
}

/* --------------------------------------------------------------- review */

function viewReview() {
  const q = S.snap.review_queue;
  const done = Object.keys(S.decisions).length;
  return `
  <div class="card">
    <h2>Match review queue <span class="pill grey">${q.length}</span></h2>
    <p class="hint">These are the pairs the deterministic stages could not settle. Everything
      above and below this band was decided without a human. ${done ? `<strong>${done} decided this session.</strong>` : ''}</p>
    <div class="note">A decision here would, in production, write back to the CRM and feed
      threshold tuning. In this prototype it is captured client-side only, which is stated
      rather than implied.</div>
  </div>
  <div style="margin-top:14px">${q.slice(0, 40).map(reviewCard).join('')}</div>`;
}

function reviewCard(r, i) {
  const decided = S.decisions[i];
  const f = (label, a) => `<div class="l">${label}</div><div class="val">${esc(a || '—')}</div>`;
  return `
  <div class="lead" style="margin-bottom:12px">
    <div class="pair">
      <div>
        <h4>Discovered operator</h4>
        ${f('Name', r.discovered_name)}${f('Address', r.discovered_address)}
        ${f('Website', r.discovered_website)}${f('Phone', r.discovered_phone)}
      </div>
      <div>
        <h4>Possible existing supplier</h4>
        ${f('Name', r.supplier_name)}${f('Address', r.supplier_address)}
        ${f('Website', r.supplier_website)}${f('Phone', r.supplier_phone)}
      </div>
    </div>
    <div style="padding:12px 16px;border-top:1px solid var(--line)">
      <div class="evidence">${(r.evidence || []).map(e => `
        <div class="ev"><div class="n">${esc(e.signal)}</div>
        <div class="v">${e.contribution == null ? '' : (e.contribution > 0 ? '+' : '') + Number(e.contribution).toFixed(2)}</div>
        <div class="d">${esc(e.detail)}</div></div>`).join('')}</div>
    </div>
    <div class="decide">
      <span style="color:var(--muted);font-size:13px">Similarity ${n3(r.score)}</span>
      <div class="spacer"></div>
      ${decided
        ? `<span class="pill ${decided === 'same' ? 'bad' : 'good'}">${decided === 'same' ? 'Marked as already on file' : 'Marked as net-new'}</span>`
        : `<button class="btn ghost" data-decide="${i}" data-v="same">Same business</button>
           <button class="btn" data-decide="${i}" data-v="new">Genuinely net-new</button>`}
    </div>
  </div>`;
}

/* -------------------------------------------------------------- quality */

function viewQuality() {
  const m = S.snap.metrics, mm = m.matching;
  const sweepRows = (rows) => `
    <div class="scroll"><table>
      <thead><tr><th class="num">high</th><th class="num">low</th><th class="num">precision</th>
      <th class="num">recall</th><th class="num">review</th><th class="num">missed</th><th class="num">wasted</th></tr></thead>
      <tbody>${rows.map(r => {
        const chosen = r.high === m.thresholds.high && r.low === m.thresholds.low;
        return `<tr style="${chosen ? 'background:rgba(79,209,197,.09)' : ''}">
          <td class="num">${r.high}${chosen ? ' ←' : ''}</td><td class="num">${r.low}</td>
          <td class="num">${n3(r.precision)}</td><td class="num">${n3(r.recall)}</td>
          <td class="num">${pct(r.review_rate)}</td>
          <td class="num" style="color:var(--bad)">${r.missed_opportunity}</td>
          <td class="num" style="color:var(--muted)">${r.wasted_call}</td></tr>`;
      }).join('')}</tbody>
    </table></div>`;

  return `
  <div class="grid g4">
    ${stat('Precision', n3(mm.precision), 'Of those called existing, how many were', 'good')}
    ${stat('Missed opportunities', mm.missed_opportunity, 'Real operators wrongly written off', mm.missed_opportunity > 12 ? 'bad' : 'warn')}
    ${stat('Wasted calls', mm.wasted_call, 'Existing suppliers sent to Sales again')}
    ${stat('Decided automatically', pct(mm.automation_rate), `${pct(mm.review_rate)} went to a human`)}
  </div>

  <div class="card" style="margin-top:16px">
    <h2>Why these two errors are not equal</h2>
    <p class="hint">The metrics are named after their business consequence rather than the
      confusion matrix, because the people who read them run Sales teams.</p>
    <div class="grid g2">
      <div>
        <p><strong style="color:var(--bad)">Missed opportunity</strong> — an operator we
        already have is wrongly recorded as already being a supplier. Nobody ever contacts
        them. Invisible, permanent, expensive.</p>
      </div>
      <div>
        <p><strong>Wasted call</strong> — an existing supplier is handed to Sales as a fresh
        lead. One awkward call, self-corrects immediately.</p>
      </div>
    </div>
    <div class="note">Every threshold in the system is set to spend the cheap error to buy
      down the expensive one. The classification audit samples deterministic <em>rejects</em>
      four times more heavily than accepts for the same reason.</div>
  </div>

  <p class="section-title">Upper threshold — governs the expensive error</p>
  <div class="card">${sweepRows(m.sweep_upper)}</div>

  <p class="section-title">Lower threshold — governs human review load</p>
  <div class="card">${sweepRows(m.sweep_lower)}
    <div class="note">Thresholds are read off these curves, not chosen. The highlighted row
      is what ships.</div>
  </div>

  <p class="section-title">How each decision was reached</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Verdict and stage</th><th class="num">Count</th></tr></thead>
    <tbody>${Object.entries(m.decisions_by_stage).sort().map(([k, v]) =>
      `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td class="num">${v}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="note">Most already-on-file decisions come from deterministic keys. That is
      why tuning the thresholds could not fix the two identity bugs found on real data —
      they were never in the fuzzy path.</div>
  </div>

  <p class="section-title">Corruptions applied to the synthetic supplier list <span class="synthetic">synthetic</span></p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Corruption</th><th class="num">Records</th></tr></thead>
    <tbody>${Object.entries(m.corruptions_applied).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
      `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td class="num">${v}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="note">The supplier list is seeded from real discovered operators and then
      degraded the way a real CRM degrades. Because we know what was seeded, precision and
      recall are measurable rather than asserted.</div>
  </div>`;
}

/* ------------------------------------------------------------ economics */

function viewEconomics() {
  const e = S.snap.economics, p = e.per_destination, v = e.versus_manual;
  return `
  <div class="grid g4">
    ${stat('Cost per destination', gbp(p.total_gbp), 'measured, not modelled')}
    ${stat('Manual equivalent', gbp(v.manual_cost_gbp), `${v.manual_hours}h of analyst time`, 'warn')}
    ${stat('Ratio', v.cost_ratio + '×', 'cheaper than manual research', 'good')}
    ${stat('Per operator surfaced', gbp(p.gbp_per_operator_surfaced), `${p.operators_surfaced} operators`)}
  </div>

  <p class="section-title">Where the money goes, per destination</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Stage</th><th class="num">USD</th><th class="num">Share</th></tr></thead>
    <tbody>
      ${costRow('Google Places discovery', p.places_usd, p.total_usd)}
      ${costRow('Classification (Sonnet)', p.classification_usd, p.total_usd)}
      ${costRow('Website enrichment (Sonnet)', p.enrichment_usd, p.total_usd)}
    </tbody>
  </table></div>
    <div class="note">Enrichment dominates because operator websites are large. Running it
      through the Batch API halves model cost, which is free money for an overnight job:
      <strong>${gbp(p.total_gbp_batched)}</strong> per destination.</div>
  </div>

  <p class="section-title">At scale</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th class="num">Destinations</th><th class="num">Cost</th><th class="num">Batched</th>
    <th class="num">Manual equivalent</th><th class="num">Analyst days saved</th></tr></thead>
    <tbody>${e.at_scale.map(r => `
      <tr><td class="num">${r.destinations}</td><td class="num">${gbp(r.cost_gbp)}</td>
      <td class="num">${gbp(r.cost_gbp_batched)}</td>
      <td class="num" style="color:var(--warn)">${gbp(r.manual_equivalent_gbp)}</td>
      <td class="num">${r.manual_analyst_days}</td></tr>`).join('')}
    </tbody></table></div>
  </div>

  <p class="section-title">Assumptions behind these numbers</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Assumption</th><th>Value</th><th>Basis</th></tr></thead>
    <tbody>${e.assumptions.map(a => `
      <tr><td>${esc(a.name)}</td><td>${esc(a.value)}</td>
      <td style="color:var(--muted);font-size:13px">${esc(a.source)}</td></tr>`).join('')}
    </tbody></table></div>
    <div class="note warn">The manual baseline is the number that moves this comparison most,
      and it is an assumption rather than a measurement. Correct it and every figure above
      moves with it.</div>
  </div>`;
}

function costRow(label, usd, total) {
  return `<tr><td>${label}</td><td class="num">$${Number(usd).toFixed(3)}</td>
    <td class="num">${pct(usd / total)}</td></tr>`;
}

/* ---------------------------------------------------------------- admin */

function viewAdmin() {
  if (!S.adminCfg) {
    return `
    <div class="card" style="max-width:460px">
      <h2>Administrator</h2>
      <p class="hint">Controls what everyone else is allowed to search, which model
        runs, and how much can be spent.</p>
      <label class="field"><span>Admin code</span>
        <input type="password" id="adminCode" placeholder="5-digit code"></label>
      <button class="btn" id="btnAdmin">Unlock</button>
      <div id="adminErr"></div>
      <div class="note">A separate key from the one that authorises a sweep.
        Authorising spend and changing what others may spend it on are different
        privileges. Role-based access replaces both on day 2.</div>
    </div>`;
  }

  const c = S.adminCfg;
  return `
  <div class="note warn" style="margin-bottom:16px"><strong>Changes apply to this
    running instance only.</strong> ${esc(c.persistence.note)}</div>

  <div class="grid g2">
    <div class="card">
      <h2>Markets</h2>
      <p class="hint">Where users may run a sweep. Closing one takes effect immediately.</p>
      ${c.regions.map(r => `
        <div style="display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)">
          <span class="pill ${r.enabled ? 'good' : 'grey'}">${r.enabled ? 'open' : 'closed'}</span>
          <div style="flex:1">
            <strong>${esc(r.name)}</strong>
            ${!r.configured_enabled ? '<span style="color:var(--dim);font-size:12px"> — not configured in this build</span>' : ''}
          </div>
          ${r.configured_enabled ? `<button class="btn ghost" data-region="${esc(r.id)}"
            data-on="${r.enabled ? '0' : '1'}">${r.enabled ? 'Close' : 'Open'}</button>` : ''}
        </div>`).join('')}
    </div>

    <div class="card">
      <h2>Model</h2>
      <p class="hint">Runs classification, match adjudication and website extraction.</p>
      ${c.models.map(m => `
        <label class="term ${m.id === c.active_model ? 'on' : ''}" style="display:block;margin-bottom:6px">
          <input type="radio" name="model" value="${esc(m.id)}" ${m.id === c.active_model ? 'checked' : ''}>
          <strong>${esc(m.label)}</strong>
          <div style="color:var(--dim);font-size:12.5px;margin-left:22px">${esc(m.note)}</div>
        </label>`).join('')}

      <h2 style="margin-top:18px">Spend</h2>
      <label class="field"><span>Daily cap (GBP)</span>
        <input type="number" id="cap" value="${c.spend.daily_cap_gbp}" min="0" max="500" step="5"></label>
      <div class="note">${esc(c.spend.note)}</div>
      <button class="btn" id="btnSaveAdmin" style="margin-top:12px">Save model and cap</button>
      <div id="adminMsg"></div>
    </div>
  </div>

  <p class="section-title">Search terms offered to users</p>
  <div class="card">
    <p class="hint">Users pick up to ${c.max_selectable} from the enabled list. Free text
      is deliberately not offered: a term nobody has mapped to a category produces
      operators with no category, which silently zeroes the gap-fit axis.</p>
    <div class="scroll"><table>
      <thead><tr><th>Term</th><th>Category</th><th>Offered</th><th></th></tr></thead>
      <tbody>${c.search_terms.map(t => `
        <tr>
          <td>${esc(t.term)}${t.default ? ' <span class="pill grey">default</span>' : ''}
            ${t.note ? `<div style="color:var(--dim);font-size:12px">${esc(t.note)}</div>` : ''}</td>
          <td style="color:var(--muted)">${esc(t.category.replace(/_/g, ' '))}</td>
          <td><span class="pill ${t.enabled ? 'good' : 'grey'}">${t.enabled ? 'yes' : 'no'}</span></td>
          <td style="text-align:right"><button class="btn ghost" data-term="${esc(t.term)}"
            data-on="${t.enabled ? '0' : '1'}">${t.enabled ? 'Disable' : 'Enable'}</button></td>
        </tr>`).join('')}
      </tbody>
    </table></div>
  </div>

  <p class="section-title">Not yet built</p>
  <div class="card">
    <div class="note">Day 2, and named rather than implied: persisting these settings to
      a config store with an audit trail of who changed what; role-based access replacing
      both shared codes; per-locale threshold calibration when a second market opens; and
      alerting when a run trips the daily cap.</div>
  </div>`;
}

async function adminUnlock() {
  const code = $('#adminCode').value.trim();
  try {
    S.adminCfg = await api('/api/admin/config', { headers: { 'X-Admin-Code': code } });
    S.adminCode = code;
    render();
  } catch (err) {
    $('#adminErr').innerHTML = `<div class="note warn">${esc(err.message)}</div>`;
  }
}

async function adminPost(changes) {
  try {
    const res = await api('/api/admin/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Code': S.adminCode },
      body: JSON.stringify(changes),
    });
    S.adminCfg = res.config;
    S.terms = await api('/api/search-terms');
    S.regions = await api('/api/regions');
    S.selectedTerms = S.selectedTerms.filter(
      t => (S.terms.terms || []).some(x => x.term === t));
    render();
    if (res.applied.length) {
      const el = $('#adminMsg');
      if (el) el.innerHTML = `<div class="note">${res.applied.map(esc).join('<br>')}</div>`;
    }
  } catch (err) {
    const el = $('#adminMsg');
    if (el) el.innerHTML = `<div class="note warn">${esc(err.message)}</div>`;
  }
}

function bindAdmin() {
  const unlock = $('#btnAdmin');
  if (unlock) { unlock.onclick = adminUnlock;
    $('#adminCode').onkeydown = e => { if (e.key === 'Enter') adminUnlock(); }; return; }

  document.querySelectorAll('[data-term]').forEach(b => b.onclick = () => adminPost(
    b.dataset.on === '1' ? { enable_terms: [b.dataset.term] } : { disable_terms: [b.dataset.term] }));
  document.querySelectorAll('[data-region]').forEach(b => b.onclick = () => adminPost(
    b.dataset.on === '1' ? { enable_regions: [b.dataset.region] } : { disable_regions: [b.dataset.region] }));
  const save = $('#btnSaveAdmin');
  if (save) save.onclick = () => adminPost({
    model: document.querySelector('input[name=model]:checked')?.value,
    daily_cap_gbp: parseFloat($('#cap').value),
  });
}

/* ----------------------------------------------------------------- shell */

const VIEWS = {
  overview: viewOverview, discover: viewDiscover, leads: viewLeads,
  review: viewReview, quality: viewQuality, economics: viewEconomics,
  admin: viewAdmin,
};

function render() {
  $('#main').innerHTML = VIEWS[S.view]();
  document.querySelectorAll('#tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.view === S.view));
  if (S.view === 'discover') { initMap(); bindDiscover(); }
  if (S.view === 'leads') bindLeads();
  if (S.view === 'review') bindReview();
  if (S.view === 'admin') bindAdmin();
  window.scrollTo(0, 0);
}

function bindDiscover() {
  document.querySelectorAll('.modes button').forEach(b => b.onclick = () => {
    S.mode = b.dataset.mode;
    document.querySelectorAll('.modes button').forEach(x =>
      x.classList.toggle('active', x === b));
    S.layer.clearLayers(); S.shape = null; clearEstimate(); applyMode();
  });
  $('#radius').oninput = () => { if (S.shape?.kind === 'circle') setCircle(L.latLng(S.shape.lat, S.shape.lng)); };
  $('#cell').oninput = clearEstimate;

  const max = S.terms?.max_selectable || 3;
  $('#terms').onchange = (e) => {
    const cb = e.target;
    if (!cb.matches('input[type=checkbox]')) return;
    if (cb.checked) {
      if (S.selectedTerms.length >= max) { cb.checked = false; return; }
      S.selectedTerms.push(cb.value);
    } else {
      S.selectedTerms = S.selectedTerms.filter(t => t !== cb.value);
    }
    cb.closest('.term').classList.toggle('on', cb.checked);
    $('#terms').querySelectorAll('input[type=checkbox]').forEach(x => {
      x.disabled = !x.checked && S.selectedTerms.length >= max;
      x.closest('.term').classList.toggle('off', x.disabled);
    });
    clearEstimate();
  };
  $('#btnEstimate').onclick = doEstimate;
  $('#btnRun').onclick = doRun;
}

function bindLeads() {
  document.querySelectorAll('.lead-head').forEach(h =>
    h.onclick = () => h.parentElement.classList.toggle('open'));
}

function bindReview() {
  document.querySelectorAll('[data-decide]').forEach(b => b.onclick = () => {
    S.decisions[b.dataset.decide] = b.dataset.v;
    render();
  });
}

async function boot() {
  try {
    [S.snap, S.regions, S.terms] = await Promise.all([
      api('/api/snapshot'), api('/api/regions'), api('/api/search-terms'),
    ]);
    S.selectedTerms = (S.terms.terms || []).filter(t => t.default).map(t => t.term);
  } catch (err) {
    $('#main').innerHTML = `<div class="card"><div class="note warn">
      Could not load the snapshot: ${esc(err.message)}</div></div>`;
    return;
  }
  const c = S.snap.counts;
  $('#topmeta').innerHTML = `
    <div><div class="k">Destination</div><div class="v">${esc(S.snap.destination)}</div></div>
    <div><div class="k">Operators</div><div class="v">${c.operators}</div></div>
    <div><div class="k">Net-new</div><div class="v">${c.net_new} <span style="color:var(--dim);font-size:12px">of ${c.operators}</span></div></div>`;
  document.querySelectorAll('#tabs button').forEach(b =>
    b.onclick = () => { S.view = b.dataset.view; render(); });
  render();
}

boot();
