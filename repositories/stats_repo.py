# repositories/stats_repo.py
from __future__ import annotations

from typing import Any, Mapping

from repositories.base_repo import BaseRepo, to_json


class StatsRepo(BaseRepo):
    async def upsert_match_player_stat(
        self,
        *,
        event_match_id: int,
        account_id: int,
        event_team_id: int,
        kills: int = 0,
        deaths: int = 0,
        assists: int = 0,
        participated: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO event_match_player_stat
              (event_match_id, account_id, event_team_id, kills, deaths, assists, is_participated, metadata)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              event_team_id     = VALUES(event_team_id),
              kills             = VALUES(kills),
              deaths            = VALUES(deaths),
              assists           = VALUES(assists),
              is_participated   = VALUES(is_participated),
              metadata          = COALESCE(VALUES(metadata), metadata);
            """,
            (
                event_match_id,
                account_id,
                event_team_id,
                max(0, int(kills)),
                max(0, int(deaths)),
                max(0, int(assists)),
                1 if participated else 0,
                to_json(metadata),
            ),
        )

    async def event_player_totals(self, *, event_id: int) -> list[Mapping[str, Any]]:
        """
        Aggregates across all completed matches in an event.
        Returns totals for kills/deaths/assists + win/loss derived from match results.
        """
        return await self.fetch_all(
            """
            SELECT
              pa.account_id,
              pa.display_name,

              COALESCE(SUM(s.kills), 0)   AS kills,
              COALESCE(SUM(s.deaths), 0)  AS deaths,
              COALESCE(SUM(s.assists), 0) AS assists,

              COALESCE(SUM(CASE WHEN m.status='completed' AND m.winner_event_team_id = s.event_team_id THEN 1 ELSE 0 END), 0) AS wins,
              COALESCE(SUM(CASE WHEN m.status='completed' AND m.loser_event_team_id  = s.event_team_id THEN 1 ELSE 0 END), 0) AS losses,

              COALESCE(SUM(CASE WHEN s.is_participated=1 THEN 1 ELSE 0 END), 0) AS match_participations
            FROM event_match_player_stat s
            JOIN event_match m ON m.event_match_id = s.event_match_id
            JOIN platform_account pa ON pa.account_id = s.account_id
            WHERE m.event_id=%s AND m.status='completed'
            GROUP BY pa.account_id, pa.display_name
            ORDER BY wins DESC, kills DESC, deaths ASC, pa.display_name ASC;
            """,
            (event_id,),
        )

    async def event_team_records(self, *, event_id: int) -> list[Mapping[str, Any]]:
        """
        Computes team W/L for the event based on completed matches.
        Includes seed for human-friendly reporting alignment.
        """
        return await self.fetch_all(
            """
            SELECT
              et.event_team_id,
              et.seed,
              COALESCE(et.display_name, CONCAT('Seed ', COALESCE(et.seed, et.event_team_id))) AS team_name,

              SUM(CASE WHEN m.status='completed' AND m.winner_event_team_id = et.event_team_id THEN 1 ELSE 0 END) AS wins,
              SUM(CASE WHEN m.status='completed' AND m.loser_event_team_id  = et.event_team_id THEN 1 ELSE 0 END) AS losses
            FROM event_team et
            LEFT JOIN event_match m
              ON m.event_id = et.event_id
             AND m.status = 'completed'
             AND (m.winner_event_team_id = et.event_team_id OR m.loser_event_team_id = et.event_team_id)
            WHERE et.event_id=%s
            GROUP BY et.event_team_id, et.seed, team_name
            ORDER BY wins DESC, losses ASC, et.seed IS NULL, et.seed ASC, team_name ASC;
            """,
            (event_id,),
        )

