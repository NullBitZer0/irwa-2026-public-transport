import { useCallback, useEffect, useRef, useState } from 'react'
import { chat, health } from './api.js'
import { renderMarkdown } from './markdown.js'
import ChatMessage from './components/ChatMessage.jsx'
import Sidebar from './components/Sidebar.jsx'

const GREETING = `Welcome to **LankaJourney AI** 🚆

Ask me to plan a journey in **English or Singlish**. Try:

- *"Heta ude Colombo indan Kandy yanna train ekak balanna"*
- *"Find a train from Colombo Fort to Kandy tomorrow morning"*
- *"What are the baggage rules on SLR?"*`

export default function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sessionId, setSessionId] = useState(null)
  const [pendingRoute, setPendingRoute] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [status, setStatus] = useState('checking')
  const [agentStatus, setAgentStatus] = useState(null)

  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    health().then((h) => {
      setStatus(h.status === 'ok' ? 'online' : 'offline')
      setAgentStatus(h)
    })
  }, [])

  /** POST a turn and append the agent's reply. */
  const send = useCallback(
    async (text, options = {}) => {
      const trimmed = text.trim()
      if (!trimmed || busy) return

      setBusy(true)
      setError(null)
      setMessages((prev) => [...prev, { role: 'user', text: trimmed }])
      setInput('')

      try {
        const data = await chat({
          query: trimmed,
          sessionId,
          selectedRouteId: options.selectedRouteId ?? null,
          hitlApproved: options.hitlApproved ?? false,
        })

        if (data.session_id) setSessionId(data.session_id)

        setMessages((prev) => [
          ...prev,
          {
            role: 'agent',
            text: data.response,
            intent: data.intent,
            routes: data.route_options ?? [],
bookingReference: data.booking_reference,
              bookingStatus: data.booking_status,
              onSelectRoute: handleSelectRoute,
          },
        ])
      } catch (err) {
        setError(err.message)
        setMessages((prev) => [
          ...prev,
          { role: 'agent', text: `⚠️ Could not reach the orchestrator: ${err.message}` },
        ])
      } finally {
        setBusy(false)
      }
    },
    [busy, sessionId],
  )

  /**
   * Step 1 of booking: the user picks a route, which reaches the HITL checkpoint.
   * @param {string} routeId
   */
  function handleSelectRoute(routeId) {
    setPendingRoute(routeId)
    send(`Book route ${routeId}`, { selectedRouteId: routeId })
  }

  /** Step 2 of booking: the user explicitly approves, clearing the HITL gate. */
  function handleApprove() {
    if (!pendingRoute) return
    send(`YES confirm ${pendingRoute}`, {
      selectedRouteId: pendingRoute,
      hitlApproved: true,
    })
    setPendingRoute(null)
  }

  function handleReset() {
    setMessages([])
    setSessionId(null)
    setPendingRoute(null)
    setError(null)
  }

  function handleSubmit(event) {
    event.preventDefault()
    send(input)
  }

  function handleExample(query) {
    send(query)
  }

  return (
    <div className="layout">
      <Sidebar
        sessionId={sessionId}
        status={status}
        agentStatus={agentStatus}
        onExample={handleExample}
        onReset={handleReset}
      />

      <main className="chat">
        <header className="chat__header">
          <h2>Chat</h2>
          {status !== 'online' && (
            <span className="status status--offline">Reconnecting…</span>
          )}
        </header>

        <div className="chat__log">
          {messages.length === 0 ? (
            <div className="msg msg--agent">
              <div className="msg__avatar" aria-hidden="true">🚆</div>
              <div
                className="msg__text"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(GREETING) }}
              />
            </div>
          ) : (
            messages.map((message, index) => (
              <ChatMessage key={index} message={message} />
            ))
          )}

          {busy && (
            <div className="msg msg--agent">
              <div className="msg__avatar" aria-hidden="true">🚆</div>
              <div className="msg__text muted">Thinking…</div>
            </div>
          )}

          {error && <div className="alert">Error: {error}</div>}
          <div ref={endRef} />
        </div>

        {pendingRoute && (
          <div className="hitl">
            <span>
              Human-in-the-Loop: approve the seat hold for{' '}
              <code>{pendingRoute}</code>?
            </span>
            <button className="btn btn--primary" onClick={handleApprove} disabled={busy}>
              ✅ Confirm &amp; Hold Seat
            </button>
          </div>
        )}

        <form className="composer" onSubmit={handleSubmit}>
          <input
            className="composer__input"
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="e.g. Heta ude Colombo indan Kandy yanna train ekak"
            disabled={busy}
            aria-label="Message"
          />
          <button className="btn btn--primary" type="submit" disabled={busy || !input.trim()}>
            Send
          </button>
        </form>
      </main>
    </div>
  )
}