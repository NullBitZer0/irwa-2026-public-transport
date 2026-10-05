import { useCallback, useEffect, useRef, useState } from 'react'
import { chat, fetchPendingHolds, fetchPurchases, health } from './api.js'
import { renderMarkdown } from './markdown.js'
import ChatMessage from './components/ChatMessage.jsx'
import PaymentPortal, { PaymentReceipt } from './components/PaymentPortal.jsx'
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
  // Payment step: a held seat awaiting settlement, and the last receipt.
  const [paymentHold, setPaymentHold] = useState(null)
  const [receipt, setReceipt] = useState(null)
  const [purchases, setPurchases] = useState([])
  const [pendingHolds, setPendingHolds] = useState([])
  const [purchasesLoading, setPurchasesLoading] = useState(false)

  const endRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    health().then((h) => {
      setStatus(h.status === 'ok' ? 'online' : 'offline')
      setAgentStatus(h)
    })
    loadPurchases()
  }, [])

  /** Reloads the sidebar purchase history from the booking agent. */
  async function loadPurchases() {
    setPurchasesLoading(true)
    const [settled, waiting] = await Promise.all([fetchPurchases(), fetchPendingHolds()])
    setPurchases(settled)
    setPendingHolds(waiting)
    setPurchasesLoading(false)
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
      hitlApproved: true,
    })
    setPendingRoute(null)
  }

  /** Payment settled: show the e-ticket and refresh the purchase history. */
  function handlePaid(result) {
    setReceipt(result)
    setPaymentHold(null)
    loadPurchases()
  }

  /** A one-tap answer to the planner's question, e.g. "bus". */
  function handleQuickReply(value) {
    const last = [...messages].reverse().find((m) => m.role === 'user')
    const prior = last?.text ?? ''
    // Extend the traveller's own message rather than replacing it, so the planner
    // keeps the origin, destination and time already given.
    send(`${prior} ${value}`.trim())
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
        purchases={purchases}
        purchasesLoading={purchasesLoading}
        onExample={handleExample}
        onReset={handleReset}
        onRefreshPurchases={loadPurchases}
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
      </main>
    </div>
  )
}