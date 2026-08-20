import { useState } from 'react'
import OAuthButtons from './OAuthButtons'

interface Props {
  onSubmit: (email: string, username: string, password: string) => void
  onSwitch: () => void
  loading: boolean
  error: string | null
}

export default function RegisterForm({ onSubmit, onSwitch, loading, error }: Props) {
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const complete = email.trim() && username.trim() && password

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    if (complete) {
      // the password is passed through untrimmed — surrounding spaces are
      // legitimate characters in a passphrase
      onSubmit(email.trim(), username.trim(), password)
    }
  }

  return (
    <form className="card" onSubmit={handleSubmit} data-testid="register-form">
      <h2>Create an account</h2>
      {error && (
        <p className="error" data-testid="register-error">
          {error}
        </p>
      )}
      <div className="field">
        <label htmlFor="register-email">Email</label>
        <input
          className="input"
          id="register-email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          autoComplete="email"
          disabled={loading}
          data-testid="register-email"
        />
      </div>
      <div className="field">
        <label htmlFor="register-username">Username</label>
        <input
          className="input"
          id="register-username"
          type="text"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          disabled={loading}
          data-testid="register-username"
        />
      </div>
      <div className="field">
        <label htmlFor="register-password">Password</label>
        <input
          className="input"
          id="register-password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="At least 8 characters"
          autoComplete="new-password"
          disabled={loading}
          data-testid="register-password"
        />
      </div>
      <button
        className="button block"
        type="submit"
        disabled={loading || !complete}
        data-testid="register-submit"
      >
        {loading ? 'Creating…' : 'Create account'}
      </button>
      <OAuthButtons disabled={loading} />
      <p className="switch">
        Already have an account?{' '}
        <button className="button-link" type="button" onClick={onSwitch} data-testid="show-login">
          Sign in
        </button>
      </p>
    </form>
  )
}
