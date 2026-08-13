"""Pure Gate 3 model-development primitives.

No module in this package reads the database, calls providers, or mutates an
operational forecast.  ``run_temporal_model_development`` is the integrated
entry point; lower-level contracts remain public for focused evaluation.
"""

from .ablation import (
    AblationSpec,
    apply_ablation,
    feature_family_ablations,
    lifecycle_ablations,
    lifecycle_cohorts,
    recent_window_ablations,
    single_feature_ablations,
)
from .betting import BettingThresholdResult, simulate_betting_thresholds
from .calibration import (
    CalibrationSelection,
    IdentityCalibrator,
    IsotonicCalibrator,
    PlattCalibrator,
    select_probability_calibrator,
)
from .diagnostics import OODDetector, OODReport
from .estimators import (
    EmpiricalBayesTeamClassifier,
    HomePriorClassifier,
    LimitedXGBoostClassifier,
    NegativeBinomialRunRegressor,
    PoissonRunRegressor,
    RatingClassifier,
    RegularizedLogisticClassifier,
    RunStrengthClassifier,
)
from .metrics import (
    ClassificationMetrics,
    PairedBootstrapDelta,
    brier_score,
    calibration_intercept_slope,
    classification_metrics,
    expected_calibration_error,
    paired_block_bootstrap_delta,
    probability_log_loss,
    rmse,
)
from .pipeline import (
    FittedModelBundle,
    FoldFitAudit,
    ModelingConfig,
    OOFGenerationResult,
    PredictionBundle,
    StackFoldAudit,
    StackOOFPredictionRow,
    TemporalModelDevelopmentResult,
    run_temporal_model_development,
)
from .preprocessing import FoldPreprocessor, PreprocessingAudit
from .schema import ExtractedFeatures, FeatureSpec, FrozenFeatureSchema, GATE3_FEATURE_SCHEMA
from .stacking import NonnegativeStacker, OOFPredictionRow, StackingFit
from .temporal import (
    DevelopmentHoldoutSplit,
    FinalHoldoutReport,
    LabeledGame,
    LockedHoldout,
    RollingOriginFold,
    RunHoldoutMetrics,
    UnlabeledGame,
    date_group_chronological_holdout,
    rolling_origin_folds,
)

__all__ = [name for name in globals() if not name.startswith("_")]
