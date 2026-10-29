import * as maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
// MapLibre 6 picks its worker file at runtime, which Vite can't see. Importing it with ?url
// makes Vite copy it into the build, and we hand MapLibre the final URL.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import * as d3 from 'd3'
import './style.css'

maplibregl.setWorkerUrl(new URL(workerUrl, location.href).href)

const DATA = `${import.meta.env.BASE_URL}data/`
const BASEMAP = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json'
const CERTIFIED_MI = 42.195 / 1.609344
const FT_PER_MI = 5280
const C = { inner: '#3ddbd9', outer: '#ff6a3d', course: '#f4f1ea' }

// Rough borough breaks by mile, for chart context.
const BOROUGHS = [
  { mile: 0, name: 'SI' }, { mile: 1.9, name: 'Brooklyn' }, { mile: 13.1, name: 'Qns' },
  { mile: 15.5, name: 'Man' }, { mile: 19.5, name: 'Bx' }, { mile: 20.8, name: 'Man' },
]

const state = { mode: 'arc', drift: 1, selected: null }
const fmtFt = d3.format(',.0f')

const [course, miles, turns, paths, cumulative, summary] = await Promise.all(
  ['course.geojson', 'miles.geojson', 'turns.geojson', 'turn_paths.geojson', 'cumulative.json', 'summary.json']
    .map((f) => fetch(DATA + f).then((r) => r.json())),
)
const turnById = new Map(turns.features.map((f) => [f.properties.id, f]))
const ranked = [...turns.features].sort((a, b) => a.properties.rank - b.properties.rank)
document.getElementById('n-turns').textContent = turns.features.length

const extraOf = (p) => (state.mode === 'arc' ? p.extra_arc_ft : p.extra_sharp_ft) * state.drift
const totalExtra = () => (state.mode === 'arc' ? summary.extra_arc_ft : summary.extra_sharp_ft) * state.drift

// ---------- Odometer ----------
const odo = document.getElementById('odo')
const odoFt = document.getElementById('odo-ft')
let shownFt = 0
function setOdometer(ft, animate = true) {
  const from = shownFt
  shownFt = ft
  d3.select(odo).interrupt().transition().duration(animate ? 900 : 0).ease(d3.easeCubicOut)
    .tween('text', () => {
      const i = d3.interpolateNumber(from, ft)
      return (t) => {
        const v = i(t)
        odo.textContent = (CERTIFIED_MI + v / FT_PER_MI).toFixed(2)
        odoFt.textContent = `+${fmtFt(v)} ft (+${(v / FT_PER_MI).toFixed(2)} mi)`
      }
    })
}

