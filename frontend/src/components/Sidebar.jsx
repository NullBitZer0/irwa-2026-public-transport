import { useState } from 'react'
import { health } from '../api.js'
import ConversationHistory from './ConversationHistory.jsx'

/**
 * Renders an operator's contact block, shown when a ticket card is expanded.
 *
 * The website is rendered as a link, so the scheme is checked rather than
 * trusted: a `javascript:` or `data:` URL here would be a script injection
 * vector the moment any of this data became caller-influenced.
 */
function OperatorContact({ contact }) {
  if (!contact?.name) return null

  const site = safeHttpUrl(contact.website)

  return (
    <div className="contact">
      <dl>
        <div>
          <dt>Operator</dt>
          <dd>{contact.name}</dd>
        </div>
        {contact.customer_care && (
          <div>
            <dt>Customer care</dt>
            <dd className="mono">
              {contact.customer_care}
              {/* These numbers are placeholders, not the operator's real line.
                  Labelled so nobody dials one expecting a real desk. */}
              {contact.demo_contact && (
                <span className="contact__demo" title="Not a real number">
                  {' '}
                  demo
                </span>
              )}
            </dd>
          </div>
        )}
        {site && (
          <div>
            <dt>Website</dt>
            <dd>
              <a href={site} target="_blank" rel="noreferrer noopener">
                {contact.website.replace(/^https?:\/\//, '')}
              </a>
            </dd>
          </div>
        )}
        {contact.notes && (
          <div>
            <dt>Note</dt>
            <dd className="muted">{contact.notes}</dd>
          </div>
        )}
      </dl>
    </div>
  )
}

/** Returns the URL only if it is plain http(s); anything else becomes null. */
function safeHttpUrl(value) {
  if (typeof value !== 'string' || !value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null
  } catch {
    return null
  }
}

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
 *
 * The history panel has two kinds of card:
 *  - a settled ticket, which expands to show the operator's contact details;
 *  - a booking awaiting payment, which reopens the payment portal, because a
 *    seat hold only lives 10 minutes and would otherwise be lost silently.
 * @param {{sessionId:string|null, status:string, agentStatus:object|null,
 *          purchases:Array, pendingHolds:Array, purchasesLoading:boolean,
 *          demoIncidentActive:boolean, demoBusy:boolean,
 *          onExample:(q:string)=>void, onReset:()=>void,
 *          onRefreshPurchases:()=>void, onResumePayment:(hold:object)=>void,
 *          onToggleDemoIncident:()=>void,
 *          conversations:Array, viewingId:string|null,
 *          onOpenConversation:(id:string)=>void, onNewConversation:()=>void}} props
 */
export default function Sidebar({
  sessionId,
  status,
  agentStatus,
  purchases,
  pendingHolds,
  purchasesLoading,
  demoIncidentActive,
  demoBusy,
  onExample,
  onReset,
  onRefreshPurchases,
  onResumePayment,
  onToggleDemoIncident,
  conversations,
  viewingId,
  onOpenConversation,
  onNewConversation,
  user,
  view,
  onChangeView,
  onOpenProfile,
  onSignOut,
}) {
  const [openContact, setOpenContact] = useState(null)

  // Only one card expands at a time; the sidebar is narrow enough that stacking
  // contact blocks just pushes the rest of the panel off screen.
  function toggleContact(reference) {
    setOpenContact((current) => (current === reference ? null : reference))
  }

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <h1>🚆 LankaJourney AI</h1>
        <p>Multi-agent public transit planning &amp; booking for Sri Lanka</p>
      </div>

      <nav className="sidebar__nav" aria-label="Sections">
        {[
          { id: 'chat', label: '💬 Chat' },
          { id: 'schedules', label: '🗓 Timetables' },
          { id: 'map', label: '🗺 Route map' },
        ].map((item) => (
          <button
            key={item.id}
            type="button"
            className={view === item.id ? 'active' : ''}
            aria-current={view === item.id ? 'page' : undefined}
            onClick={() => onChangeView(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <button type="button" className="profile-chip" onClick={onOpenProfile}>
        <span className="profile-chip__avatar" aria-hidden="true">
          {(user?.full_name || user?.email || '?')
            .split(/[\s@.]+/)
            .filter(Boolean)
            .slice(0, 2)
            .map((part) => part[0].toUpperCase())
            .join('')}
        </span>
        <span className="profile-chip__text">
          <strong>{user?.full_name || 'Your profile'}</strong>
          <small>
            {user?.has_card
              ? `${user.card_brand} •••• ${user.card_last4}`
              : user?.email}
          </small>
        </span>
        <span className="profile-chip__chevron" aria-hidden="true">›</span>
      </button>

      {/* Always available, not only for a named account: this used to render
          only when a full name was set, so a traveller who had not filled one in
          — including the demo account — had no way to sign out from the
          sidebar at all. */}
      <button type="button" className="sidebar__signout" onClick={onSignOut}>
        Sign out
      </button>

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
          {pendingHolds.map((hold) => (
            <li key={hold.transaction_id} className="purchase purchase--pending">
              <button
                type="button"
                className="purchase__btn"
                onClick={() => onResumePayment(hold)}
                title="Complete payment for this booking"
              >
                <div className="purchase__top">
                  <span className="purchase__ref mono">{hold.transaction_id}</span>
                  <span className="purchase__fare">
                    LKR {Number(hold.amount_due_lkr ?? hold.fare_lkr ?? 0).toLocaleString('en-LK')}
                  </span>
                </div>
                <div className="purchase__meta">
                  <span className="mono">{hold.route_id}</span>
                  {' · '}
                  {hold.seat_count} seat{hold.seat_count > 1 ? 's' : ''}
                  {hold.provider ? ` · ${hold.provider}` : ''}
                </div>
                <div className="purchase__foot">
                  <span className="badge badge--pending">⏳ Awaiting payment</span>
                  {hold.hold_expires_at && (
                    <span className="muted">
                      expires {hold.hold_expires_at.replace('T', ' ').slice(11, 16)}
                    </span>
                  )}
                </div>
                <div className="purchase__cta">Complete payment →</div>
              </button>
            </li>
          ))}

          {purchases.map((purchase) => (
            <li
              key={purchase.transaction_id ?? purchase.booking_reference}
              className="purchase"
            >
              <button
                type="button"
                className="purchase__btn"
                onClick={() => toggleContact(purchase.booking_reference)}
                aria-expanded={openContact === purchase.booking_reference}
                title="Show operator contact details"
              >
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
                {purchase.provider_contact?.name && (
                  <div className="purchase__cta">
                    {openContact === purchase.booking_reference
                      ? 'Hide contact ▲'
                      : 'Operator contact ▼'}
                  </div>
                )}
              </button>

              {openContact === purchase.booking_reference && (
                <OperatorContact contact={purchase.provider_contact} />
              )}
            </li>
          ))}
        </ul>

        {purchases.length === 0 && pendingHolds.length === 0 && (
          <p className="muted">
            No tickets yet. Complete a payment and the ticket will appear here.
          </p>
        )}
      </section>

      <section className="panel">
        <h2>
          🧪 Demo: incident alert
          <button
            className="panel__refresh"
            onClick={onToggleDemoIncident}
            disabled={demoBusy}
            title={
              demoIncidentActive
                ? 'Clear the simulated incident'
                : 'Simulate an accident at the Negombo highway entrance'
            }
          >
            {demoBusy ? '…' : demoIncidentActive ? '⏹ Clear' : '▶ Simulate'}
          </button>
        </h2>
        <p className="muted">
          {demoIncidentActive
            ? 'A simulated accident at the Negombo highway entrance is active. Ask for a '
              + 'bus route and the agent will warn you and suggest the next entrance.'
            : 'Push a simulated accident into the live-conditions agent to demonstrate '
              + 'the advisory and alternative-route logic.'}
        </p>
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

      <ConversationHistory
        conversations={conversations}
        onOpen={onOpenConversation}
        onNew={onNewConversation}
        viewingId={viewingId}
      />

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