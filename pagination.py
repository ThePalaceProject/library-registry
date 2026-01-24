"""Pagination support for OPDS feeds."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import flask
from sqlalchemy import func

from model import Library


class OrderFacet(StrEnum):
    """Sort order options for library feeds."""

    DEFAULT = "default"  # Alias for TIMESTAMP (reverse chronological).
    TIMESTAMP = "timestamp"  # Newest first (matches crawlable feed default).
    NAME = "name"  # Alphabetical A-Z.
    NEARBY = "nearby"  # Nearby libraries first (requires location data).
    RANDOM = "random"  # Randomized order.

    @property
    def requires_location(self) -> bool:
        """Return True if this order requires location data."""
        return self == self.NEARBY

    @property
    def sort_order_expressions(self) -> list:
        """Return SQLAlchemy order_by expressions for this sort order.

        Note: NEARBY ordering is handled differently via Library.nearby() query.
        """
        if self in (self.DEFAULT, self.TIMESTAMP):
            # Reverse chronological: newest libraries first, then alphabetical.
            return [
                Library.timestamp.desc(),
                func.upper(Library.name).asc(),
                Library.id.asc(),
            ]
        elif self == self.NAME:
            # Alphabetical: A-Z by name, then newest first as tiebreaker.
            return [
                func.upper(Library.name).asc(),
                Library.timestamp.desc(),
                Library.id.asc(),
            ]
        elif self == self.NEARBY:
            # Location-based ordering is handled separately in controller.
            # This returns fallback ordering for libraries beyond nearby radius.
            return [
                func.upper(Library.name).asc(),
                Library.id.asc(),
            ]
        elif self == self.RANDOM:
            # Randomized order (PostgreSQL).
            return [func.random()]
        else:
            raise ValueError(f"Unknown order facet: {self}")


@dataclass
class Pagination:
    """Offset-based pagination for library feeds.

    Similar to Palace Circulation Manager's Pagination class, but standalone.
    """

    DEFAULT_SIZE = 100
    MAX_SIZE = 100
    MIN_SIZE = 1

    offset: int = 0
    size: int = DEFAULT_SIZE
    total_count: int | None = None  # Total items across all pages.

    @classmethod
    def from_request(cls, request: flask.Request = None, _db=None) -> Pagination:
        """Parse pagination parameters from Flask request.

        :param request: Flask request object (defaults to flask.request).
        :param _db: Database session for loading configuration (optional).
        :return: Pagination instance with validated parameters.
        """
        from config import Configuration
        from model import ConfigurationSetting

        request = request or flask.request

        # Get configurable default.
        default_size = cls.DEFAULT_SIZE
        if _db:
            setting = ConfigurationSetting.sitewide(
                _db, Configuration.CRAWLABLE_PAGE_SIZE
            )
            if setting and setting.int_value:
                default_size = setting.int_value

        # Parse and validate size parameter.
        try:
            size = int(request.args.get("size", default_size))
            size = max(cls.MIN_SIZE, min(cls.MAX_SIZE, size))  # Clamp to [1, 100].
        except (ValueError, TypeError):
            size = default_size

        # Parse offset.
        try:
            offset = int(request.args.get("after", 0))
            offset = max(0, offset)
        except (ValueError, TypeError):
            offset = 0

        return cls(offset=offset, size=size)

    @property
    def next_page(self) -> Pagination:
        """Return pagination for the next page."""
        return Pagination(
            offset=self.offset + self.size, size=self.size, total_count=self.total_count
        )

    @property
    def previous_page(self) -> Pagination | None:
        """Return pagination for the previous page, or None if on first page."""
        if self.offset == 0:
            return None
        prev_offset = max(0, self.offset - self.size)
        return Pagination(
            offset=prev_offset, size=self.size, total_count=self.total_count
        )

    @property
    def first_page(self) -> Pagination:
        """Return pagination for the first page."""
        return Pagination(offset=0, size=self.size, total_count=self.total_count)

    @property
    def last_page(self) -> Pagination | None:
        """Return Pagination for last page based on total_count, or None if count unknown."""
        if self.total_count is None:
            return None
        # Calculate last page offset: floor division ensures we land on a page boundary.
        last_offset = max(0, ((self.total_count - 1) // self.size) * self.size)
        return Pagination(
            offset=last_offset, size=self.size, total_count=self.total_count
        )

    def modify_query(self, query):
        """Apply pagination to a SQLAlchemy query.

        :param query: SQLAlchemy query object.
        :return: Modified query with OFFSET and LIMIT applied (+1 to detect next page).
        """
        return query.offset(self.offset).limit(self.size + 1)

    def page_loaded(self, results: list) -> tuple[list, bool]:
        """Process query results to determine if there's a next page.

        :param results: List of results from database (may have size+1 items).
        :return: Tuple of (trimmed_results, has_next_page).
        """
        if len(results) > self.size:
            return results[: self.size], True
        return results, False

    def __repr__(self):
        return f"Pagination(offset={self.offset}, size={self.size}, total_count={self.total_count})"
