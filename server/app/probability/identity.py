"""The exact producer artifacts this consumer integration was verified against.

``market_probabilities.py`` in this package is a byte-identical copy of the
Model Studio module at the producer commit below (its SHA-256 is pinned here
and re-checked by the tests). Nothing in AISP imports the Model Studio
checkout at runtime.

Cross-game disposition
----------------------
The frozen cross-game independence study found that no examined category met
every calibration, reliability and precision criterion. AISP therefore never
serves a cross-game probability, fair price, edge or expected return, and the
producer's optional ``INDEPENDENCE_ASSUMPTION`` convolution is not reachable
from any API route or page. Requests fail closed with
``CROSS_GAME_CALIBRATION_NOT_VALIDATED``.
"""

from __future__ import annotations

PRODUCER_REPOSITORY = "Gearhj/asp-model-studio"
PRODUCER_CONTRACT_BRANCH = "codex/aisp-market-probability-contract-v1"
PRODUCER_CONTRACT_SHA = "e0d1638626ad33a7a794d4f94882acf363c78652"
PRODUCER_IMPLEMENTATION_SHA = "bcca9c9dce7c2804ae538671e646797715c05291"
PRODUCER_HANDOFF_SHA256 = "135db1a8c146a3bed0411c091fbb9876baecf87bce364e9d32eed884112d5a36"
#: SHA-256 of server/sports_shared/market_probabilities.py at the producer SHA.
PRODUCER_MODULE_SHA256 = "2e7673d1ec4e5868cc27f5b152bf83cf75a91787e676f61ce0c24c8b462e1af1"
GOLDEN_VECTORS_SHA256 = "75cdd680eecf62ce2bc96c474d271abac98eaef303e8cf90787b666773aee3f0"
REQUEST_SCHEMA_SHA256 = "12b032d48952d10d52add8241a45f5cdae2b500bd0895ce2cc5919b8867aa2dc"
RESPONSE_SCHEMA_SHA256 = "8e9b2e40edf3cefaa9408a5b51e508956b796fb81047767be480fbcfce95c39c"
CAPABILITY_MATRIX_SHA256 = "5df41af5085f42ec106341f5aae4d7de8efa09919fcfab2f177d2b8522019a00"

CROSS_GAME_STUDY_BRANCH = "codex/cross-game-independence-validation-v1"
CROSS_GAME_STUDY_SHA = "d5702cb6df25bf703c1c598fb7a3a4085531c703"
CROSS_GAME_RESULTS_SHA256 = "fafc0b9bd9997e65fa6db209bceb6dacb91943528e136ed23603daf922ffef24"

CROSS_GAME_CALIBRATION_NOT_VALIDATED = "CROSS_GAME_CALIBRATION_NOT_VALIDATED"
CROSS_GAME_MESSAGE = (
    "Cross-game combinations are not yet validated. AISP is collecting prospective "
    "calibration and dependency evidence."
)

#: The consumer's display name for the producer's per-draw same-game method
#: (``PER_DRAW_SAME_GAME_INTERSECTION`` in the contract response).
SAME_GAME_DISPLAY_METHOD = "PAIRED_DRAW_INTERSECTION"
PARLAY_PRICE_UNAVAILABLE = (
    "Model probability available. Sportsbook parlay price unavailable, so edge and "
    "expected return cannot be assessed."
)
#: The only parlay price provenance AISP would ever compare against. Nothing
#: stores one today; a price multiplied from leg prices never qualifies.
RECORDED_COMBINED_QUOTE = "RECORDED_SPORTSBOOK_COMBINED_QUOTE"
