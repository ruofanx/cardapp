function LoginScreen({ onLogin }) {
  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');
  const [isSignUp, setIsSignUp] = React.useState(false);
  const [error, setError] = React.useState('');
  const [loading, setLoading] = React.useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      let result;
      if (isSignUp) {
        result = await window._supabase.auth.signUp({ email, password });
      } else {
        result = await window._supabase.auth.signInWithPassword({ email, password });
      }
      if (result.error) throw result.error;
      const token = result.data.session?.access_token;
      if (token) {
        onLogin(token, result.data.session);
      } else {
        setError('Check your email to confirm your account, then sign in.');
      }
    } catch (err) {
      setError(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
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

      <form onSubmit={handleSubmit} style={{ width: '100%', maxWidth: 340, display: 'flex', flexDirection: 'column', gap: 10 }}>
        <input
          type="email" value={email} onChange={e => setEmail(e.target.value)}
          placeholder="Email" required autoComplete="email"
          style={{
            padding: '12px 16px', borderRadius: 12,
            border: '1px solid var(--hairline-soft)',
            background: 'var(--bg-1)', color: 'var(--ink)',
            fontSize: 16, outline: 'none',
          }}
        />
        <input
          type="password" value={password} onChange={e => setPassword(e.target.value)}
          placeholder="Password" required minLength={6} autoComplete="current-password"
          style={{
            padding: '12px 16px', borderRadius: 12,
            border: '1px solid var(--hairline-soft)',
            background: 'var(--bg-1)', color: 'var(--ink)',
            fontSize: 16, outline: 'none',
          }}
        />
        {error && <p style={{ color: 'var(--neg)', fontSize: 13, margin: 0 }}>{error}</p>}
        <button type="submit" disabled={loading}
          style={{
            padding: '13px', borderRadius: 12,
            background: 'var(--accent)', color: 'var(--accent-ink)',
            fontWeight: 700, fontSize: 16, border: 'none',
            cursor: loading ? 'not-allowed' : 'pointer',
            opacity: loading ? 0.6 : 1,
          }}>
          {loading ? 'Please wait…' : (isSignUp ? 'Create account' : 'Sign in')}
        </button>
        <button type="button" onClick={() => { setIsSignUp(!isSignUp); setError(''); }}
          style={{
            background: 'none', border: 'none',
            color: 'var(--ink-3)', fontSize: 13,
            cursor: 'pointer', padding: '4px',
          }}>
          {isSignUp ? 'Already have an account? Sign in' : "New here? Create an account"}
        </button>
      </form>
    </div>
  );
}
