# Keep this module lightweight: importing `app.api.schemas` must not load `routes`
# (avoids circular imports via integrations → schemas → api package).
