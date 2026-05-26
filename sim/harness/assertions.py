"""Assertion helpers: event timeouts, branch path verification."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .observers.amqp_sniffer import AmqpMessage, AmqpSniffer
    from .observers.lake_watcher import LakeWatcher, ParquetFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssertionResult:
    passed: bool
    message: str


def assert_amqp_event(
    sniffer: "AmqpSniffer",
    routing_key: str,
    since: datetime,
    timeout_ms: int,
) -> AssertionResult:
    msg = sniffer.wait_for_routing_key(routing_key, since=since, timeout_ms=timeout_ms)
    if msg:
        return AssertionResult(True, f"Received '{routing_key}' after {(msg.received_at - since).total_seconds()*1000:.0f}ms")
    return AssertionResult(False, f"Timeout ({timeout_ms}ms): no '{routing_key}' message received")


def assert_parquet_written(
    watcher: "LakeWatcher",
    since: datetime,
    timeout_ms: int,
    path_contains: str = "",
) -> AssertionResult:
    pf = watcher.wait_for_parquet(since=since, timeout_ms=timeout_ms, path_contains=path_contains)
    if pf:
        return AssertionResult(True, f"Parquet written: {pf.path}")
    return AssertionResult(False, f"Timeout ({timeout_ms}ms): no parquet file detected under lake")


def assert_http_status(
    actual: int,
    expected: list[int],
    url: str,
) -> AssertionResult:
    if actual in expected:
        return AssertionResult(True, f"HTTP {actual} for {url}")
    return AssertionResult(False, f"HTTP {actual} (expected one of {expected}) for {url}")
