/**
 * Sign in / create an account.
 *
 * One screen with a mode toggle rather than two pages: the difference between
 * them is two fields and a label, and a traveller who picks the wrong one
 * should not have to navigate to fix it.
 */
import { useState } from 'react'
import { login, register } from '../api.js'

const DEMO_EMAIL = 'demo@lankajourney.lk'
const DEMO_PASSWORD = 'demotravel123'

export default function Login({ onSignedIn }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const isRegister = mode === 'register'

  async function handleSubmit(event) {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const data = isRegister ? await register(email, password) : await login(email, password)
      onSignedIn(data.user)
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.')
      setBusy(false)
    }
  }

  function useDemoAccount() {
    setEmail(DEMO_EMAIL)
    setPassword(DEMO_PASSWORD)
    setMode('login')
    setError(null)
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <span className="login-mark" aria-hidden="true">🚆</span>
          <h1>LankaJourney AI</h1>
          <p className="login-tagline">
            Plan a journey across Sri Lanka by bus and train, check the weather and
            live incidents, and book your seat.
          </p>
        </div>

        <div className="login-modes" role="tablist" aria-label="Sign in or register">
          <button
            type="button"
            role="tab"
            aria-selected={!isRegister}
            className={!isRegister ? 'active' : ''}
            onClick={() => { setMode('login'); setError(null) }}
          >
            Sign in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={isRegister}
            className={isRegister ? 'active' : ''}
            onClick={() => { setMode('register'); setError(null) }}
          >
            Create account
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <label htmlFor="login-email">Email</label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
          />

          <label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            type="password"
            // Not "current-password" on the register form: browsers offer saved
            // credentials there, which quietly fills the wrong box.
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={isRegister ? 'At least 8 characters' : 'Your password'}
          />
          {isRegister && (
            <p className="login-hint">Use at least 8 characters.</p>
          )}

          {error && (
            <p className="login-error" role="alert">{error}</p>
          )}

          <button type="submit" className="login-submit" disabled={busy}>
            {busy
              ? 'Please wait…'
              : isRegister
                ? 'Create account'
                : 'Sign in'}
          </button>
        </form>

        <div className="login-demo">
          <p>
            Just looking around? Use the demo traveller — no sign-up needed.
          </p>
          <button type="button" onClick={useDemoAccount}>
            Fill demo credentials
          </button>
          <p className="login-demo-credentials">
            <code>{DEMO_EMAIL}</code> · <code>{DEMO_PASSWORD}</code>
          </p>
        </div>

        <p className="login-footnote">
          Your card details are never stored in full. This system keeps only the
          brand and last four digits, and never sends a card number to an agent or
          a language model.
        </p>
      </div>
    </div>
  )
}