/**
 * Sri Lanka route map.
 *
 * Drawn as inline SVG rather than with a tile library, for three reasons: it
 * works offline, it renders the same in CI, and it cannot leak the traveller's
 * viewport to a tile server as a side effect of looking at a bus route.
 *
 * The island outline is real Natural Earth 1:10m boundary data served by the
 * planner, including the offshore islands — Mannar is a town on Mannar Island, so
 * drawing only the main landmass would put it in the sea. Bounds come from the
 * server with the geometry, so the projection cannot drift out of step with the
 * shape it is projecting.
 *
 * What it is: city-level corridors from the timetable, plotted on that boundary.
 * What it is not: a survey map. Stops inside Greater Colombo are clustered into
 * Colombo, and services that never leave their city have no line to draw — both
 * are stated on screen, because a map that quietly omits a fifth of the network
 * reads as "that route does not exist".
 */
import { useEffect, useMemo, useState } from 'react'
import { getMapRoutes } from '../api.js'

// Fallback extent in degrees, used only before the server responds. The real
// bounds arrive with the geometry.
const FALLBACK_BOUNDS = { min_lat: 5.86, max_lat: 9.89, min_lng: 79.6, max_lng: 81.95 }
const VIEW_HEIGHT = 900

/**
 * Equirectangular projection, built from the bounds the server sent.
 *
 * Width follows the data's own aspect ratio, so the island is not stretched: a
 * fixed viewBox would deform every circle-ish feature on the coast and quietly
 * move plotted towns a few degrees off their real positions.
 */
function makeProjection(bounds) {
  const latSpan = bounds.max_lat - bounds.min_lat
  const lngSpan = bounds.max_lng - bounds.min_lng
  const width = Math.round((lngSpan / latSpan) * VIEW_HEIGHT)
  const project = (lat, lng) => {
    const x = ((lng - bounds.min_lng) / lngSpan) * width
    // Latitude increases northward and SVG y increases downward, so it flips.
    const y = ((bounds.max_lat - lat) / latSpan) * VIEW_HEIGHT
    return [x, y]
  }
  project.width = width
  project.height = VIEW_HEIGHT
  return project
}

const MODES = [
  { value: 'ALL', label: 'All' },
  { value: 'BUS', label: 'Buses' },
  { value: 'TRAIN', label: 'Trains' },
]

