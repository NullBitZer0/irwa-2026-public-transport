import { health } from '../api.js'

const EXAMPLES = [
  'Heta ude Colombo indan Kandy yanna train ekak balanna',
  'Express train from Colombo Fort to Kandy tomorrow morning',
  'Makumbura idala Galle yanna highway bus ekak thiyeda?',
  'Kandy indan Jaffna yanna train ekak thiyeda?',
  'Kandy indan Galle yanna train ekak',
  'Kandy yanna train ekak thiyeda?',
  'Heta ude 6ta Kandy yanna dumriya ekak',
  'What are the baggage rules on SLR?',
]

/**
 * Session sidebar: connection status, purchase history, example prompts and the
 * Zero Trust note.
 * @param {{sessionId:string|null, status:string, agentStatus:object|null,
 *          purchases:Array, purchasesLoading:boolean,
 *          onExample:(q:string)=>void, onReset:()=>void, onRefreshPurchases:()=>void}} props
 */
export default function Sidebar({
  sessionId,
  status,
  agentStatus,
  purchases,
  purchasesLoading,
  onExample,
  onReset,
  onRefreshPurchases,
}) {
  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <h1>🚆 LankaJourney AI</h1>
        <p>Multi-agent public transit planning &amp; booking for Sri Lanka</p>
      </div>

      <section className="panel">
        <h2>Connection</h2>
        <div className={`status status--${status}`}>
          <span className="status__dot" />
          {status === 'online' ? 'Orchestrator online' : 'Orchestrator offline'}
        </div>
        {agentStatus?.agent && (
          <p className="muted">
            {agentStatus.agent} v{agentStatus.version}
          </p>
        )}
      </section>

      <section className="panel">
        <h2>
          🎫 Purchase history
          <button
            className="panel__refresh"
            onClick={onRefreshPurchases}
            disabled={purchasesLoading}
            title="Reload purchases"
          >
            {purchasesLoading ? '…' : '↻'}
          </button>
        </h2>

        {purchasesLoading && <p className="muted">Loading…</p>}

        {!purchasesLoading && purchases.length === 0 && (
          <p className="muted">
            No tickets purchased yet. Complete a payment and the ticket will appear here.
          </p>
        )}

        <ul className="purchases">
          {purchases.map((purchase) => (
            <li key={purchase.transaction_id ?? purchase.booking_reference} className="purchase">
              <div className="purchase__top">
                <span className="purchase__ref mono">{purchase.booking_reference}</span>
                <span className="purchase__fare">
                  LKR {Number(purchase.amount_paid_lkr ?? purchase.fare_lkr ?? 0).toLocaleString('en-LK')}
                </span>
              </div>
              <div className="purchase__meta">
                <span className="mono">{purchase.route_id}</span>
                {' · '}
                {purchase.seat_count} seat{purchase.seat_count > 1 ? 's' : ''}
                {purchase.provider ? ` · ${purchase.provider}` : ''}
              </div>
              <div className="purchase__foot">
                <span>{purchase.purchased_at?.replace('T', ' ').slice(0, 16)}</span>
                {purchase.card_last4 && <span className="mono">•••• {purchase.card_last4}</span>}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h2>Try a query</h2>
        <ul className="examples">
          {EXAMPLES.map((q) => (
            <li key={q}>
              <button className="examples__btn" onClick={() => onExample(q)}>
                {q}
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h2>Session</h2>
        <p className="muted mono">{sessionId ?? 'not started'}</p>
        <button className="btn btn--ghost" onClick={onReset}>
          New conversation
        </button>
      </section>

      <section className="panel panel--note">
        <h2>🔒 Responsible AI</h2>
        <p>
          Zero Trust is active — NIC numbers are masked before any LLM call, and a
          human-in-the-loop checkpoint gates every seat hold.
        </p>
        <p className="muted">English and Singlish queries are parsed identically.</p>
      </section>
    </aside>
  )
}