"""Read-only Prediction Market Observer (Polymarket + Kalshi).

A sport-neutral market-observation and research layer. It discovers, maps,
normalizes, stores, compares and displays prediction-market data alongside the
existing AISP forecast and sportsbook layers. It NEVER trades, never signs an
order, never mutates a forecast or the official experiment, and never treats a
prediction-market price as a model input. All provider access is off by default
and confined to scheduled/admin jobs; public reads touch only persisted rows.
"""
