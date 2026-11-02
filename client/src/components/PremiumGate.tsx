/**
 * Wraps a premium feature. Open to everyone while gating is off (the beta), with a small PREMIUM tag so members
 * learn which parts will be paid; once gating is on, a member without the entitlement sees what the feature is and
 * where to get it instead of the feature.
 */
import { Link } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { usePremium } from '../hooks/usePremium'
import type { ReactNode } from 'react'

export function PremiumTag() {
  return <span className="premium-tag" title="A premium feature. Open to everyone during the beta."><Sparkles size={11} /> PREMIUM</span>
}

export function PremiumGate({ feature, blurb, children }: { feature: string; blurb: string; children: ReactNode }) {
  const { allowed, gating, signedIn, loading } = usePremium()
  if (loading) return null
  if (allowed) {
    return (
      <>
        {!gating ? <p className="premium-note"><PremiumTag /> {feature} will be part of the premium membership. During the beta it is open to everyone.</p> : null}
        {children}
      </>
    )
  }
  return (
    <section className="premium-wall" aria-label={`${feature}: premium members`}>
      <PremiumTag />
      <h2>{feature}</h2>
      <p>{blurb}</p>
      <p className="premium-wall__how">
        {signedIn
          ? <>This account does not have the premium membership yet. See <Link to="/account">your account</Link> for how to get it.</>
          : <><Link to="/login">Sign in</Link> with a premium account to open it.</>}
      </p>
      <p className="premium-wall__fine">AI Sports Predictor is an information and entertainment service. A membership buys access to research, never a wager or a prize.</p>
    </section>
  )
}
