After creating the database for this application, you must enable two
PostgreSQL extensions:

```
CREATE EXTENSION postgis;
CREATE EXTENSION fuzzystrmatch;
```

This cannot be done from within the application because it requires
superuser permission.

The `postgis` extension requires that PostGIS be installed; the
`fuzzystrmatch` extension comes with PostgreSQL.
