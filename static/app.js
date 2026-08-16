/* Supply Radar front end.
   Vanilla JS, no build step, no framework. One file, six views. */

const S = { snap: null, regions: null, terms: null, selectedTerms: [],
            view: 'overview', map: null, layer: null,
            mode: 'circle', shape: null, estimate: null, decisions: {}, removed: {},
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
    ${stat('Net-new leads', c.net_new, `of the ${c.operators} operators`, 'good')}
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
          <td style="color:var(--muted)">Judged on what they <em>sell</em> (e.g. a cooking
            class), not what they are (e.g. a restaurant)</td></tr>
        <tr style="border-top:2px solid var(--line)"><td><strong>Experience operators</strong></td>
          <td class="num"><strong>${c.operators}</strong></td>
          <td style="color:var(--muted)">Everything below is a subset of this</td></tr>
        <tr class="nav" onclick="go('matched')" title="Open the match log"><td>Already a Viator supplier</td><td class="num">${c.already_on_file}</td>
          <td style="color:var(--muted)">Matched to the supplier list</td></tr>
        <tr class="nav" onclick="go('review')" title="Open the review queue"><td>Needs a human decision</td><td class="num">${c.needs_review}</td>
          <td style="color:var(--muted)">Too close to call automatically</td></tr>
        <tr class="nav" onclick="go('leads')" title="Open the lead list"><td><strong>Net-new leads</strong></td><td class="num"><strong>${c.net_new}</strong></td>
          <td style="color:var(--muted)">What the matcher decided. ${c.existing_wrongly_in_leads !== undefined
            ? `${c.net_new_correct_in_leads} of them are right` : 'Operators Viator does not have'}</td></tr>
      </tbody>
    </table></div>
    ${c.net_new_actual === undefined ? '' : note(
      `<strong>How many of these ${c.net_new} leads are genuinely net-new?</strong>
       On this benchmark we can actually check. On your data you could not.`,
      `<p>We know the true answer only because the supplier list here is made up, so we can
        mark our own work. On real data this comparison does not exist.</p>
      <table style="width:100%;margin:8px 0">
        <tr><td>Leads we published</td><td class="num">${c.net_new}</td></tr>
        <tr><td>&nbsp;&nbsp;genuinely net-new</td><td class="num">${c.net_new_correct_in_leads}</td></tr>
        <tr><td>&nbsp;&nbsp;already a Viator supplier</td><td class="num">${c.existing_wrongly_in_leads}</td></tr>
        <tr><td>Net-new still sat in the review queue</td><td class="num">${c.net_new_held_in_review}</td></tr>
        <tr style="border-top:1px solid var(--line)"><td><strong>True total</strong></td>
          <td class="num"><strong>${c.net_new_actual}</strong></td></tr>
      </table>
      <p>So ${c.existing_wrongly_in_leads} businesses Viator already has are sitting in that
        lead list. Sales would ring them, get told, and move on. That is the cheap mistake,
        and every threshold in this build is set to take it rather than the expensive one.</p>
      <p>We could delete those ${c.existing_wrongly_in_leads} using the answer key, and it
        would be cheating: on your data there is no answer key to delete them with.</p>`)}

    ${note(
      `All ${c.net_new} net-new operators become leads. Nothing is sampled or dropped.`,
      `<p>${S.snap.leads.filter(l => l.no_website).length} of them have no website. We could
        not read how they sell, so they score low. That is missing evidence, not bad
        evidence, and each one says so on its card.</p>
      <p>This used to be wrong. We enriched a sample of all ${c.operators} operators and
        forgot to filter out the ones Viator already had, so 14 of 40 "leads" were existing
        suppliers. The build now refuses to publish a lead that is not net-new.</p>`)}

    ${note(
      `The numbers add up: ${c.already_on_file} + ${c.needs_review} + ${c.net_new} = ${c.operators}.`,
      `<p>We decide what a business <em>is</em> before we check whether Viator has it. So the
        matcher never compares a car park against the supplier list.</p>
      <p>That order matters. It means every figure on this page counts the same
        ${c.operators} operators, so nothing is double-counted or lost.</p>`)}

    ${note(
      'We judge a business on what it sells, not on what Google calls it.',
      `<p>A museum that runs guided tours is an operator. A museum that only sells a ticket
        at the door is not. A restaurant that sells a cooking class is an operator. One
        where you turn up and eat is not.</p>
      <p>Two real decisions from this run: <em>"Meštrović Gallery, selling admission to
        view exhibits, with no scheduled guided activity"</em> and <em>"Restaurant Krug, no
        evidence of scheduled tastings or classes, just dining"</em>.</p>
      <p>Every reason is shown to the reviewer, so a human can overrule it.</p>`)}
  </div>

  <p class="section-title">What the pipeline does</p>
  <div class="card">
    <div class="scroll"><table>
      <thead><tr><th>Stage</th><th>What it decides</th><th class="num">Cost</th><th>Who decides</th></tr></thead>
      <tbody>
        <tr><td>Discover</td><td>Which operators exist in the area</td><td class="num">£0.76</td><td>Google Places, adaptive cell subdivision</td></tr>
        <tr><td>Classify</td><td>Is this an experience operator at all</td><td class="num">£0.29</td>
          <td>${s.classification
            ? `Type rules settled ${s.classification.by_rules} of ${s.classification.total}
               for free. The model was asked about the other ${s.classification.by_model}`
            : 'Type rules first, and the model only for what they cannot settle'}</td></tr>
        <tr><td>Match</td><td>Are they already a Viator supplier</td><td class="num">£0.00</td><td>Deterministic keys, then fuzzy, then a human</td></tr>
        <tr><td>Enrich</td><td>Can they actually transact</td><td class="num">£1.53</td><td>Their own website, read by the model</td></tr>
        <tr><td>Score</td><td>Which leads are worth Sales time</td><td class="num">£0.00</td><td>Three separate axes, evidence shown</td></tr>
      </tbody>
    </table></div>
    ${note(
      `<strong>${gbp(s.economics.per_destination.total_gbp)}</strong> per destination, against
       <strong>${gbp(s.economics.versus_manual.manual_cost_gbp)}</strong> for doing it by hand.`,
      `<p>These costs were measured from the real Split run, not estimated.</p>
      <p>The manual figure is an assumption: one analyst day at &pound;32 an hour. It is
        editable on the Economics page, so you can put your own number in.</p>`)}
  </div>

  ${s.taxonomy ? `
  <p class="section-title">Coverage of Viator's own catalogue</p>
  <div class="card">
    <p class="hint">Categories below use Viator's published taxonomy, not my own labels.
      ${s.taxonomy.total_nodes} nodes across ${s.taxonomy.tier1} top-level categories,
      ${s.taxonomy.tier2} second-level and ${s.taxonomy.tier3} third-level.</p>
    <div class="grid g2">
      <div>
        <div style="font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">Searched for today</div>
        ${s.taxonomy.tier1_covered.map(t => `<span class="pill good" style="margin:0 4px 4px 0;display:inline-block">${esc(t)}</span>`).join('')}
      </div>
      <div>
        <div style="font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.07em;margin-bottom:6px">Not yet searched for</div>
        ${s.taxonomy.tier1_not_covered.map(t => `<span class="pill grey" style="margin:0 4px 4px 0;display:inline-block">${esc(t)}</span>`).join('')}
      </div>
    </div>
    ${note(
      'This build covers part of your catalogue, and we can tell you exactly which part.',
      `<p>Widening it means adding search terms and demand rows. It does not mean new code.</p>
      <p>The category list is Viator's own file, loaded as-is. When your categories change,
        you replace the file.</p>`)}
  </div>` : ''}

  <p class="section-title">Where the opportunity actually is <span class="synthetic">demand data synthetic</span></p>
  <div class="card">
    <p class="hint">Discovery found the most boat-tour operators. Gap fit says they are the
      least valuable, because that category is already saturated. This is the judgement a
      generic lead-generation tool cannot make.</p>
    <div class="scroll"><table>
      <thead><tr><th>Category</th><th class="num">Operators found</th><th class="num">Gap fit</th><th style="width:34%">Unmet demand</th><th>Evidence</th></tr></thead>
      <tbody>${gaps.map(g => `
        <tr>
          <td>${esc(g.viator_label || g.category.replace(/_/g, ' '))}
            ${g.viator_path ? `<div style="color:var(--dim);font-size:11.5px">${esc(g.viator_path)}</div>` : ''}</td>
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
          <span>Search terms (pick up to ${S.terms?.max_selectable || 3})</span>
          <div id="terms" class="termlist">${termOptions()}</div>
        </label>
        <button class="btn ghost" id="btnEstimate" style="width:100%">Estimate cost</button>

        <div class="estimate" id="estimate"></div>

        <!-- Hidden until a price exists. The Run button was previously disabled
             rather than absent, which enforced the same rule invisibly: nothing
             on screen said why it could not be pressed. Spending money is the
             one action in this app that cannot be undone, so the sequence is
             made structural rather than implied. -->
        <div id="rungate" style="display:none">
          <div class="note warn" style="margin-top:14px">Spends real money. The estimate
            above is what it will cost.</div>
          <button class="btn" id="btnRun" style="width:100%">Run sweep</button>
        </div>
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
        ${note(
          'Opening a new market is a config change, not a code change.',
          `<p>That is what "scales to hundreds of destinations" has to mean in practice.</p>
          <p>The greyed-out map is a courtesy. The server checks every request itself, so
            the limit holds even if someone edits the page.</p>`)}
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

  // Only the two draw buttons. Edit and removal toggles are deliberately gone:
  // a shape's handles are always live (see watchEdits / enableEditing), so
  // there is nothing for a user to switch on, and a toggle that must be found
  // before the map responds is a trap rather than a control.
  map.pm.addControls({
    position: 'topright', drawCircle: false, drawMarker: false,
    drawCircleMarker: false, drawPolyline: false, drawText: false,
    drawRectangle: true, drawPolygon: true, editMode: false,
    removalMode: false, dragMode: false, cutPolygon: false, rotateMode: false,
  });
  map.on('pm:create', (e) => {
    S.layer.clearLayers();
    e.layer.setStyle({ color: '#4fd1c5', weight: 2, fillOpacity: 0.08 });
    S.layer.addLayer(e.layer);
    watchEdits(e.layer);
    syncShapeFrom(e.layer);
    map.pm.disableDraw();
  });
  // removalMode is on by default in the toolbar, so a shape can be deleted.
  // Without this the deleted shape stayed in S.shape and remained runnable.
  map.on('pm:remove', () => { S.shape = null; clearEstimate(); });

  applyMode();
  setCircle(L.latLng(43.5081, 16.4402));
}

function applyMode() {
  $('#modehelp').textContent = MODE_HELP[S.mode];
  // Driven by a class on the map container rather than by setting display on
  // the toolbar directly. Geoman attaches its control after this first runs, so
  // the direct version hit its own `if (ctl)` guard, did nothing, and left the
  // draw buttons showing in circle mode until the user happened to switch modes
  // and back. A CSS rule applies whenever the toolbar turns up.
  const el = $('#map');
  if (el) el.classList.toggle('hide-draw-tools', S.mode !== 'polygon');
  $('#radius').closest('label').style.display = S.mode === 'circle' ? '' : 'none';
}

function setCircle(latlng) {
  const radius = parseFloat($('#radius').value) || 4;
  S.layer.clearLayers();
  S.shape = { kind: 'circle', lat: latlng.lat, lng: latlng.lng, radius_km: radius };
  const circle = L.circle(latlng, {
    radius: radius * 1000, color: '#4fd1c5', weight: 2, fillOpacity: 0.08,
  }).addTo(S.layer);
  watchEdits(circle);
  clearEstimate();
}

/* The drawn layer is the source of truth, not S.shape.
 *
 * Geoman's edit mode lets a user resize the circle or drag a polygon vertex
 * directly on the map. Before this, nothing listened for that: S.shape kept
 * whatever the radius field said at the moment the shape was placed, and
 * requestBody() feeds S.shape to BOTH /api/estimate and /api/run. Enlarging the
 * circle on the map therefore left the sweep searching the original area and
 * quoting the original price, with the map showing something else entirely.
 *
 * That is the silent-truncation failure mode this pipeline exists to avoid,
 * reintroduced in the browser: a search that quietly covers less than it claims
 * and says nothing about it.
 */
// Event names verified against the vendored Leaflet-Geoman 2.17.0 rather than
// taken from the docs: this version emits no 'pm:update', so listening for it
// would have looked like a fix and done nothing. 'pm:change' is the general
// geometry-changed event; the rest are belt and braces, and the handler is
// idempotent so overlapping events are harmless.
function watchEdits(layer) {
  layer.on(
    'pm:change pm:edit pm:markerdragend pm:dragend pm:centerplaced',
    () => syncShapeFrom(layer),
  );
  enableEditing(layer);
}

// Handles on, always, from the moment a shape exists. Geoman defaults to
// requiring a toolbar toggle first, which meant the circle looked draggable,
// was not, and gave no clue why.
function enableEditing(layer) {
  try {
    layer.pm.enable({ allowSelfIntersection: false, preventMarkerRemoval: true });
  } catch { /* a layer type Geoman cannot edit is not worth failing over */ }
}

function syncShapeFrom(layer) {
  if (layer instanceof L.Circle) {
    const centre = layer.getLatLng();
    // Two decimals: the field steps in 0.5 km and a radius of 4.0231 km reads
    // as false precision on a quadtree that subdivides in kilometres.
    const km = Math.round((layer.getRadius() / 1000) * 100) / 100;
    S.shape = { kind: 'circle', lat: centre.lat, lng: centre.lng, radius_km: km };
    const field = $('#radius');
    // Deliberately NOT clamped to the field's max of 25. The server rejects a
    // larger radius with an explicit message, and silently shrinking what
    // someone just drew would be the same lie in the opposite direction.
    if (field) field.value = km;
  } else if (typeof layer.getLatLngs === 'function') {
    S.shape = { kind: 'polygon', points: layer.getLatLngs()[0].map(p => [p.lat, p.lng]) };
  } else {
    return;
  }
  // Any edit invalidates a completed estimate, so Run is disabled until the
  // new shape has been priced. Otherwise a sweep could be launched against a
  // shape nobody ever costed.
  clearEstimate();
}

function clearEstimate() {
  S.estimate = null;
  $('#btnRun').disabled = true;
  // Editing the shape invalidates the price, so the authorisation gate closes
  // with it. Otherwise a sweep could be launched against a shape nobody costed.
  const gate = $('#rungate');
  if (gate) gate.style.display = 'none';
  const check = checkShape(S.shape);
  S.permitted = check.ok;
  S.permitMsg = check.msg;
  const btn = $('#btnEstimate');
  if (btn) btn.disabled = !!S.shape && !check.ok;
  $('#estimate').innerHTML = (S.shape && !check.ok)
    ? `<div class="note warn"><strong>Outside a permitted market.</strong><br>${esc(check.msg)}</div>`
    : (S.shape && check.msg ? `<div class="note">${esc(check.msg)} Estimate the cost to continue.</div>` : '');
}

/* The starting grid square, chosen by the system rather than typed by a user.
 *
 * This was a "Cell size (km half-side)" input, which is the Places API's
 * internals wearing a label. Nobody outside this codebase could answer what to
 * put in it, and the honest reason is that it barely matters: the quadtree
 * already splits any square that comes back at the 60-result cap, so the
 * starting size only decides how many rounds of splitting it takes to get
 * there. Too coarse costs a little time; too fine costs a lot of calls.
 *
 * So it is derived from the area. Small areas get a fine grid because a coarse
 * one would be a single query over the whole thing; large areas start coarse
 * and let subdivision find the dense parts, which is the entire point of
 * adaptive subdivision and a better story than a number in a box.
 */
function autoCellKm(shape) {
  const km2 = shape
    ? (shape.kind === 'circle'
        ? Math.PI * shape.radius_km * shape.radius_km
        : approxPolygonKm2(shape.points))
    : 50;
  if (km2 <= 25) return 1.5;
  if (km2 <= 120) return 3;
  if (km2 <= 400) return 4;
  return 5;
}

function approxPolygonKm2(points) {
  if (!points || points.length < 3) return 50;
  const latMid = points.reduce((s, p) => s + p[0], 0) / points.length;
  const kx = 111.32 * Math.cos(latMid * Math.PI / 180), ky = 110.57;
  let area = 0;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    area += (points[j][1] * kx) * (points[i][0] * ky)
          - (points[i][1] * kx) * (points[j][0] * ky);
  }
  return Math.abs(area / 2);
}

function requestBody() {
  const queries = S.selectedTerms.slice();
  const cell = autoCellKm(S.shape);
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
      <div class="row"><span>Search grid</span><span>${e.cells} squares of
        ${autoCellKm(S.shape)} km, chosen automatically</span></div>
      <div class="row"><span>Search terms</span><span>${e.queries_per_cell}</span></div>
      <div class="row"><span>API calls</span><span>${e.estimated_calls}</span></div>
      <div class="row"><span>Estimated cost</span><span>${gbp(e.estimated_gbp)} to
        <strong>${gbp(e.estimated_gbp_max)}</strong></span></div>
      <div class="row"><span>Estimated time</span><span>${e.estimated_seconds} to
        ${e.estimated_seconds_max}s</span></div>
      <div class="note ${e.within_live_run_limit ? '' : 'warn'}">${esc(e.message)}</div>
      ${note(
        `Budget for the higher number, ${gbp(e.estimated_gbp_max)}.`,
        `<p>The lower figure is searching the squares exactly as drawn. Two things push it
          up: any square that comes back full gets split into four and searched again, and
          every place we find is then read by a model to decide whether it is an experience
          operator.</p>
        <p>Neither can be known before we look, so you get a range rather than a number
          that turns out to be wrong.</p>`)}`;
    $('#btnRun').disabled = !e.within_live_run_limit;
    // Over the cell limit there is nothing to authorise, so the gate stays shut
    // and the estimate message is the only thing on screen to act on.
    $('#rungate').style.display = e.within_live_run_limit ? '' : 'none';
  } catch (err) {
    $('#estimate').innerHTML = `<div class="note warn">${esc(err.message)}</div>`;
    $('#rungate').style.display = 'none';
  }
}

async function doRun() {
  const body = requestBody();
  $('#btnRun').disabled = true;
  $('#runout').innerHTML = '<div class="card" style="margin-top:16px">Running a live sweep… discovery, then classification, then scoring.</div>';
  try {
    const r = await api('/api/run', {
      method: 'POST',
      // No code header: the session cookie set at the door already carries it.
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    renderRun(r);
  } catch (err) {
    $('#runout').innerHTML = `<div class="card" style="margin-top:16px"><div class="note warn">${esc(err.message)}</div></div>`;
  }
  $('#btnRun').disabled = false;
}

/* A sweep costs real money, so its result is treated as something the user owns
 * rather than as transient DOM. It was previously written straight into #runout
 * and held nowhere else, so changing tab or pressing F5 destroyed results that
 * had just been paid for. sessionStorage rather than localStorage: the results
 * should outlive a refresh, not the browser session. */
const RUN_KEY = 'supply-radar.lastRun';

/* Where a live sweep stops, and why.
 *
 * The reason was already in the API payload and in a prose banner, but neither
 * shows WHERE in the chain the stop happens. Net-new is a relation between an
 * operator and Viator's supply list, so it cannot be computed with one side
 * missing. Shipping a synthetic supplier list into the container would not fix
 * that: the synthetic list is generated FROM the operators being matched, so
 * the match rate could be made to say anything. The gap is honest and stating
 * it plainly is worth more than papering over it. */
const SWEEP_STAGES = [
  ['Discover', 'ran', 'Google Places, adaptive cell subdivision'],
  ['Classify', 'ran', 'Type rules first, and the model only for what they cannot settle'],
  ['Score', 'partial', 'Quality is final. Readiness and gap fit are provisional until enrichment'],
  ['Match against supplier list', 'stopped', 'Could run right here. Missing because we have no supplier records, not because it is slow'],
  ['Net-new determination', 'stopped', 'Needs the supplier list above, and nothing else'],
  ['Enrich', 'stopped', 'The only step that genuinely cannot run live: about 3 seconds a website, against a 900 second ceiling'],
];

const STAGE_MARK = { ran: '&#10003;', partial: '&#189;', stopped: '&#9679;' };

function pipelineStrip() {
  return `
    <div class="section-title" style="margin-top:18px">What this sweep did, and where it stopped</div>
    <div class="stages">${SWEEP_STAGES.map(([name, state, why]) => `
      <div class="stage ${state}">
        <div class="s-head">${STAGE_MARK[state]} ${esc(name)}</div>
        <div class="s-why">${esc(why)}</div>
      </div>`).join('')}</div>
    ${note(
      '<strong>Only one of these three stopped stages actually needs to be overnight work.</strong>',
      `<p><strong>Matching could run right here.</strong> It makes no network calls and no
        model calls: it is string and geometry work, about 11 milliseconds an operator. It
        is missing because we do not hold Viator's supplier list, not because it is slow.</p>
      <p><strong>Reading websites is the real batch job.</strong> Roughly 3 seconds each,
        against a request that gets killed at 900 seconds. That one has to happen overnight
        however it is built.</p>
      <p>So with the supplier list connected, a sweep would show only genuinely net-new
        operators and the queue button would mean one thing: enrich this. That queue is the
        single piece of the architecture this build does not have. Today's published lead
        list was produced by running the pipeline scripts by hand.</p>`, 'warn')}`;
}

function sweepCard(l, i) {
  const c = l.classification;
  return `
  <div class="lead" data-sweep="${i}">
    <div class="lead-head">
      <div>
        <div class="lead-name">${esc(l.name)}${l.known
          ? ` <span class="pill ${l.known.where === 'matched' ? 'grey' : 'good'}"
                    style="font-size:11px">${esc(l.known.label)}</span>` : ''}</div>
        <div class="lead-meta">${l.category
            ? esc(l.category.replace(/_/g, ' '))
            : '<span style="color:var(--dim)">category not determined: found by the catch-all search term, and a live sweep does not read websites</span>'}
          ${l.rating ? ` &middot; ${l.rating} from ${l.review_count || 0} reviews` : ''}
          ${l.website ? ` &middot; ${esc(l.website.replace(/^https?:\/\//, '').slice(0, 44))}` : ' &middot; no website'}</div>
      </div>
      <div class="axes">
        ${ax('Quality', l.quality, 'q')}
        ${ax('Contactability', l.readiness, 'r')}
        ${ax('Gap fit', l.gap_fit, 'g')}
        <div class="axis"><div class="k">Provisional</div>
          <div class="v" style="font-size:16px">${n3(l.composite)}</div></div>
      </div>
    </div>
    <div class="lead-body">
      <div class="grid g3" style="margin-top:14px">
        ${axisCard('Quality', l.quality)}
        ${axisCard('Contactability', l.readiness)}
        ${axisCard('Gap fit', l.gap_fit)}
      </div>
      ${note(
        'Websites are only read by the batch job that turns these into leads, so readiness will change.',
        `<p><strong>Contactability</strong> is not readiness. It scores only what a sweep can
          see: whether they have a website at all, and whether we have a phone number. It
          says nothing about online booking, languages sold in, email contact, or whether
          they already sell on a marketplace. Those four are dropped here rather than
          scored at zero, which is why this is a different axis with a different name.</p>
        <p><strong>No band is shown.</strong> A, B and C are calibrated against the enriched
          lead list, so the same letter would mean something different here. The provisional
          score is a sort order for this sweep and nothing more.</p>`, 'warn')}
      ${c && c.reason ? `<div class="note"><strong>Classified ${esc(c.verdict.replace(/_/g, ' '))}</strong>
        by ${esc(c.decided_by || 'rules')}${c.confidence ? `, confidence ${n3(c.confidence)}` : ''}:
        ${esc(c.reason)}</div>` : ''}
      <div class="decide" style="margin-top:4px">
        <span style="color:var(--muted);font-size:13px">${l.known
          ? esc(l.known.label) + '. Nothing to queue.'
          : 'Queueing sends this for website enrichment, which is the one step that has to run overnight.'}</span>
        <div class="spacer"></div>
        ${l.known ? '' :
          `<button class="btn ghost" data-addlead="${esc(l.name)}">Queue for lead enrichment</button>`}
      </div>
    </div>
  </div>`;
}

/* Enabled rather than disabled, and it opens an explanation.
 *
 * It was a disabled button carrying its reason in a title tooltip. Nobody
 * hovers a disabled button and no tooltip exists on touch, so the reason was
 * effectively invisible. A button that responds by explaining itself is read;
 * one that does nothing is not. */
function explainAddToLeads(name) {
  const wrap = document.createElement('div');
  wrap.className = 'modal-overlay';
  wrap.innerHTML = `
    <div class="modal-card">
      <h3 style="margin:0 0 4px">There is no queue to add it to yet</h3>
      <div style="color:var(--muted);font-size:13px;margin-bottom:12px">${esc(name)}</div>
      ${note(
        'This is the one part of the design that was not built.',
        `<p>Queueing an operator means reading its website and checking it against Viator's
          supply. Reading one website takes about 3 seconds and a web request is killed at
          900, so it cannot happen while you wait. It belongs in an overnight job.</p>
        <p>In a working system this button adds the operator to that queue, the job does the
          work, and the lead appears in the list when it is ready. The job is a job and a
          queue; nothing in the discovery, matching or scoring code changes.</p>`)}
      ${note(
        'The other half is missing too: no supplier records ship with this deployment.',
        `<p>"Net-new" is a comparison and we hold only one side of it here. The matching
          itself is proven on the published Split run, where we know the right answers.</p>`)}
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
        <button class="btn" id="addLeadOk">Understood</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const close = () => wrap.remove();
  wrap.querySelector('#addLeadOk').onclick = close;
  wrap.onclick = (e) => { if (e.target === wrap) close(); };
}

function rememberRun(r) {
  S.lastRun = r;
  try {
    sessionStorage.setItem(RUN_KEY, JSON.stringify(r));
  } catch {
    // Quota or private mode. In-memory recall still works, and losing the
    // refresh-survival is not worth breaking the run over.
  }
}

function recallRun() {
  if (S.lastRun) return S.lastRun;
  try {
    const raw = sessionStorage.getItem(RUN_KEY);
    if (raw) S.lastRun = JSON.parse(raw);
  } catch {
    S.lastRun = null;
  }
  return S.lastRun;
}

function renderRun(r) {
  rememberRun(r);
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
        ${stat('Operators', r.leads.length, c
          ? `of ${c.total} places found; ${c.model_calls} needed the model`
          : `of ${d.places} places found`)}
        ${stat('Cost', gbp(r.cost.gbp), 'this run, measured')}
        ${r.already_known ? stat('Already known', r.already_known,
          'seen before, so not offered again') : ''}
      </div>
      ${d.unresolved_cells ? note(
        `<strong>${d.unresolved_cells} ${d.unresolved_cells === 1 ? 'square' : 'squares'} had
         more operators than we could reach. There are others we did not find.</strong>`,
        `<p>When a square comes back full, we split it into four smaller ones and search
          again. There is a limit to how many times we do that, and ${d.unresolved_cells}
          ${d.unresolved_cells === 1 ? 'square was' : 'squares were'} still full when we hit it.</p>
        <p>Search a smaller area to get complete coverage there.</p>`, 'warn') : ''}
      ${r.scope ? note(
        '<strong>These are discovered operators, not net-new leads.</strong>',
        `<p>${esc(r.scope.detail)}</p>`, 'warn') : ''}
      ${r.caveats.map(c => `<div class="note">${esc(c)}</div>`).join('')}
      ${pipelineStrip()}
      <div class="section-title" style="display:flex;align-items:center;gap:12px">
        <span>Top discovered operators from this sweep</span>
        <button class="btn ghost" id="btnExportRun" style="padding:4px 10px;font-size:13px">Export CSV</button>
      </div>
      <div>${r.leads.slice(0, 15).map(sweepCard).join('')}</div>
      ${r.leads.length > 15 ? `<div class="note">Showing the top 15 of ${r.leads.length}.
        The CSV carries all of them.</div>` : ''}
    </div>`;

  $('#btnExportRun').onclick = () => downloadCsv(
    `supply-radar-sweep-${stamp()}.csv`,
    toCsv(r.leads.map(l => ({
      name: l.name, category: l.category, viator_category: l.viator_top || '',
      website: l.website, phone: l.phone, address: l.address,
      rating: l.rating, review_count: l.review_count,
      composite: l.composite, band: l.band,
      quality: l.quality?.score, readiness: l.readiness?.score, gap_fit: l.gap_fit?.score,
      net_new_determined: 'no - see scope note',
    }))),
  );

  // Same interaction as the Leads page, because it is the same component. A
  // sweep result that behaved differently from a published lead would teach the
  // reader that the two are different kinds of thing, which they are not.
  document.querySelectorAll('#runout .lead-head').forEach(h =>
    h.onclick = () => h.parentElement.classList.toggle('open'));
  document.querySelectorAll('[data-addlead]').forEach(b => b.onclick = (e) => {
    e.stopPropagation();  // the card header toggles open/closed on click
    explainAddToLeads(b.dataset.addlead);
  });
}

// The reason the path from Discover to Leads is closed. It is a fact about the
// data rather than an oversight, so it is stated on demand instead of the
// button being hidden.
const ADD_TO_LEADS_WHY =
  'Queueing an operator sends it for website enrichment and a check against ' +
  'Viator\'s supply. Neither runs in this deployment: there is no queue, and no ' +
  'supplier records ship with the container.';

/* ------------------------------------------------------------------ csv */

// Hand-rolled rather than pulled in as a dependency: it is twenty lines, and a
// build step for one button would cost more than it saves.
function toCsv(rows) {
  if (!rows.length) return '';
  const cols = Object.keys(rows[0]);
  const cell = (v) => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    // Quote anything containing a delimiter, a quote or a newline. Croatian
    // operator names carry commas often enough that skipping this would
    // silently shift every column after the offending one.
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(','), ...rows.map(r => cols.map(c => cell(r[c])).join(','))].join('\r\n');
}

function stamp() {
  return new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
}

function downloadCsv(filename, csv) {
  // A BOM, so Excel opens UTF-8 correctly. Without it "Poljička" arrives as
  // mojibake on a default Windows install, which is exactly the audience.
  const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* ---------------------------------------------------------------- leads */

const BANDS = {
  A: ['Contact first', 'Highest on the weighted composite. Open the lead to see which axes carried it — an operator can reach A on quality and readiness alone, even where the category has no supply gap.'],
  B: ['Worth contacting', 'Solid on at least one axis with a visible caveat on another. Read the evidence before calling.'],
  C: ['Park for now', 'Thin evidence, or the category is already well served and adding supply mostly cannibalises it.'],
};

/* Filters live on the lists, not on the Overview.
 *
 * The Overview funnel's whole argument is that one denominator runs through it:
 * already_on_file + needs_review + net_new = operators. Filtering that breaks
 * the identity it exists to demonstrate. Leads and Review queue are lists, and
 * a list is the thing a filter belongs to.
 *
 * Both fields are properties of the operator rather than of the run, so neither
 * needs rewriting when a second destination is added. */
const FILTERS = {
  leads: { locality: '', type: '' },
  review: { locality: '', type: '' },
  matched: { locality: '', type: '' },
};

function filterValues(rows, key) {
  return [...new Set(rows.map(r => r[key]).filter(Boolean))].sort();
}

function applyFilters(rows, scope, typeKey) {
  const f = FILTERS[scope];
  return rows.filter(r =>
    (!f.locality || r.locality === f.locality) &&
    (!f.type || r[typeKey] === f.type));
}

function filterBar(rows, scope, typeKey, total) {
  const label = v => String(v).replace(/_/g, ' ');
  const sel = (key, values, name, all) => `
    <label class="field" style="margin:0;flex:1;min-width:170px">
      <span>${name}</span>
      <select data-filter="${scope}:${key}">
        <option value="">${all}</option>
        ${values.map(v => `<option value="${esc(v)}"${FILTERS[scope][key] === v ? ' selected' : ''}>${esc(label(v))}</option>`).join('')}
      </select>
    </label>`;
  const shown = applyFilters(rows, scope, typeKey).length;
  return `
    <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;margin:10px 0 4px">
      ${sel('locality', filterValues(rows, 'locality'), 'Location', 'All locations')}
      ${sel('type', filterValues(rows, typeKey), 'Operator type', 'All operator types')}
      <div style="color:var(--muted);font-size:12.5px;padding-bottom:10px">
        ${shown === total ? `${total} shown` : `${shown} of ${total} shown`}
      </div>
    </div>`;
}

function bindFilters() {
  document.querySelectorAll('[data-filter]').forEach(el => {
    el.onchange = () => {
      const [scope, key] = el.dataset.filter.split(':');
      FILTERS[scope][key] = el.value;
      render();
    };
  });
}

function viewLeads() {
  const removedIds = S.removed;
  const all = S.snap.leads.filter(l => !removedIds[l.place_source_id]);
  const leads = applyFilters(all, 'leads', 'category');
  const removed = Object.values(removedIds);
  const counts = { A: 0, B: 0, C: 0 };
  leads.forEach(l => counts[l.band]++);

  return `
  <div class="card">
    <h2>Qualified leads <span class="pill grey">${leads.length}</span>
      <button class="btn ghost" id="btnExportLeads"
        style="float:right;padding:6px 12px;font-size:13px">Export CSV</button></h2>
    <p class="hint">Ranked by composite score. Click any lead for the full evidence trail
      behind all three axes. The composite is a sort order, not a decision.</p>
    ${filterBar(all, 'leads', 'category', all.length)}
    ${(() => {
      // Tripadvisor owns Viator. An operator already selling through the parent
      // but not the subsidiary is the warmest lead in the list: they have
      // already accepted third-party distribution, agreed commission and
      // handed over their inventory once. The objection is not "why would I
      // list with a marketplace", it is only "why this one too".
      const ta = all.filter(l => l.claims_tripadvisor && !l.claims_viator).length;
      if (!ta) return '';
      return note(
        `<strong>${ta} of these ${all.length} already sell on Tripadvisor but not Viator.</strong>`,
        `<p>Tripadvisor owns Viator. So these operators have already agreed to sell through a
          marketplace, agreed commission, and handed over their inventory once. Someone else
          had the hard conversation.</p>
        <p>The objection is not "why a marketplace". It is only "why this one too". That is a
          much easier call.</p>
        <p><strong>Spot-checked by hand against viator.com on 15 August 2026.</strong> The
          three highest-scoring operators in this group were all absent from Viator:
          <em>Art Bottega Split</em> (composite 0.788), <em>MySplitTours</em> (0.687) and
          <em>Apodos Travel Agency</em>, trading as Sightseeing Split (0.669). Three of
          ${ta} checked, not all of them, so treat this as evidence the signal works rather
          than as a verified list.</p>
        <p>We read this from each operator's own website. Anyone who sells on a marketplace
          without saying so is missed, so the real number is higher. A production version
          would query Tripadvisor's own data instead, which Viator has and this build
          does not.</p>`);
    })()}

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

    ${note(
      S.snap.bands
        ? `Bands are set per destination. Here, <strong>A is ${n3(S.snap.bands.band_a)} or above, B is ${n3(S.snap.bands.band_b)} or above.</strong>`
        : 'Bands are set per destination, not by a fixed number.',
      `<p>The score is 35% quality, 35% readiness, 30% gap fit.</p>
      <p><strong>Gap fit cannot reach 1.0, by design.</strong> It multiplies how short a
        category is by how big that category is. Scoring near 1.0 would need huge demand and
        almost no operators, which no real market has. The best possible score anywhere in
        this demand data is 0.42, and 0.38 in this destination. So gap fit adds at most
        about 0.11 to any lead.</p>
      <p>That is deliberate. A tiny unserved category should not beat a large one.</p>
      <p>${S.snap.bands ? esc(S.snap.bands.basis) : 'The cut-offs are worked out from the best score actually achievable here, not from a theoretical 1.0.'}</p>`)}

    ${counts.A === 0 ? note(
      '<strong>No A-band leads here. That is the model working, not failing.</strong>',
      `<p>Every operator found sells something this destination already has plenty of.</p>
      <p>They are good businesses in a crowded market. Worth knowing about, not worth
        putting ahead of an under-served category somewhere else.</p>`, 'warn') : (() => {
      // Counted from the leads on screen rather than asserted. The line this
      // replaced claimed every lead scored 0.00 on gap fit, which was true of
      // the boat-tour-skewed sample it was written against and false the moment
      // the sample was fixed. Nobody re-read it for two weeks.
      const scored = S.snap.leads.filter(l => l.gap_fit.score > 0).length;
      const flat = S.snap.leads.length - scored;
      return note(
        `${scored} of ${S.snap.leads.length} leads score above zero on gap fit.`,
        `<p>The other ${flat} sell things this destination already has plenty of, so gap fit
          gives them nothing.</p>
        <p>They earned their band on quality and readiness alone. They are good operators in
          a crowded market, rather than an unmet need.</p>`);
    })()}
    ${removed.length ? `<div class="note warn"><strong>${removed.length} lead${removed.length > 1 ? 's' : ''} removed this session.</strong>
      ${removed.map(r => `<div style="margin-top:6px">${esc(r.name)} — <span style="color:var(--muted)">${esc(r.reason)}</span></div>`).join('')}
      <div style="margin-top:8px">Session only. In production these write to the decisions
        table and retrain the thresholds, which is the point of capturing a reason rather
        than just a rejection.</div></div>` : ''}
  </div>
  <div style="margin-top:14px">${leads.map(leadRow).join('')}</div>`;
}

// Shared by leadRow and sweepCard. A published lead and a swept operator are
// scored on the same three axes, so they are shown on the same three dials.
function ax(name, axis, cls) {
  return `
    <div class="axis"><div class="k">${name}</div>
      <div class="v">${n3(axis.score)}</div>${bar(axis.score, cls)}</div>`;
}

function leadRow(l, i) {
  return `
  <div class="lead" data-lead="${i}">
    <div class="lead-head">
      <div>
        <div class="lead-name">${esc(l.name)}
          <span class="pill ${l.band}" title="${esc(BANDS[l.band][0])}: ${esc(BANDS[l.band][1])}">${l.band}</span></div>
        <div class="lead-meta">${l.category
            ? esc(l.viator_label || l.category.replace(/_/g, ' ')) +
              (l.viator_path ? `<span style="color:var(--dim)"> · ${esc(l.viator_path)}</span>` : '') +
              (l.category_source === 'search term'
                ? '<span title="No classifier call was needed for this operator, so the category comes from the search term that found them" style="color:var(--dim)"> (from search term)</span>'
                : '')
            : l.sells_categories
              ? `<span title="Read from the operator's own website during enrichment. No single search term finds a business like this, which is why the catch-all query did.">sells across ${l.sells_categories.length} categories<span style="color:var(--dim)"> &middot; ${esc((l.viator_labels || l.sells_categories).join(', '))}</span></span>`
              : '<span style="color:var(--dim)">category not determined: no single category applies and no website was read</span>'}
          ${l.website ? ` &middot; ${esc(l.website.replace(/^https?:\/\//, '').slice(0, 44))}` : ' &middot; no website'}
          ${l.extract ? ` &middot; ${esc(l.extract.booking.replace(/_/g, ' '))}` : ''}</div>
      </div>
      <div class="axes">
        ${ax('Quality', l.quality, 'q')}
        ${ax('Contactability', l.readiness, 'r')}
        ${ax('Gap fit', l.gap_fit, 'g')}
        <div class="axis"><div class="k">Provisional</div>
          <div class="v" style="font-size:16px">${n3(l.composite)}</div></div>
      </div>
    </div>
    <div class="lead-body">
      <div class="grid g3" style="margin-top:14px">
        ${axisCard('Quality', l.quality)}
        ${axisCard('Contactability', l.readiness)}
        ${axisCard('Gap fit', l.gap_fit)}
      </div>
      ${l.claims_viator ? note(
        `<strong>Their own website says they already sell on Viator. We have still listed
         them as net-new.</strong>`,
        `<p>Both things are true of the data here, and the contradiction is worth understanding
          rather than hiding.</p>
        <p>The supplier list in this demo is made up. We seeded 40% of the operators we found
          into it; the rest are net-new because the coin landed that way. So whether this
          business is "on Viator" here was decided by a random number, not by reality.</p>
        <p>Against Viator's real supplier list it would almost certainly match, and never
          reach a lead list at all.</p>
        <p>We left it alone on purpose. Acting on the website would disagree with the answer
          key we measure accuracy against, and would report three misses that are artefacts
          of the fake data rather than mistakes by the matcher.</p>`, 'warn') : ''}
      ${l.no_website ? note(
        'No website, so the low score means we know little, not that they are poor.',
        `<p>We could not read how they sell, what they offer, or whether they take bookings
          online. Readiness falls back to whether we can contact them at all.</p>
        <p>Treat this as an unknown to check by phone, not as a business to skip.</p>`,
        'warn') : ''}
      <div class="decide" style="margin-top:4px">
        <span style="color:var(--muted);font-size:13px">Not a fit? Removing it records
          why, which is what turns a rejection into a scoring signal.</span>
        <div class="spacer"></div>
        <button class="btn ghost" data-remove="${esc(l.place_source_id)}">Remove lead</button>
      </div>
    </div>
  </div>`;
}

/* Removals are the useful half of a review loop.
 *
 * An accepted lead teaches nothing — it was already ranked highly. A rejection
 * with a reason is the only signal that says the ranking was wrong and in what
 * way, and it is the input threshold tuning actually needs. So the reason is
 * required rather than optional.
 *
 * Session-only, and said so in the UI rather than implied: there is no
 * decisions store yet. The schema this would write to is in the day-2 list.
 */
// Keyed on place_source_id, never on list position: the list is filtered as
// leads are removed, so an index captured at render time points somewhere else
// by the second removal.
function removeLead(placeId) {
  const lead = S.snap.leads.find(l => l.place_source_id === placeId);
  if (!lead) return;
  showRemoveDialog(lead, (reason) => {
    S.removed[lead.place_source_id] = {
      name: lead.name, reason, at: new Date().toISOString(),
    };
    render();
  });
}

function showRemoveDialog(lead, onConfirm) {
  const wrap = document.createElement('div');
  wrap.className = 'modal-overlay';
  wrap.innerHTML = `
    <div class="modal-card">
      <h3 style="margin:0 0 4px">Remove this lead</h3>
      <div style="color:var(--muted);font-size:13px;margin-bottom:12px">${esc(lead.name)}</div>
      <label style="display:block">
        <span>Why is this not a fit?</span>
        <textarea id="removeReason" rows="3" placeholder="e.g. already a supplier under a different trading name; not an experience operator; ceased trading"></textarea>
      </label>
      ${note('Recorded in this browser only. In a real system this feeds back into the scoring.')}
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" id="removeCancel">Cancel</button>
        <button class="btn" id="removeConfirm" disabled>Remove lead</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const ta = wrap.querySelector('#removeReason');
  const ok = wrap.querySelector('#removeConfirm');
  const close = () => wrap.remove();
  ta.focus();
  ta.oninput = () => { ok.disabled = ta.value.trim().length < 3; };
  wrap.querySelector('#removeCancel').onclick = close;
  wrap.onclick = (e) => { if (e.target === wrap) close(); };
  ok.onclick = () => { const r = ta.value.trim(); close(); onConfirm(r); };
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
  const all = S.snap.review_queue;
  const q = applyFilters(all, 'review', 'experience_type');
  const done = Object.keys(S.decisions).length;
  return `
  <div class="card">
    <h2>Match review queue <span class="pill grey">${q.length}</span></h2>
    <p class="hint">These are the pairs the deterministic stages could not settle. Everything
      above and below this band was decided without a human. ${done ? `<strong>${done} decided this session.</strong>` : ''}</p>
    ${note(
      'Decisions here are kept in your browser and go nowhere else.',
      `<p>In a real system each decision writes back to the CRM and tunes the thresholds, so
        the matcher gets better at the cases people keep overruling.</p>`)}
    ${filterBar(all, 'review', 'experience_type', all.length)}
  </div>
  <div style="margin-top:14px">${q.slice(0, 40).map(reviewCard).join('')}</div>`;
}

/* One field of a discovered-vs-supplier comparison.
 *
 * `omitIfEmpty` exists for trading name. synth.py never populates it: when a
 * record gets legal_name_substituted, the CRM keeps only the registered entity
 * ("Horvat d.o.o.") and the operating name is gone, which is the hard case the
 * matcher exists for. Printing "Trading name —" on all 58 rows advertises a
 * field this dataset structurally does not have. Real CRM data does have it, so
 * the field stays and simply hides when absent.
 */
function pairField(label, value, omitIfEmpty) {
  if (omitIfEmpty && !value) return '';
  return `<div class="l">${label}</div><div class="val">${esc(value || '—')}</div>`;
}

function reviewCard(r, i) {
  const decided = S.decisions[i];
  return `
  <div class="lead" style="margin-bottom:12px">
    <div class="pair">
      <div>
        <h4>Discovered operator</h4>
        ${pairField('Name', r.discovered_name)}${pairField('Address', r.discovered_address)}
        ${pairField('Website', r.discovered_website)}${pairField('Phone', r.discovered_phone)}
      </div>
      <div>
        <h4>Possible existing supplier</h4>
        ${pairField('Name', r.supplier_name)}
        ${pairField('Trading name', r.supplier_trading_name, true)}
        ${pairField('Address', r.supplier_address)}${pairField('Website', r.supplier_website)}
        ${pairField('Phone', r.supplier_phone)}
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

/* ------------------------------------------------------------ match log */

/* Every pair the matcher settled on its own, published so a human can disagree.
 *
 * Not a QA report. Precision needs an answer key and production has none, so
 * the number is not calculated there, it is earned: someone opens a pair and
 * says "those are two different businesses". This page is what produces the
 * metric rather than what displays it.
 *
 * Its own page rather than a panel inside Accuracy & QA, because it grows with
 * every destination added and a QA panel does not.
 */
const FLAGS = {};

function viewMatched() {
  const all = S.snap.matched || [];
  const rows = applyFilters(all, 'matched', 'experience_type');
  const flagged = Object.keys(FLAGS).length;
  const byStage = all.reduce((a, m) => (a[m.decided_by] = (a[m.decided_by] || 0) + 1, a), {});

  return `
  <div class="card">
    <h2>Match log <span class="pill grey">${all.length}</span></h2>
    <p class="hint">Operators the matcher decided Viator already has, weakest match first.
      Nothing here needs doing unless a pair looks wrong.</p>

    ${note(
      '<strong>This page is how accuracy gets measured on your own data.</strong>',
      `<p>Precision needs a right answer to compare against. On the made-up supplier list
        here we have one, which is the only reason this build can quote a number at all.
        On Viator's real data there is no answer key, so on day one there is no score.</p>
      <p>It is earned instead. Every time someone opens a pair here and says "those are two
        different businesses", that is one data point. Enough of them and you have a real
        precision figure, measured on real supply rather than on a benchmark.</p>
      <p>There is no "confirm correct" button on purpose. Nobody clicks confirm ${all.length}
        times, so the absence of a flag has to mean "not looked at yet" rather than
        "checked and fine".</p>`)}

    ${note(
      `${byStage.hard_key || 0} of these were decided by an exact key. Only ${byStage.fuzzy_score || 0} needed fuzzy matching.`,
      `<p>An exact key is a shared website domain, a shared phone number, or a registration
        number: either it matches or it does not, and there is no judgement in it.</p>
      <p>That ratio is the useful part. Tuning the fuzzy thresholds only ever moves those
        ${byStage.fuzzy_score || 0}, which is why threshold changes could not fix the two
        worst identity bugs found on real data.</p>`)}

    ${flagged ? note(
      `<strong>${flagged} flagged as wrong this session.</strong>`,
      `<p>Kept in this browser only. In production each flag writes back and moves the
        measured precision figure.</p>`, 'warn') : ''}

    ${filterBar(all, 'matched', 'experience_type', all.length)}
  </div>
  <div style="margin-top:14px">${rows.map(matchedCard).join('')}</div>
  ${rows.length ? '' : '<div class="card"><p class="hint">Nothing matches those filters.</p></div>'}`;
}

function matchedCard(m) {
  const id = m.place_source_id;
  const flag = FLAGS[id];
  return `
  <div class="lead" style="margin-bottom:12px">
    <div class="pair">
      <div>
        <h4>Discovered operator</h4>
        ${pairField('Name', m.discovered_name)}${pairField('Address', m.discovered_address)}
        ${pairField('Website', m.discovered_website)}${pairField('Phone', m.discovered_phone)}
      </div>
      <div>
        <h4>Viator supplier on file</h4>
        ${pairField('Name', m.supplier_name)}
        ${pairField('Trading name', m.supplier_trading_name, true)}
        ${pairField('Address', m.supplier_address)}${pairField('Website', m.supplier_website)}
        ${pairField('Phone', m.supplier_phone)}
      </div>
    </div>
    <div style="padding:12px 16px;border-top:1px solid var(--line)">
      <div class="evidence">${(m.evidence || []).map(e => `
        <div class="ev"><div class="n">${esc(e.signal)}</div>
        <div class="v">${e.contribution == null ? '' : (e.contribution > 0 ? '+' : '') + Number(e.contribution).toFixed(2)}</div>
        <div class="d">${esc(e.detail)}</div></div>`).join('')}</div>
    </div>
    <div class="decide">
      <span style="color:var(--muted);font-size:13px">Matched ${esc(m.matched_on)} &middot;
        ${esc((m.decided_by || '').replace(/_/g, ' '))} &middot; similarity ${n3(m.score)}</span>
      <div class="spacer"></div>
      ${flag
        ? `<span class="pill bad" title="${esc(flag.reason)}">Flagged as wrong</span>`
        : `<button class="btn ghost" data-flag="${esc(id)}">Not the same business</button>`}
    </div>
  </div>`;
}

function bindMatched() {
  bindFilters();
  document.querySelectorAll('[data-flag]').forEach(b => b.onclick = () => {
    const id = b.dataset.flag;
    const m = (S.snap.matched || []).find(x => x.place_source_id === id);
    askFlag(m, (reason) => {
      // source distinguishes a flag a person made from one seeded by a
      // benchmark. Not rendered; without it, counting real feedback later means
      // string-matching reason text.
      FLAGS[id] = { reason, source: 'user' };
      render();
    });
  });
}

function askFlag(m, onConfirm) {
  const wrap = document.createElement('div');
  wrap.className = 'modal-overlay';
  wrap.innerHTML = `
    <div class="modal-card">
      <h3 style="margin:0 0 4px">Not the same business</h3>
      <div style="color:var(--muted);font-size:13px;margin-bottom:12px">
        ${esc(m.discovered_name)} &nbsp;vs&nbsp; ${esc(m.supplier_name)}</div>
      <label style="display:block">
        <span>What tells you they are different?</span>
        <textarea id="flagReason" rows="3" placeholder="e.g. same name, different town; one closed years ago; franchise of the other, not the same entity"></textarea>
      </label>
      ${note('This is the feedback that builds a real precision figure. The reason matters more than the flag.')}
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
        <button class="btn ghost" id="flagCancel">Cancel</button>
        <button class="btn" id="flagConfirm" disabled>Flag as wrong</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  const ta = wrap.querySelector('#flagReason');
  const ok = wrap.querySelector('#flagConfirm');
  const close = () => wrap.remove();
  ta.focus();
  ta.oninput = () => { ok.disabled = ta.value.trim().length < 3; };
  wrap.querySelector('#flagCancel').onclick = close;
  wrap.onclick = (e) => { if (e.target === wrap) close(); };
  ok.onclick = () => { const r = ta.value.trim(); close(); onConfirm(r); };
}

/* -------------------------------------------------------------- quality */

function viewQuality() {
  const m = S.snap.metrics, mm = m.matching;
  /* The caveat that matters most is not that the supplier list is synthetic,
   * which this page already says in three places. It is that precision and
   * recall cannot be computed at all without an answer key, and production has
   * none. The honest production answer is a feedback loop, not a number. */
  const scopeNote = note(
    `<strong>On your real data, none of these numbers would exist on day one.</strong>`,
    `<p>Every figure here is measured against a made-up supplier list. We made it up on
      purpose: it is the only way to know the right answers and therefore the only way to
      mark our own work.</p>
    <p>Accuracy needs a right answer to compare against. Viator has real supply data but no
      marked answers, so on day one there is nothing to score.</p>
    <p>In use these numbers are earned, not calculated. Someone calls a lead and finds it is
      already a supplier. A reviewer overturns a match. Each of those is one data point, and
      the match log is where they come from.</p>`);
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
  ${scopeNote}
  <div class="grid g4">
    ${stat('Precision', n3(mm.precision), `${mm.correct_existing} of ${mm.correct_existing + mm.missed_opportunity + mm.wrong_supplier} already-on-file calls were right`, 'good')}
    ${stat('Missed opportunities', mm.missed_opportunity, 'Real operators wrongly written off', mm.missed_opportunity > 12 ? 'bad' : 'warn')}
    ${stat('Wasted calls', mm.wasted_call, 'Existing suppliers sent to Sales again')}
    ${stat('Decided automatically', pct(mm.automation_rate), `${pct(mm.review_rate)} went to a human`)}
  </div>

  ${mm.precision >= 0.999 ? `<div class="card" style="margin-top:16px">
    ${note(
      '<strong>Precision 1.000 is a real measurement. It is not a promise.</strong>',
      `<p>It means that of the ${mm.correct_existing + mm.missed_opportunity + mm.wrong_supplier}
        operators we said Viator already had, all ${mm.correct_existing} were right. Three
        things to know before anyone quotes it.</p>
      <p><strong>It is a small sample.</strong> ${mm.correct_existing} decisions in one town.
        One bad call takes it to ${n3(mm.correct_existing / (mm.correct_existing + 1))}.</p>
      <p><strong>It started at 0.803.</strong> The gap between those two numbers is 26
        corrections. That journey is the evidence here. The end number on its own is not.</p>
      <p><strong>Real data would score lower, and should.</strong> Our fake supplier list is
        messy in realistic ways, but a real extract also has the same operator entered twice,
        franchises and parent companies, records years out of date, and genuinely similar
        businesses that nobody can tell apart. Expect this to drop. What carries over is the
        method, not the figure.</p>`, 'warn')}
  </div>` : ''}

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
    ${note(
      'Every setting here trades a cheap mistake to avoid an expensive one.',
      `<p>A wasted call costs one awkward conversation. A missed operator costs a supplier
        you never knew you could have had, and nobody ever finds out.</p>
      <p>So when in doubt we send it to a human. For the same reason, the quality audit
        checks four rejected businesses for every one we accepted.</p>`)}
  </div>

  <p class="section-title">Upper threshold — governs the expensive error</p>
  <div class="card">${sweepRows(m.sweep_upper)}</div>

  <p class="section-title">Lower threshold — governs human review load</p>
  <div class="card">${sweepRows(m.sweep_lower)}
    ${note('We read the settings off these tables rather than picking them. The highlighted row is what ships.')}
  </div>

  <p class="section-title">How each decision was reached</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Verdict and stage</th><th class="num">Count</th></tr></thead>
    <tbody>${Object.entries(m.decisions_by_stage).sort().map(([k, v]) =>
      `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td class="num">${v}</td></tr>`).join('')}
    </tbody></table></div>
    ${note(
      'Most matches are decided by an exact key, not by fuzzy comparison.',
      `<p>An exact key is something like a shared website domain or phone number: either it
        matches or it does not.</p>
      <p>This is why turning the fuzzy dials could not fix the two worst bugs we found on
        real data. Those cases never reached the fuzzy stage at all.</p>`)}
  </div>

  <p class="section-title">Corruptions applied to the synthetic supplier list <span class="synthetic">synthetic</span></p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Corruption</th><th class="num">Records</th></tr></thead>
    <tbody>${Object.entries(m.corruptions_applied).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
      `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td class="num">${v}</td></tr>`).join('')}
    </tbody></table></div>
    ${note(
      'We built the fake supplier list from real operators, then broke it on purpose.',
      `<p>Names get typos, phone numbers get reformatted, addresses go stale, some records
        are for businesses that no longer exist. That is how real CRM data rots.</p>
      <p>Because we know exactly what we put in, we can mark our own answers. That is the
        only reason accuracy on this page is a measurement rather than a claim.</p>`)}
  </div>`;
}

/* ------------------------------------------------------------ economics */

function viewEconomics() {
  const e = S.snap.economics, p = e.per_destination, v = e.versus_manual;
  return `
  ${note(
    'This is what one destination costs to run. It is not what this build cost.',
    `<p>The unit cost is measured from the real ${esc(S.snap.destination)} run: its actual API
      calls and its ${p.operators_surfaced} operators. Everything else on this page is that
      measurement projected forward.</p>
    <p>We use full list prices and ignore free allowances, because a free tier that covers
      one destination will not cover two hundred. That makes these numbers deliberately
      pessimistic.</p>`)}

  <div class="grid g4">
    ${stat('Cost per destination', gbp(p.total_gbp), 'list price, free tiers excluded')}
    ${stat('Manual equivalent', gbp(v.manual_cost_gbp), `${v.manual_hours}h of analyst time`, 'warn')}
    ${stat('Ratio', v.cost_ratio + '×', 'cheaper than manual research', 'good')}
    ${stat('Per operator surfaced', gbp(p.gbp_per_operator_surfaced), `${p.operators_surfaced} operators`)}
  </div>

  ${e.basis ? `<div class="card">${note(
    'The actual spend on this whole build was about &pound;0.82.',
    `<p>${esc(e.basis)}</p>`, 'warn')}</div>` : ''}

  <p class="section-title">Where the money goes, per destination</p>
  <div class="card"><div class="scroll"><table>
    <thead><tr><th>Stage</th><th class="num">USD</th><th class="num">Share</th></tr></thead>
    <tbody>
      ${costRow('Google Places discovery', p.places_usd, p.total_usd)}
      ${costRow('Classification (Sonnet)', p.classification_usd, p.total_usd)}
      ${costRow('Website enrichment (Sonnet)', p.enrichment_usd, p.total_usd)}
    </tbody>
  </table></div>
    ${note(
      `Run overnight instead of live, this halves to <strong>${gbp(p.total_gbp_batched)}</strong> per destination.`,
      `<p>Reading websites is the expensive part, because websites are long and the model is
        paid by the amount of text it reads.</p>
      <p>Anthropic charges half price for work you are willing to wait for. This job runs
        overnight anyway, so that discount costs nothing to take.</p>`)}
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
    ${note(
      'The manual figure is our biggest assumption. Change it and every number above changes.',
      `<p>We assumed one analyst day per destination at &pound;32 an hour. Nobody measured it.</p>
      <p>If your team does it in half a day, halve the saving. If it takes two days, double
        it. The tool cost stays the same either way.</p>`, 'warn')}
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
      ${note(
        'Same code as the one that opened the site, for this demonstration only.',
        `<p>Two shared codes would mean two things to remember for a 20-minute demo, so for
          now they are the same. It does mean anyone who can open the site can change these
          settings.</p>
        <p>That is not how it would ship. Opening the console, spending money on a sweep and
          changing what everyone else may spend it on are three different permissions, and
          they belong to roles rather than to shared codes.</p>
        <p>Properly, this signs in through your existing SSO and reads the permission from
          the person, not from a code they were told.</p>`)}
    </div>`;
  }

  const c = S.adminCfg;
  return `
  ${note(
    '<strong>Changes here last until the next deploy, then reset.</strong>',
    `<p>${esc(c.persistence.note)}</p>`, 'warn')}

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
    ${note(
      'Four things we know are missing here.',
      `<p>Settings that survive a deploy, with a record of who changed what.</p>
      <p>SSO sign-in with permissions per role, instead of one shared code.</p>
      <p>Separate matching thresholds per country, once a second market opens.</p>
      <p>An alert when a run hits the daily spend cap.</p>`)}
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
  review: viewReview, matched: viewMatched, quality: viewQuality,
  economics: viewEconomics,
  admin: viewAdmin,
};

/* Notes say one thing, and hide the rest until asked.
 *
 * These had grown into paragraphs. Every sentence was load-bearing to whoever
 * wrote it and none of it was readable at a glance, which is the only way a
 * caveat gets read during a demo. So: a single plain-English line on the page,
 * and the reasoning behind a disclosure triangle for anyone who wants it.
 *
 * <details> rather than a JS toggle, because it opens without scripting, it is
 * keyboard-accessible for free, and browser find-in-page can still reach the
 * collapsed text.
 */
function note(lead, rest, kind) {
  const cls = `note${kind ? ' ' + kind : ''}`;
  if (!rest) return `<div class="${cls}">${lead}</div>`;
  return `<details class="${cls}"><summary>${lead}</summary>
    <div class="note-rest">${rest}</div></details>`;
}

/* The funnel is the journey, so its rows are the navigation. Reading "105
 * net-new leads" and then hunting the tab row for where they live was the gap
 * between understanding the numbers and seeing them. */
function go(view) {
  S.view = view;
  render();
  window.scrollTo(0, 0);
}

function render() {
  $('#main').innerHTML = VIEWS[S.view]();
  document.querySelectorAll('#tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.view === S.view));
  renderTopMeta();
  if (S.view === 'discover') {
    initMap();
    bindDiscover();
    // Re-render a previous sweep rather than leaving the page blank. renderRun
    // repaints the map markers too, which are destroyed with the map on every
    // view change.
    const previous = recallRun();
    if (previous) renderRun(previous);
  }
  if (S.view === 'leads') bindLeads();
  if (S.view === 'review') bindReview();
  if (S.view === 'matched') bindMatched();
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
  bindFilters();
  document.querySelectorAll('.lead-head').forEach(h =>
    h.onclick = () => h.parentElement.classList.toggle('open'));

  document.querySelectorAll('[data-remove]').forEach(b => b.onclick = (e) => {
    e.stopPropagation();  // the card header toggles open/closed on click
    removeLead(b.dataset.remove);
  });

  const btn = $('#btnExportLeads');
  if (btn) btn.onclick = () => downloadCsv(
    `supply-radar-leads-${S.snap.destination.split(',')[0].trim().toLowerCase()}-${stamp()}.csv`,
    // Column order is the order a Destination Specialist reads in: who they
    // are and how to reach them first, scores after, evidence last. A CSV that
    // opens on three decimal places and makes you scroll for the phone number
    // is a CSV nobody uses twice.
    // Exports what is on screen. Exporting leads the user just removed would
    // make the removal cosmetic.
    // Filters included deliberately: the button sits above a filtered list, so
    // exporting the unfiltered set would be a different answer to the one on
    // screen. Removed leads were already excluded for the same reason.
    toCsv(applyFilters(
      S.snap.leads.filter(l => !S.removed[l.place_source_id]), 'leads', 'category',
    ).map(l => ({
      band: l.band,
      name: l.name,
      category: l.viator_label || l.category || '',
      viator_category_path: l.viator_path || '',
      website: l.website || '',
      phone: l.phone || '',
      address: l.address || '',
      email: (l.extract && l.extract.contact_email) || '',
      booking: (l.extract && l.extract.booking) || 'not assessed',
      languages: (l.extract && (l.extract.languages || []).join(' ')) || '',
      rating: l.rating ?? '',
      reviews: l.review_count ?? '',
      composite: l.composite,
      quality: l.quality.score,
      readiness: l.readiness.score,
      gap_fit: l.gap_fit.score,
      why_this_band: BAND_MEANING[l.band] || '',
      destination: S.snap.destination,
      snapshot_generated: S.snap.generated_from || '',
    }))),
  );
}

const BAND_MEANING = {
  A: 'Contact first. Strong on quality and readiness, in a category with room for more supply.',
  B: 'Worth contacting. Solid on at least one axis, with a visible caveat on another.',
  C: 'Park for now. Thin evidence, or the category is already well served.',
};

function bindReview() {
  bindFilters();
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
  document.querySelectorAll('#tabs button').forEach(b =>
    b.onclick = () => { S.view = b.dataset.view; render(); });
  render();
}

/* The banner describes the published Split snapshot, and nothing else.
 *
 * It used to sit above every page including Discover, where a live sweep over
 * Dubrovnik ran underneath a header reading "Split, Croatia — 167 operators —
 * 105 net-new". Those numbers were never wrong; they were answering a question
 * nobody had asked on that page, next to results they had nothing to do with,
 * which is a more effective way to mislead than being wrong would be.
 *
 * Hidden on Discover, and named for what it is everywhere else.
 */
function renderTopMeta() {
  const el = $('#topmeta');
  if (!el) return;
  if (S.view === 'discover') {
    el.innerHTML = '';
    el.style.display = 'none';
    return;
  }
  el.style.display = '';
  const c = S.snap.counts;
  el.innerHTML = `
    <div><div class="k">Coverage to date</div>
      <div class="v" title="Sweeping another destination adds to these tables, it does not replace them">${esc(S.snap.destination)}</div></div>
    <div><div class="k">Operators</div><div class="v">${c.operators}</div></div>
    <div><div class="k">Net-new</div><div class="v">${c.net_new}</div></div>`;
}

boot();
