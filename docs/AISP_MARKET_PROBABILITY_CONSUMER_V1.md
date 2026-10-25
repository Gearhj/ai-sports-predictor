# AISP consumer: market-probability contract v1

AISP now uses Model Studio's probability contract to turn one verified paired
final-score population into moneyline, spread, total and same-game results.
It does this for exactly one canonical game and forecast revision at a time.
This consumer integration is not a model import or a forecast promotion, and
it grants no decision authority.

## Producer artifacts

| Item | Identity |
|---|---|
| Contract (Gearhj/asp-model-studio, `codex/aisp-market-probability-contract-v1`) | `e0d1638626ad33a7a794d4f94882acf363c78652`; implementation `bcca9c9dce7c2804ae538671e646797715c05291` |
| Claude handoff SHA-256 | `135db1a8c146a3bed0411c091fbb9876baecf87bce364e9d32eed884112d5a36` |
| `market_probabilities.py` SHA-256 | `2e7673d1ec4e5868cc27f5b152bf83cf75a91787e676f61ce0c24c8b462e1af1` |
| Cross-game study (`codex/cross-game-independence-validation-v1`) | `d5702cb6df25bf703c1c598fb7a3a4085531c703`; `RESULTS.json` `fafc0b9bd9997e65fa6db209bceb6dacb91943528e136ed23603daf922ffef24` |

- **Vendored module.** `server/app/probability/market_probabilities.py` is a
  byte-identical copy of the producer module.
- **Pinned fixtures.** The golden vectors, schemas and capability matrix are
  copied under `server/tests/fixtures/market_probability_contract_v1/`, pinned
  by hash in `app/probability/identity.py`.
- **No runtime dependency.** Nothing reads the Model Studio checkout at runtime.

## Adapter (`app/probability/adapter.py`)

| Sport | Forecast bound | Population re-read and verified from |
|---|---|---|
| MLB | Latest `MLB_V18_WHOLE_GAME` shadow forecast (admitted model artifact and parent bundle) | The V18 object's `execution-inputs.json`. Its manifest must hash to the object SHA, and the file must match its manifest digest. |
| NFL | Latest `NFL_M1_XGBOOST` shadow forecast (admitted configuration and model artifact) | The stored joint score distribution. It must convert exactly to 10,000 whole draw counts. |
| NBA | The `baseline` joint comparison arm | `populations/baseline-joint-draws.parquet`. The file SHA-256 and the logical int64 (home, away) digest must match. |
| NBA point/Elo | — | Winner channel only: no spread, total or parlay. It is never substituted for the joint population. |
| CFB | Latest research forecast for the game's CFBD binding | `baseline-joint-draws.parquet`. The file SHA-256 must match, and the package index must address its directory. |

For every sport:

- **Stored mass must match.** The winner and tie mass implied by the
  population must equal the stored forecast probabilities.
- **Any disagreement fails closed.** Hash, count, identity or mass mismatches
  all produce a stable error code.
- **Requests must echo the binding.** A request must repeat the current
  binding exactly (`BINDING_MISMATCH`, 409).
- **Tie rule.** AISP settles a tie on a two-way moneyline as `PUSH`, the rule
  `forecast_exchange.pricing` already applies.

## Endpoints (read-only, no provider call)

- `GET /api/v1/probability/games/{game_id}`: binding, capabilities and
  authority for each channel.
- `POST /api/v1/probability/evaluate`: request `ASP_MARKET_PROBABILITY_REQUEST_V1`.
  - One leg evaluates a single market.
  - Two or more legs evaluate a same-game per-draw intersection, displayed as
    `PAIRED_DRAW_INTERSECTION`.
- `POST /api/v1/probability/cross-game`: always returns 422
  `CROSS_GAME_CALIBRATION_NOT_VALIDATED`. The same code is returned for any
  `crossGameMode` other than `REJECT`.
- `GET /api/v1/command-center/assessments?date=` returns, for Daily Decisions:
  - model masses beside every stored quote side;
  - edge and expected return only for a current, governed, two-sided quote;
  - same-game Parlay Lab examples, built only from quoted lines;
  - NBA and CFB research comparisons.

## Parlay rules

- **Price required for any assessment.** Implied probability, edge and
  expected return require a recorded sportsbook combined quote. None is
  stored, so the Parlay Lab shows: "Model probability available. Sportsbook
  parlay price unavailable, so edge and expected return cannot be assessed."
- **No synthesized prices.** A price multiplied from leg prices is never
  accepted.
- **Nothing is recorded.** No parlay decision, position or ledger entry is
  created.
- **No cross-game output.** No cross-game probability, fair price, edge or
  expected return is served or displayed. The producer's independence
  convolution is not imported by any application module.
