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

/** A single route option with its own "Confirm & Hold Seat" action. */
function RouteCard({ route, onSelect }) {
  return (
    <article className="route">
      <header className="route__head">
        <div>
          <h3 className="route__name">{route.service_name}</h3>
          <span className="route__id">{route.route_id}</span>
        </div>
        <span className="route__fare">
          LKR {Number(route.base_fare_lkr ?? 0).toLocaleString('en-LK')}
        </span>
      </header>

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
          <dd>{route.arrival_time ?? '—'}</dd>
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

      {onSelect && (
        <button className="btn btn--primary" onClick={() => onSelect(route.route_id)}>
          Confirm &amp; Hold Seat
        </button>
      )}
    </article>
  )
}