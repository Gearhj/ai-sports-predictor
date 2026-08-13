import { useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound, LogOut, ShieldCheck, UserRound } from 'lucide-react'
import { PageHeader, Panel } from '../components/ui'
import { useAuth } from '../hooks/useAuth'

export function AuthPage() {
  const { user, loading, login, register, logout } = useAuth()
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setMessage(null)
    try {
      if (mode === 'login') await login(email, password)
      else await register(email, password, displayName)
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : 'Authentication failed.')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <div className="page auth-page"><div className="auth-loading">Checking your session…</div></div>

  if (user) {
    return (
      <div className="page account-page">
        <PageHeader eyebrow="ACCOUNT" title={`Welcome, ${user.display_name}`} description="Your ASP identity and access level." />
        <div className="account-grid">
          <Panel title="Profile" eyebrow="SIGNED IN">
            <div className="profile-card"><span className="profile-avatar">{user.display_name.slice(0, 2).toUpperCase()}</span><div><h2>{user.display_name}</h2><p>{user.email}</p><span className="role-chip"><ShieldCheck size={13} /> {user.role}</span></div></div>
            <dl className="account-details"><div><dt>Account ID</dt><dd className="mono">{user.id}</dd></div><div><dt>Entitlement</dt><dd>Phase 1 access</dd></div><div><dt>Session</dt><dd><CheckCircle2 size={13} /> Active</dd></div></dl>
            <button type="button" className="secondary-button danger-button" onClick={logout}><LogOut size={15} /> Sign out</button>
          </Panel>
          <Panel title="Member access" eyebrow="PHASE 1">
            <div className="access-list"><div><CheckCircle2 size={16} /><span><strong>MLB forecasts</strong><small>Today, analysis, and forecast history</small></span></div><div><CheckCircle2 size={16} /><span><strong>Market research</strong><small>Book-level prices and no-vig consensus</small></span></div><div><CheckCircle2 size={16} /><span><strong>Public model audit</strong><small>Performance and virtual experiment</small></span></div></div>
            <p className="callout-note">Membership-provider integrations are intentionally deferred. ASP accounts are not tied to a billing provider in Phase 1.</p>
          </Panel>
        </div>
      </div>
    )
  }

  return (
    <div className="page auth-page">
      <div className="auth-layout">
        <section className="auth-intro">
          <span className="auth-intro__mark"><KeyRound size={25} /></span>
          <p className="eyebrow">ASP MEMBER ACCESS</p><h1>Research that shows its work.</h1><p>Sign in to access account features while public forecasts, methodology, model performance, and the official experiment remain transparent.</p>
          <div className="auth-principles"><div><ShieldCheck size={16} /><span><strong>Secure credentials</strong><small>Passwords are handled by the API and never stored in this browser.</small></span></div><div><UserRound size={16} /><span><strong>Portable identity</strong><small>Entitlements remain independent from future billing providers.</small></span></div></div>
        </section>
        <section className="auth-card">
          <div className="auth-tabs"><button type="button" className={mode === 'login' ? 'is-active' : ''} onClick={() => { setMode('login'); setMessage(null) }}>Sign in</button><button type="button" className={mode === 'register' ? 'is-active' : ''} onClick={() => { setMode('register'); setMessage(null) }}>Create account</button></div>
          <div className="auth-card__heading"><h2>{mode === 'login' ? 'Welcome back' : 'Create your ASP account'}</h2><p>{mode === 'login' ? 'Use your registered email and password.' : 'Registration may require an eligible email in future phases.'}</p></div>
          <form onSubmit={submit} className="auth-form">
            {mode === 'register' && <label><span>Display name</span><input type="text" value={displayName} onChange={(event) => setDisplayName(event.target.value)} autoComplete="name" minLength={2} required placeholder="Your name" /></label>}
            <label><span>Email address</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required placeholder="you@example.com" /></label>
            <label><span>Password</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={8} required placeholder="At least 8 characters" /></label>
            {message && <div className="form-error" role="alert">{message}</div>}
            <button className="primary-button auth-submit" type="submit" disabled={submitting}>{submitting ? 'Please wait…' : mode === 'login' ? 'Sign in securely' : 'Create account'}</button>
          </form>
          <p className="auth-privacy">By continuing, you acknowledge that ASP provides probabilistic sports research and virtual simulation—not guaranteed outcomes or wagering services.</p>
        </section>
      </div>
    </div>
  )
}
