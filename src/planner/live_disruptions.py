"""
Live Transit Disruption Feed Ingestor — STUB
Member 2 — NLP & Information Retrieval Lead

TODO (Member 2):
  - Add more RSS feed URLs (Ada Derana transport section, SLR official notices)
  - Add in-memory TTL cache so alerts expire after 2 hours
  - Integrate alert overlay into hybrid_retriever.py ranking
"""

from __future__ import annotations

from typing import Any

RSS_FEEDS: list[str] = [
    "http://www.adaderana.lk/rss.php",
    # Add verified public transit feeds here
]

TRANSIT_KEYWORDS: list[str] = [
    "train", "railway", "bus", "strike", "delay", "derailment",
    "expressway", "sltb", "slr", "blocked", "cancelled",
]


def fetch_live_transit_alerts() -> list[dict[str, str]]:
    """
    Fetches and filters transit-related alerts from public RSS feeds.
    TODO (Member 2): Replace stub with feedparser implementation.

    Returns:
        List of alert dicts with keys: headline, summary, published.
    """
    # STUB — returns empty list; Member 2 will implement feedparser logic
    return []


def has_disruption_for_route(route_id: str, alerts: list[dict[str, Any]]) -> bool:
    """
    Checks if any active alert affects the given route.
    TODO (Member 2): Implement proper route-to-alert matching.
    """
    return False

