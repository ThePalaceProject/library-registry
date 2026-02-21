import re
from collections.abc import Callable, Iterator
from typing import Any, TypedDict


class _LinkEntry(TypedDict):
    endpoint: str
    production_only: bool | None
    url_kwargs: dict[str, Any]
    link_attrs: dict[str, Any]


class RouteLinkRegistry:
    """Tracks OPDS catalog links registered via the @register decorator."""

    def __init__(self):
        self._entries: list[_LinkEntry] = []

    def register(
        self,
        *,
        url_kwargs: dict[str, Any] | None = None,
        production_only: bool | None = None,
        **link_attrs: Any,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator that marks a route as a link to include in OPDS catalog responses.

        :param url_kwargs: Keyword arguments passed to url_for when resolving the link href.
        :param production_only: Controls when the link is included. None means always; True
            means only in production-only library feeds; False means all-library feeds.
        :param link_attrs: Attributes added to the link element (e.g. rel, type, templated).
        """

        def decorator(f):
            self._entries.append(
                {
                    "endpoint": f.__name__,
                    "production_only": production_only,
                    "url_kwargs": url_kwargs or {},
                    "link_attrs": link_attrs,
                }
            )
            return f

        return decorator

    def links(
        self, url_for: Callable[..., str], production_only: bool = True
    ) -> Iterator[dict[str, Any]]:
        """Yield a link dict for each registered link, resolving URLs.

        Links registered with `production_only=None` are always included. Links registered
        with a boolean `production_only` are included only when that value matches the
        `production_only` argument passed here.

        :param url_for: Callable that resolves an endpoint name and kwargs to a URL.
        :param production_only: Filters links registered with a non-None `production_only` value,
            including only those whose registered value matches this parameter.
        """
        for entry in self._entries:
            if (
                entry["production_only"] is not None
                and entry["production_only"] != production_only
            ):
                continue
            href = url_for(entry["endpoint"], **entry["url_kwargs"])
            link_attrs = entry["link_attrs"]
            if link_attrs.get("templated"):
                # Flask percent-encodes URLs. We need to undo that for `{` and `}` in templated links.
                href = re.sub(r"%7[Bb]", "{", href)
                href = re.sub(r"%7[Dd]", "}", href)
            yield {"href": href, **link_attrs}
