"""
We rely on all of our sqlalchemy models being listed here, so that we can
make sure they are all registered with the declarative base.
This is necessary to make sure that all of our models are properly reflected in
the database when we run migrations or create a new database.
"""

import palace.registry.sqlalchemy.model.admin
import palace.registry.sqlalchemy.model.audience
import palace.registry.sqlalchemy.model.base
import palace.registry.sqlalchemy.model.collection_summary
import palace.registry.sqlalchemy.model.configuration_setting
import palace.registry.sqlalchemy.model.delegated_patron_identifier
import palace.registry.sqlalchemy.model.external_integration
import palace.registry.sqlalchemy.model.hyperlink
import palace.registry.sqlalchemy.model.library
import palace.registry.sqlalchemy.model.place
import palace.registry.sqlalchemy.model.resource
import palace.registry.sqlalchemy.model.service_area
