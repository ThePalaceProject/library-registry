from io import StringIO

from lxml import etree


class XMLParser:
    """Helper functions to process XML data."""

    ##### Class Constants ####################################################  # noqa: E266
    NAMESPACES = {}

    ##### Public Interface / Magic Methods ###################################  # noqa: E266
    def process_all(self, xml, xpath, namespaces=None, handler=None, parser=None):
        if not parser:
            parser = etree.XMLParser()

        if not handler:
            handler = self.process_one

        if isinstance(xml, str):
            root = etree.parse(StringIO(xml), parser)
        else:
            root = xml

        for i in root.xpath(xpath, namespaces=namespaces):
            data = handler(i, namespaces)
            if data:
                yield data

    def process_one(self, tag, namespaces):
        """Abstract, should be overridden by child class"""
        return None

    ##### Private Methods ####################################################  # noqa: E266
    def _cls(self, tag_name, class_name):
        """Return an XPath expression that will find a tag with the given CSS class."""
        fmt_string = 'descendant-or-self::node()/%s[contains(concat(" ", normalize-space(@class), " "), " %s ")]'
        return fmt_string % (tag_name, class_name)

    ##### Properties and Getters/Setters #####################################  # noqa: E266

    ##### Class Methods ######################################################  # noqa: E266

    ##### Private Class Methods ##############################################  # noqa: E266
    @classmethod
    def _xpath(cls, tag, expression, namespaces=None):
        """Wrapper to do a namespaced XPath expression."""
        if not namespaces:
            namespaces = cls.NAMESPACES

        return tag.xpath(expression, namespaces=namespaces)

    @classmethod
    def _xpath1(cls, tag, expression, namespaces=None):
        """Wrapper to do a namespaced XPath expression."""
        values = cls._xpath(tag, expression, namespaces=namespaces)
        if not values:
            return None

        return values[0]
