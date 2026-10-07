/**
 * Bus and train timetables.
 *
 * A table, not a chat answer: "what trains run to Jaffna" is a question with a
 * list as its answer, and scrolling a table beats scrolling a transcript.
 *
 * Synthetic services are labelled. The Kandy–Jaffna intercity has generated
 * times and a generated fare, and a timetable that does not say so is a lie
 * told in a table.
 */
import { useEffect, useMemo, useState } from 'react'
import { getSchedules } from '../api.js'

const MODES = [
  { value: 'ALL', label: 'All services' },
  { value: 'BUS', label: 'Buses' },
  { value: 'TRAIN', label: 'Trains' },
]

export default function Schedules() {
  const [mode, setMode] = useState('ALL')
  const [filter, setFilter] = useState('')
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getSchedules({ mode, limit: 500 })
      .then((payload) => {
        if (!cancelled) {
          setData(payload)
          setError(null)
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Could not load the timetable.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [mode])

  // Filtering happens here rather than server-side so that typing is instant:
  // one round trip per keystroke would be both slower and noisier in the logs.
  const rows = useMemo(() => {
    const services = data?.services || []
    const needle = filter.trim().toLowerCase()
    if (!needle) return services
    return services.filter((s) =>
      [s.origin, s.destination, s.route_id, s.service_name, s.provider]
        .filter(Boolean)
        .some((field) => String(field).toLowerCase().includes(needle)),
    )
  }, [data, filter])

  const counts = useMemo(() => {
    const services = data?.services || []
    return {
      bus: services.filter((s) => s.mode === 'BUS').length,
      train: services.filter((s) => s.mode === 'TRAIN').length,
    }
  }, [data])

  return (
    <section className="view schedules-view">
      <header className="view-header">
        <div>
          <h2>Timetables</h2>
          <p>
            {loading
              ? 'Loading services…'
              : `Showing ${rows.length} of ${data?.matched ?? 0} matching service(s)`}
          </p>
        </div>
        <div className="schedules-controls">
          <div className="segmented" role="group" aria-label="Filter by mode">
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
          <input
            type="search"
            className="schedules-search"
            placeholder="Filter by town, route or operator"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter services"
          />
        </div>
      </header>

      {counts.bus + counts.train > 0 && (
        <p className="schedules-tally">
          {counts.bus} bus · {counts.train} train in this list
          {data?.estimated_fares > 0 && (
            <span className="schedules-tally__note">
              {' · '}
              {data.estimated_fares} of these fares are{' '}
              <strong>estimated from distance</strong> — the operator has not
              published a price for that route. They are shown for comparison and
              cannot be booked.
            </span>
          )}
        </p>
      )}

      {error && <p className="login-error" role="alert">{error}</p>}

      {!loading && !error && (
        <div className="table-wrap">
          <table className="schedules-table">
            <caption className="sr-only">
              Sri Lanka bus and train services, sorted by departure time
            </caption>
            <thead>
              <tr>
                <th scope="col">Departs</th>
                <th scope="col">Mode</th>
                <th scope="col">From</th>
                <th scope="col">To</th>
                <th scope="col">Arrives</th>
                <th scope="col">Operator</th>
                <th scope="col">Fare</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((service) => (
                <tr key={service.route_id}>
                  <td className="num">{service.departure_time}</td>
                  <td>
                    <span className={`mode-chip ${service.mode.toLowerCase()}`}>
                      {service.mode === 'TRAIN' ? 'Train' : 'Bus'}
                    </span>
                  </td>
                  <td>{service.origin}</td>
                  <td>{service.destination}</td>
                  <td className="num">{service.arrival_time}</td>
                  <td>
                    {service.provider}
                  </td>
                  <td className="num">
                    {service.fare_lkr != null ? (
                      <>
                        LKR {Number(service.fare_lkr).toLocaleString('en-LK')}
                        {/* Priced by distance rather than published by the
                            operator, so it is marked at the cell it affects —
                            a column of confident-looking numbers is how a
                            modelled fare becomes a quoted one. */}
                        {service.fare_estimated && (
                          <span
                            className="fare-est"
                            title={service.fare_basis || 'Estimated from distance'}
                          >
                            est.
                          </span>
                        )}
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p className="empty">No services match that filter.</p>}
        </div>
      )}

      {data?.truncated && (
        <p className="schedules-note">
          Showing the first {data.returned} of {data.matched} services. Narrow the
          filter to see the rest.
        </p>
      )}
    </section>
  )
}