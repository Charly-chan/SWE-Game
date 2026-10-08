"""Compatibility import for the single five-component release protocol."""

from .scoring import REGISTRY_VERSION, RANKING_SCOPE
# Historical callers used this name for the old objective-only ceiling. The
# release score is a fixed /100 proxy; keep the import surface without keeping
# the retired 70-point contract alive.
RAW_SCORE_CEILING = 100.0

__all__ = ["RAW_SCORE_CEILING", "RANKING_SCOPE", "REGISTRY_VERSION"]
