"""Frozen initial canonical AI Sports Predictor persistence schema.

Revision ID: 20260812_0001
Revises:
Create Date: 2026-08-12

This revision intentionally contains explicit Alembic operations. It must remain
stable as ORM models evolve; later schema changes belong in later revisions.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260812_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('external_id_mappings',
    sa.Column('entity_type', sa.String(length=60), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('external_id', sa.String(length=255), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('entity_type', 'source', 'external_id', name='uq_external_source_id')
    )
    op.create_index('ix_external_entity', 'external_id_mappings', ['entity_type', 'entity_id'], unique=False)
    op.create_table('feature_versions',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('version', sa.String(length=80), nullable=False),
    sa.Column('schema_hash', sa.String(length=64), nullable=False),
    sa.Column('definition', sa.JSON(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'version', name='uq_feature_name_version')
    )
    op.create_table('ingestion_runs',
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('job_name', sa.String(length=120), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', name='runstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('records_processed', sa.Integer(), nullable=False),
    sa.Column('warnings', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('raw_reference', sa.String(length=500), nullable=True),
    sa.Column('schema_version', sa.String(length=80), nullable=True),
    sa.Column('source_metadata', sa.JSON(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ingestion_runs_source'), 'ingestion_runs', ['source'], unique=False)
    op.create_table('leaderboard_periods',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sports',
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('sportsbooks',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('region', sa.String(length=20), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=100), nullable=False),
    sa.Column('role', sa.Enum('ADMIN', 'MEMBER', name='userrole', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'DISABLED', 'PENDING', name='userstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('venues',
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('region', sa.String(length=80), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('latitude', sa.Float(), nullable=True),
    sa.Column('longitude', sa.Float(), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('analyst_ratings',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('tier', sa.Enum('UNRATED', 'PROVISIONAL', 'ESTABLISHED', name='analysttier', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('rating', sa.Float(), nullable=True),
    sa.Column('sample_size', sa.Integer(), nullable=False),
    sa.Column('dimensions', sa.JSON(), nullable=True),
    sa.Column('calculated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_analyst_ratings_user_id'), 'analyst_ratings', ['user_id'], unique=False)
    op.create_table('audit_events',
    sa.Column('actor_user_id', sa.Uuid(), nullable=True),
    sa.Column('action', sa.String(length=160), nullable=False),
    sa.Column('entity_type', sa.String(length=80), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('metadata_json', sa.JSON(), nullable=True),
    sa.Column('ip_address', sa.String(length=64), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_action'), 'audit_events', ['action'], unique=False)
    op.create_index(op.f('ix_audit_events_actor_user_id'), 'audit_events', ['actor_user_id'], unique=False)
    op.create_table('data_quality_checks',
    sa.Column('ingestion_run_id', sa.Uuid(), nullable=True),
    sa.Column('check_name', sa.String(length=160), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['ingestion_run_id'], ['ingestion_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_data_quality_checks_ingestion_run_id'), 'data_quality_checks', ['ingestion_run_id'], unique=False)
    op.create_table('entitlements',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.Enum('MEMBER', 'ADMIN', 'PREMIUM', name='entitlementkind', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('subscription_provider', sa.String(length=80), nullable=True),
    sa.Column('external_membership_id', sa.String(length=255), nullable=True),
    sa.Column('entitlement_status', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kind', name='uq_entitlement_user_kind')
    )
    op.create_index(op.f('ix_entitlements_user_id'), 'entitlements', ['user_id'], unique=False)
    op.create_table('job_runs',
    sa.Column('job_name', sa.String(length=120), nullable=False),
    sa.Column('status', sa.Enum('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'SKIPPED', name='runstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('requested_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('parameters', sa.JSON(), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_job_runs_job_name'), 'job_runs', ['job_name'], unique=False)
    op.create_table('leaderboard_snapshots',
    sa.Column('period_id', sa.Uuid(), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('entries', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['period_id'], ['leaderboard_periods.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_leaderboard_snapshots_captured_at'), 'leaderboard_snapshots', ['captured_at'], unique=False)
    op.create_index(op.f('ix_leaderboard_snapshots_period_id'), 'leaderboard_snapshots', ['period_id'], unique=False)
    op.create_table('leagues',
    sa.Column('sport_id', sa.Uuid(), nullable=False),
    sa.Column('code', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['sport_id'], ['sports.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_index(op.f('ix_leagues_sport_id'), 'leagues', ['sport_id'], unique=False)
    op.create_table('model_versions',
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('version', sa.String(length=80), nullable=False),
    sa.Column('sport_id', sa.Uuid(), nullable=True),
    sa.Column('feature_version_id', sa.Uuid(), nullable=True),
    sa.Column('algorithm', sa.String(length=160), nullable=False),
    sa.Column('status', sa.Enum('DEVELOPMENT', 'CHALLENGER', 'CHAMPION', 'RETIRED', name='modelstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('training_window', sa.JSON(), nullable=True),
    sa.Column('hyperparameters', sa.JSON(), nullable=True),
    sa.Column('artifact_uri', sa.String(length=500), nullable=True),
    sa.Column('artifact_checksum', sa.String(length=64), nullable=True),
    sa.Column('parameters', sa.JSON(), nullable=True),
    sa.Column('metrics', sa.JSON(), nullable=True),
    sa.Column('trained_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['feature_version_id'], ['feature_versions.id'], ),
    sa.ForeignKeyConstraint(['sport_id'], ['sports.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'version', name='uq_model_name_version')
    )
    op.create_index(op.f('ix_model_versions_feature_version_id'), 'model_versions', ['feature_version_id'], unique=False)
    op.create_index(op.f('ix_model_versions_sport_id'), 'model_versions', ['sport_id'], unique=False)
    op.create_table('private_leagues',
    sa.Column('owner_user_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('join_code_hash', sa.String(length=255), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_private_leagues_owner_user_id'), 'private_leagues', ['owner_user_id'], unique=False)
    op.create_table('user_virtual_bankrolls',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('starting_balance', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('current_balance', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('peak_balance', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('private_league_members',
    sa.Column('league_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['private_leagues.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'user_id', name='uq_private_league_member')
    )
    op.create_index(op.f('ix_private_league_members_league_id'), 'private_league_members', ['league_id'], unique=False)
    op.create_index(op.f('ix_private_league_members_user_id'), 'private_league_members', ['user_id'], unique=False)
    op.create_table('seasons',
    sa.Column('league_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'name', name='uq_season_league_name')
    )
    op.create_index(op.f('ix_seasons_league_id'), 'seasons', ['league_id'], unique=False)
    op.create_table('teams',
    sa.Column('league_id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('abbreviation', sa.String(length=12), nullable=False),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('league_id', 'abbreviation', name='uq_team_league_abbreviation')
    )
    op.create_index(op.f('ix_teams_league_id'), 'teams', ['league_id'], unique=False)
    op.create_table('games',
    sa.Column('league_id', sa.Uuid(), nullable=False),
    sa.Column('season_id', sa.Uuid(), nullable=True),
    sa.Column('venue_id', sa.Uuid(), nullable=True),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('SCHEDULED', 'LIVE', 'FINAL', 'POSTPONED', 'CANCELED', name='gamestatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('home_score', sa.Integer(), nullable=True),
    sa.Column('away_score', sa.Integer(), nullable=True),
    sa.Column('scheduled_innings', sa.Integer(), nullable=True),
    sa.Column('final_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['league_id'], ['leagues.id'], ),
    sa.ForeignKeyConstraint(['season_id'], ['seasons.id'], ),
    sa.ForeignKeyConstraint(['venue_id'], ['venues.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_games_league_id'), 'games', ['league_id'], unique=False)
    op.create_index(op.f('ix_games_season_id'), 'games', ['season_id'], unique=False)
    op.create_index(op.f('ix_games_starts_at'), 'games', ['starts_at'], unique=False)
    op.create_index(op.f('ix_games_venue_id'), 'games', ['venue_id'], unique=False)
    op.create_table('leaderboard_entries',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('season_id', sa.Uuid(), nullable=True),
    sa.Column('metric', sa.String(length=80), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('calculated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['season_id'], ['seasons.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'season_id', 'metric', 'calculated_at', name='uq_leaderboard_measure')
    )
    op.create_index(op.f('ix_leaderboard_entries_season_id'), 'leaderboard_entries', ['season_id'], unique=False)
    op.create_index(op.f('ix_leaderboard_entries_user_id'), 'leaderboard_entries', ['user_id'], unique=False)
    op.create_table('players',
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('full_name', sa.String(length=160), nullable=False),
    sa.Column('position', sa.String(length=32), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_players_team_id'), 'players', ['team_id'], unique=False)
    op.create_table('batters',
    sa.Column('player_id', sa.Uuid(), nullable=False),
    sa.Column('bats', sa.String(length=8), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('player_id')
    )
    op.create_table('batting_features',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('feature_version_id', sa.Uuid(), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['feature_version_id'], ['feature_versions.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_batting_features_game_id'), 'batting_features', ['game_id'], unique=False)
    op.create_index(op.f('ix_batting_features_team_id'), 'batting_features', ['team_id'], unique=False)
    op.create_table('bullpen_features',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('feature_version_id', sa.Uuid(), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['feature_version_id'], ['feature_versions.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bullpen_features_game_id'), 'bullpen_features', ['game_id'], unique=False)
    op.create_index(op.f('ix_bullpen_features_team_id'), 'bullpen_features', ['team_id'], unique=False)
    op.create_table('community_posts',
    sa.Column('author_user_id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'PUBLISHED', 'HIDDEN', name='communitypoststatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['author_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_community_posts_author_user_id'), 'community_posts', ['author_user_id'], unique=False)
    op.create_index(op.f('ix_community_posts_game_id'), 'community_posts', ['game_id'], unique=False)
    op.create_table('forecasts',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('model_version_id', sa.Uuid(), nullable=False),
    sa.Column('feature_version_id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'PUBLISHED', 'SUPERSEDED', name='forecaststatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('lifecycle', sa.Enum('MORNING', 'EARLY', 'PREGAME', 'FINAL', name='forecastlifecycle', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('home_win_probability', sa.Float(), nullable=False),
    sa.Column('away_win_probability', sa.Float(), nullable=False),
    sa.Column('expected_home_runs', sa.Float(), nullable=True),
    sa.Column('expected_away_runs', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('readiness_score', sa.Float(), nullable=True),
    sa.Column('fair_home_moneyline', sa.Integer(), nullable=True),
    sa.Column('fair_away_moneyline', sa.Integer(), nullable=True),
    sa.Column('expected_total_runs', sa.Float(), nullable=True),
    sa.Column('expected_run_difference', sa.Float(), nullable=True),
    sa.Column('uncertainty', sa.Float(), nullable=True),
    sa.Column('readiness_components', sa.JSON(), nullable=True),
    sa.Column('model_drivers', sa.JSON(), nullable=True),
    sa.Column('feature_snapshot_reference', sa.String(length=500), nullable=True),
    sa.Column('market_snapshot_reference', sa.String(length=500), nullable=True),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('simulation_summary', sa.JSON(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['feature_version_id'], ['feature_versions.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('game_id', 'version', name='uq_forecast_game_version')
    )
    op.create_index(op.f('ix_forecasts_game_id'), 'forecasts', ['game_id'], unique=False)
    op.create_index(op.f('ix_forecasts_generated_at'), 'forecasts', ['generated_at'], unique=False)
    op.create_table('game_contexts',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('feature_version_id', sa.Uuid(), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['feature_version_id'], ['feature_versions.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_game_contexts_game_id'), 'game_contexts', ['game_id'], unique=False)
    op.create_table('game_teams',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=False),
    sa.Column('is_home', sa.Boolean(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('probable_pitcher_id', sa.Uuid(), nullable=True),
    sa.Column('pitcher_confirmed', sa.Boolean(), nullable=False),
    sa.Column('pitcher_status_observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('record_wins', sa.Integer(), nullable=True),
    sa.Column('record_losses', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['probable_pitcher_id'], ['players.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('game_id', 'is_home', name='uq_game_home_away'),
    sa.UniqueConstraint('game_id', 'team_id', name='uq_game_team')
    )
    op.create_table('markets',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('market_type', sa.Enum('MONEYLINE', 'SPREAD', 'TOTAL', name='markettype', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('period', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('game_id', 'market_type', 'period', name='uq_game_market_period')
    )
    op.create_index(op.f('ix_markets_game_id'), 'markets', ['game_id'], unique=False)
    op.create_table('pitch_events',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('pitcher_id', sa.Uuid(), nullable=True),
    sa.Column('batter_id', sa.Uuid(), nullable=True),
    sa.Column('sequence_number', sa.Integer(), nullable=False),
    sa.Column('event_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['batter_id'], ['players.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['pitcher_id'], ['players.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('game_id', 'sequence_number', name='uq_pitch_event_sequence')
    )
    op.create_index(op.f('ix_pitch_events_available_as_of'), 'pitch_events', ['available_as_of'], unique=False)
    op.create_index(op.f('ix_pitch_events_batter_id'), 'pitch_events', ['batter_id'], unique=False)
    op.create_index(op.f('ix_pitch_events_game_id'), 'pitch_events', ['game_id'], unique=False)
    op.create_index(op.f('ix_pitch_events_pitcher_id'), 'pitch_events', ['pitcher_id'], unique=False)
    op.create_table('pitchers',
    sa.Column('player_id', sa.Uuid(), nullable=False),
    sa.Column('throws', sa.String(length=8), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('player_id')
    )
    op.create_table('pitching_features',
    sa.Column('player_id', sa.Uuid(), nullable=True),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('feature_version_id', sa.Uuid(), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['feature_version_id'], ['feature_versions.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pitching_features_game_id'), 'pitching_features', ['game_id'], unique=False)
    op.create_index(op.f('ix_pitching_features_player_id'), 'pitching_features', ['player_id'], unique=False)
    op.create_index(op.f('ix_pitching_features_team_id'), 'pitching_features', ['team_id'], unique=False)
    op.create_table('player_game_stats',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('player_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('game_id', 'player_id', 'available_as_of', name='uq_player_game_stats_asof')
    )
    op.create_index(op.f('ix_player_game_stats_available_as_of'), 'player_game_stats', ['available_as_of'], unique=False)
    op.create_index(op.f('ix_player_game_stats_game_id'), 'player_game_stats', ['game_id'], unique=False)
    op.create_index(op.f('ix_player_game_stats_player_id'), 'player_game_stats', ['player_id'], unique=False)
    op.create_index(op.f('ix_player_game_stats_team_id'), 'player_game_stats', ['team_id'], unique=False)
    op.create_table('player_team_history',
    sa.Column('player_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_player_team_history_player_id'), 'player_team_history', ['player_id'], unique=False)
    op.create_index(op.f('ix_player_team_history_team_id'), 'player_team_history', ['team_id'], unique=False)
    op.create_table('rosters',
    sa.Column('season_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=False),
    sa.Column('player_id', sa.Uuid(), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.ForeignKeyConstraint(['season_id'], ['seasons.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('season_id', 'team_id', 'player_id', 'available_as_of', name='uq_roster_asof')
    )
    op.create_index(op.f('ix_rosters_player_id'), 'rosters', ['player_id'], unique=False)
    op.create_index(op.f('ix_rosters_season_id'), 'rosters', ['season_id'], unique=False)
    op.create_index(op.f('ix_rosters_team_id'), 'rosters', ['team_id'], unique=False)
    op.create_table('team_features',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=True),
    sa.Column('feature_version_id', sa.Uuid(), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['feature_version_id'], ['feature_versions.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_team_features_game_id'), 'team_features', ['game_id'], unique=False)
    op.create_index(op.f('ix_team_features_team_id'), 'team_features', ['team_id'], unique=False)
    op.create_table('team_game_stats',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('team_id', sa.Uuid(), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('values', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('game_id', 'team_id', 'available_as_of', name='uq_team_game_stats_asof')
    )
    op.create_index(op.f('ix_team_game_stats_available_as_of'), 'team_game_stats', ['available_as_of'], unique=False)
    op.create_index(op.f('ix_team_game_stats_game_id'), 'team_game_stats', ['game_id'], unique=False)
    op.create_index(op.f('ix_team_game_stats_team_id'), 'team_game_stats', ['team_id'], unique=False)
    op.create_table('weather_snapshots',
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('temperature_f', sa.Float(), nullable=True),
    sa.Column('wind_mph', sa.Float(), nullable=True),
    sa.Column('wind_direction', sa.String(length=32), nullable=True),
    sa.Column('precipitation_probability', sa.Float(), nullable=True),
    sa.Column('raw_payload', sa.JSON(), nullable=True),
    sa.Column('payload_checksum', sa.String(length=64), nullable=True),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_weather_snapshots_game_id'), 'weather_snapshots', ['game_id'], unique=False)
    op.create_index(op.f('ix_weather_snapshots_observed_at'), 'weather_snapshots', ['observed_at'], unique=False)
    op.create_table('community_reactions',
    sa.Column('post_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('reaction', sa.String(length=32), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['post_id'], ['community_posts.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('post_id', 'user_id', 'reaction', name='uq_community_reaction')
    )
    op.create_table('forecast_components',
    sa.Column('forecast_id', sa.Uuid(), nullable=False),
    sa.Column('component_name', sa.String(length=120), nullable=False),
    sa.Column('weight', sa.Float(), nullable=True),
    sa.Column('output', sa.JSON(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['forecast_id'], ['forecasts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('forecast_id', 'component_name', name='uq_forecast_component')
    )
    op.create_index(op.f('ix_forecast_components_forecast_id'), 'forecast_components', ['forecast_id'], unique=False)
    op.create_table('market_outcomes',
    sa.Column('market_id', sa.Uuid(), nullable=False),
    sa.Column('outcome_key', sa.String(length=80), nullable=False),
    sa.Column('label', sa.String(length=160), nullable=False),
    sa.Column('participant_team_id', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['market_id'], ['markets.id'], ),
    sa.ForeignKeyConstraint(['participant_team_id'], ['teams.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('market_id', 'outcome_key', name='uq_market_outcome_key')
    )
    op.create_index(op.f('ix_market_outcomes_market_id'), 'market_outcomes', ['market_id'], unique=False)
    op.create_table('market_comparisons',
    sa.Column('forecast_id', sa.Uuid(), nullable=False),
    sa.Column('market_outcome_id', sa.Uuid(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('asp_probability', sa.Float(), nullable=False),
    sa.Column('market_no_vig_probability', sa.Float(), nullable=True),
    sa.Column('edge', sa.Float(), nullable=True),
    sa.Column('best_decimal_odds', sa.Float(), nullable=True),
    sa.Column('consensus_decimal_odds', sa.Float(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['forecast_id'], ['forecasts.id'], ),
    sa.ForeignKeyConstraint(['market_outcome_id'], ['market_outcomes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_market_comparisons_forecast_id'), 'market_comparisons', ['forecast_id'], unique=False)
    op.create_table('odds_snapshots',
    sa.Column('market_outcome_id', sa.Uuid(), nullable=False),
    sa.Column('sportsbook_id', sa.Uuid(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('available_as_of', sa.DateTime(timezone=True), nullable=False),
    sa.Column('american_odds', sa.Integer(), nullable=True),
    sa.Column('decimal_odds', sa.Float(), nullable=False),
    sa.Column('point', sa.Float(), nullable=True),
    sa.Column('implied_probability', sa.Float(), nullable=True),
    sa.Column('status', sa.Enum('OPEN', 'SUSPENDED', 'CLOSED', name='oddsstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('is_closing', sa.Boolean(), nullable=False),
    sa.Column('raw_payload', sa.JSON(), nullable=True),
    sa.Column('payload_checksum', sa.String(length=64), nullable=True),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('source_event_id', sa.String(length=255), nullable=True),
    sa.Column('source_mode', sa.String(length=16), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['market_outcome_id'], ['market_outcomes.id'], ),
    sa.ForeignKeyConstraint(['sportsbook_id'], ['sportsbooks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_odds_snapshots_market_outcome_id'), 'odds_snapshots', ['market_outcome_id'], unique=False)
    op.create_index(op.f('ix_odds_snapshots_observed_at'), 'odds_snapshots', ['observed_at'], unique=False)
    op.create_index(op.f('ix_odds_snapshots_source_event_id'), 'odds_snapshots', ['source_event_id'], unique=False)
    op.create_index(op.f('ix_odds_snapshots_sportsbook_id'), 'odds_snapshots', ['sportsbook_id'], unique=False)
    op.create_table('official_asp_positions',
    sa.Column('user_id', sa.Uuid(), nullable=True),
    sa.Column('forecast_id', sa.Uuid(), nullable=True),
    sa.Column('market_outcome_id', sa.Uuid(), nullable=False),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stake', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('decimal_odds', sa.Float(), nullable=False),
    sa.Column('result', sa.Enum('PENDING', 'WIN', 'LOSS', 'PUSH', 'VOID', name='positionresult', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'SETTLED', 'VOID', name='positionstatus', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('payout', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('closing_decimal_odds', sa.Float(), nullable=True),
    sa.Column('strategy', sa.String(length=64), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['forecast_id'], ['forecasts.id'], ),
    sa.ForeignKeyConstraint(['market_outcome_id'], ['market_outcomes.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_official_asp_positions_forecast_id'), 'official_asp_positions', ['forecast_id'], unique=False)
    op.create_index(op.f('ix_official_asp_positions_user_id'), 'official_asp_positions', ['user_id'], unique=False)
    op.create_table('virtual_positions',
    sa.Column('bankroll_id', sa.Uuid(), nullable=False),
    sa.Column('game_id', sa.Uuid(), nullable=False),
    sa.Column('market_outcome_id', sa.Uuid(), nullable=True),
    sa.Column('selection', sa.String(length=160), nullable=False),
    sa.Column('stake', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('decimal_odds', sa.Float(), nullable=False),
    sa.Column('placed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result', sa.Enum('PENDING', 'WIN', 'LOSS', 'PUSH', 'VOID', name='positionresult', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('profit_loss', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['bankroll_id'], ['user_virtual_bankrolls.id'], ),
    sa.ForeignKeyConstraint(['game_id'], ['games.id'], ),
    sa.ForeignKeyConstraint(['market_outcome_id'], ['market_outcomes.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_virtual_positions_bankroll_id'), 'virtual_positions', ['bankroll_id'], unique=False)
    op.create_index(op.f('ix_virtual_positions_game_id'), 'virtual_positions', ['game_id'], unique=False)
    op.create_index(op.f('ix_virtual_positions_market_outcome_id'), 'virtual_positions', ['market_outcome_id'], unique=False)
    op.create_table('position_results',
    sa.Column('position_id', sa.Uuid(), nullable=False),
    sa.Column('result', sa.Enum('PENDING', 'WIN', 'LOSS', 'PUSH', 'VOID', name='positionresult', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('settled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('profit_loss', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('game_home_score', sa.Integer(), nullable=True),
    sa.Column('game_away_score', sa.Integer(), nullable=True),
    sa.Column('settlement_note', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.ForeignKeyConstraint(['position_id'], ['official_asp_positions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('position_id', name='uq_position_result')
    )
    op.create_index(op.f('ix_position_results_position_id'), 'position_results', ['position_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_position_results_position_id'), table_name='position_results')
    op.drop_table('position_results')
    op.drop_index(op.f('ix_virtual_positions_market_outcome_id'), table_name='virtual_positions')
    op.drop_index(op.f('ix_virtual_positions_game_id'), table_name='virtual_positions')
    op.drop_index(op.f('ix_virtual_positions_bankroll_id'), table_name='virtual_positions')
    op.drop_table('virtual_positions')
    op.drop_index(op.f('ix_official_asp_positions_user_id'), table_name='official_asp_positions')
    op.drop_index(op.f('ix_official_asp_positions_forecast_id'), table_name='official_asp_positions')
    op.drop_table('official_asp_positions')
    op.drop_index(op.f('ix_odds_snapshots_sportsbook_id'), table_name='odds_snapshots')
    op.drop_index(op.f('ix_odds_snapshots_source_event_id'), table_name='odds_snapshots')
    op.drop_index(op.f('ix_odds_snapshots_observed_at'), table_name='odds_snapshots')
    op.drop_index(op.f('ix_odds_snapshots_market_outcome_id'), table_name='odds_snapshots')
    op.drop_table('odds_snapshots')
    op.drop_index(op.f('ix_market_comparisons_forecast_id'), table_name='market_comparisons')
    op.drop_table('market_comparisons')
    op.drop_index(op.f('ix_market_outcomes_market_id'), table_name='market_outcomes')
    op.drop_table('market_outcomes')
    op.drop_index(op.f('ix_forecast_components_forecast_id'), table_name='forecast_components')
    op.drop_table('forecast_components')
    op.drop_table('community_reactions')
    op.drop_index(op.f('ix_weather_snapshots_observed_at'), table_name='weather_snapshots')
    op.drop_index(op.f('ix_weather_snapshots_game_id'), table_name='weather_snapshots')
    op.drop_table('weather_snapshots')
    op.drop_index(op.f('ix_team_game_stats_team_id'), table_name='team_game_stats')
    op.drop_index(op.f('ix_team_game_stats_game_id'), table_name='team_game_stats')
    op.drop_index(op.f('ix_team_game_stats_available_as_of'), table_name='team_game_stats')
    op.drop_table('team_game_stats')
    op.drop_index(op.f('ix_team_features_team_id'), table_name='team_features')
    op.drop_index(op.f('ix_team_features_game_id'), table_name='team_features')
    op.drop_table('team_features')
    op.drop_index(op.f('ix_rosters_team_id'), table_name='rosters')
    op.drop_index(op.f('ix_rosters_season_id'), table_name='rosters')
    op.drop_index(op.f('ix_rosters_player_id'), table_name='rosters')
    op.drop_table('rosters')
    op.drop_index(op.f('ix_player_team_history_team_id'), table_name='player_team_history')
    op.drop_index(op.f('ix_player_team_history_player_id'), table_name='player_team_history')
    op.drop_table('player_team_history')
    op.drop_index(op.f('ix_player_game_stats_team_id'), table_name='player_game_stats')
    op.drop_index(op.f('ix_player_game_stats_player_id'), table_name='player_game_stats')
    op.drop_index(op.f('ix_player_game_stats_game_id'), table_name='player_game_stats')
    op.drop_index(op.f('ix_player_game_stats_available_as_of'), table_name='player_game_stats')
    op.drop_table('player_game_stats')
    op.drop_index(op.f('ix_pitching_features_team_id'), table_name='pitching_features')
    op.drop_index(op.f('ix_pitching_features_player_id'), table_name='pitching_features')
    op.drop_index(op.f('ix_pitching_features_game_id'), table_name='pitching_features')
    op.drop_table('pitching_features')
    op.drop_table('pitchers')
    op.drop_index(op.f('ix_pitch_events_pitcher_id'), table_name='pitch_events')
    op.drop_index(op.f('ix_pitch_events_game_id'), table_name='pitch_events')
    op.drop_index(op.f('ix_pitch_events_batter_id'), table_name='pitch_events')
    op.drop_index(op.f('ix_pitch_events_available_as_of'), table_name='pitch_events')
    op.drop_table('pitch_events')
    op.drop_index(op.f('ix_markets_game_id'), table_name='markets')
    op.drop_table('markets')
    op.drop_table('game_teams')
    op.drop_index(op.f('ix_game_contexts_game_id'), table_name='game_contexts')
    op.drop_table('game_contexts')
    op.drop_index(op.f('ix_forecasts_generated_at'), table_name='forecasts')
    op.drop_index(op.f('ix_forecasts_game_id'), table_name='forecasts')
    op.drop_table('forecasts')
    op.drop_index(op.f('ix_community_posts_game_id'), table_name='community_posts')
    op.drop_index(op.f('ix_community_posts_author_user_id'), table_name='community_posts')
    op.drop_table('community_posts')
    op.drop_index(op.f('ix_bullpen_features_team_id'), table_name='bullpen_features')
    op.drop_index(op.f('ix_bullpen_features_game_id'), table_name='bullpen_features')
    op.drop_table('bullpen_features')
    op.drop_index(op.f('ix_batting_features_team_id'), table_name='batting_features')
    op.drop_index(op.f('ix_batting_features_game_id'), table_name='batting_features')
    op.drop_table('batting_features')
    op.drop_table('batters')
    op.drop_index(op.f('ix_players_team_id'), table_name='players')
    op.drop_table('players')
    op.drop_index(op.f('ix_leaderboard_entries_user_id'), table_name='leaderboard_entries')
    op.drop_index(op.f('ix_leaderboard_entries_season_id'), table_name='leaderboard_entries')
    op.drop_table('leaderboard_entries')
    op.drop_index(op.f('ix_games_venue_id'), table_name='games')
    op.drop_index(op.f('ix_games_starts_at'), table_name='games')
    op.drop_index(op.f('ix_games_season_id'), table_name='games')
    op.drop_index(op.f('ix_games_league_id'), table_name='games')
    op.drop_table('games')
    op.drop_index(op.f('ix_teams_league_id'), table_name='teams')
    op.drop_table('teams')
    op.drop_index(op.f('ix_seasons_league_id'), table_name='seasons')
    op.drop_table('seasons')
    op.drop_index(op.f('ix_private_league_members_user_id'), table_name='private_league_members')
    op.drop_index(op.f('ix_private_league_members_league_id'), table_name='private_league_members')
    op.drop_table('private_league_members')
    op.drop_table('user_virtual_bankrolls')
    op.drop_index(op.f('ix_private_leagues_owner_user_id'), table_name='private_leagues')
    op.drop_table('private_leagues')
    op.drop_index(op.f('ix_model_versions_sport_id'), table_name='model_versions')
    op.drop_index(op.f('ix_model_versions_feature_version_id'), table_name='model_versions')
    op.drop_table('model_versions')
    op.drop_index(op.f('ix_leagues_sport_id'), table_name='leagues')
    op.drop_table('leagues')
    op.drop_index(op.f('ix_leaderboard_snapshots_period_id'), table_name='leaderboard_snapshots')
    op.drop_index(op.f('ix_leaderboard_snapshots_captured_at'), table_name='leaderboard_snapshots')
    op.drop_table('leaderboard_snapshots')
    op.drop_index(op.f('ix_job_runs_job_name'), table_name='job_runs')
    op.drop_table('job_runs')
    op.drop_index(op.f('ix_entitlements_user_id'), table_name='entitlements')
    op.drop_table('entitlements')
    op.drop_index(op.f('ix_data_quality_checks_ingestion_run_id'), table_name='data_quality_checks')
    op.drop_table('data_quality_checks')
    op.drop_index(op.f('ix_audit_events_actor_user_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_action'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index(op.f('ix_analyst_ratings_user_id'), table_name='analyst_ratings')
    op.drop_table('analyst_ratings')
    op.drop_table('venues')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_table('sportsbooks')
    op.drop_table('sports')
    op.drop_table('leaderboard_periods')
    op.drop_index(op.f('ix_ingestion_runs_source'), table_name='ingestion_runs')
    op.drop_table('ingestion_runs')
    op.drop_table('feature_versions')
    op.drop_index('ix_external_entity', table_name='external_id_mappings')
    op.drop_table('external_id_mappings')
