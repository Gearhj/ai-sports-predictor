/**
 * Keeps one page region's render failure inside that region. Without it an
 * unexpected payload in the command panel took the whole page down with it,
 * including the regions whose data was fine.
 */
import { Component, type ErrorInfo, type ReactNode } from 'react'

export class RegionBoundary extends Component<{ label: string; children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`${this.props.label} failed to render`, error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <p className="cc-empty" role="alert">
          The {this.props.label} could not be displayed. The rest of this page is unaffected.{' '}
          <button type="button" className="cc-more" onClick={() => this.setState({ error: null })}>Try again</button>
        </p>
      )
    }
    return this.props.children
  }
}
