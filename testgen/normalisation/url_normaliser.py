from __future__ import annotations
"""URL normalisation — replace dynamic segments with named parameters.

Detects and replaces UUIDs, MongoDB ObjectIDs, numeric IDs, and slugs
with named parameters like :uuid, :objectId, :id, :slug.
"""

import re
from urllib.parse import urlparse, urlunparse

# Patterns for dynamic URL segments
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
OBJECTID_PATTERN = re.compile(r"[0-9a-f]{24}", re.IGNORECASE)
NUMERIC_ID_PATTERN = re.compile(r"^\d+$")
# Slug: lowercase alphanumeric with hyphens, 3+ chars, at least one hyphen
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")


def normalise_url(url: str) -> str:
    """Normalise a URL by replacing dynamic path segments with parameters.

    Examples:
        /users/123/posts → /users/:id/posts
        /api/items/550e8400-e29b-41d4-a716-446655440000 → /api/items/:uuid
        /blog/my-first-post → /blog/:slug
    """
    try:
        parsed = urlparse(url)
        path = parsed.path
    except Exception:
        path = url

    segments = path.split("/")
    normalised = []

    for segment in segments:
        if not segment:
            normalised.append(segment)
            continue

        normalised.append(_normalise_segment(segment))

    normalised_path = "/".join(normalised)

    # Reconstruct URL if it was a full URL
    if url.startswith("http"):
        try:
            parsed = urlparse(url)
            return urlunparse(parsed._replace(path=normalised_path, query="", fragment=""))
        except Exception:
            pass

    return normalised_path


def _normalise_segment(segment: str) -> str:
    """Normalise a single URL path segment."""
    # UUID (full match or within segment)
    if UUID_PATTERN.fullmatch(segment):
        return ":uuid"

    # MongoDB ObjectID (24 hex chars)
    if OBJECTID_PATTERN.fullmatch(segment) and len(segment) == 24:
        return ":objectId"

    # Numeric ID
    if NUMERIC_ID_PATTERN.match(segment):
        return ":id"

    # Slug (only if it looks like a content slug, not a known route)
    if SLUG_PATTERN.match(segment) and len(segment) >= 5:
        return ":slug"

    return segment


def normalise_url_sequence(urls: list[str]) -> str:
    """Normalise a sequence of URLs into a canonical flow pattern.

    Returns a string like: /login → /dashboard → /settings
    """
    normalised = []
    prev = None
    for url in urls:
        n = normalise_url(url)
        # Deduplicate consecutive identical URLs
        if n != prev:
            normalised.append(n)
            prev = n
    return " → ".join(normalised)
