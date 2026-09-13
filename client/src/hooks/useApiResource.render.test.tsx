import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useApiResource } from './useApiResource'
import { clearCache, setCacheScope } from '../services/queryCache'

interface Slate { data_mode?: 'live'; label: string }

function Probe({ loader, cacheKey }: {
  loader: (signal: AbortSignal) => Promise<Slate>
  cacheKey?: string
}) {
  const resource = useApiResource<Slate>(loader, {
    key: cacheKey, staleTimeMs: 60_000,
  })
  return (
    <div>
      <span data-testid="label">{resource.data?.label ?? '—'}</span>
      <span data-testid="loading">{String(resource.loading)}</span>
      <span data-testid="refreshing">{String(resource.refreshing)}</span>
      <span data-testid="error">{resource.error ?? ''}</span>
      <button onClick={resource.refresh}>refresh</button>
    </div>
  )
}

beforeEach(() => {
  setCacheScope('anon')
  clearCache()
})

afterEach(cleanup)

describe('useApiResource', () => {
  it('renders a keyed page from cache on return, with no loading state', async () => {
    const loader = vi.fn(async () => ({ data_mode: 'live', label: 'first' } as Slate))

    const first = render(<Probe loader={loader} cacheKey="slate" />)
    await waitFor(() => expect(screen.getByTestId('label')).toHaveTextContent('first'))
    first.unmount()

    // Navigating back must show the slate immediately rather than an empty
    // page. This is the reported symptom, expressed as a test.
    render(<Probe loader={loader} cacheKey="slate" />)
    expect(screen.getByTestId('label')).toHaveTextContent('first')
    expect(screen.getByTestId('loading')).toHaveTextContent('false')
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('keeps the visible slate when a refresh fails', async () => {
    let attempt = 0
    const loader = vi.fn(async () => {
      attempt += 1
      if (attempt === 1) return { data_mode: 'live', label: 'good' } as Slate
      throw new Error('backend down')
    })

    render(<Probe loader={loader} cacheKey="slate" />)
    await waitFor(() => expect(screen.getByTestId('label')).toHaveTextContent('good'))

    await act(async () => { screen.getByText('refresh').click() })

    await waitFor(() => expect(screen.getByTestId('error')).toHaveTextContent('backend down'))
    // The error is reported beside the content it could not replace.
    expect(screen.getByTestId('label')).toHaveTextContent('good')
  })

  it('reports loading only while there is nothing to show', async () => {
    let release: (value: Slate) => void = () => {}
    const loader = vi.fn(() => new Promise<Slate>((resolve) => { release = resolve }))

    render(<Probe loader={loader} cacheKey="slate" />)
    expect(screen.getByTestId('loading')).toHaveTextContent('true')
    expect(screen.getByTestId('refreshing')).toHaveTextContent('false')

    await act(async () => { release({ data_mode: 'live', label: 'arrived' }) })
    expect(screen.getByTestId('loading')).toHaveTextContent('false')
  })

  it('never shows one key’s data under another key', async () => {
    const loader = (label: string) => async () => ({ data_mode: 'live', label } as Slate)

    const a = render(<Probe loader={loader('game-a')} cacheKey="game?id=a" />)
    await waitFor(() => expect(screen.getByTestId('label')).toHaveTextContent('game-a'))
    a.unmount()

    render(<Probe loader={loader('game-b')} cacheKey="game?id=b" />)
    // Game B starts empty rather than briefly wearing game A's numbers.
    expect(screen.getByTestId('label')).toHaveTextContent('—')
    await waitFor(() => expect(screen.getByTestId('label')).toHaveTextContent('game-b'))
  })

  it('shares one request between two components reading the same key', async () => {
    const loader = vi.fn(async () => ({ data_mode: 'live', label: 'shared' } as Slate))
    render(
      <>
        <Probe loader={loader} cacheKey="slate" />
        <Probe loader={loader} cacheKey="slate" />
      </>,
    )
    await waitFor(() => expect(screen.getAllByTestId('label')[1]).toHaveTextContent('shared'))
    expect(loader).toHaveBeenCalledTimes(1)
  })

  it('drops cached content when the account changes', async () => {
    let calls = 0
    // The refetch under the new scope is held open, so what the assertion sees
    // is the state between scopes rather than the replacement arriving.
    const loader = vi.fn(() => {
      calls += 1
      return calls === 1
        ? Promise.resolve({ data_mode: 'live', label: 'private' } as Slate)
        : new Promise<Slate>(() => {})
    })
    render(<Probe loader={loader} cacheKey="bankroll" />)
    await waitFor(() => expect(screen.getByTestId('label')).toHaveTextContent('private'))

    await act(async () => { setCacheScope('user:other') })
    // The previous account's figures leave the screen at once, rather than
    // staying up until a replacement happens to arrive.
    expect(screen.getByTestId('label')).toHaveTextContent('—')
    expect(screen.getByTestId('loading')).toHaveTextContent('true')
  })

  it('keeps unkeyed callers per-mount, as they were before', async () => {
    const loader = vi.fn(async () => ({ data_mode: 'live', label: 'local' } as Slate))
    const first = render(<Probe loader={loader} />)
    await waitFor(() => expect(screen.getByTestId('label')).toHaveTextContent('local'))
    first.unmount()

    render(<Probe loader={loader} />)
    expect(screen.getByTestId('label')).toHaveTextContent('—')
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2))
  })
})
