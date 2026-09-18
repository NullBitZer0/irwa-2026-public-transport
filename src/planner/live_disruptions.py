from __future__ import annotations

import time
from typing import Any

import feedparser

RSS_FEEDS: list[str] = [
    "http://www.adaderana.lk/rss.php",
    # Add more verified Sri Lankan news/transit RSS feeds here as you find them
]

TRANSIT_KEYWORDS: list[str] = [
    "train", "railway", "bus", "strike", "delay", "derailment",
    "expressway", "sltb", "slr", "blocked", "cancelled", "landslide",
]

# Simple in-memory cache so we don't hit the RSS feed on every single query,
# and so alerts automatically "expire" after a set time (TTL = time-to-live).
_CACHE: dict[str, Any] = {"alerts": [], "fetched_at": 0.0}
_TTL_SECONDS = 2 * 60 * 60  # 2 hours


def fetch_live_transit_alerts(force_refresh: bool = False) -> list[dict[str, str]]:
    """
    Fetches transit-related alerts from public RSS feeds, filtered by keyword.
    Cached for _TTL_SECONDS so repeated queries don't re-hit the network.
    """
    now = time.time()
    if not force_refresh and (now - _CACHE["fetched_at"] < _TTL_SECONDS) and _CACHE["alerts"]:
        return _CACHE["alerts"]

    alerts: list[dict[str, str]] = []
    for feed_url in RSS_FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[live_disruptions] Could not fetch {feed_url}: {e}")
            continue

        for entry in parsed.entries[:15]:
            title_lower = entry.get("title", "").lower()
            if any(k in title_lower for k in TRANSIT_KEYWORDS):
                alerts.append(
                    {
                        "headline": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                        "published": entry.get("published", "Recent"),
                    }
                )

    _CACHE["alerts"] = alerts
    _CACHE["fetched_at"] = now
    return alerts


def has_disruption_for_route(route_id: str, alerts: list[dict[str, Any]]) -> bool:
    """
    Naive keyword match: flags a route if any alert headline/summary mentions
    a stop on that route. (Member 2 note: this can be improved by matching
    against the route's actual `stops` list instead of just route_id.)
    """
    route_key = route_id.lower()
    for alert in alerts:
        text = f"{alert.get('headline', '')} {alert.get('summary', '')}".lower()
        if route_key in text:
            return True
    return False

if __name__ == "__main__":
    alerts = fetch_live_transit_alerts()
    print(f"Found {len(alerts)} transit-related alerts")
    for a in alerts:
        print(f"- {a['headline']}")