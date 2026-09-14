"""AISP_NFL_PAPER_POLICY_V1 — the activated NFL virtual research policy.

WHAT THIS IS
------------
The $1,000 virtual NFL paper experiment, opened on the project owner's explicit
2026-09-14 approval. Virtual research only: no real-money wagering and no
sportsbook execution, ever.

It was prepared under this same module as ``..._PROPOSED`` with
``ACTIVATED = False``; the owner's approval is what changed, and the arithmetic
below did not.

WHY IT IS NOT A RENAMED MLB POLICY
----------------------------------
AISP_PRACTICAL_PREGAME_POLICY_V2_1 carries two empirical penalties that are
baseball facts, not policy shapes:

  * The 2.0-point starter penalty is calibrated on STARTING PITCHERS -- 88 live
    games, 4,122 observations, zero games ever confirmed before first pitch.
    There is no NFL analogue. A football team has no starting pitcher, and
    copying the number into NFL would assert an audit that was never run.
  * The 0.5-point lineup penalty is calibrated on MLB lineup xwOBA revisions
    (n=57, p95 0.000 points). NFL inactives are a different mechanism on a
    different clock, and that distribution says nothing about them.

Both are therefore ABSENT here rather than renamed, and what replaces them is
declared as uncalibrated (see ``UNCALIBRATED_GAPS``) instead of being given an
invented number. Quarter-Kelly and the exposure caps DO carry over, because
those are staking choices about a bankroll and contain no baseball.

THE TIE IS THE THING NFL ADDS
-----------------------------
Baseball has no ties. NFL does, the model prices them explicitly, and a policy
that ignores the tie is wrong in two separate places at once -- see
``moneyline_ev`` and ``no_vig_conditional`` below, which are deliberately
different functions because the two comparisons are NOT the same number.

Nothing here reads an outcome, calls a provider, or writes a row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Proposed identity. Distinct from every MLB policy version on purpose: a
#: shared name would let an MLB evaluator pick up NFL games, or an NFL result
#: be scored under a baseball contract.
POLICY_VERSION = "AISP_NFL_PAPER_POLICY_V1"
STAKING_VERSION = "AISP_QUARTER_KELLY_NFL_V1"

#: Activated on the project owner's explicit approval, 2026-09-14T16:50:39Z.
ACTIVATED = True

#: The proposed bankroll. Virtual, and separate from every MLB account.
STARTING_BANKROLL = 1000.00

#: Quarter Kelly, as in the MLB staking policy. This carries over because it is
#: a statement about bankroll risk, not about baseball.
KELLY_FRACTION = 0.25

#: Exposure caps as fractions of bankroll. NFL begins at the PENALISED cap, not
#: the full one: no NFL evidence penalty has been calibrated, so the policy
#: cannot claim the evidence quality that the higher cap is meant to reward.
POSITION_CAP_UNCALIBRATED = 0.005   # 0.5% -> $5.00 on $1,000
POSITION_CAP_IF_CALIBRATED = 0.01   # 1.0% -> $10.00, unavailable until calibrated

#: Same-game and daily exposure ceilings, as fractions of bankroll. Carried
#: from the prepared proposal unchanged. They bind on TOTAL exposure, so two
#: positions on one game cannot each take the per-position cap.
MAX_SAME_GAME_PERCENTAGE = 0.01     # 1.0% -> $10.00 across every leg of a game
MAX_DAILY_PERCENTAGE = 0.02         # 2.0% -> $20.00 across the whole day

#: Minimum adjusted edge, unchanged in shape from MLB (2.0 points) because it is
#: a threshold on a probability difference and carries no sport in it.
MIN_ADJUSTED_EDGE = 0.02

#: How the two-way NFL moneyline settles a tie. This is the market's rule, not
#: a choice AISP makes, and every number below depends on it.
TIE_TREATMENT = "STAKE_REFUNDED_PUSH"


@dataclass(frozen=True)
class TiedMarket:
    """One side of a two-way moneyline on an event that can end in a tie."""

    win_probability: float
    tie_probability: float
    decimal_odds: float

    @property
    def loss_probability(self) -> float:
        return max(0.0, 1.0 - self.win_probability - self.tie_probability)


def moneyline_ev(market: TiedMarket) -> float:
    """Expected value per unit staked at the OFFERED price.

    A tie refunds the stake, so it contributes exactly zero -- neither the
    profit of a win nor the loss of a loss::

        EV = pWin x (decimal - 1) - pLoss

    Treating the tie as a loss instead subtracts a `pTie` the market gives back.
    On the DEN at KC forecast that single mistake moves EV by 4.3 points, which
    is larger than the policy's entire 2.0-point qualification threshold.
    """

    return (market.win_probability * (market.decimal_odds - 1.0)
            - market.loss_probability)


def no_vig_conditional(away_decimal: float, home_decimal: float) -> tuple[float, float]:
    """No-vig probabilities from a two-way price pair, CONDITIONAL ON NO TIE.

    A refund-on-tie market has no tie outcome to price, so its two prices span
    "away wins given somebody wins". Normalising them yields a conditional
    probability, and comparing that against an UNCONDITIONAL model probability
    compares two different quantities -- which is why `model_conditional` exists,
    and why this function's result must never be called the offered-price edge.
    """

    away_implied = 1.0 / away_decimal
    home_implied = 1.0 / home_decimal
    overround = away_implied + home_implied
    return (away_implied / overround, home_implied / overround)


def model_conditional(win_probability: float, tie_probability: float) -> float:
    """The model's win probability on the market's own conditional basis."""

    decisive = 1.0 - tie_probability
    return win_probability / decisive if decisive > 0 else 0.0


def american_to_decimal(american: int) -> float:
    return 1.0 + (american / 100.0 if american > 0 else 100.0 / abs(american))


def kelly_fraction(market: TiedMarket) -> float:
    """Full-Kelly fraction of bankroll on a market that can push.

        f* = EV / [(decimal - 1) x (pWin + pLoss)]

    The `(pWin + pLoss)` term is what a push requires and is easy to drop. On a
    two-outcome market it is 1 and vanishes; here the bet only resolves on the
    decisive branch, so the growth-optimal fraction is conditioned on the game
    being decided. Omitting it understates the stake -- at +124 on the DEN at KC
    probabilities it gives 6.69% where the correct quarter-Kelly is 6.99%.
    """

    profit = market.decimal_odds - 1.0
    decisive = market.win_probability + market.loss_probability
    if profit <= 0 or decisive <= 0:
        return 0.0
    return max(0.0, moneyline_ev(market) / (profit * decisive))


def proposed_stake(market: TiedMarket, *, bankroll: float = STARTING_BANKROLL,
                   cap: float = POSITION_CAP_UNCALIBRATED) -> float:
    """What the proposal WOULD stake. It stakes nothing while ACTIVATED is False."""

    return round(min(kelly_fraction(market) * KELLY_FRACTION, cap) * bankroll, 2)


#: Gaps that have no calibrated NFL number. Declared rather than filled in: an
#: invented penalty is worse than a named absence, because it looks audited.
UNCALIBRATED_GAPS: tuple[dict[str, Any], ...] = (
    {
        "gap": "INACTIVES_AND_INJURY_STATUS",
        "detail": (
            "NFL inactives are declared 90 minutes before kickoff. AISP holds no "
            "NFL injury or inactive feed at all, so the policy cannot observe the "
            "evidence state, let alone penalise it. The MLB starter and lineup "
            "penalties measure a different sport's mechanism and are not "
            "transferable."
        ),
        "blocks_activation": False,
        "consequence": "Position cap stays at the uncalibrated 0.5%.",
    },
    {
        "gap": "TIE_RATE_CALIBRATION",
        "detail": (
            "Measured, not suspected. On the producer's own chronological "
            "assessment population the final-score model predicts ties at 3.07% "
            "against 0.28% observed across 1,408 games (4 tied finals), and 0 "
            "observed in the 570 held-out 2023-2024 games. The cause is "
            "identified: the tie mass is created entirely by stochastic rounding "
            "of a continuous latent margin -- 0 of 10,000 DEN at KC draws were "
            "tied before discretisation -- and the resulting integer margin "
            "distribution is smooth, so it cannot reproduce the hole at zero "
            "that overtime puts in real NFL margins, nor their spikes at 3 and "
            "7. This is a calibration gap in the margin law, NOT a coding "
            "defect, and NOT something an overtime stage fixes: the fitted "
            "targets are already post-overtime final scores."
        ),
        "blocks_activation": False,
        "consequence": (
            "EV must be reported with the tie mass shown beside it. The tie "
            "treatment itself is settled -- a push is a push -- but roughly 4 "
            "points of probability sit on an outcome that almost never occurs, "
            "and where that mass belongs is not determined. Until it is, the "
            "win and loss probabilities are both understated by an unknown "
            "split of it, so an edge computed from them carries that width."
        ),
    },
    {
        "gap": "NUMERICAL_FEATURE_FRESHNESS",
        "detail": (
            "Week 1 forecasts carry 44 missing numerical features -- every "
            "season-to-date rate a Week 1 game cannot have. Readiness scores 78 "
            "against a threshold of 50, so the forecast is READY, but ready is "
            "not the same as informed, and the missing set shrinks as the season "
            "produces data."
        ),
        "blocks_activation": False,
        "consequence": "A Week 1 position is the least informed one the season will offer.",
    },
    {
        "gap": "PRICE_FRESHNESS",
        "detail": (
            "No NFL pregame quote lane is funded, so the newest stored NFL "
            "moneyline is the one captured on 2026-08-29. A price older than the "
            "forecast being compared against it measures the gap between two "
            "clocks, not an edge."
        ),
        "blocks_activation": True,
        "consequence": (
            "Any qualification computed against a stale price is disqualified by "
            "the existing 240-minute freshness rule before it reaches a stake."
        ),
    },
)


#: Producer classification and consumer authority, kept apart.
AUTHORITY = {
    "producer_classification": "RESEARCH_SHADOW",
    "producer_note": (
        "Model Studio publishes NFL_M1_XGBOOST as research output. That is the "
        "producer's own classification of its forecasts and it is UNCHANGED by "
        "this activation: NOT_PROMOTED, NOT_EXECUTABLE, researchShadowOnly, and "
        "executionAllowed / modelPromotionAllowed / parlaysAllowed all false."
    ),
    "consumer_paper_authority": "GRANTED",
    "consumer_granted_utc": "2026-09-14T16:50:39+00:00",
    "consumer_note": (
        "The project owner granted AISP a virtual paper experiment on those "
        "forecasts. This is a CONSUMER decision and it changes nothing about the "
        "producer's classification -- the two were always separate, and a paper "
        "position is not an execution."
    ),
    "real_money": "NEVER",
}


__all__ = [
    "ACTIVATED",
    "AUTHORITY",
    "KELLY_FRACTION",
    "MAX_DAILY_PERCENTAGE",
    "MAX_SAME_GAME_PERCENTAGE",
    "MIN_ADJUSTED_EDGE",
    "POLICY_VERSION",
    "POSITION_CAP_IF_CALIBRATED",
    "POSITION_CAP_UNCALIBRATED",
    "STAKING_VERSION",
    "STARTING_BANKROLL",
    "TIE_TREATMENT",
    "TiedMarket",
    "UNCALIBRATED_GAPS",
    "american_to_decimal",
    "kelly_fraction",
    "model_conditional",
    "moneyline_ev",
    "no_vig_conditional",
    "proposed_stake",
]