// ---------- Chart ----------
const chart = (() => {
  const el = document.getElementById('chart')
  const width = el.clientWidth || 380
  const height = 190
  const m = { top: 14, right: 12, bottom: 24, left: 44 }
  const svg = d3.select(el).append('svg').attr('width', width).attr('height', height)
  const x = d3.scaleLinear([0, CERTIFIED_MI], [m.left, width - m.right])
  const y = d3.scaleLinear().range([height - m.bottom, m.top])

  const boroughs = svg.append('g').attr('class', 'borough')
  BOROUGHS.forEach((b, i) => {
    const g = boroughs.append('g').attr('transform', `translate(${x(b.mile)},0)`)
    if (i) g.append('line').attr('y1', m.top - 4).attr('y2', height - m.bottom)
    g.append('text').attr('x', 3).attr('y', m.top - 2).text(b.name)
  })
  const gridG = svg.append('g').attr('class', 'grid')
  const yAxisG = svg.append('g').attr('class', 'axis').attr('transform', `translate(${m.left},0)`)
  svg.append('g').attr('class', 'axis').attr('transform', `translate(0,${height - m.bottom})`)
    .call(d3.axisBottom(x).tickValues([0, 5, 10, 13.1, 15, 20, 26.2]).tickFormat((d) => (d === 26.2 ? '26.2 mi' : d)).tickSizeOuter(0))
  const area = svg.append('path').attr('class', 'area')
  const line = svg.append('path').attr('class', 'step')
  const dots = svg.append('g')
  const rule = svg.append('line').attr('class', 'rule').attr('y1', m.top).attr('y2', height - m.bottom).style('display', 'none')
  const readout = svg.append('text').attr('class', 'readout').attr('y', m.top + 10)

  const key = () => (state.mode === 'arc' ? 'arc' : 'sharp')
  function draw(first = false) {
    const k = key()
    const pts = cumulative.map((d) => ({ ...d, v: d[k] * state.drift }))
    y.domain([0, (d3.max(cumulative, (d) => d.sharp) || 1) * 1.05]).nice()
    yAxisG.call(d3.axisLeft(y).ticks(4).tickFormat((d) => (d ? `${fmtFt(d)}′` : '0')).tickSizeOuter(0))
    gridG.call(d3.axisLeft(y).ticks(4).tickSize(-(width - m.left - m.right)).tickFormat(''))
      .attr('transform', `translate(${m.left},0)`).select('.domain').remove()
    const lineGen = d3.line().curve(d3.curveStepAfter).x((d) => x(d.mile)).y((d) => y(d.v))
    const areaGen = d3.area().curve(d3.curveStepAfter).x((d) => x(d.mile)).y0(y(0)).y1((d) => y(d.v))
    const t = svg.transition().duration(first ? 0 : 600)
    area.transition(t).attr('d', areaGen(pts))
    line.transition(t).attr('d', lineGen(pts))
    if (first) {
      // Draw the line on like a runner covering the course.
      const len = line.node().getTotalLength()
      line.attr('stroke-dasharray', `${len} ${len}`).attr('stroke-dashoffset', len)
        .transition().duration(2600).ease(d3.easeSinInOut).attr('stroke-dashoffset', 0)
        .on('end', () => line.attr('stroke-dasharray', null))
      area.attr('opacity', 0).transition().delay(1600).duration(1000).attr('opacity', 1)
    }
    const turnPts = pts.filter((d) => d.id != null)
    dots.selectAll('circle').data(turnPts, (d) => d.id).join('circle')
      .attr('class', 'turn-dot').attr('r', (d) => (turnById.get(d.id).properties.rank <= 10 ? 3.5 : 2.2))
      .on('click', (_, d) => selectTurn(d.id))
      .transition(t).attr('cx', (d) => x(d.mile)).attr('cy', (d) => y(d.v))
    highlight(state.selected)
  }
  function highlight(id) {
    dots.selectAll('circle').classed('active', (d) => d.id === id)
  }
  svg.on('mousemove', (ev) => {
    const [mx] = d3.pointer(ev)
    const mile = x.invert(mx)
    if (mile < 0 || mile > CERTIFIED_MI) return
    const k = key()
    const i = d3.bisector((d) => d.mile).right(cumulative, mile) - 1
    const v = cumulative[Math.max(i, 0)][k] * state.drift
    rule.style('display', null).attr('x1', x(mile)).attr('x2', x(mile))
    readout.attr('x', Math.min(x(mile) + 6, width - 120)).text(`mi ${mile.toFixed(1)} · +${fmtFt(v)} ft`)
    map.getSource('cursor')?.setData(pointAtMile(mile))
  }).on('mouseleave', () => {
    rule.style('display', 'none'); readout.text('')
    map.getSource('cursor')?.setData(empty())
  })
  return { draw, highlight }
})()

// ---------- Top turns list ----------
const list = d3.select('#top-turns')
function drawList() {
  const top = ranked.slice(0, 8)
  const max = d3.max(top, (f) => extraOf(f.properties))
  list.selectAll('li').data(top, (f) => f.properties.id).join((enter) => {
    const li = enter.append('li').on('click', (_, f) => selectTurn(f.properties.id))
    li.append('span').attr('class', 'rank').text((f) => f.properties.rank)
    li.append('span').attr('class', 'name').html((f) => `${prettyStreets(f.properties.streets)}<small>mile ${f.properties.mile.toFixed(1)} · ${Math.abs(f.properties.angle_deg).toFixed(0)}° ${f.properties.direction}</small>`)
    li.append('span').attr('class', 'ft')
    li.append('span').attr('class', 'bar')
    return li
  }).classed('active', (f) => f.properties.id === state.selected)
  list.selectAll('li').select('.ft').text((f) => `+${fmtFt(extraOf(f.properties))}′`)
  list.selectAll('li').select('.bar').style('width', (f) => `${(extraOf(f.properties) / max) * 100}%`)
}

