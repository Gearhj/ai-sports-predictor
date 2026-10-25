import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { FlowRiver } from './FlowRiver'

type Bet = { sport: string; price: string; result: string; stake: number }
const BETS: Bet[] = [
  { sport: 'NFL', price: 'Underdog', result: 'Won', stake: 10 },
  { sport: 'NFL', price: 'Favorite', result: 'Lost', stake: 20 },
  { sport: 'MLB', price: 'Underdog', result: 'Lost', stake: 30 },
]
const COLUMNS = [
  { title: 'SPORT', of: (b: Bet) => b.sport },
  { title: 'PRICE', of: (b: Bet) => b.price, order: ['Favorite', 'Underdog'] },
  { title: 'RESULT', of: (b: Bet) => b.result, order: ['Won', 'Lost'] },
]

describe('FlowRiver', () => {
  afterEach(cleanup)

  it('sizes every block by the value that reached it', () => {
    render(<FlowRiver items={BETS} columns={COLUMNS} value={(b) => b.stake} colorOf={() => '#fff'} label="test river" />)
    expect(screen.getByRole('button', { name: /^Underdog: 40\./ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Lost: 50\./ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Won: 10\./ })).toBeInTheDocument()
  })

  it('traces a block through the other columns and clears on a second press', () => {
    const { container } = render(<FlowRiver items={BETS} columns={COLUMNS} value={(b) => b.stake} colorOf={() => '#fff'} label="test river" />)
    const dog = screen.getByRole('button', { name: /^Underdog: 40\./ })
    fireEvent.click(dog)
    expect(screen.getByText(/Tracing/)).toHaveTextContent('Tracing Underdog through every column.')
    // the underdogs' 30 of the 50 lost is lit on the Lost block
    expect(screen.getByRole('button', { name: /^Lost: 50\./ })).toHaveTextContent('30 traced')
    expect(container.querySelectorAll('.fr__trace').length).toBeGreaterThan(0)
    fireEvent.click(dog)
    expect(container.querySelectorAll('.fr__trace').length).toBe(0)
  })
})
