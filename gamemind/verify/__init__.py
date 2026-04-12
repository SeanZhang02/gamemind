"""Verify — predicate evaluation for success/abort conditions.

v1 scope: inventory_count, time_limit, health_threshold.
vision_critic deferred.
"""

from __future__ import annotations

from gamemind.verify.checks import check_predicate

__all__ = ["check_predicate"]
