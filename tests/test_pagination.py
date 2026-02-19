"""Tests for pagination module."""

import pytest
from flask import Flask

from pagination import Pagination


class TestPagination:
    """Tests for Pagination class."""

    @pytest.fixture
    def app(self):
        """Create a Flask app for testing."""
        app = Flask(__name__)
        app.config["TESTING"] = True
        return app

    def test_sensible_size_constants(self):
        """Test that size constants are reasonable."""
        assert Pagination.MIN_SIZE > 0
        assert Pagination.MAX_SIZE > Pagination.MIN_SIZE
        assert Pagination.DEFAULT_SIZE >= Pagination.MIN_SIZE
        assert Pagination.DEFAULT_SIZE <= Pagination.MAX_SIZE

    def test_default_values(self):
        """Test default pagination values."""
        p = Pagination()
        assert p.offset == 0
        assert p.size == Pagination.DEFAULT_SIZE
        assert p.total_count is None

    def test_from_request_defaults(self, app):
        """Test parsing request with no parameters."""
        with app.test_request_context("/"):
            p = Pagination.from_request()
            assert p.offset == 0
            assert p.size == Pagination.DEFAULT_SIZE
            assert p.total_count is None

    def test_from_request_with_params(self, app):
        """Test parsing request with pagination parameters."""
        with app.test_request_context("/?after=50&size=25"):
            p = Pagination.from_request()
            assert p.offset == 50
            assert p.size == 25

    def test_from_request_clamps_size(self, app):
        """Test that size is clamped to MAX_SIZE."""
        # Ensure that our value is greater than MAX_SIZE.
        test_size = Pagination.MAX_SIZE + 97

        with app.test_request_context(f"/?size={test_size}"):
            p = Pagination.from_request()
            assert p.size == Pagination.MAX_SIZE

    def test_from_request_clamps_size_min(self, app):
        """Test that size is clamped to MIN_SIZE."""
        with app.test_request_context("/?size=0"):
            p = Pagination.from_request()
            assert p.size == Pagination.MIN_SIZE

    def test_from_request_negative_offset(self, app):
        """Test that negative offset defaults to 0."""
        with app.test_request_context("/?after=-50"):
            p = Pagination.from_request()
            assert p.offset == 0

    def test_from_request_invalid_values(self, app):
        """Test handling of invalid parameter values."""
        with app.test_request_context("/?after=abc&size=xyz"):
            p = Pagination.from_request()
            assert p.offset == 0
            assert p.size == Pagination.DEFAULT_SIZE

    def test_next_page(self):
        """Test next page calculation."""
        p = Pagination(offset=0, size=50, total_count=200)
        next_p = p.next_page
        assert next_p.offset == 50
        assert next_p.size == 50
        assert next_p.total_count == 200

    def test_previous_page(self):
        """Test previous page calculation."""
        p = Pagination(offset=100, size=50, total_count=200)
        prev_p = p.previous_page
        assert prev_p.offset == 50
        assert prev_p.size == 50
        assert prev_p.total_count == 200

    def test_previous_page_first_page(self):
        """Test that first page has no previous."""
        p = Pagination(offset=0, size=50)
        assert p.previous_page is None

    def test_previous_page_near_beginning(self):
        """Test previous page when offset < size."""
        p = Pagination(offset=25, size=50, total_count=200)
        prev_p = p.previous_page
        assert prev_p.offset == 0

    def test_first_page(self):
        """Test first page navigation."""
        p = Pagination(offset=200, size=50, total_count=500)
        first_p = p.first_page
        assert first_p.offset == 0
        assert first_p.size == 50
        assert first_p.total_count == 500

    def test_last_page_evenly_divisible(self):
        """Test last page calculation when total is evenly divisible by size."""
        p = Pagination(offset=0, size=50, total_count=200)
        last_p = p.last_page
        assert last_p.offset == 150  # (200-1) // 50 * 50 = 150
        assert last_p.size == 50
        assert last_p.total_count == 200

    def test_last_page_with_remainder(self):
        """Test last page calculation when there's a remainder."""
        p = Pagination(offset=0, size=100, total_count=235)
        last_p = p.last_page
        assert last_p.offset == 200  # (235-1) // 100 * 100 = 200
        assert last_p.size == 100

    def test_last_page_single_page(self):
        """Test last page calculation when all results fit on one page."""
        p = Pagination(offset=0, size=100, total_count=50)
        last_p = p.last_page
        assert last_p.offset == 0  # Only one page

    def test_last_page_no_total_count(self):
        """Test that last_page returns None when total_count is None."""
        p = Pagination(offset=0, size=50)
        assert p.last_page is None

    def test_page_loaded_has_next(self):
        """Test detecting next page when results > size."""
        p = Pagination(offset=0, size=10)
        results = list(range(11))  # 11 items, size=10
        trimmed, has_next = p.page_loaded(results)
        assert len(trimmed) == 10
        assert has_next is True

    def test_page_loaded_no_next(self):
        """Test detecting last page when results <= size."""
        p = Pagination(offset=0, size=10)
        results = list(range(10))  # Exactly 10 items
        trimmed, has_next = p.page_loaded(results)
        assert len(trimmed) == 10
        assert has_next is False

    def test_page_loaded_fewer_than_size(self):
        """Test when results are fewer than page size (last page with partial results)."""
        p = Pagination(offset=0, size=10)
        results = list(range(7))  # Only 7 items
        trimmed, has_next = p.page_loaded(results)
        assert len(trimmed) == 7
        assert has_next is False

    def test_repr(self):
        """Test string representation."""
        p = Pagination(offset=50, size=25, total_count=200)
        repr_str = repr(p)
        assert "offset=50" in repr_str
        assert "size=25" in repr_str
        assert "total_count=200" in repr_str
