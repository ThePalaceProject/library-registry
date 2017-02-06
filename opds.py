import datetime
import logging

from lxml import builder, etree
from nose.tools import set_trace

class AtomFeed(object):

    TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ%z'

    ATOM_NS = 'http://www.w3.org/2005/Atom'
    APP_NS = 'http://www.w3.org/2007/app'
    #xhtml_ns = 'http://www.w3.org/1999/xhtml'
    DCTERMS_NS = 'http://purl.org/dc/terms/'
    OPDS_NS = 'http://opds-spec.org/2010/catalog'
    SCHEMA_NS = 'http://schema.org/'
    DRM_NS = 'http://librarysimplified.org/terms/drm'
    
    SIMPLIFIED_NS = "http://librarysimplified.org/terms/"
    BIBFRAME_NS = "http://bibframe.org/vocab/"

    nsmap = {
        None: ATOM_NS,
        'app': APP_NS,
        'dcterms' : DCTERMS_NS,
        'opds' : OPDS_NS,
        'drm' : DRM_NS,
        'schema' : SCHEMA_NS,
        'simplified' : SIMPLIFIED_NS,
        'bibframe' : BIBFRAME_NS,
    }

    default_typemap = {datetime: lambda e, v: _strftime(v)}
    E = builder.ElementMaker(typemap=default_typemap, nsmap=nsmap)
    SIMPLIFIED = builder.ElementMaker(typemap=default_typemap, nsmap=nsmap, namespace=SIMPLIFIED_NS)
    SCHEMA = builder.ElementMaker(typemap=default_typemap, nsmap=nsmap, namespace=SCHEMA_NS)

    @classmethod
    def _strftime(self, date):
        """
        Format a date the way Atom likes it (RFC3339?)
        """
        return date.strftime(self.TIME_FORMAT)


    @classmethod
    def add_link_to_feed(cls, feed, children=None, **kwargs):
        link = cls.E.link(**kwargs)
        feed.append(link)
        if children:
            for i in children:
                link.append(i)


    @classmethod
    def add_link_to_entry(cls, entry, children=None, **kwargs):
        #links.append(E.link(rel=rel, href=url, type=image_type))
        link = cls.E.link(**kwargs)
        entry.append(link)
        if children:
            for i in children:
                link.append(i)


    @classmethod
    def author(cls, *args, **kwargs):
        return cls.E.author(*args, **kwargs)


    @classmethod
    def category(cls, *args, **kwargs):
        return cls.E.category(*args, **kwargs)


    @classmethod
    def entry(cls, *args, **kwargs):
        return cls.E.entry(*args, **kwargs)


    @classmethod
    def id(cls, *args, **kwargs):
        return cls.E.id(*args, **kwargs)


    @classmethod
    def link(cls, *args, **kwargs):
        return cls.E.link(*args, **kwargs)


    @classmethod
    def makeelement(cls, *args, **kwargs):
        return cls.E._makeelement(*args, **kwargs)


    @classmethod
    def name(cls, *args, **kwargs):
        return cls.E.name(*args, **kwargs)


    @classmethod
    def schema_(cls, field_name):
        return "{%s}%s" % (cls.SCHEMA_NS, field_name)

    @classmethod
    def summary(cls, *args, **kwargs):
        return cls.E.summary(*args, **kwargs)


    @classmethod
    def title(cls, *args, **kwargs):
        return cls.E.title(*args, **kwargs)


    @classmethod
    def update(cls, *args, **kwargs):
        return cls.E.update(*args, **kwargs)


    @classmethod
    def updated(cls, *args, **kwargs):
        return cls.E.updated(*args, **kwargs)


    def __init__(self, title, url):
        self.feed = self.E.feed(
            self.E.id(url),
            self.E.title(title),
            self.E.updated(self._strftime(datetime.datetime.utcnow())),
            self.E.link(href=url, rel="self"),
        )


    def __unicode__(self):
        if self.feed is None:
            return None

        string_tree = etree.tostring(self.feed, pretty_print=True)
        return string_tree.encode("utf8")

class OPDSFeed(AtomFeed):

    GENERIC_OPDS_TYPE = "application/atom+xml;profile=opds-catalog"
    NAVIGATION_FEED_TYPE = GENERIC_OPDS_TYPE + ";kind=navigation"
    ENTRY_TYPE = "application/atom+xml;type=entry;profile=opds-catalog"

    CATALOG_REL = "http://opds-spec.org/catalog"
    THUMBNAIL_REL = "http://opds-spec.org/image/thumbnail"


class Annotator(object):

    def annotate_feed(self, feed):
        pass

    
class NavigationFeed(OPDSFeed):

    def __init__(self, _db, title, url, libraries, annotator=None):
        """Turn a list of libraries into a feed."""
        super(NavigationFeed, self).__init__(title, url)
        
        if not annotator:
            annotator = Annotator()
        self.annotator = annotator
       
        for library in libraries:
            self.feed.append(self.library_entry(library))
        annotator.annotate_feed(self)
            
    @classmethod
    def library_entry(cls, library):
        entry = AtomFeed.entry(
            AtomFeed.id(library.urn),
            AtomFeed.title(library.name),
            AtomFeed.updated(cls._strftime(library.timestamp))
        )
        
        # Add description.
        if library.description:
            content = AtomFeed.E.content(
                type="text"
            )
            content.text = library.description
            entry.append(content)
        
        # Add links.
        if library.opds_url:
            AtomFeed.add_link_to_entry(
                entry,
                href=library.opds_url,
                rel=cls.CATALOG_REL,
                type=cls.GENERIC_OPDS_TYPE
            )
        
        if library.web_url:
            AtomFeed.add_link_to_entry(
                entry,
                href=library.web_url,
                rel="alternate",
                type="text/html"
            )

        if library.logo:
            AtomFeed.add_link_to_entry(
                entry,
                href=library.logo_data_uri,
                type="image/png",
                rel=cls.THUMBNAIL_REL
            )

        return entry
