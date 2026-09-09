# Unified sports product verification — 2026-09-09

Primary application: http://localhost:5173. Its served API configuration is http://127.0.0.1:8018/api/v1. Work began at 8a1fae52a58b50af0e31660cc6e3be68806735ea on claude/mlb-overnight-acceptance-policy-study-v2; implementation branch: codex/aisp-unified-sports-wow-labs-v1.

## Imported evidence and preservation

The live import receipt is `nfl-labs-import-20260909.json`: 48 verified, newly imported supplements (16 M1 score populations, 16 independent drive BASE, 16 independent drive pace). A subsequent read-only check reused all 48. Byte counts, SHA-256, immutable bindings and recorded sequence/distribution validation passed. Full-row hashes and counts of 12 protected tables were unchanged across the import transaction. No model execution, provider call, official position/policy/forecast write or promotion was performed by this work. Unrelated pre-existing untracked work was preserved.

## Observed primary-browser walkthrough

1. Command Center has the nine unified destinations and one workspace sport selector; no NFL sidebar destination. All sports Games showed 16 NFL and 15 MLB cards. NFL selection removed MLB. Sunday used four desktop columns at a 1600px viewport; all 16 Week 1 games remained accessible.
2. NE at SEA game analysis opened the canonical event and kept original M1 probabilities/source dates. Interactive Labs showed actual NFL cards; All sports interleaved NFL with the September 8 MLB replay slate (31 playable cards, separate date/week windows).
3. Outcome Explorer: selecting SEA 13–NE 19 showed 0.820%; it honestly reported no matching supplied representative. SEA -7 changed cover/away-cover/push to 32.9%/64.4%/2.7%. Total 45.5 changed over/under/push to 37.7%/62.3%/0.0%.
4. Drive Theater used BASE Typical simulation 946: NE 20–SEA 23, overtime, 25 recorded drives, from 1,000 stored simulations. At 0.5x, drive 1 visibly progressed from near its start (SVG marker x=470.69; Q1 14:56; 0–0) to its recorded endpoint (x=980; Q1 12:56), then showed touchdown and NE 7–SEA 0. Seattle drive 2 started at x=870 and its selected completed endpoint was x=600, opposite direction, turnover at Q1 11:13, score unchanged. Coordinates are renderer positions, not physical measurements. The TD endpoint remains the supplied opponent 12, not an invented goal-line endpoint. Celebration is separate from geometry.
5. Verified Play/Pause, Previous/Next, Restart, speed selection, supplied representative switching, BASE/pace switching, trail toggle, scoring/current/full timeline, Theater mode and exit. Scoring-only showed 8 drives; current-only showed 1. Reduced-motion behavior is protected by focused tests, not claimed as an emulated browser preference.
6. Scenario Comparison displayed actual BASE total 42.988 versus pace 45.412, home-win 60.3% versus 58.4%, and synchronized drive stepping. Typical BASE946 has 25 drives; pace600 has 19. The page explicitly says those are different representatives, not a paired causal effect.
7. NFL Markets retained 16 games with separate moneyline/spread/total. NE–SEA prices were missing; other recorded quotes were stale reference-only. Selecting point spread showed 16 spread regions and no moneyline regions. MLB selection removed NFL rows and changed market labels to run line/total runs.
8. MLB Labs opened CLE–BAL September 8 V18 (`6fd319b0-7e26-4e0a-8c91-84a6b26e54d2`). Broadcast Replay stepped from PA1 to PA2; Play Geometry loaded ball/runner traces; Simulation Population animated 50-game frames and reached all 5,000. All three tabs remained. Switching to official Gen1 showed the separate 20,000-outcome distribution, not a drive/PA replay.
9. NFL Model Performance and Historical Research retained sport=NFL and displayed only the corresponding imported research evidence. The legacy `/nfl/games/b1dec4c5-c509-4550-8853-2cffa5a89a0c/lab?view=drives&scenario=BASE` redirected to the canonical simulation URL preserving view/scenario and adding sport=NFL.
10. At 390x844, Games used one column. A real week-select overflow was corrected; final document width was 375 <= viewport390. Labs, Drive Theater and Performance also had no page overflow. Temporary viewport override was reset.

## Focused verification

- Sport selection/bookmarks: 3 passing tests; existing NFL week: 1.
- Market frontend arithmetic/selection: 4; backend NFL market arithmetic: 3.
- Labs landing: 3; recorded MLB slate/metadata/availability: 3.
- Football playback and Outcome Explorer: 10.
- New V1 supplement adapter plus directly relevant existing import/market cases: 17. A position-creation integration case was deliberately excluded.
- Initial frontend build stopped at TypeScript errors; those were corrected. Typecheck and the single production Vite build then passed (existing chunk-size warning retained). A final typecheck passed after small browser-driven wording/layout changes; production bundling was not repeated.
- No full repository test suite was run.

## Screenshots

The five requested browser captures are retained in `C:/Users/james/.codex/visualizations/2026/09/03/01a0681d-0c9c-7a02-a30d-5dcded9e8180/aisp-unified/`: `01-navigation.png`, `02-multisport-labs.png`, `03-drive-theater.png`, `04-scenario-comparison.png`, `05-nfl-markets.png`. The Drive Theater screenshot shows the scoring result; motion is evidenced by the observed state sequence above, not the still image alone.

## Open directly

- http://localhost:5173/?sport=ALL
- http://localhost:5173/games?sport=NFL&season=2026&week=1
- http://localhost:5173/labs?sport=ALL
- http://localhost:5173/games/b1dec4c5-c509-4550-8853-2cffa5a89a0c/simulation?sport=NFL&view=drives
- http://localhost:5173/games/b1dec4c5-c509-4550-8853-2cffa5a89a0c/simulation?sport=NFL&view=scenarios
- http://localhost:5173/markets?sport=NFL
- http://localhost:5173/games/6fd319b0-7e26-4e0a-8c91-84a6b26e54d2/simulation?track=v18&sport=MLB

No external blocker to the product delivery. Fresh sportsbook prices and matched prediction-market observations remain source limitations; this task did not fetch them or make old model evidence current.
