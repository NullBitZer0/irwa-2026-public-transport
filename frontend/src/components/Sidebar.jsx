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
 * Session sidebar: connection status, example prompts and the Zero Trust note.
 * @param {{sessionId:string|null, status:string, agentStatus:object|null,
 *          onExample:(q:string)=>void, onReset:()=>void}} props
 */
export default function Sidebar({ sessionId, status, agentStatus, onExample, onReset }) {
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