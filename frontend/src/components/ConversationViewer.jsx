import { renderMarkdown } from '../markdown.js'

/**
 * A past conversation, read-only.
 *
 * Shown instead of the composer so the traveller reads their transcript without
 * any affordance for typing into it. The server refuses turns on a finished
 * conversation regardless, so this is the honest presentation of that rule
 * rather than the enforcement of it.
 */
export default function ConversationViewer({ conversation, onClose }) {
  if (!conversation) return null
  const messages = conversation.messages ?? []

  return (
    <div className="viewer">
      <div className="viewer__head">
        <div>
          <h2>{conversation.title || 'Conversation'}</h2>
          <p className="muted">
            {String(conversation.created_at ?? '').replace('T', ' ').slice(0, 16)}
            {conversation.booking_reference
              ? ` · booking ${conversation.booking_reference}`
              : ''}
          </p>
        </div>
        <button className="btn btn--ghost" onClick={onClose}>
          ✕ Back to chat
        </button>
      </div>

      <p className="viewer__notice">
        🔒 This conversation ended when the payment completed. You can read it
        here, but it can no longer be continued — start a new chat to plan
        another journey.
      </p>

      <div className="viewer__log">
        {messages.length === 0 && (
          <p className="muted">This conversation has no messages.</p>
        )}
        {messages.map((message) => (
          <div key={message.id} className={`msg msg--${message.role === 'user' ? 'user' : 'agent'}`}>
            <div className="msg__avatar" aria-hidden="true">
              {message.role === 'user' ? '🧑' : '🚆'}
            </div>
            <div
              className="msg__text"
              // Safe: renderMarkdown escapes all HTML before applying its own tags.
              dangerouslySetInnerHTML={{ __html: renderMarkdown(message.text) }}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
