import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DataModeBanner, formatMoneyline, formatSignedCurrency, formatSignedPct, StatusPill } from './ui'

describe('shared presentation utilities', () => {
  it('formats moneylines and signed values without malformed double signs', () => {
    expect(formatMoneyline(120)).toBe('+120')
    expect(formatMoneyline(-135)).toBe('-135')
    expect(formatSignedCurrency(12.5)).toBe('+$12.50')
    expect(formatSignedCurrency(-12.5)).toBe('-$12.50')
    expect(formatSignedPct(4.2)).toBe('+4.2%')
    expect(formatSignedPct(-4.2)).toBe('-4.2%')
  })

  it('distinguishes explicit demo data from an unavailable backend', () => {
    const onRetry = vi.fn()
    const { rerender } = render(<DataModeBanner state="demo" loading={false} error={null} onRetry={onRetry} />)
    expect(screen.getByText('ILLUSTRATIVE DEMO — NOT LIVE RESULTS')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument()

    rerender(<DataModeBanner state="unavailable" loading={false} error="network" onRetry={onRetry} />)
    expect(screen.getByText(/LIVE DATA UNAVAILABLE/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
  })

  it('communicates source status with text in addition to color and icons', () => {
    render(<StatusPill status="watch" />)
    expect(screen.getByText('watch')).toBeInTheDocument()
  })
})
