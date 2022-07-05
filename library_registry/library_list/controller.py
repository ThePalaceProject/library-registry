import flask
import time
from flask import (Response, request)
from flask_babel import lazy_gettext as _
from sqlalchemy.orm import (defer, joinedload)
from library_registry.opds import OPDSCatalog
from library_registry.config import CannotLoadConfiguration
from library_registry.emailer import Emailer
from library_registry.util.shared_controller import BaseController, LibraryRegistryAnnotator
from library_registry.library_list.templates.templates import OPENSEARCH_TEMPLATE
from library_registry.util.app_server import catalog_response
from library_registry.constants import (
    OPENSEARCH_MEDIA_TYPE,
)
from library_registry.model import (
    Library,
)

class LibraryListController(BaseController):

    OPENSEARCH_TEMPLATE = OPENSEARCH_TEMPLATE

    def __init__(self, app, emailer_class=Emailer):
        super(LibraryListController, self).__init__(app)
        self.annotator = LibraryRegistryAnnotator(app)
        self.log = self.app.log
        emailer = None
        try:
            emailer = emailer_class.from_sitewide_integration(self._db)
        except CannotLoadConfiguration as e:
            self.log.error(
                "Cannot load email configuration. Will not be sending any emails.",
                exc_info=e
            )
        self.emailer = emailer

    # Not currently used
    def nearby(self, location, live=True):
        qu = Library.nearby(self._db, location, production=live)
        qu = qu.limit(5)
        if live:
            nearby_controller = 'libr_list.nearby'
        else:
            nearby_controller = 'libr_list.nearby_qa'
        this_url = self.app.url_for(nearby_controller)
        catalog = OPDSCatalog(
            self._db, str(_("Libraries near you")), this_url, qu,
            annotator=self.annotator, live=live
        )
        return catalog_response(catalog)

    def search(self, location, live=True):
        query = request.args.get('q')
        if live:
            search_controller = 'libr_list.search'
        else:
            search_controller = 'libr_list.search_qa'
        if query:
            # Run the query and send the results.
            results = Library.search(
                self._db, location, query, production=live
            )

            this_url = self.app.url_for(
                search_controller, q=query
            )
            catalog = OPDSCatalog(
                self._db, str(_('Search results for "%s"')) % query,
                this_url, results,
                annotator=self.annotator, live=live
            )
            return catalog_response(catalog)
        else:
            # Send the search form.
            body = self.OPENSEARCH_TEMPLATE % dict(
                name=_("Find your library"),
                description=_("Search by ZIP code, city or library name."),
                tags="",
                url_template=self.app.url_for(
                    search_controller) + "?q={searchTerms}"
            )
            headers = {}
            headers['Content-Type'] = OPENSEARCH_MEDIA_TYPE
            headers['Cache-Control'] = "public, no-transform, max-age: %d" % (
                3600 * 24 * 30
            )
            return Response(body, 200, headers)

    def libraries_opds(self, live=True, location=None):
        """Return all the libraries in OPDS format

        :param live: If this is True, then only production libraries are shown.
        :param location: If this is set, then libraries near this point will be
           promoted out of the alphabetical list.
        """
        alphabetical = self._db.query(Library).order_by(Library.name)

        # We always want to filter out cancelled libraries.  If live, we also filter out
        # libraries that are in the testing stage, i.e. only show production libraries.
        alphabetical = alphabetical.filter(
            Library._feed_restriction(production=live))

        # Pick up each library's hyperlinks and validation
        # information; this will save database queries when building
        # the feed.
        alphabetical = alphabetical.options(
            joinedload('hyperlinks'),
            joinedload('hyperlinks', 'resource'),
            joinedload('hyperlinks', 'resource', 'validation'),
        )
        alphabetical = alphabetical.options(defer('logo'))
        if location is None:
            # No location data is available. Use the alphabetical list as
            # the list of libraries.
            a = time.time()
            libraries = alphabetical.all()
            b = time.time()
            self.log.info(
                "Built alphabetical list of all libraries in %.2fsec" % (b-a))
        else:
            # Location data is available. Get the list of nearby libraries, then get
            # the rest of the list in alphabetical order.

            # We can't easily do the joindeload() thing for this
            # query, because it doesn't simply return Library objects,
            # but it won't return more than five results.
            a = time.time()
            nearby_libraries = Library.nearby(
                self._db, location, production=live
            ).limit(5).all()
            b = time.time()
            self.log.info("Fetched libraries near %s in %.2fsec" %
                          (location, b-a))

            # Exclude nearby libraries from the alphabetical query
            # to get a list of faraway libraries.
            faraway_libraries = alphabetical.filter(
                ~Library.id.in_([x.id for x, distance in nearby_libraries])
            )
            c = time.time()
            libraries = nearby_libraries + faraway_libraries.all()
            self.log.info("Fetched libraries far from %s in %.2fsec" %
                          (location, c-b))

        url = self.app.url_for("libr_list.libraries_opds")
        a = time.time()
        catalog = OPDSCatalog(
            self._db, 'Libraries', url, libraries,
            annotator=self.annotator, live=live
        )
        b = time.time()
        self.log.info("Built library catalog in %.2fsec" % (b-a))
        return catalog_response(catalog)

    def library(self):
        library = request.library
        this_url = self.app.url_for(
            'libr_list.library', uuid=library.internal_urn
        )
        catalog = OPDSCatalog(
            self._db, library.name,
            this_url, [library],
            annotator=self.annotator, live=False,
        )
        return catalog_response(catalog)
