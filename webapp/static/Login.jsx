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
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '24px', background: 'var(--bg, #0f172a)' }}>
      <div style={{ fontSize: '48px', marginBottom: '8px' }}>🃏</div>
      <h1 style={{ fontSize: '24px', fontWeight: 700, marginBottom: '4px', color: 'var(--text, #f1f5f9)' }}>PokeCollect</h1>
      <p style={{ color: 'var(--text-secondary, #94a3b8)', marginBottom: '32px', fontSize: '14px' }}>Track your collection. Trade at shows.</p>

      <form onSubmit={handleSubmit} style={{ width: '100%', maxWidth: '340px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
        <input
          type="email" value={email} onChange={e => setEmail(e.target.value)}
          placeholder="Email" required autoComplete="email"
          style={{ padding: '12px 16px', borderRadius: '10px', border: '1px solid var(--border, #334155)', background: 'var(--surface, #1e293b)', color: 'var(--text, #f1f5f9)', fontSize: '16px' }}
        />
        <input
          type="password" value={password} onChange={e => setPassword(e.target.value)}
          placeholder="Password" required minLength={6} autoComplete="current-password"
          style={{ padding: '12px 16px', borderRadius: '10px', border: '1px solid var(--border, #334155)', background: 'var(--surface, #1e293b)', color: 'var(--text, #f1f5f9)', fontSize: '16px' }}
        />
        {error && <p style={{ color: '#ef4444', fontSize: '13px', margin: 0 }}>{error}</p>}
        <button type="submit" disabled={loading}
          style={{ padding: '14px', borderRadius: '10px', background: '#3b82f6', color: 'white', fontWeight: 600, fontSize: '16px', border: 'none', cursor: loading ? 'not-allowed' : 'pointer', opacity: loading ? 0.7 : 1 }}>
          {loading ? 'Please wait…' : (isSignUp ? 'Create account' : 'Sign in')}
        </button>
        <button type="button" onClick={() => { setIsSignUp(!isSignUp); setError(''); }}
          style={{ background: 'none', border: 'none', color: 'var(--text-secondary, #94a3b8)', fontSize: '14px', cursor: 'pointer', padding: '4px' }}>
          {isSignUp ? 'Already have an account? Sign in' : "Don't have an account? Sign up — free 14-day trial"}
        </button>
      </form>
    </div>
  );
}
