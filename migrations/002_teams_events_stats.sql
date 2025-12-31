/* migrations/002_teams_events_stats.sql */

START TRANSACTION;

-- ------------------------------------------------------------
-- 1) Persistent Teams / Squads (ladder reset + reusable teams)
-- ------------------------------------------------------------
CREATE TABLE team (
  team_id bigint unsigned NOT NULL AUTO_INCREMENT,

  -- Discord guild stored in channel table (per your existing pattern)
  guild_channel_id bigint unsigned NOT NULL,

  context varchar(32) NOT NULL DEFAULT 'ladder_reset', -- ladder_reset | event

  name varchar(128) NOT NULL,
  tag varchar(16) DEFAULT NULL,

  captain_account_id bigint unsigned DEFAULT NULL,

  is_active tinyint(1) NOT NULL DEFAULT 1,

  metadata json DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (team_id),

  UNIQUE KEY uk_team_guild_name (guild_channel_id, context, name),
  KEY ix_team_guild (guild_channel_id, context, created_at),

  CONSTRAINT fk_team_guild_channel
    FOREIGN KEY (guild_channel_id) REFERENCES channel (channel_id),

  CONSTRAINT fk_team_captain
    FOREIGN KEY (captain_account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE team_member (
  team_id bigint unsigned NOT NULL,
  account_id bigint unsigned NOT NULL,

  role varchar(16) NOT NULL DEFAULT 'starter',  -- starter | backup
  slot tinyint unsigned DEFAULT NULL,           -- optional ordering (1..4), null allowed
  joined_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  metadata json DEFAULT NULL,

  PRIMARY KEY (team_id, account_id),
  KEY ix_team_member_account (account_id),

  CONSTRAINT fk_team_member_team
    FOREIGN KEY (team_id) REFERENCES team (team_id) ON DELETE CASCADE,

  CONSTRAINT fk_team_member_account
    FOREIGN KEY (account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ------------------------------------------------------------
-- 2) Events (1v1 / 2v2 / 3v3 / 4v4)
--    Can use persistent teams OR event-only teams (from tournament_team)
-- ------------------------------------------------------------
CREATE TABLE event (
  event_id bigint unsigned NOT NULL AUTO_INCREMENT,

  guild_channel_id bigint unsigned NOT NULL,
  announce_channel_id bigint unsigned DEFAULT NULL,

  name varchar(128) NOT NULL,
  event_type varchar(16) NOT NULL DEFAULT 'pvp',   -- pvp, pvm, etc. (future)
  format varchar(16) NOT NULL DEFAULT 'double_elim', -- single_elim | double_elim
  team_size tinyint unsigned NOT NULL,             -- 1..4
  max_players smallint unsigned NOT NULL DEFAULT 48,

  status varchar(16) NOT NULL DEFAULT 'draft',     -- draft|open|locked|active|completed
  created_by_account_id bigint unsigned DEFAULT NULL,

  starts_at datetime(6) DEFAULT NULL,
  ended_at datetime(6) DEFAULT NULL,

  rules_json json DEFAULT NULL,
  metadata json DEFAULT NULL,

  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (event_id),

  KEY ix_event_guild (guild_channel_id, created_at),
  KEY ix_event_status (status, starts_at),

  CONSTRAINT fk_event_guild_channel
    FOREIGN KEY (guild_channel_id) REFERENCES channel (channel_id),

  CONSTRAINT fk_event_announce_channel
    FOREIGN KEY (announce_channel_id) REFERENCES channel (channel_id),

  CONSTRAINT fk_event_created_by
    FOREIGN KEY (created_by_account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Central registration for an event (players), independent of team assignment
CREATE TABLE event_registration (
  event_id bigint unsigned NOT NULL,
  account_id bigint unsigned NOT NULL,

  status varchar(16) NOT NULL DEFAULT 'active', -- active|dropped|dq
  joined_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  metadata json DEFAULT NULL,

  PRIMARY KEY (event_id, account_id),

  KEY ix_event_reg_joined (event_id, joined_at),

  CONSTRAINT fk_event_reg_event
    FOREIGN KEY (event_id) REFERENCES event (event_id) ON DELETE CASCADE,

  CONSTRAINT fk_event_reg_account
    FOREIGN KEY (account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Teams participating in an event:
-- can reference persistent team.team_id OR be event-local (team_id null, and we store a name)
CREATE TABLE event_team (
  event_team_id bigint unsigned NOT NULL AUTO_INCREMENT,
  event_id bigint unsigned NOT NULL,

  base_team_id bigint unsigned DEFAULT NULL, -- references team.team_id if using persistent squads
  display_name varchar(128) DEFAULT NULL,

  seed int unsigned DEFAULT NULL,
  metadata json DEFAULT NULL,

  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (event_team_id),

  KEY ix_event_team_event (event_id, event_team_id),
  KEY ix_event_team_seed (event_id, seed),

  CONSTRAINT fk_event_team_event
    FOREIGN KEY (event_id) REFERENCES event (event_id) ON DELETE CASCADE,

  CONSTRAINT fk_event_team_base
    FOREIGN KEY (base_team_id) REFERENCES team (team_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE event_team_member (
  event_team_id bigint unsigned NOT NULL,
  account_id bigint unsigned NOT NULL,

  role varchar(16) NOT NULL DEFAULT 'starter', -- starter|backup
  slot tinyint unsigned DEFAULT NULL,          -- 1..4
  metadata json DEFAULT NULL,

  PRIMARY KEY (event_team_id, account_id),
  KEY ix_event_team_member_account (account_id),

  CONSTRAINT fk_event_team_member_team
    FOREIGN KEY (event_team_id) REFERENCES event_team (event_team_id) ON DELETE CASCADE,

  CONSTRAINT fk_event_team_member_account
    FOREIGN KEY (account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- ------------------------------------------------------------
-- 3) Matches + Stats (kills/deaths/wins/losses/participation)
-- ------------------------------------------------------------
CREATE TABLE event_match (
  event_match_id bigint unsigned NOT NULL AUTO_INCREMENT,
  event_id bigint unsigned NOT NULL,

  bracket varchar(8) NOT NULL DEFAULT 'W', -- W|L|GF
  round_no int unsigned NOT NULL,
  match_no int unsigned NOT NULL,

  team1_event_team_id bigint unsigned NOT NULL,
  team2_event_team_id bigint unsigned DEFAULT NULL, -- null = bye

  status varchar(16) NOT NULL DEFAULT 'pending', -- pending|open|completed

  winner_event_team_id bigint unsigned DEFAULT NULL,
  loser_event_team_id bigint unsigned DEFAULT NULL,

  reported_by_account_id bigint unsigned DEFAULT NULL,
  reported_at datetime(6) DEFAULT NULL,

  metadata json DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (event_match_id),

  UNIQUE KEY uk_event_match_round (event_id, bracket, round_no, match_no),
  KEY ix_event_match_status (event_id, status, updated_at),

  CONSTRAINT fk_event_match_event
    FOREIGN KEY (event_id) REFERENCES event (event_id) ON DELETE CASCADE,

  CONSTRAINT fk_event_match_team1
    FOREIGN KEY (team1_event_team_id) REFERENCES event_team (event_team_id),

  CONSTRAINT fk_event_match_team2
    FOREIGN KEY (team2_event_team_id) REFERENCES event_team (event_team_id),

  CONSTRAINT fk_event_match_winner
    FOREIGN KEY (winner_event_team_id) REFERENCES event_team (event_team_id),

  CONSTRAINT fk_event_match_loser
    FOREIGN KEY (loser_event_team_id) REFERENCES event_team (event_team_id),

  CONSTRAINT fk_event_match_reporter
    FOREIGN KEY (reported_by_account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Per-player stats recorded per match (this is the source of truth)
CREATE TABLE event_match_player_stat (
  event_match_id bigint unsigned NOT NULL,
  account_id bigint unsigned NOT NULL,
  event_team_id bigint unsigned NOT NULL,

  kills int unsigned NOT NULL DEFAULT 0,
  deaths int unsigned NOT NULL DEFAULT 0,
  assists int unsigned NOT NULL DEFAULT 0,

  is_participated tinyint(1) NOT NULL DEFAULT 1, -- allows “registered but no-show” marking

  metadata json DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (event_match_id, account_id),
  KEY ix_stat_team (event_team_id),
  KEY ix_stat_account (account_id),

  CONSTRAINT fk_stat_match
    FOREIGN KEY (event_match_id) REFERENCES event_match (event_match_id) ON DELETE CASCADE,

  CONSTRAINT fk_stat_account
    FOREIGN KEY (account_id) REFERENCES platform_account (account_id),

  CONSTRAINT fk_stat_event_team
    FOREIGN KEY (event_team_id) REFERENCES event_team (event_team_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

COMMIT;
