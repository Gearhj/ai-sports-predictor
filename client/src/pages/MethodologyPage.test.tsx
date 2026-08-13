import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MethodologyPage } from './MethodologyPage'

vi.mock('../hooks/usePublicPolicy', () => ({
  usePublicPolicy: () => ({
    policy: {
      modelStatus: 'DEVELOPMENT',
      componentWeights: { baseline: 35, run: 40, boosting: 25 },
      bankroll: { kellyFraction: 0.25, maximumStakePercentage: 2 },
    },
    source: 'api',
    loading: false,
  }),
}))

describe('MethodologyPage Gate 3 evaluation policy', () => {
  afterEach(cleanup)

  it('distinguishes training, validation, locked holdout, and explicit promotion', () => {
    render(<MethodologyPage />)

    expect(screen.getByRole('heading', { name: /Training chooses the model/i })).toBeInTheDocument()
    expect(screen.getByText('Training window')).toBeInTheDocument()
    expect(screen.getByText('Validation window')).toBeInTheDocument()
    expect(screen.getByText('Locked holdout')).toBeInTheDocument()
    expect(screen.getByText('Rolling-origin folds')).toBeInTheDocument()
    expect(screen.getByText('Cross-fitted validation')).toBeInTheDocument()
    expect(screen.getByText('Explicit promotion')).toBeInTheDocument()
    expect(screen.getByText(/NOT RUN or INSUFFICIENT DATA/i)).toBeInTheDocument()
  })
})
