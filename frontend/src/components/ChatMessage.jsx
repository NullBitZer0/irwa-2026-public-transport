import { renderMarkdown } from '../markdown.js'

/**
 * One turn in the conversation.
 * @param {{role:'user'|'agent', text:string, routes?:Array, intent?:string|null,
 *          bookingReference?:string|null, bookingStatus?:string|null}} message
 */
export default function ChatMessage({ message }) {
  const isUser = message.role === 'user'

  return (
    <div className={`msg ${isUser ? 'msg--user' : 'msg--agent'}`}>
      <div className="msg__avatar" aria-hidden="true">
        {isUser ? '🧑' : '🚆'}
      </div>

      <div className="msg__body">
        {message.intent && !isUser && (
          <span className={`badge badge--${message.intent.toLowerCase()}`}>
            {message.intent.replace('_', ' ')}
          </span>
        )}

        <div
          className="msg__text"
          // Safe: renderMarkdown escapes all HTML before applying its own tags.
          dangerouslySetInnerHTML={{ __html: renderMarkdown(message.text) }}
        />

        {message.routes?.length > 0 && (
          <div className="routes">
            {message.routes.map((route) => (
              <RouteCard
                key={route.route_id ?? route.service_name}
                route={route}
                onSelect={message.onSelectRoute}
              />
            ))}
          </div>
        )}

        {/* Quick replies while the planner is waiting on a mode or a time */}
        {message.clarification?.options?.length > 0 && (
          <div className="chips">
            {message.clarification.options.map((option) => (
              <button
                key={option.value}
                className="chip"
                onClick={() => message.onQuickReply?.(option.value)}
                disabled={message.onQuickReply === undefined}
              >
                {option.label}
              </button>
            ))}
          </div>
        )}

        {message.bookingReference && (
          <div className="booking">
            <div>
              <strong>Booking reference:</strong>{' '}
              <code>{message.bookingReference}</code>
            </div>
            <div className="booking__status">{message.bookingStatus}</div>
          </div>
        )}
      </div>
    </div>
  )
}

