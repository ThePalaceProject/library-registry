OPENSEARCH_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
    '<ShortName>%(name)s</ShortName>'
    '<Description>%(description)s</Description>'
    '<Tags>%(tags)s</Tags>'
    '<Url type="application/atom+xml;profile=opds-catalog" template="%(url_template)s"/>'
    '</OpenSearchDescription>'
)