// Street names arrive already cleaned up by the pipeline (see friendly() in turns.py).
const prettyStreets = (s) => s

// ---------- Map ----------
const isMobile = matchMedia('(max-width: 820px)').matches
const map = new maplibregl.Map({
  container: 'map',
  style: BASEMAP,
  bounds: summary.bounds,
  fitBoundsOptions: { padding: isMobile ? 24 : 60 },
  attributionControl: { compact: true },
})
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

const empty = () => ({ type: 'FeatureCollection', features: [] })
const courseLine = course.features.flatMap((f) => f.geometry.coordinates)

function pointAtMile(mile) {
  const f = course.features.find((c) => mile >= c.properties.mile_start && mile <= c.properties.mile_end)
  if (!f) return empty()
  const t = (mile - f.properties.mile_start) / Math.max(f.properties.mile_end - f.properties.mile_start, 1e-9)
  return { type: 'Feature', geometry: { type: 'Point', coordinates: along(f.geometry.coordinates, t) }, properties: {} }
}

// Position a fraction t (0..1) along a lon/lat polyline, using a local flat-earth approximation.
function measure(coords) {
  const k = Math.cos((coords[0][1] * Math.PI) / 180)
  const cum = [0]
  for (let i = 1; i < coords.length; i++) {
    const dx = (coords[i][0] - coords[i - 1][0]) * k
    const dy = coords[i][1] - coords[i - 1][1]
    cum.push(cum[i - 1] + Math.hypot(dx, dy))
  }
  return cum
}
function along(coords, t, cum = measure(coords)) {
  const target = Math.min(Math.max(t, 0), 1) * cum[cum.length - 1]
  const i = Math.max(1, d3.bisectLeft(cum, target))
  const seg = cum[i] - cum[i - 1] || 1
  const u = (target - cum[i - 1]) / seg
  const a = coords[i - 1], b = coords[Math.min(i, coords.length - 1)]
  return [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]
}

