import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BuildRefresh, readBuildCommit } from './BuildRefresh'

afterEach(cleanup)

const identity = (commit: string) => Promise.resolve(new Response(JSON.stringify({ commit }), { status: 200 }))

let go: (to: string) => void = () => {}
function Nav() { go = useNavigate(); return null }

describe('BuildRefresh', () => {
  it('reads the deployed commit and tolerates a missing identity', async () => {
    expect(await readBuildCommit(() => identity('abc'))).toBe('abc')
    expect(await readBuildCommit(() => Promise.resolve(new Response('<html>', { status: 200 })))).toBeNull()
    expect(await readBuildCommit(() => Promise.reject(new Error('offline')))).toBeNull()
  })

  it('offers a reload after a new deployment and reloads on the next page change', async () => {
    let commit = 'old'
    const fetcher = vi.fn(() => identity(commit)) as unknown as typeof fetch
    const reload = vi.fn()
    render(<MemoryRouter><Nav /><BuildRefresh fetcher={fetcher} reload={reload} enabled /></MemoryRouter>)
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByRole('status')).toBeNull()
    commit = 'new'
    await act(async () => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
      document.dispatchEvent(new Event('visibilitychange'))
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(screen.getByRole('status')).toHaveTextContent('AISP has been updated.')
    expect(reload).not.toHaveBeenCalled()
    act(() => go('/decisions'))
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('stays quiet when the build is unchanged', async () => {
    const reload = vi.fn()
    render(<MemoryRouter><Nav /><BuildRefresh fetcher={() => identity('same')} reload={reload} enabled /></MemoryRouter>)
    await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
    act(() => go('/markets'))
    expect(reload).not.toHaveBeenCalled()
  })
})
