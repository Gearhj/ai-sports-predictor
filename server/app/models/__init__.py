"""Public ORM model exports."""

from app.models.entities import (
    AnalystRating, AnalystTier, AuditEvent, AvailabilityEvent, Batter, BattingFeature,
    BullpenAvailabilityObservation, BullpenFeature,
    CommunityPost, CommunityPostStatus, CommunityReaction, DataQualityCheck,
    Entitlement, EntitlementKind, ExternalIdMapping, FeatureVersion, Forecast,
    ExperimentDecisionPhase,
    ForecastComponent, ForecastLifecycle, ForecastMovementEvidence, ForecastStatus,
    ForecastTrack,
    Game, GameContext, GameFeatureSnapshot, GameRefreshState, GameStateObservation,
    GameFinalOutcome, GameStatus, GameTeam, HistoricalEvidenceStateAttestation,
    HistoricalAcquisitionStage, HistoricalEventPartition,
    HistoricalFeatureMaterialization, HistoricalProviderAccessAuthorization,
    HistoricalRequestAttempt, HistoricalSourceOwnerApproval, HistoricalSourceVersion,
    HistoryBackfillCheckpoint, SourceOwnerApproval, ProviderAccessAuthorization,
    ExistingEvidenceDerivedUseDecision, ExistingEvidenceUseDecision,
    DerivedEvidenceUseDecision,
    IngestionRun, JobRun, LeaderboardEntry, LiveShadowPollState,
    LeaderboardPeriod, LeaderboardSnapshot, League, Market, MarketComparison,
    MarketOutcome, MarketType, ModelEvaluation, ModelStatus, ModelVersion,
    OpenDataDownloadManifest, OpenDataIdentityMapping,
    OddsSnapshot, OddsStatus,
    OfficialAspPosition, ParkFactorVersion, PitchEvent, Pitcher, PitchingFeature,
    Player, PlayerGameHistoryObservation, PlayerTalentEstimate, ProviderRequestLedger,
    PlayerGameStat, PlayerTeamHistory, PositionResult, PositionSettlement,
    PositionStatus, PrivateLeague, PrivateLeagueMember, Roster,
    ProspectiveEvaluationClaim, ProspectiveEvaluationClaimBinding,
    ProspectiveEvaluationGameAssignment, ProspectiveEvaluationLabelAccess,
    ProspectiveEvaluationPlan,
    RosterAvailabilityObservation, RunStatus, Season, SourceFreshnessPolicy, Sport,
    NflFinalObservation,
    ShadowThresholdSelection,
    ResearchForecastEvaluation,
    ResearchSupplement,
    ShadowBoardDecision, ShadowDecisionSettlement, ShadowForecast, ShadowForecastBundle,
    ShadowExperiment, ShadowExperimentDecision,
    ShadowQuoteCapture, ShadowRescore,
    Sportsbook, StarterObservation, StatcastPlayerObservation, Team, TeamFeature,
    TeamGameHistoryObservation, TeamGameStat, TeamRatingObservation,
    TrainingDatasetManifest, User, UserRole, UserStatus,
    UserVirtualBankroll, Venue, VirtualPosition, WeatherSnapshot, LineupEntry,
    LineupObservation,
)
from app.models.prediction_market import (
    PredictionMarketAlert,
    PredictionMarketConsensusSnapshot,
    PredictionMarketContract,
    PredictionMarketEvent,
    PredictionMarketMapping,
    PredictionMarketOrderbookSnapshot,
    PredictionMarketSnapshot,
    PredictionMarketSemanticTemplate,
)

# NBA intake tables live in their own module and their own namespace. They are
# imported here so the ORM registers them; nothing in the MLB/NFL path reads
# them, and every row carries data_mode.
from app.models.nba import (  # noqa: E402,F401
    NbaGame,
    NbaPlayer,
    NbaPlayerGameStat,
    NbaRosterMembership,
    NbaSourcePayload,
    NbaTeam,
    NbaTeamGameStat,
)

# Season-scoped paper-bankroll accounts. Additive: the experiment, decision,
# position and settlement tables are unchanged, and membership links an era to
# its season account without rewriting the append-only era row.
from app.models.experiment_accounts import (  # noqa: E402,F401
    AccountStatus,
    ExperimentAccount,
    ExperimentAccountFunding,
    ExperimentAccountMembership,
    FundingKind,
    MembershipRole,
)
from app.models.parlay import (  # noqa: E402,F401
    FUNDING_RESEARCH_ONLY,
    FUNDING_SEASON_ACCOUNT,
    ParlayTicket,
    ParlayTicketLeg,
)
from app.models.totals_attachment import (  # noqa: E402,F401
    TotalsIndexAttachment,
)
# Observed scoreboard state. Append-only and display-only: separate from
# game_state_observations so a two-minute presentation refresh never enters the
# evidence stream the scorer and label gate read.
from app.models.scoreboard import (  # noqa: E402,F401
    SCOREBOARD_STATUSES,
    GameScoreboardObservation,
)

__all__ = [name for name in globals() if not name.startswith("_")]
