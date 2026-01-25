# repositories/event_repo.py
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from repositories.base_repo import BaseRepo, to_json


class EventRepo(BaseRepo):
    async def create_event(
        self,
        *,
        guild_channel_id: int,
        announce_channel_id: int | None,
        name: str,
        format: str,
        team_size: int,
        max_players: int,
        created_by_account_id: int | None,
        starts_at: datetime | None = None,
        rules_json: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        return await self.insert_returning_id(
            """
            INSERT INTO event
              (guild_channel_id, announce_channel_id, name, format, team_size, max_players,
               created_by_account_id, starts_at, rules_json, metadata)
            VALUES
              (%s, %s, %s, %s, %s, %s,
               %s, %s, %s, %s);
            """,
            (
                guild_channel_id,
                announce_channel_id,
                name,
                format,
                team_size,
                max_players,
                created_by_account_id,
                starts_at,
                to_json(rules_json),
                to_json(metadata),
            ),
        )

    async def get_event(self, *, event_id: int) -> Mapping[str, Any] | None:
        return await self.fetch_one(
            """
            SELECT *
            FROM event
            WHERE event_id=%s;
            """,
            (event_id,),
        )

    async def set_event_status(self, *, event_id: int, status: str) -> int:
        return await self.execute(
            "UPDATE event SET status=%s, updated_at=NOW(6) WHERE event_id=%s;",
            (status, event_id),
        )

    async def register_player(self, *, event_id: int, account_id: int, metadata: Mapping[str, Any] | None = None) -> None:
        await self.execute(
            """
            INSERT INTO event_registration (event_id, account_id, metadata)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
              status='active',
              metadata = COALESCE(VALUES(metadata), metadata);
            """,
            (event_id, account_id, to_json(metadata)),
        )

    async def drop_player(self, *, event_id: int, account_id: int) -> int:
        return await self.execute(
            """
            UPDATE event_registration
            SET status='dropped'
            WHERE event_id=%s AND account_id=%s;
            """,
            (event_id, account_id),
        )

    async def list_registrations(self, *, event_id: int) -> list[Mapping[str, Any]]:
        return await self.fetch_all(
            """
            SELECT er.account_id, er.status, er.joined_at, pa.display_name
            FROM event_registration er
            JOIN platform_account pa ON pa.account_id = er.account_id
            WHERE er.event_id=%s
            ORDER BY er.joined_at ASC;
            """,
            (event_id,),
        )

    async def create_event_team(
        self,
        *,
        event_id: int,
        base_team_id: int | None = None,
        display_name: str | None = None,
        seed: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        return await self.insert_returning_id(
            """
            INSERT INTO event_team (event_id, base_team_id, display_name, seed, metadata)
            VALUES (%s, %s, %s, %s, %s);
            """,
            (event_id, base_team_id, display_name, seed, to_json(metadata)),
        )

    async def add_event_team_member(
        self,
        *,
        event_team_id: int,
        account_id: int,
        role: str = "starter",
        slot: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self.execute(
            """
            INSERT INTO event_team_member (event_team_id, account_id, role, slot, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              role = VALUES(role),
              slot = VALUES(slot),
              metadata = COALESCE(VALUES(metadata), metadata);
            """,
            (event_team_id, account_id, role, slot, to_json(metadata)),
        )

    async def list_event_teams(self, *, event_id: int) -> list[Mapping[str, Any]]:
        return await self.fetch_all(
            """
            SELECT et.event_team_id, et.event_id, et.base_team_id, et.display_name, et.seed
            FROM event_team et
            WHERE et.event_id=%s
            ORDER BY et.seed IS NULL, et.seed, et.event_team_id;
            """,
            (event_id,),
        )

    async def get_event_team_roster(self, *, event_team_id: int) -> list[Mapping[str, Any]]:
        return await self.fetch_all(
            """
            SELECT etm.account_id, etm.role, etm.slot, pa.display_name
            FROM event_team_member etm
            JOIN platform_account pa ON pa.account_id = etm.account_id
            WHERE etm.event_team_id=%s
            ORDER BY
              CASE etm.role WHEN 'starter' THEN 0 ELSE 1 END,
              etm.slot IS NULL, etm.slot,
              pa.display_name;
            """,
            (event_team_id,),
        )

    async def create_match(
        self,
        *,
        event_id: int,
        bracket: str,
        round_no: int,
        match_no: int,
        team1_event_team_id: int,
        team2_event_team_id: int | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        return await self.insert_returning_id(
            """
            INSERT INTO event_match
              (event_id, bracket, round_no, match_no,
               team1_event_team_id, team2_event_team_id, metadata)
            VALUES
              (%s, %s, %s, %s, %s, %s, %s);
            """,
            (event_id, bracket, round_no, match_no, team1_event_team_id, team2_event_team_id, to_json(metadata)),
        )

    async def get_match_by_code(self, *, event_id: int, match_code: str) -> Mapping[str, Any] | None:
        """
        match_code examples: W1-01, L2-03, GF-01
        """
        code = (match_code or "").strip().upper()
        if not code:
            return None

        # GF treated as bracket GF
        if code.startswith("GF"):
            bracket = "GF"
            rest = code[2:].lstrip("-")
        else:
            bracket = code[0]
            rest = code[1:].lstrip("-")

        try:
            round_s, match_s = rest.split("-", 1)
            round_no = int(round_s)
            match_no = int(match_s)
        except Exception:
            return None

        return await self.fetch_one(
            """
            SELECT *
            FROM event_match
            WHERE event_id=%s AND bracket=%s AND round_no=%s AND match_no=%s;
            """,
            (event_id, bracket, round_no, match_no),
        )


    async def set_match_result(
        self,
        *,
        event_match_id: int,
        winner_event_team_id: int,
        loser_event_team_id: int,
        reported_by_account_id: int | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        return await self.execute(
            """
            UPDATE event_match
            SET
              status='completed',
              winner_event_team_id=%s,
              loser_event_team_id=%s,
              reported_by_account_id=%s,
              reported_at=NOW(6),
              updated_at=NOW(6),
              metadata=COALESCE(%s, metadata)
            WHERE event_match_id=%s;
            """,
            (winner_event_team_id, loser_event_team_id, reported_by_account_id, to_json(metadata), event_match_id),
        )

    async def list_matches(self, *, event_id: int) -> list[Mapping[str, Any]]:
        return await self.fetch_all(
            """
            SELECT *
            FROM event_match
            WHERE event_id=%s
            ORDER BY
              CASE bracket WHEN 'W' THEN 0 WHEN 'L' THEN 1 ELSE 2 END,
              round_no, match_no;
            """,
            (event_id,),
        )

    async def list_open_matches(self, *, event_id: int) -> list[Mapping[str, Any]]:
        return await self.fetch_all(
            """
            SELECT *
            FROM event_match
            WHERE event_id=%s AND status IN ('pending','open')
            ORDER BY
              CASE bracket WHEN 'W' THEN 0 WHEN 'L' THEN 1 ELSE 2 END,
              round_no, match_no;
            """,
            (event_id,),
        )

    #async def execute(self, sql: str, params: tuple = ()) -> int:
        #return await self._db.execute(sql, params)
