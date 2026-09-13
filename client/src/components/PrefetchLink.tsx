import { useCallback, useRef } from 'react'
import { Link, NavLink, type LinkProps, type NavLinkProps } from 'react-router-dom'
import { prefetchRoute } from '../services/routePrefetch'

/**
 * The signals that mean "this reader is probably about to go here": the pointer
 * settling on a link, keyboard focus reaching it, or a touch starting on it.
 * Warming on a keyboard focus matters as much as on hover — a reader tabbing
 * through the slate should not be the only one who waits.
 */
function useWarm(to: string) {
  const warmed = useRef(false)
  return useCallback(() => {
    if (warmed.current) return
    warmed.current = true
    prefetchRoute(typeof to === 'string' ? to : String(to))
  }, [to])
}

function handlers(warm: () => void) {
  return {
    onMouseEnter: warm,
    onFocus: warm,
    onTouchStart: warm,
  }
}

/** A `Link` that warms its destination when the reader shows intent. */
export function PrefetchLink({ to, children, ...rest }: LinkProps) {
  const warm = useWarm(String(to))
  return <Link to={to} {...handlers(warm)} {...rest}>{children}</Link>
}

/** A `NavLink` that does the same, for the primary navigation. */
export function PrefetchNavLink({ to, children, ...rest }: NavLinkProps) {
  const warm = useWarm(String(to))
  return <NavLink to={to} {...handlers(warm)} {...rest}>{children}</NavLink>
}