/** A single route option, or a multi-leg connection when `route.legs` is present. */
function RouteCard({ route, onSelect }) {
  const isConnection = Boolean(route.is_connection)
  // A connection is bookable as a journey — two tickets, one approval each,
  // held together — and the planner says whether it can be. It cannot when a leg
  // has no published fare, because the traveller would be approving a charge we
  // cannot quote. Falls back to inspecting the legs for older payloads.
  const connectionBookable =
    isConnection &&
    (route.bookable_as_connection ??
      (route.fare_known && (route.legs ?? []).every((leg) => leg.base_fare_lkr)))
  const canBook =
    onSelect &&
    (isConnection ? connectionBookable : route.bookable !== false)
  // A missing fare must not read as "free".
  const fare =
    route.fare_unknown || !route.base_fare_lkr
      ? 'Fare not published'
      : `LKR ${Number(route.base_fare_lkr).toLocaleString('en-LK')}`

  return (
    <article className={`route ${isConnection ? 'route--connection' : ''}`}>
      <header className="route__head">
        <div>
          <h3 className="route__name">
            {isConnection ? (
              <>
                <span className="route__mode route__mode--mixed" title="Mixed mode">
                  🔀
                </span>{' '}
                {route.origin} → {route.destination}
              </>
            ) : (
              route.service_name
            )}
          </h3>
          <span className="route__id">
            {route.route_id}
            {route.route_number && (
              <>
                {' · '}
                <span title="Operator route number">Route {route.route_number}</span>
              </>
            )}
            {route.synthetic && (
              <span
                className="route__synthetic"
                title={`Generated for demonstration. Derived: ${
                  (route.synthetic_fields ?? []).join(', ') || 'times'
                }`}
              >
                {' '}
                · demo data
              </span>
            )}
          </span>
        </div>
        <span className={`route__fare ${route.fare_unknown ? 'route__fare--unknown' : ''}`}>
          {fare}
        </span>
      </header>

      {isConnection ? (
        <ConnectionDetails route={route} />
      ) : (
        <>
          <div className="route__path">
            <span>{route.origin}</span>
            <span className="route__arrow" aria-hidden="true">→</span>
            <span>{route.destination}</span>
          </div>

          {/* A long-distance coach that only passes through the boarding point */}
          {route.board_type && (
            <p className="route__board">
              {route.board_type === 'passing' ? (
                <>
                  🛏️ Long-distance service — it starts at{' '}
                  <strong>{route.service_origin}</strong> and passes your stop,
                  boarding at <strong>{route.boards_at}</strong>.
                </>
              ) : (
                <>
                  🚏 Starts here, boards at <strong>{route.boards_at}</strong>.
                </>
              )}
            </p>
          )}

          <dl className="route__meta">
            <div>
              <dt>Departs</dt>
              <dd>{route.departure_time}</dd>
            </div>
            <div>
              <dt>Arrives</dt>
              <dd>
                {route.arrival_time ?? '—'}
                {route.arrival_next_day && (
                  <span className="conn__nextDay"> +1 day</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Provider</dt>
              <dd>{route.provider}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{String(route.transit_type ?? '').replace(/_/g, ' ')}</dd>
            </div>
          </dl>

          {route.stops?.length > 0 && (
            <p className="route__stops">
              <span className="route__stopsLabel">Stops:</span> {route.stops.join(' · ')}
            </p>
          )}

          {route.classes?.length > 0 && (
            <p className="route__classes">{route.classes.join(' · ')}</p>
          )}

        </>
      )}

      {/* Provenance is decided server-side (src/responsible_ai/grounding.py) and
          arrives on the route, so this UI cannot drift from the citation logic. */}
      {route.citation_source && (
        <p className="route__source" title="Responsible AI — data provenance">
          🔍 Source: {route.citation_source}
        </p>
      )}

      {canBook && (
        <button className="btn btn--primary" onClick={() => onSelect(route)}>
          {isConnection ? `Book both tickets` : 'Confirm & Hold Seat'}
        </button>
      )}

      {isConnection && (
        <p className="route__note">
          🔁 Two tickets — one per leg, changing at{' '}
          <strong>{route.transfer_station}</strong>.{' '}
          {connectionBookable
            ? "You'll approve both, and pay for both together."
            : 'A leg has no published fare, so this journey cannot be booked online.'}
        </p>
      )}
    </article>
  )
}

/** Leg-by-leg timeline for a connection. */
function ConnectionDetails({ route }) {
  const legs = route.legs ?? []

  return (
    <>
      <div className="conn__summary">
        <span>
          <span className="route__stopsLabel">Departs</span> {route.departure_time}
        </span>
        <span>
          <span className="route__stopsLabel">Arrives</span> {route.arrival_time}
        </span>
        <span>
          <span className="route__stopsLabel">Total</span>{' '}
          {Math.floor(route.duration_minutes / 60)}h {route.duration_minutes % 60}m
        </span>
        <span className={route.mixed_mode ? 'conn__mixed' : ''}>
          {route.mixed_mode ? 'Train + bus' : 'Same mode'}
        </span>
      </div>

      <ol className="conn__legs">
        {legs.map((leg, index) => (
          <li key={`${leg.route_id}-${index}`} className="conn__leg">
            <span
              className={`route__mode route__mode--${leg.mode === 'TRAIN' ? 'train' : 'bus'}`}
              title={leg.mode}
            >
              {leg.mode === 'TRAIN' ? '🚆' : '🚌'}
            </span>

            <div className="conn__legBody">
              <div className="conn__legTitle">
                {leg.service_name} <span className="route__id">{leg.route_id}</span>
              </div>
              <div className="conn__legPath">
                {leg.origin} <span className="route__arrow">→</span> {leg.destination}
              </div>
              <div className="conn__legTimes">
                {leg.departure_time} – {leg.arrival_time}
                {leg.next_day && <span className="conn__nextDay"> (+1 day)</span>}
              </div>
            </div>

            {/* A modelled or unpublished leg has no fare. Rendering LKR 0 would
                read as free, which is the one thing it is not. */}
            <div className="conn__legFare">
              {leg.base_fare_lkr ? (
                `LKR ${Number(leg.base_fare_lkr).toLocaleString('en-LK')}`
              ) : (
                'Fare not published'
              )}
            </div>
          </li>
        ))}

        {route.transfer_station && (
          <li className="conn__transfer">
            <span className="conn__transferIcon" aria-hidden="true">⇄</span>
            <span>
              Change at <strong>{route.transfer_station}</strong> —{' '}
              {Math.floor(route.transfer_minutes / 60)}h {route.transfer_minutes % 60}m
            </span>
          </li>
        )}
      </ol>
    </>
  )
}