map.on('load', () => {
  // Calm the basemap down so the course pops.
  for (const layer of map.getStyle().layers) {
    if (layer.type === 'symbol' && /poi|housenumber|transit/.test(layer.id)) map.setLayoutProperty(layer.id, 'visibility', 'none')
  }

  map.addSource('course', { type: 'geojson', data: course })
  map.addSource('miles', { type: 'geojson', data: miles })
  map.addSource('turns', { type: 'geojson', data: turns, promoteId: 'id' })
  map.addSource('paths', { type: 'geojson', data: empty() })
  map.addSource('ghosts', { type: 'geojson', data: empty() })
  map.addSource('cursor', { type: 'geojson', data: empty() })

  map.addLayer({ id: 'course-glow', type: 'line', source: 'course', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': C.outer, 'line-opacity': 0.25, 'line-blur': 6, 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 6, 16, 22] } })
  map.addLayer({ id: 'course', type: 'line', source: 'course', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': C.course, 'line-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0.95, 16, 0.25],
      'line-width': ['interpolate', ['linear'], ['zoom'], 10, 1.6, 16, 4] } })
  map.addLayer({ id: 'paths', type: 'line', source: 'paths', layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: {
      'line-color': ['match', ['get', 'kind'], 'inner', C.inner, C.outer],
      'line-width': ['match', ['get', 'kind'], 'outer_sharp', 1.5, 4],
      'line-opacity': ['match', ['get', 'kind'], 'outer_sharp', 0.45, 0.95],
      'line-dasharray': ['match', ['get', 'kind'], 'outer_sharp', ['literal', [2, 2]], ['literal', [1, 0]]],
    } })
  map.addLayer({ id: 'miles', type: 'circle', source: 'miles', minzoom: 11.5,
    paint: { 'circle-radius': 7, 'circle-color': '#0d0f14', 'circle-stroke-color': '#5d6577', 'circle-stroke-width': 1 } })
  map.addLayer({ id: 'miles-label', type: 'symbol', source: 'miles', minzoom: 11.5,
    layout: { 'text-field': ['to-string', ['get', 'mile']], 'text-size': 9, 'text-font': ['Open Sans Bold'], 'text-allow-overlap': true },
    paint: { 'text-color': '#aab2c3' } })
  map.addLayer({ id: 'turns', type: 'circle', source: 'turns',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'],
        10, ['*', 0.45, ['sqrt', ['get', 'extra_arc_ft']]], 14, ['*', 1.1, ['sqrt', ['get', 'extra_arc_ft']]]],
      'circle-color': ['interpolate', ['linear'], ['get', 'extra_arc_ft'], 0, '#ffd2a8', 60, C.outer, 110, '#ff2d55'],
      'circle-opacity': ['case', ['boolean', ['feature-state', 'selected'], false], 0.15, 0.85],
      'circle-stroke-color': '#0d0f14',
      'circle-stroke-width': ['case', ['boolean', ['feature-state', 'hover'], false], 2.5, 1],
    } })
  map.addLayer({ id: 'cursor', type: 'circle', source: 'cursor',
    paint: { 'circle-radius': 6, 'circle-color': '#fff', 'circle-stroke-color': C.outer, 'circle-stroke-width': 3 } })
  map.addLayer({ id: 'ghosts', type: 'circle', source: 'ghosts',
    paint: { 'circle-radius': 8, 'circle-color': ['match', ['get', 'kind'], 'inner', C.inner, C.outer],
      'circle-stroke-color': '#fff', 'circle-stroke-width': 2, 'circle-blur': 0.1 } })

  // Hover on turns and course.
  const tip = document.getElementById('hover-tip')
  let hovered = null
  map.on('mousemove', 'turns', (e) => {
    const f = e.features[0]
    if (hovered !== null) map.setFeatureState({ source: 'turns', id: hovered }, { hover: false })
    hovered = f.id
    map.setFeatureState({ source: 'turns', id: hovered }, { hover: true })
    map.getCanvas().style.cursor = 'pointer'
    showTip(e.point, `${prettyStreets(f.properties.streets)} · <b>+${fmtFt(extraOf(f.properties))} ft</b>`)
  })
  map.on('mouseleave', 'turns', () => {
    if (hovered !== null) map.setFeatureState({ source: 'turns', id: hovered }, { hover: false })
    hovered = null
    map.getCanvas().style.cursor = ''
    tip.hidden = true
  })
  map.on('mousemove', 'course-glow', (e) => {
    if (hovered !== null) return
    const p = e.features[0].properties
    showTip(e.point, `${p.street} · ${Math.round(p.width_ft)} ft wide`)
  })
  map.on('mouseleave', 'course-glow', () => { if (hovered === null) tip.hidden = true })
  map.on('click', 'turns', (e) => selectTurn(e.features[0].id))
  function showTip(pt, html) {
    tip.innerHTML = html
    tip.style.left = `${pt.x}px`
    tip.style.top = `${pt.y}px`
    tip.hidden = false
  }
})

// The panel doesn't need the map, so animate it right away instead of waiting for basemap tiles.
chart.draw(true)
drawList()
setTimeout(() => setOdometer(totalExtra()), 300)

// ---------- Selecting a turn: fly there and race the ghosts ----------
const card = document.getElementById('turn-card')
const set = (k, v) => { card.querySelector(`[data-k="${k}"]`).textContent = v }
let raceFrame = null

