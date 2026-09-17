import { describe, expect, it } from 'vitest'
import { modelName } from './CommandPerformance'

describe('modelName', () => {
  it('names models for readers and tells candidates apart', () => {
    expect(modelName('MLB_V18_WHOLE_GAME', 'mlb-v18-whole-game-a6e7e99122f1b60701a4728b', 'MLB_V18_WHOLE_GAME')).toBe('MLB V18 whole-game model')
    expect(modelName('MLB_ENSEMBLE_CANDIDATE', 'mlb-v18-three-arm-duration-v1')).toBe('MLB ensemble candidate · three arm duration')
    expect(modelName('MLB_ENSEMBLE_CANDIDATE', 'mlb-v18-three-arm-transition-v1')).toBe('MLB ensemble candidate · three arm transition')
    expect(modelName('MLB_ENSEMBLE_CANDIDATE', 'mlb-v18-score-limited-v1')).toBe('MLB ensemble candidate · score limited')
    expect(modelName('SOME_NEW_ENGINE', 'x')).toBe('Some new engine')
  })
})
