"""DB poller — psycopg queries for run/position/progression/occupancy tables."""

from __future__ import annotations

import logging
from typing import Any

import psycopg

from ..config import Config

logger = logging.getLogger(__name__)


class DbPoller:
    def __init__(self, config: Config) -> None:
        self._dsn = config.pg_dsn
        self._conn: psycopg.Connection | None = None

    def connect(self) -> None:
        try:
            self._conn = psycopg.connect(self._dsn)
            logger.debug("DB connected to %s", self._dsn)
        except Exception as exc:
            logger.warning("DbPoller could not connect: %s", exc)

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        if self._conn is None:
            self.connect()
        if self._conn is None:
            return None
        with self._conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        if self._conn is None:
            self.connect()
        if self._conn is None:
            return []
        with self._conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def execute(self, sql: str, params: tuple = ()) -> None:
        if self._conn is None:
            self.connect()
        if self._conn is None:
            raise RuntimeError("No DB connection")
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
        self._conn.commit()
