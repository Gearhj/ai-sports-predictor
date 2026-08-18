import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { ShieldAlert } from 'lucide-react'
import { PageHeader, Panel } from './ui'
import { useAuth } from '../hooks/useAuth'

/**
 * Gate an operational/admin route on the server-derived role. The backend
 * remains authoritative (protected endpoints return 401/403); this only keeps
 * non-admins from mounting the page and calling those endpoints, and shows a
 * clean access-controlled state instead of a misleading "no data" panel.
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return <div className="route-loading" role="status">Checking access…</div>
  }

  if (user?.role === 'ADMIN') {
    return <>{children}</>
  }

  return (
    <div className="page health-page">
      <PageHeader
        eyebrow="OPERATIONS · ADMIN"
        title="Administrator access required"
        description="Operational data health is restricted to administrator accounts."
        actions={<span className="admin-chip"><ShieldAlert size={14} /> ADMIN ONLY</span>}
      />
      <Panel title="Restricted operations area" eyebrow="ACCESS CONTROLLED">
        {user ? (
          <p className="muted">
            Your account ({user.role}) does not have administrator access. Operational
            source health, provider quota, and pipeline controls are available to
            administrators only.
          </p>
        ) : (
          <p className="muted">
            Sign in with an administrator account to view operational data health.{' '}
            <NavLink to="/account" className="inline-link">Sign in</NavLink>.
          </p>
        )}
      </Panel>
    </div>
  )
}
