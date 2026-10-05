/**
 * Thin API client for the Orchestration Agent.
 *
 * Requests go to /api/* which nginx (prod) or Vite (dev) reverse-proxies to the
 * orchestrator, so no CORS configuration is required in the browser.
 */

const BASE = '/api'

/**
 * Send a chat turn to the orchestrator.
 * @param {object} opts
 * @param {string} opts.query                 user message text
 * @param {string|null} [opts.sessionId]      conversation id to continue
 * @param {string|null} [opts.selectedRouteId] route the user picked from the results
 * @param {boolean} [opts.hitlApproved]       user cleared the human-in-the-loop gate
 */
export async function chat({
  query,
  sessionId = null,
  selectedRouteId = null,
  hitlApproved = false,
}) {
  const res = await fetch(`${BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      session_id: sessionId,
      selected_route_id: selectedRouteId,
      hitl_approved: hitlApproved,
    }),
  })

  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }

  return res.json()
}

/** Liveness probe for the orchestrator. */
export async function health() {
  try {
    const res = await fetch(`${BASE}/health`)
    if (!res.ok) return { status: 'offline' }
    return await res.json()
  } catch {
    return { status: 'offline' }
  }
}

/**
 * Settle a held seat.
 * Only the card's last four digits are sent — no PAN or CVV ever reaches an agent.
 */
export async function pay({ transactionId, cardLast4, provider = 'SLR' }) {
  const res = await fetch(`${BASE}/payment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      transaction_id: transactionId,
      card_last4: cardLast4,
      provider,
    }),
  })

  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(body?.detail || `Payment failed (HTTP ${res.status})`)
  }
  return body
}

/** Completed ticket purchases, newest first. */
export async function fetchPurchases() {
  try {
    const res = await fetch(`${BASE}/purchases`)
    if (!res.ok) return []
    const body = await res.json()
    return body.purchases ?? []
  } catch {
    return []
  }
}

/**
 * Bookings still awaiting payment.
 *
 * Lets the history panel keep an interrupted checkout alive: the seat hold only
 * lasts 10 minutes, so without this a closed payment portal means losing the
 * seat with no way back.
 */
export async function fetchPendingHolds() {
  try {
    const res = await fetch(`${BASE}/pending_holds`)
    if (!res.ok) return []
    const body = await res.json()
    return body.pending_holds ?? []
  } catch {
    return []
  }
}