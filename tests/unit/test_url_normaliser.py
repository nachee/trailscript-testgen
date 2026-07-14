"""Unit tests for URL normalisation."""

from testgen.normalisation.url_normaliser import normalise_url, normalise_url_sequence


class TestNormaliseUrl:
    def test_uuid_segment(self):
        result = normalise_url("/users/550e8400-e29b-41d4-a716-446655440000/profile")
        assert result == "/users/:uuid/profile"

    def test_uppercase_uuid(self):
        result = normalise_url("/items/550E8400-E29B-41D4-A716-446655440000")
        assert result == "/items/:uuid"

    def test_objectid_segment(self):
        result = normalise_url("/posts/507f1f77bcf86cd799439011")
        assert result == "/posts/:objectId"

    def test_numeric_id(self):
        result = normalise_url("/orders/12345")
        assert result == "/orders/:id"

    def test_slug_segment(self):
        result = normalise_url("/blog/my-first-post")
        assert result == "/blog/:slug"

    def test_short_slug_not_replaced(self):
        # Short segments (< 5 chars) are not treated as slugs
        result = normalise_url("/api/v1")
        assert result == "/api/v1"

    def test_mixed_dynamic_segments(self):
        result = normalise_url("/users/123/posts/550e8400-e29b-41d4-a716-446655440000")
        assert result == "/users/:id/posts/:uuid"

    def test_static_path_unchanged(self):
        result = normalise_url("/settings/account/notifications")
        assert result == "/settings/account/notifications"

    def test_root_path(self):
        result = normalise_url("/")
        assert result == "/"

    def test_full_url(self):
        result = normalise_url("https://app.example.com/users/123/edit")
        assert result == "https://app.example.com/users/:id/edit"

    def test_full_url_strips_query(self):
        result = normalise_url("https://app.example.com/search?q=test")
        assert result == "https://app.example.com/search"


class TestNormaliseUrlSequence:
    def test_simple_sequence(self):
        urls = ["/login", "/dashboard", "/settings"]
        result = normalise_url_sequence(urls)
        assert result == "/login → /dashboard → /settings"

    def test_deduplicates_consecutive(self):
        urls = ["/login", "/login", "/dashboard"]
        result = normalise_url_sequence(urls)
        assert result == "/login → /dashboard"

    def test_normalises_dynamic_segments(self):
        urls = ["/products", "/products/123", "/products/123/edit"]
        result = normalise_url_sequence(urls)
        assert result == "/products → /products/:id → /products/:id/edit"

    def test_empty_sequence(self):
        result = normalise_url_sequence([])
        assert result == ""
