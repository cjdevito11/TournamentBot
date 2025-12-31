/* migrations/001_discord_tournaments.sql
   Run once against d2_discord_bot.

   Design goals:
   - Reuse existing: platform, platform_account, channel
   - Add isolated tournament tables with clean FK boundaries
   - Avoid altering existing UNIQUE constraints (use storage conventions instead)
*/

START TRANSACTION;

-- 1) Add Discord as a platform (idempotent)
INSERT INTO platform (name, metadata)
VALUES ('discord', JSON_OBJECT('source', 'migration'))
ON DUPLICATE KEY UPDATE name = VALUES(name);

-- 2) Helpful lookup unique keys (safe with NULLs; run once)
-- If these already exist in your DB, remove these lines before running.
ALTER TABLE channel
  ADD UNIQUE KEY uk_channel_platform_external_id (platform_id, external_channel_id);

ALTER TABLE platform_account
  ADD UNIQUE KEY uk_account_platform_external_id (platform_id, external_user_id);

-- 3) Tournament core
CREATE TABLE tournament (
  tournament_id bigint unsigned NOT NULL AUTO_INCREMENT,

  -- Scope: use a Discord guild represented as a row in `channel`
  guild_channel_id bigint unsigned NOT NULL,

  -- Optional: where to post bracket/announcements (Discord text channel row in `channel`)
  announce_channel_id bigint unsigned DEFAULT NULL,

  name varchar(128) NOT NULL,
  format varchar(16) NOT NULL DEFAULT 'double_elim',  -- 'single_elim' | 'double_elim'
  team_size tinyint unsigned NOT NULL DEFAULT 2,
  max_players smallint unsigned NOT NULL DEFAULT 48,

  status varchar(16) NOT NULL DEFAULT 'draft',
  -- draft -> open -> seeded -> active -> grand_finals -> completed (suggested)

  created_by_account_id bigint unsigned DEFAULT NULL,

  opened_at datetime(6) DEFAULT NULL,
  seeded_at datetime(6) DEFAULT NULL,
  started_at datetime(6) DEFAULT NULL,
  completed_at datetime(6) DEFAULT NULL,

  rules_json json DEFAULT NULL,
  state_json json DEFAULT NULL,     -- minimal state machine storage for quick MVP
  metadata json DEFAULT NULL,

  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (tournament_id),

  KEY ix_tournament_status (status, created_at),
  KEY ix_tournament_guild (guild_channel_id, created_at),

  CONSTRAINT fk_tournament_guild_channel
    FOREIGN KEY (guild_channel_id) REFERENCES channel (channel_id),

  CONSTRAINT fk_tournament_announce_channel
    FOREIGN KEY (announce_channel_id) REFERENCES channel (channel_id),

  CONSTRAINT fk_tournament_created_by
    FOREIGN KEY (created_by_account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 4) Registrations (players in the event)
CREATE TABLE tournament_entry (
  tournament_entry_id bigint unsigned NOT NULL AUTO_INCREMENT,
  tournament_id bigint unsigned NOT NULL,
  account_id bigint unsigned NOT NULL,

  status varchar(16) NOT NULL DEFAULT 'active',  -- active | dropped | disqualified
  joined_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  metadata json DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (tournament_entry_id),
  UNIQUE KEY uk_tournament_entry (tournament_id, account_id),

  KEY ix_entry_tournament (tournament_id, joined_at),
  KEY ix_entry_account (account_id, joined_at),

  CONSTRAINT fk_entry_tournament
    FOREIGN KEY (tournament_id) REFERENCES tournament (tournament_id) ON DELETE CASCADE,

  CONSTRAINT fk_entry_account
    FOREIGN KEY (account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 5) Teams (randomized 2v2 teams)
CREATE TABLE tournament_team (
  team_id bigint unsigned NOT NULL AUTO_INCREMENT,
  tournament_id bigint unsigned NOT NULL,

  seed int unsigned DEFAULT NULL,
  display_name varchar(128) DEFAULT NULL,

  metadata json DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (team_id),

  KEY ix_team_tournament (tournament_id, team_id),
  KEY ix_team_seed (tournament_id, seed),

  CONSTRAINT fk_team_tournament
    FOREIGN KEY (tournament_id) REFERENCES tournament (tournament_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE tournament_team_member (
  tournament_id bigint unsigned NOT NULL,
  team_id bigint unsigned NOT NULL,
  account_id bigint unsigned NOT NULL,
  slot tinyint unsigned NOT NULL, -- 1..team_size

  metadata json DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

  PRIMARY KEY (team_id, slot),
  UNIQUE KEY uk_team_member_unique (team_id, account_id),
  UNIQUE KEY uk_tournament_member_one_team (tournament_id, account_id),

  KEY ix_team_member_account (account_id),

  CONSTRAINT fk_team_member_tournament
    FOREIGN KEY (tournament_id) REFERENCES tournament (tournament_id) ON DELETE CASCADE,

  CONSTRAINT fk_team_member_team
    FOREIGN KEY (team_id) REFERENCES tournament_team (team_id) ON DELETE CASCADE,

  CONSTRAINT fk_team_member_account
    FOREIGN KEY (account_id) REFERENCES platform_account (account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 6) Matches (store bracket progress)
CREATE TABLE tournament_match (
  match_id bigint unsigned NOT NULL AUTO_INCREMENT,
  tournament_id bigint unsigned NOT NULL,

  bracket varchar(8) NOT NULL DEFAULT 'W', -- W | L | GF
  round_no int unsigned NOT NULL,
  match_no int unsigned NOT NULL,

  team1_id bigint unsigned NOT NULL,
  team2_id bigint unsigned DEFAULT NULL, -- NULL = BYE

  winner_team_id bigint unsigned DEFAULT NULL,
  loser_team_id bigint unsigned DEFAULT NULL,

  status varchar(16) NOT NULL DEFAULT 'pending', -- pending | open | completed

  reported_by_account_id bigint unsigned DEFAULT NULL,
  reported_at datetime(6) DEFAULT NULL,

  -- Optional wiring for future “true bracket graph”
  next_match_id bigint unsigned DEFAULT NULL,
  next_slot tinyint unsigned DEFAULT NULL, -- 1 or 2

  metadata json DEFAULT NULL,
  created_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

  PRIMARY KEY (match_id),

  UNIQUE KEY uk_match_round (tournament_id, bracket, round_no, match_no),
  KEY ix_match_status (tournament_id, status, updated_at),

  CONSTRAINT fk_match_tournament
    FOREIGN KEY (tournament_id) REFERENCES tournament (tournament_id) ON DELETE CASCADE,

  CONSTRAINT fk_match_team1
    FOREIGN KEY (team1_id) REFERENCES tournament_team (team_id),

  CONSTRAINT fk_match_team2
    FOREIGN KEY (team2_id) REFERENCES tournament_team (team_id),

  CONSTRAINT fk_match_winner
    FOREIGN KEY (winner_team_id) REFERENCES tournament_team (team_id),

  CONSTRAINT fk_match_loser
    FOREIGN KEY (loser_team_id) REFERENCES tournament_team (team_id),

  CONSTRAINT fk_match_reporter
    FOREIGN KEY (reported_by_account_id) REFERENCES platform_account (account_id),

  CONSTRAINT fk_match_next
    FOREIGN KEY (next_match_id) REFERENCES tournament_match (match_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

COMMIT;