export default function RouteMap() {
  const [mode, setMode] = useState('ALL')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getMapRoutes({ mode })
      .then((payload) => {
        if (!cancelled) {
          setData(payload)
          setSelected(null)
          setError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Could not load the route map.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [mode])

  const project = useMemo(() => makeProjection(data?.bounds || FALLBACK_BOUNDS), [data?.bounds])

  // One path per ring: the main island plus the significant offshore islands.
  const islandPaths = useMemo(() => {
    const rings = data?.rings?.length ? data.rings : data?.outline || []
    return rings
      .map((ring) => {
        const points = ring.map(([lat, lng]) => project(lat, lng))
        if (points.length < 3) return null
        return `M ${points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' L ')} Z`
      })
      .filter(Boolean)
  }, [data, project])

  // Only the busiest corridors are drawn by default: 92 lines is a grey wash,
  // not a map. The rest are one click away.
  const [showAll, setShowAll] = useState(false)
  const corridors = useMemo(() => {
    const all = data?.corridors || []
    if (showAll) return all
    return all.filter((c) => c.service_count >= 3).slice(0, 40)
  }, [data, showAll])

  const hiddenCount = (data?.corridors?.length ?? 0) - corridors.length
  const nodeFor = (city) => (data?.nodes || []).find((n) => n.city === city)

  return (
    <section className="view map-view">
      <header className="view-header">
        <div>
          <h2>Route map</h2>
          <p>
            {loading
              ? 'Loading routes…'
              : `${data?.corridors?.length ?? 0} corridor(s) across ${data?.nodes?.length ?? 0} cities`}
          </p>
        </div>
        <div className="segmented" role="group" aria-label="Filter map by mode">
          {MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              className={mode === option.value ? 'active' : ''}
              aria-pressed={mode === option.value}
              onClick={() => setMode(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      {error && <p className="login-error" role="alert">{error}</p>}

      <div className="map-layout">
        <div className="map-canvas">
          <svg
            viewBox={`0 0 ${project.width} ${project.height}`}
            className="sri-lanka-map"
            role="img"
            aria-label="Map of Sri Lanka showing available bus and train corridors"
          >
            <g className="map-islands">
              {islandPaths.map((d, index) => (
                <path key={index} d={d} className="map-island" />
              ))}
            </g>

            <g className="map-corridors">
              {corridors.map((corridor) => {
                const [x1, y1] = project(corridor.from[0], corridor.from[1])
                const [x2, y2] = project(corridor.to[0], corridor.to[1])
                // Bow the line towards the west so the two directions of a
                // corridor do not lie exactly on top of each other.
                const midX = (x1 + x2) / 2
                const midY = (y1 + y2) / 2
                const dx = x2 - x1
                const dy = y2 - y1
                const length = Math.sqrt(dx * dx + dy * dy) || 1
                const bow = Math.min(length * 0.12, 26)
                const cxp = midX - (dy / length) * bow
                const cyp = midY + (dx / length) * bow
                const isTrain = corridor.modes.includes('TRAIN')
                const isSelected = selected === corridor

                return (
                  <path
                    key={corridorKey(corridor)}
                    d={`M ${x1},${y1} Q ${cxp},${cyp} ${x2},${y2}`}
                    className={`corridor ${isTrain ? 'train' : 'bus'} ${isSelected ? 'selected' : ''}`}
                    strokeWidth={Math.max(1.2, Math.min(corridor.service_count / 2, 5))}
                    strokeOpacity={isSelected ? 0.95 : 0.32}
                    onClick={() => setSelected(selected === corridor ? null : corridor)}
                  >
                    <title>
                      {`${corridor.origin_city} → ${corridor.destination_city}: ${corridor.service_count} service(s)`}
                    </title>
                  </path>
                )
              })}
            </g>

            <g className="map-nodes">
              {(data?.nodes || []).map((node) => {
                const [x, y] = project(node.lat, node.lng)
                const radius = Math.max(3, Math.min(node.service_count / 22, 9))
                return (
                  <g key={node.city}>
                    <circle cx={x} cy={y} r={radius} className="map-node" />
                    <text x={x + radius + 4} y={y + 4} className="map-label">
                      {node.city}
                    </text>
                  </g>
                )
              })}
            </g>
          </svg>

          {hiddenCount > 0 && (
            <button type="button" className="map-show-all" onClick={() => setShowAll((v) => !v)}>
              {showAll ? 'Show main corridors only' : `Show all ${hiddenCount + corridors.length} corridors`}
            </button>
          )}
        </div>

        <aside className="map-detail">
          {selected ? (
            <div>
              <h3>{selected.origin_city} → {selected.destination_city}</h3>
              <dl>
                <dt>Services</dt>
                <dd>{selected.service_count}</dd>
                <dt>Modes</dt>
                <dd>{selected.modes.map((m) => (m === 'TRAIN' ? 'Train' : 'Bus')).join(', ')}</dd>
                <dt>Operators</dt>
                <dd>{selected.providers.join(', ')}</dd>
                <dt>Fare range</dt>
                <dd>
                  {selected.min_fare_lkr != null
                    ? `LKR ${selected.min_fare_lkr} – ${selected.max_fare_lkr}`
                    : 'Not published'}
                </dd>
              </dl>
              {selected.stops.length > 0 && (
                <p className="map-stops">
                  <strong>Via:</strong> {selected.stops.slice(0, 12).join(' → ')}
                  {selected.stops.length > 12 ? ' …' : ''}
                </p>
              )}
              {selected.synthetic && (
                <p className="map-flag">Includes generated demo data.</p>
              )}
              <p className="map-hint">Select the same line again to clear.</p>
            </div>
          ) : (
            <div>
              <h3>Pick a route</h3>
              <p className="map-hint">
                Click a line to see the services, operators and fare range on that
                corridor. Line thickness is the number of services.
              </p>
            </div>
          )}

          {data?.coverage && (
            <div className="map-coverage">
              <h4>What this map shows</h4>
              <p>{data.coverage.note}</p>
              <p className="map-coverage-numbers">
                {data.coverage.drawn_as_corridors} of {data.coverage.services_in_scope} services
                drawn · {data.coverage.intra_city_not_drawn} run within a single city
              </p>
              {data.boundary_source && (
                <p className="map-coverage-source">
                  Outline: {data.boundary_source}. City markers use settlement
                  centres, so a coastal town can sit a couple of kilometres off
                  this generalised coastline.
                </p>
              )}
              {data.coverage.unmapped_places?.length > 0 && (
                <p className="map-flag">
                  {data.coverage.unmapped_places.length} place(s) we cannot place on the
                  map yet, so they are not drawn.
                </p>
              )}
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}

function corridorKey(corridor) {
  return `${corridor.origin_city}->${corridor.destination_city}`
}