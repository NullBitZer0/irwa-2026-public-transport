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
                key={route.route_id}
                route={route}
                onSelect={message.onSelectRoute}
              />
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
  const canBook = onSelect && route.bookable !== false && !isConnection

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
          </span>
        </div>
        <span className="route__fare">
          LKR {Number(route.base_fare_lkr ?? 0).toLocaleString('en-LK')}
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

      {canBook && (
        <button className="btn btn--primary" onClick={() => onSelect(route.route_id)}>
          Confirm &amp; Hold Seat
        </button>
      )}

      {isConnection && (
        <p className="route__note">
          🔁 Two legs — book each ticket separately. Change at{' '}
          <strong>{route.transfer_station}</strong>.
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

            <div className="conn__legFare">
              LKR {Number(leg.base_fare_lkr ?? 0).toLocaleString('en-LK')}
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