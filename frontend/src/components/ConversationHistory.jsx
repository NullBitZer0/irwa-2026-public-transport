import { useState } from 'react'

/**
 * Operator contact block, shown when a ticket card is expanded.
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

/**
 * Past conversations, newest first.
 *
 * A finished conversation is history: you can open it and read it, but not
 * continue it. That rule is enforced by the server as well — this is the
 * affordance, not the control.
 */
function ConversationHistory({ conversations, onOpen, onNew, viewingId }) {
  const archived = conversations.filter((c) => c.status === 'archived')
  const active = conversations.filter((c) => c.status === 'active')

  if (conversations.length === 0 && !active.length) {
    return (
      <section className="panel">
        <h2>💬 Chat history</h2>
        <p className="muted">
          Finished conversations appear here once you complete a payment.
        </p>
      </section>
    )
  }

  return (
    <section className="panel">
      <h2>
        💬 Chat history
        <button
          className="panel__refresh"
          onClick={onNew}
          title="Start a new conversation"
        >
          ＋ New
        </button>
      </h2>

      {archived.length === 0 ? (
        <p className="muted">No finished conversations yet.</p>
      ) : (
        <ul className="history">
          {archived.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                className={`history__item${
                  viewingId === conversation.id ? ' history__item--open' : ''
                }`}
                onClick={() => onOpen(conversation.id)}
                title="View this conversation"
              >
                <span className="history__title">
                  {conversation.title || 'Conversation'}
                </span>
                <span className="history__meta">
                  {conversation.booking_reference
                    ? `${conversation.booking_reference} · `
                    : ''}
                  {String(conversation.created_at ?? '').replace('T', ' ').slice(0, 16)}
                </span>
                <span className="history__cta">
                  {conversation.message_count} messages · view
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

export default ConversationHistory
