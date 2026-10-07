/**
 * The traveller's own details, opened from the header.
 *
 * The card field is deliberately odd-looking: you type a full card number, and
 * on save it collapses to "Visa •••• 4242" forever. That is the honest shape of
 * the feature — the server validates the number and keeps four digits — so the
 * panel explains it rather than letting the traveller wonder where it went.
 */
import { useEffect, useState } from 'react'
import { saveProfile } from '../api.js'

export default function ProfilePanel({ user, onClose, onSaved, onSignOut }) {
  const [fullName, setFullName] = useState(user?.full_name || '')
  const [contact, setContact] = useState(user?.contact_number || '')
  const [cardNumber, setCardNumber] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    setFullName(user?.full_name || '')
    setContact(user?.contact_number || '')
  }, [user])

  async function handleSubmit(event) {
    event.preventDefault()
    setError(null)
    setSaved(false)
    setBusy(true)
    try {
      const data = await saveProfile({
        fullName,
        contactNumber: contact,
        cardNumber: cardNumber.trim() || undefined,
      })
      // The typed number is dropped immediately: keeping it in component state
      // after a successful save would leave a PAN sitting in the tab.
      setCardNumber('')
      setSaved(true)
      onSaved?.(data.user)
    } catch (err) {
      setError(err.message || 'Could not save your details.')
    } finally {
      setBusy(false)
    }
  }

  const initials = (user?.full_name || user?.email || '?')
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('')

  return (
    <div className="profile-overlay" onClick={onClose}>
      <div
        className="profile-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Your profile"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="profile-header">
          <span className="profile-avatar" aria-hidden="true">{initials}</span>
          <div>
            <h2>{user?.full_name || 'Your profile'}</h2>
            <p>{user?.email}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Close profile">
            ✕
          </button>
        </header>

        <form onSubmit={handleSubmit} className="profile-form">
          <label htmlFor="profile-name">Full name</label>
          <input
            id="profile-name"
            type="text"
            maxLength={80}
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            placeholder="Nimal Perera"
          />

          <label htmlFor="profile-contact">Contact number</label>
          <input
            id="profile-contact"
            type="tel"
            value={contact}
            onChange={(e) => setContact(e.target.value)}
            placeholder="07XXXXXXXX"
          />
          <p className="field-hint">
            Used to reach you about a held seat. Stored as +94XXXXXXXXX.
          </p>

          <label htmlFor="profile-card">Card number</label>
          {user?.has_card ? (
            <div className="saved-card">
              <span className="saved-card-brand">{user.card_brand}</span>
              <span className="saved-card-digits">•••• {user.card_last4}</span>
            </div>
          ) : (
            <p className="field-hint">No card saved yet.</p>
          )}
          <input
            id="profile-card"
            type="text"
            inputMode="numeric"
            autoComplete="cc-number"
            // Not cc-number: that tells the browser to autofill a stored card,
            // which would push the real number straight into a form the user
            // cannot see the contents of.
            autoComplete="off"
            value={cardNumber}
            onChange={(e) => setCardNumber(e.target.value.replace(/[^\d\s]/g, ''))}
            placeholder={user?.has_card ? 'Enter a new number to replace it' : '4111 1111 1111 1111'}
          />
          <p className="field-hint">
            {user?.has_card
              ? 'Enter a new number to replace the saved card.'
              : 'Checked for typos, then reduced to the brand and last four digits.'}
          </p>

          <div className="card-privacy">
            <strong>We never store your full card number.</strong> It is checked
            here, its brand and last four digits are saved, and the rest is
            discarded. Nothing is sent to an agent or a language model. A real
            payment would take the card straight to the payment provider without
            passing through this server.
          </div>

          {error && <p className="login-error" role="alert">{error}</p>}
          {saved && !error && <p className="profile-saved" role="status">Saved.</p>}

          <button type="submit" className="login-submit" disabled={busy}>
            {busy ? 'Saving…' : 'Save details'}
          </button>
        </form>

        {/* Signing out lives with the account details, not buried in a sidebar
            corner: the panel is where a traveller goes to manage the account. */}
        <div className="profile-footer">
          <button type="button" className="profile-signout" onClick={onSignOut}>
            <span aria-hidden="true">⏻</span> Sign out
          </button>
          <p className="profile-footer__note">
            Signing out clears the session on this device. Your saved details,
            tickets and conversation history stay on the account.
          </p>
        </div>
      </div>
    </div>
  )
}