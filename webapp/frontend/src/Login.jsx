import { useState } from 'react'
import { supabase } from './supabase.js'
import { ListGroup, ListRow, Button } from './components.jsx'

export default function LoginScreen({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isSignUp, setIsSignUp] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      let result
      if (isSignUp) {
        result = await supabase.auth.signUp({ email, password })
      } else {
        result = await supabase.auth.signInWithPassword({ email, password })
      }
      if (result.error) throw result.error
      const token = result.data.session?.access_token
      if (token) {
        onLogin(token, result.data.session)
      } else {
        setError('Check your email to confirm your account, then sign in.')
      }
    } catch (err) {
      setError(err.message || 'Authentication failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', padding: '24px',
      background: 'var(--bg)',
    }}>
      <div className="row gap-2" style={{ marginBottom: 10, alignItems: 'center' }}>
        <div className="foil" style={{ width: 36, height: 36, borderRadius: 10, animation: 'foilRot 18s linear infinite', flexShrink: 0 }}/>
        <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--ink)' }}>PokeCollect</div>
      </div>
      <p style={{ color: 'var(--ink-3)', marginBottom: 32, fontSize: 13, fontWeight: 500, letterSpacing: '0.02em' }}>
        Track your collection · Trade at shows
      </p>

      <form onSubmit={handleSubmit} style={{ width: '100%', maxWidth: 340, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <ListGroup>
          <ListRow>
            <input
              type="email" value={email} onChange={e => setEmail(e.target.value)}
              placeholder="Email" required autoComplete="email"
            />
          </ListRow>
          <ListRow>
            <input
              type="password" value={password} onChange={e => setPassword(e.target.value)}
              placeholder="Password" required minLength={6} autoComplete="current-password"
            />
          </ListRow>
        </ListGroup>

        {error && <p style={{ color: 'var(--neg)', fontSize: 13, margin: 0 }}>{error}</p>}

        <div className="col" style={{ gap: 6 }}>
          <Button type="submit" loading={loading}>
            {isSignUp ? 'Create account' : 'Sign in'}
          </Button>
          <button type="button" onClick={() => { setIsSignUp(!isSignUp); setError('') }}
            style={{
              background: 'none', border: 'none',
              color: 'var(--ink-3)', fontSize: 13,
              cursor: 'pointer', padding: '8px 4px',
            }}>
            {isSignUp ? 'Already have an account? Sign in' : 'New here? Create an account'}
          </button>
        </div>
      </form>
    </div>
  )
}