function selectTurn(id) {
  const f = turnById.get(id)
  if (!f) return
  if (!map.getSource('turns')) return map.once('load', () => selectTurn(id))
  if (state.selected !== null) map.setFeatureState({ source: 'turns', id: state.selected }, { selected: false })
  state.selected = id
  map.setFeatureState({ source: 'turns', id }, { selected: true })
  const p = f.properties
  set('mile', p.mile.toFixed(1)); set('rank', p.rank); set('n', turns.features.length)
  set('streets', prettyStreets(p.streets))
  set('angle', `${Math.abs(p.angle_deg).toFixed(0)}°`)
  set('width', `${Math.round(p.width_ft)} ft`)
  set('extra', `+${fmtFt(extraOf(p))} ft`)
  card.hidden = false
  chart.highlight(id)
  drawList()

  const pathFeatures = paths.features.filter((pf) => pf.properties.id === id &&
    (pf.properties.kind !== 'outer_sharp' || state.mode === 'sharp'))
  map.getSource('paths').setData({ type: 'FeatureCollection', features: pathFeatures })
  const bounds = new maplibregl.LngLatBounds()
  pathFeatures.forEach((pf) => pf.geometry.coordinates.forEach((c) => bounds.extend(c)))
  // Keep the turn clear of the info card (bottom-left on desktop, bottom on mobile).
  map.fitBounds(bounds, { padding: isMobile ? { top: 30, bottom: 200, left: 30, right: 30 } : { top: 60, bottom: 300, left: 60, right: 60 },
    maxZoom: 18.5, duration: 1800, essential: true })
  map.once('moveend', () => race(id))
}

function race(id) {
  cancelAnimationFrame(raceFrame)
  const kinds = ['inner', state.mode === 'sharp' ? 'outer_sharp' : 'outer']
  const lanes = kinds.map((kind) => {
    const pf = paths.features.find((x) => x.properties.id === id && x.properties.kind === kind)
    return pf && { kind: kind === 'inner' ? 'inner' : 'outer', coords: pf.geometry.coordinates,
      cum: measure(pf.geometry.coordinates), len: pf.properties.length_ft }
  }).filter(Boolean)
  if (lanes.length < 2) return
  // Both ghosts run at the same speed; the longer (outside) lane takes ~3.2 s.
  const ftPerMs = Math.max(lanes[0].len, lanes[1].len) / 3200
  const start = performance.now()
  set('inner-status', ''); set('outer-status', '')
  const tick = (now) => {
    const ran = (now - start) * ftPerMs
    map.getSource('ghosts').setData({ type: 'FeatureCollection', features: lanes.map((l) => ({
      type: 'Feature', properties: { kind: l.kind },
      geometry: { type: 'Point', coordinates: along(l.coords, ran / l.len, l.cum) } })) })
    if (ran >= lanes[0].len) set('inner-status', 'finished ✓')
    if (ran >= lanes[0].len && ran < lanes[1].len) set('outer-status', `${fmtFt(lanes[1].len - ran)} ft to go…`)
    if (ran >= lanes[1].len) { set('outer-status', `+${fmtFt(extraOf(turnById.get(id).properties))} ft longer`); return }
    raceFrame = requestAnimationFrame(tick)
  }
  raceFrame = requestAnimationFrame(tick)
}

card.querySelector('.replay').addEventListener('click', () => state.selected !== null && race(state.selected))
card.querySelector('.close').addEventListener('click', () => {
  card.hidden = true
  cancelAnimationFrame(raceFrame)
  if (state.selected !== null) map.setFeatureState({ source: 'turns', id: state.selected }, { selected: false })
  state.selected = null
  map.getSource('paths').setData(empty())
  map.getSource('ghosts').setData(empty())
  chart.highlight(null); drawList()
  map.fitBounds(summary.bounds, { padding: isMobile ? 24 : 60, duration: 1500 })
})

// ---------- Controls ----------
document.querySelectorAll('.seg button').forEach((btn) => btn.addEventListener('click', () => {
  state.mode = btn.dataset.mode
  document.querySelectorAll('.seg button').forEach((b) => {
    b.classList.toggle('on', b === btn); b.setAttribute('aria-checked', b === btn)
  })
  refresh()
  if (state.selected !== null) selectTurn(state.selected)
}))
const drift = document.getElementById('drift')
drift.addEventListener('input', () => {
  state.drift = drift.value / 100
  document.getElementById('drift-val').textContent = `${drift.value}%`
  refresh(false)
})
function refresh(animate = true) {
  setOdometer(totalExtra(), animate)
  chart.draw()
  drawList()
  if (state.selected !== null) set('extra', `+${fmtFt(extraOf(turnById.get(state.selected).properties))} ft`)
}
