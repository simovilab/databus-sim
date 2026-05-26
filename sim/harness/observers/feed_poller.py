"""Feed poller — GET GTFS-RT protobuf feeds and decode them."""

from __future__ import annotations

import logging

import httpx
from google.transit import gtfs_realtime_pb2

from ..config import Config

logger = logging.getLogger(__name__)


class FeedPoller:
    def __init__(self, config: Config) -> None:
        self._base = config.backend.rstrip("/")
        self._client = httpx.Client(timeout=10.0)

    def close(self) -> None:
        self._client.close()

    def fetch_vehicle_positions(self) -> gtfs_realtime_pb2.FeedMessage:
        return self._fetch(f"{self._base}/gtfs/realtime/vp.pb")

    def fetch_trip_updates(self) -> gtfs_realtime_pb2.FeedMessage:
        return self._fetch(f"{self._base}/gtfs/realtime/tu.pb")

    def _fetch(self, url: str) -> gtfs_realtime_pb2.FeedMessage:
        resp = self._client.get(url)
        resp.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        logger.debug("Fetched feed from %s: %d entities", url, len(feed.entity))
        return feed
