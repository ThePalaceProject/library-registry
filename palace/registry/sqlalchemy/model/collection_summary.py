"""CollectionSummary model for library collection information."""

from __future__ import annotations

from flask_babel import lazy_gettext as _
from sqlalchemy import Column, ForeignKey, Index, Integer, Unicode
from sqlalchemy.orm.session import Session

from palace.registry.sqlalchemy.model.base import Base
from palace.registry.sqlalchemy.util import get_one_or_create
from palace.registry.util.language import LanguageCodes


class CollectionSummary(Base):
    """A summary of a collection held by a library.

    We only need to know the language of the collection and
    approximately how big it is.
    """

    __tablename__ = "collectionsummaries"

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("libraries.id"), index=True)
    language = Column(Unicode)
    size = Column(Integer)

    @classmethod
    def set(cls, library, language, size):
        """Create or update a CollectionSummary for the given
        library and language.

        :return: An up-to-date CollectionSummary.
        """
        _db = Session.object_session(library)

        size = int(size)
        if size < 0:
            raise ValueError(_("Collection size cannot be negative."))

        # This might return None, which is fine. We'll store it as a
        # collection with an unknown language. This also covers the
        # case where the library specifies its collection size but
        # doesn't mention any languages.
        language_code = LanguageCodes.string_to_alpha_3(language)

        summary, is_new = get_one_or_create(
            _db, CollectionSummary, library=library, language=language_code
        )
        summary.size = size
        return summary


Index(
    "ix_collectionsummary_language_size",
    CollectionSummary.language,
    CollectionSummary.size,
)
