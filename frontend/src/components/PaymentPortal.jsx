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
          <div>
            <dt>Transaction</dt>
            <dd className="mono">{hold.transactionId}</dd>
          </div>
          {hold.routeId && (
            <div>
              <dt>Route</dt>
              <dd className="mono">{hold.routeId}</dd>
            </div>
          )}
          <div>
            <dt>Seats</dt>
            <dd>{hold.seatCount ?? 1}</dd>
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