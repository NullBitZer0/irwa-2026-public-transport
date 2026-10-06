/**
 * Thin API client for the Orchestration Agent.
 *
 * Requests go to /api/* which nginx (prod) or Vite (dev) reverse-proxies to the
 * orchestrator, so no CORS configuration is required in the browser.
 */

const BASE = '/api'

/**
 * The session lives in an HttpOnly cookie set by the server, so every call has
 * to carry it. Without `credentials` the cookie is dropped and every request
 * comes back 401 — which looks exactly like the backend being broken.
 */
async function request(path, { method = 'GET', body } = {}) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    method,
    // Same-origin in production, but the dev server runs on a different port to
    // the API, so this is not a no-op.
    credentials: 'include',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })

  let payload = null
  try {
    payload = await res.json()
  } catch {
    /* empty or non-JSON body */
  }

  if (!res.ok) {
    const error = new Error(payload?.detail || `HTTP ${res.status}`)
    error.status = res.status
    throw error
  }
  return payload
}

// ── Auth ──────────────────────────────────────────────────────────────────────

/** The signed-in traveller, or null. Used to decide login vs app on load. */
export async function me() {
  try {
    const data = await request('/auth/me')
    return data.user
  } catch (error) {
    if (error.status === 401) return null
    throw error
  }
}

export function login(email, password) {
  return request('/auth/login', { method: 'POST', body: { email, password } })
}

export function register(email, password) {
  return request('/auth/register', { method: 'POST', body: { email, password } })
}

export function logout() {
  return request('/auth/logout', { method: 'POST' })
}

/**
 * Saves the profile. `cardNumber` is sent once and never kept in the browser.
 *
 * The server validates it and keeps four digits; see src/orchestrator/accounts.py
 * for why the full number is not stored.
 */
export function saveProfile({ fullName, contactNumber, cardNumber }) {
  return request('/profile', {
    method: 'PATCH',
    body: {
      full_name: fullName,
      contact_number: contactNumber,
      card_number: cardNumber || undefined,
    },
  })
}

// ── Schedules and map ─────────────────────────────────────────────────────────

export function getSchedules({ mode = 'ALL', origin = '', destination = '', limit = 200 } = {}) {
  const params = new URLSearchParams({ mode, origin, destination, limit: String(limit) })
  return request(`/schedules?${params}`)
}

export function getMapRoutes({ mode = 'ALL' } = {}) {
  return request(`/map-routes?mode=${encodeURIComponent(mode)}`)
}

/**
 * Send a chat turn to the orchestrator.
 * @param {object} opts
 * @param {string} opts.query                 user message text
 * @param {string|null} [opts.sessionId]      conversation id to continue
 * @param {string|null} [opts.selectedRouteId] route the user picked from the results
 * @param {string|null} [opts.hitlToken]       signed confirmation returned by the
 *   traveller's approval (R-09). There is no boolean flag: the client cannot
 *   assert its own approval, it can only hand back what the server issued.
 */
export async function chat({
  query,
  sessionId = null,
  selectedRouteId = null,
  hitlToken = null,
}) {
  const res = await fetch(`${BASE}/chat`, {
    credentials: 'include',
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      session_id: sessionId,
      selected_route_id: selectedRouteId,
      hitl_token: hitlToken,
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
    // The status is carried on the error so callers can react to it: a 409 means
    // "this conversation is finished", which is recoverable, unlike a 500.
    const error = new Error(detail)
    error.status = res.status
    throw error
  }

  return res.json()
}

/** Liveness probe for the orchestrator. */
export async function health() {
  try {
    const res = await fetch(`${BASE}/health`, { credentials: 'include' })
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
    credentials: 'include',
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
    const res = await fetch(`${BASE}/purchases`, { credentials: 'include' })
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
    const res = await fetch(`${BASE}/pending_holds`, { credentials: 'include' })
    if (!res.ok) return []
    const body = await res.json()
    return body.pending_holds ?? []
  } catch {
    return []
  }
}

/**
 * Demo control: switch a simulated transit incident on or off.
 *
 * The incident is invented by us and fed through the same pipeline as a live
 * news headline, so the advisory and the alternative-route logic can be shown
 * without waiting for a real accident. It is always labelled as simulated.
 *
 * @param {object} opts
 * @param {string|null} [opts.incidentId] omit with active:false to clear all
 * @param {boolean} [opts.active]
 */
export async function setDemoIncident({ incidentId = null, active = true } = {}) {
  try {
    const res = await fetch(`${BASE}/demo_incident`, {
    credentials: 'include',
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ incident_id: incidentId, active }),
    })
    if (!res.ok) return { active: [], ok: false }
    return { ...(await res.json()), ok: true }
  } catch {
    return { active: [], ok: false }
  }
}

/**
 * Opens a new conversation.
 *
 * Called on load and again after a payment completes: the finished trip becomes
 * history, and the next one starts with no memory of it.
 */
export async function startConversation() {
  try {
    const res = await fetch(`${BASE}/conversations`, {
      method: 'POST',
      credentials: 'include',
    })
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

/**
 * Conversation summaries for the sidebar, newest first.
 * @param {string|null} [status] 'active' filters to the in-progress one
 */
export async function fetchConversations(status = null) {
  try {
    const query = status ? `?status=${encodeURIComponent(status)}` : ''
    const res = await fetch(`${BASE}/conversations${query}`, { credentials: 'include' })
    if (!res.ok) return []
    return (await res.json()).conversations ?? []
  } catch {
    return []
  }
}

/**
 * One conversation with its transcript. Read-only once it has been archived —
 * the server refuses new turns, and this only ever reads.
 */
export async function fetchConversation(conversationId) {
  try {
    const res = await fetch(`${BASE}/conversations/${encodeURIComponent(conversationId)}`, {
      credentials: 'include',
    })
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}
