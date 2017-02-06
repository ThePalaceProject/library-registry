from nose.tools import (
    eq_,
    set_trace,
)

import feedparser
from lxml import etree

from . import (
    DatabaseTest,
)

from opds import NavigationFeed

class TestOPDS(DatabaseTest):

    def test_library_feed(self):
        l1 = self._library("The New York Public Library")
        l2 = self._library("Brooklyn Public Library")
        class TestAnnotator(object):
            def annotate_feed(self, feed_obj):
                new_tag = feed_obj.E.randomtag()
                new_tag.text = "Random text inserted by annotator."
                feed_obj.feed.append(new_tag)
                
        feed = NavigationFeed(
            self._db, "A Feed!", "http://url/", [l1, l2],
            TestAnnotator()
        )
        feed = unicode(feed)
        # The annotator modified the feed.
        assert (
            '<randomtag>Random text inserted by annotator.</randomtag>'
            in feed
        )

        parsed = feedparser.parse(feed)
        eq_("A Feed!", parsed['feed']['title'])
        eq_("http://url/", parsed['feed']['link'])

        # Each library became an entry in the feed.
        eq_([l1.name, l2.name], [x['title'] for x in parsed['entries']])
        
    def test_library_entry(self):

        library = self._library("The New York Public Library")
        library.urn = "123-abc"
        library.description = "It's a wonderful library."
        library.opds_url = "https://opds/"
        library.web_url = "https://nypl.org/"
        library.logo = "Fake logo"
        
        entry = NavigationFeed.library_entry(library)
        entry = etree.tostring(entry)
        feed = feedparser.parse(entry)
        [entry] = feed['entries']
        eq_(library.name, entry['title'])
        eq_(library.urn, entry['id'])
        [content] = entry['content']
        eq_(library.description, content['value'])
        eq_("text/plain", content['type'])

        eq_(entry['updated'], NavigationFeed._strftime(library.timestamp))

        [web, opds, logo] = sorted(entry['links'], key=lambda x: x['rel'])

        eq_(library.web_url, web['href'])
        eq_("alternate", web['rel'])
        eq_("text/html", web['type'])

        eq_(library.opds_url, opds['href'])
        eq_(NavigationFeed.CATALOG_REL, opds['rel'])
        eq_(NavigationFeed.GENERIC_OPDS_TYPE, opds['type'])

        eq_(library.logo_data_uri, logo['href'])
        eq_(NavigationFeed.THUMBNAIL_REL, logo['rel'])
        eq_("image/png", logo['type'])

