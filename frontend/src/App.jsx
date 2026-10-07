import { useCallback, useEffect, useRef, useState } from 'react'
import {
  chat,
  fetchConversation,
  fetchConversations,
  fetchPendingHolds,
  fetchPurchases,
  health,
  logout as logoutRequest,
  me,
  setDemoIncident,
  startConversation,
} from './api.js'
import { renderMarkdown } from './markdown.js'
import ChatMessage from './components/ChatMessage.jsx'
import ConversationHistory from './components/ConversationHistory.jsx'
import ConversationViewer from './components/ConversationViewer.jsx'
import PaymentPortal, { PaymentReceipt } from './components/PaymentPortal.jsx'
import Login from './components/Login.jsx'
import ProfilePanel from './components/ProfilePanel.jsx'
import RouteMap from './components/RouteMap.jsx'
import Schedules from './components/Schedules.jsx'
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
  // The signed confirmation for the booking currently on screen (R-09). Held
  // while we wait for the traveller, then returned verbatim on approval.
  const [hitlToken, setHitlToken] = useState(null)
  // The conversation in progress, and the history list beside it. A
  // conversation ends when its payment completes, and becomes read-only.
  const [conversationId, setConversationId] = useState(null)
  const [conversations, setConversations] = useState([])
  // A past conversation opened from the history list, shown read-only.
  const [viewing, setViewing] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [status, setStatus] = useState('checking')
  const [agentStatus, setAgentStatus] = useState(null)
  // Payment step: a held seat awaiting settlement, and the last receipt.
  const [paymentHold, setPaymentHold] = useState(null)
  const [receipt, setReceipt] = useState(null)
  const [purchases, setPurchases] = useState([])
  const [pendingHolds, setPendingHolds] = useState([])
  // Demo control for the live-conditions advisory (see Sidebar).
  const [demoIncidentActive, setDemoIncidentActive] = useState(false)
  const [demoBusy, setDemoBusy] = useState(false)
  const [purchasesLoading, setPurchasesLoading] = useState(false)
  // Who is signed in, and which main view is open. The API is authenticated, so
  // until there is a session there is nothing to show but the login screen —
  // rendering the chat first and locking it afterwards would flash a stranger's
  // (or rather, an empty) shell at someone on a shared machine.
  const [user, setUser] = useState(undefined) // undefined = still checking
  const [view, setView] = useState('chat')
  const [profileOpen, setProfileOpen] = useState(false)

  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    me().then(setUser).catch(() => setUser(null))
  }, [])

  // Health is public, so it is checked before sign-in to show an honest
  // "offline" rather than a login form that can never work.
  useEffect(() => {
    health().then((h) => {
      setStatus(h.status === 'ok' ? 'online' : 'offline')
      setAgentStatus(h)
    })
  }, [])

  useEffect(() => {
    if (!user) return
    loadPurchases()
    refreshConversations()
    beginConversation()
  }, [user])

  async function handleSignOut() {
    await logoutRequest().catch(() => {})
    setUser(null)
    setProfileOpen(false)
    // Anything on screen belongs to the session that just ended.
    setMessages([])
    setConversations([])
    setConversationId(null)
    setPurchases([])
    setPendingHolds([])
    setViewing(null)
    setView('chat')
  }

  /**
   * Opens a new conversation and clears the view.
   *
   * Called on load and after a payment: the finished trip goes to history and
   * the next one starts with no memory of it, which is the product rule.
   */
  async function beginConversation() {
    const created = await startConversation()
    if (created) {
      setConversationId(created.conversation_id)
      setSessionId(created.session_id)
      // The greeting comes from the server so it is the same message that is
      // stored in the transcript, rather than a second copy living in the UI.
      setMessages(
        created.greeting ? [{ role: 'agent', text: created.greeting }] : [],
      )
    } else {
      setMessages([])
    }
    setPendingRoute(null)
    setHitlToken(null)
    setPaymentHold(null)
    setViewing(null)
  }

  /** Reloads the sidebar history list. */
  async function refreshConversations() {
    setConversations(await fetchConversations())
  }

  /** Opens a past conversation as read-only. */
  async function openConversation(id) {
    const data = await fetchConversation(id)
    if (!data) return
    setViewing(data.conversation)
  }

  /** Reloads the sidebar purchase history from the booking agent. */
  async function loadPurchases() {
    setPurchasesLoading(true)
    const [settled, waiting] = await Promise.all([fetchPurchases(), fetchPendingHolds()])
    setPurchases(settled)
    setPendingHolds(waiting)
    setPurchasesLoading(false)
  }

  /**
   * Toggles the simulated transit incident.
   *
   * The incident is invented and pushed into the Conditions Agent, which feeds
   * it through the same classification and planner logic as a live headline — so
   * the demo shows the real advisory path, and the agent's own reply stays
   * labelled as simulated.
   */
  async function handleToggleDemoIncident() {
    setDemoBusy(true)
    const next = !demoIncidentActive
    const result = await setDemoIncident({
      incidentId: next ? 'negombo_highway_accident' : null,
      active: next,
    })
    setDemoIncidentActive(result.ok && (result.incident_check_armed ?? false) === true)
    setDemoBusy(false)
    // Deliberately no query is sent here. The switch arms the incident check for
    // this conversation; the agent consults it the next time it gives routes, so
    // the traveller keeps driving the conversation rather than the button
    // hijacking it with a message they did not write.
  }

  /**
   * Reopens the payment portal for a booking that is still awaiting payment.
   *
   * Reached from the history card, so it covers the case where the traveller
   * closed the portal or reloaded the page mid-checkout and the hold is about
   * to expire.
   */
  function handleResumePayment(hold) {
    setReceipt(null)
    setPaymentHold({
      transactionId: hold.transaction_id,
      amountLkr: hold.amount_due_lkr ?? hold.fare_lkr,
      seatCount: hold.seat_count,
      routeId: hold.route_id,
      provider: hold.provider,
    })
  }

  /** POST a turn and append the agent's reply. */
  const send = useCallback(
    async (text, options = {}) => {
      const trimmed = text.trim()
      // History is read-only: the server refuses these turns too, but not
      // making the request is clearer than surfacing a 409.
      if (!trimmed || busy || viewing) return

      setBusy(true)
      setError(null)
      setMessages((prev) => [...prev, { role: 'user', text: trimmed }])
      setInput('')

      try {
        const data = await chat({
          query: trimmed,
          sessionId,
          selectedRouteId: options.selectedRouteId ?? null,
          hitlToken: options.hitlToken ?? null,
          conversationId,
        })

        if (data.session_id) setSessionId(data.session_id)
        if (data.conversation_id) setConversationId(data.conversation_id)
        refreshConversations()

        // The gate is presented with a token attached. Hold on to it so the
        // approve turn can hand it back — that return *is* the approval.
        if (data.hitl_token) setHitlToken(data.hitl_token)
        if (data.booking_status === 'CONFIRMED') setHitlToken(null)

        // A held seat with an amount due opens the payment portal instead of
        // silently completing the booking.
        if (data.booking_status === 'AWAITING_PAYMENT' && data.transaction_id) {
          setPaymentHold({
            transactionId: data.transaction_id,
            amountLkr: data.amount_lkr,
            seatCount: data.seat_count,
            routeId: options.selectedRouteId,
          })
        }

        setMessages((prev) => [
          ...prev,
          {
            role: 'agent',
            text: data.response,
            intent: data.intent,
            routes: data.route_options ?? [],
bookingReference: data.booking_reference,
            bookingStatus: data.booking_status,
            clarification: data.clarification,
            onQuickReply: handleQuickReply,
            onSelectRoute: handleSelectRoute,
          },
        ])
      } catch (err) {
        // 409 means this conversation is finished history. Start a new one so the
        // traveller can plan another trip instead of hitting a dead composer.
        if (err.status === 409) {
          await beginConversation()
          setError(null)
        } else {
          setError(err.message)
          setMessages((prev) => [
            ...prev,
            {
              role: 'agent',
              text: `⚠️ Could not reach the orchestrator: ${err.message}`,
            },
          ])
        }
      } finally {
        setBusy(false)
      }
    },
    [busy, sessionId, conversationId, viewing],
  )

  /** Step 1 of booking: the user picks a route, which reaches the HITL checkpoint. */
  function handleSelectRoute(route) {
    setPendingRoute(route)
    send(`Book route ${route.route_id}`, { selectedRouteId: route.route_id })
  }

  /** Step 2 of booking: the user explicitly approves, clearing the HITL gate. */
  function handleApprove() {
    if (!pendingRoute) return
    send(`YES confirm ${pendingRoute.route_id}`, {
      selectedRouteId: pendingRoute.route_id,
      hitlToken,
    })
    setPendingRoute(null)
  }

  /** Payment settled: show the e-ticket and refresh the purchase history. */
  function handlePaid(result) {
    setReceipt(result)
    setPaymentHold(null)
    loadPurchases()
    refreshConversations()
    // The trip is done: its conversation becomes read-only history, and the next
    // booking starts from a clean slate.
    beginConversation()
  }

  /** A one-tap answer to the planner's question, e.g. "bus". */
  function handleQuickReply(value) {
    const last = [...messages].reverse().find((m) => m.role === 'user')
    const prior = last?.text ?? ''
    // Extend the traveller's own message rather than replacing it, so the planner
    // keeps the origin, destination and time already given.
    send(`${prior} ${value}`.trim())
  }

  /** "New chat": abandons the current conversation and starts a clean one. */
  function handleReset() {
    setError(null)
    refreshConversations()
    beginConversation()
  }

  function handleSubmit(event) {
    event.preventDefault()
    send(input)
  }

  function handleExample(query) {
    send(query)
  }

  if (user === undefined) {
    return (
      <div className="login-page">
        <div className="login-card">
          <p className="muted">Checking your session…</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return <Login onSignedIn={setUser} />
  }

  return (
    <div className="layout">
      <Sidebar
        sessionId={sessionId}
        user={user}
        status={status}
        agentStatus={agentStatus}
        purchases={purchases}
        pendingHolds={pendingHolds}
        purchasesLoading={purchasesLoading}
        demoIncidentActive={demoIncidentActive}
        demoBusy={demoBusy}
        onToggleDemoIncident={handleToggleDemoIncident}
        onExample={handleExample}
        onReset={handleReset}
        onRefreshPurchases={loadPurchases}
        onResumePayment={handleResumePayment}
        conversations={conversations}
        viewingId={viewing?.id ?? null}
        onOpenConversation={openConversation}
        onNewConversation={handleReset}
        view={view}
        onChangeView={setView}
        onOpenProfile={() => setProfileOpen(true)}
        onSignOut={handleSignOut}
      />

      <main className="chat">
        {viewing ? (
          <ConversationViewer conversation={viewing} onClose={() => setViewing(null)} />
        ) : (
          <>
        <header className="chat__header">
          <h2>
            {view === 'chat' && 'Chat'}
            {view === 'schedules' && 'Timetables'}
            {view === 'map' && 'Route map'}
          </h2>
          {status !== 'online' && (
            <span className="status status--offline">Reconnecting…</span>
          )}
        </header>

        {view === 'schedules' && <Schedules />}
        {view === 'map' && <RouteMap />}

        {view === 'chat' && (
        <>
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

        {paymentHold && (
          <PaymentPortal
            hold={paymentHold}
            onPaid={handlePaid}
            onCancel={() => setPaymentHold(null)}
          />
        )}

        {receipt && !paymentHold && (
          <div className="receipt-bar">
            <PaymentReceipt result={receipt} />
            <button className="btn btn--ghost" onClick={() => setReceipt(null)}>
              Dismiss
            </button>
          </div>
        )}

        {pendingRoute && (
          <div className="hitl">
<span>
              Human-in-the-Loop: approve the seat hold for{' '}
              <code>{pendingRoute.route_id}</code>
              {pendingRoute.base_fare_lkr ? (
                <> — LKR {Number(pendingRoute.base_fare_lkr).toLocaleString('en-LK')}</>
              ) : null}
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
          </>
        )}
        </>
        )}
      </main>

      {profileOpen && (
        <ProfilePanel
          user={user}
          onClose={() => setProfileOpen(false)}
          onSaved={setUser}
          onSignOut={handleSignOut}
        />
      )}
    </div>
  )
}