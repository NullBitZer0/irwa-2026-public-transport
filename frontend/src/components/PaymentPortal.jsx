import { useState } from 'react'
import { pay } from '../api.js'

/**
 * Payment portal — shown once a seat is held and the traveller has approved.
 *
 * Card handling follows the tokenized design used by the Booking Agent: only the
 * last four digits are ever sent. A real deployment would swap the inputs for the
 * payment provider's hosted field, so no PAN or CVV transits this system at all.
 */
export default function PaymentPortal({ hold, onPaid, onCancel }) {
  const [cardLast4, setCardLast4] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const valid = /^\d{4}$/.test(cardLast4)

  async function handleSubmit(event) {
    event.preventDefault()
    if (!valid || busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await pay({
        transactionId: hold.transactionId,
        transactionIds: hold.transactionIds,
        cardLast4,
        provider: hold.provider ?? 'SLR',
      })
      onPaid(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pay" role="dialog" aria-label="Complete payment">
      <form className="pay__card" onSubmit={handleSubmit}>
        <h3 className="pay__title">💳 Secure payment</h3>

        <dl className="pay__summary">
          {(hold.transactionIds?.length ?? 0) > 1 ? (
            <>
              {/* Two tickets, listed as two. Collapsing them into one line
                  would hide that the traveller is buying two seats on two
                  different services. */}
              <div>
                <dt>Tickets</dt>
                <dd className="mono">{hold.transactionIds.length} (one per leg)</dd>
              </div>
              {hold.transactionIds.map((id) => (
                <div key={id}>
                  <dt>Transaction</dt>
                  <dd className="mono">{id}</dd>
                </div>
              ))}
            </>
          ) : (
            <div>
              <dt>Transaction</dt>
              <dd className="mono">{hold.transactionId}</dd>
            </div>
          )}
          {hold.routeId && (
            <div>
              <dt>Route</dt>
              <dd className="mono">{hold.routeId}</dd>
            </div>
          )}
          <div>
            <dt>Seats</dt>
            <dd>{(hold.seatCount ?? 1) * (hold.transactionIds?.length ?? 1)} total</dd>
          </div>
          <div>
            <dt>Amount due</dt>
            <dd className="pay__amount">
              LKR {Number(hold.amountLkr ?? 0).toLocaleString('en-LK')}
            </dd>
          </div>
        </dl>

        <label className="pay__label" htmlFor="card-last4">
          Card — last 4 digits
        </label>
        <input
          id="card-last4"
          className="pay__input mono"
          value={cardLast4}
          onChange={(event) => setCardLast4(event.target.value.replace(/\D/g, '').slice(0, 4))}
          placeholder="4242"
          inputMode="numeric"
          autoComplete="off"
          disabled={busy}
          maxLength={4}
        />
        <p className="pay__hint">
          🔒 Demo gateway. Only these 4 digits are sent — a real integration uses the
          payment provider's hosted field, so no full card number reaches LankaJourney.
        </p>

        {error && <div className="alert">Payment failed: {error}</div>}

        <div className="pay__actions">
          <button type="button" className="btn btn--ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn--primary"
            disabled={!valid || busy}
          >
            {busy ? 'Processing…' : `Pay LKR ${Number(hold.amountLkr ?? 0).toLocaleString('en-LK')}`}
          </button>
        </div>
      </form>
    </div>
  )
}

/** Confirmation shown after a successful settlement. */
export function PaymentReceipt({ result }) {
  const ticket = result.ticket ?? {}
  // A journey with a change came back as two tickets. Show both: the traveller
  // needs both references to board, not just the first.
  const tickets = result.tickets?.length > 1 ? result.tickets : null
  const receipts = result.receipts?.length > 1 ? result.receipts : null

  if (tickets) {
    return (
      <div className="receipt">
        <h4 className="receipt__title">
          🎫 {tickets.length} e-tickets issued
        </h4>
        {tickets.map((leg, index) => (
          <dl
            className="pay__summary"
            key={leg.transaction_id ?? leg.booking_reference ?? index}
          >
            <div>
              <dt>Ticket {index + 1} of {tickets.length}</dt>
              {/* The booking agent issues a transaction-shaped ticket, so it
                  identifies the service by route and reference. */}
              <dd className="mono">{leg.booking_reference ?? leg.transaction_id}</dd>
            </div>
            <div>
              <dt>Route</dt>
              <dd className="mono">{leg.route_id}</dd>
            </div>
            <div>
              <dt>Operator</dt>
              <dd>{leg.provider}</dd>
            </div>
            <div>
              <dt>Seats</dt>
              <dd>{leg.seat_count}</dd>
            </div>
            <div>
              <dt>Paid</dt>
              <dd>
                LKR{' '}
                {Number(receipts?.[index]?.amount_lkr ?? 0).toLocaleString('en-LK')}
              </dd>
            </div>
          </dl>
        ))}
      </div>
    )
  }

  return (
    <div className="receipt">
      <h4 className="receipt__title">🎫 E-ticket issued</h4>
      <dl className="pay__summary">
        <div>
          <dt>Booking reference</dt>
          <dd className="mono">{result.booking_reference}</dd>
        </div>
        <div>
          <dt>Receipt</dt>
          <dd className="mono">{result.receipt?.receipt_id}</dd>
        </div>
        <div>
          <dt>Route</dt>
          <dd className="mono">{ticket.route_id}</dd>
        </div>
        <div>
          <dt>Seats</dt>
          <dd>{ticket.seat_count}</dd>
        </div>
        <div>
          <dt>Paid</dt>
          <dd>LKR {Number(result.receipt?.amount_lkr ?? 0).toLocaleString('en-LK')}</dd>
        </div>
      </dl>
    </div>
  )
}