"""Shared parameterized metadata predicates for page and count queries."""


def metadata_filters(source=None, destination=None, cargo=None):
    predicates, args = [], []
    for value, fields in ((source, ("source_city", "source_company")),
                          (destination, ("destination_city", "destination_company")),
                          (cargo, ("cargo_name",))):
        if value is None or not value.strip():
            continue
        value = value.strip()
        if len(value) > 128:
            raise ValueError("Delivery filters must be at most 128 characters")
        # Literal substring, including %/_/backslash; not user-controlled SQL.
        predicates.append("(" + " OR ".join(f"LOCATE(%s,m.{field})>0" for field in fields) + ")")
        args.extend([value] * len(fields))
    if not predicates:
        return "", ()
    return (" AND EXISTS (SELECT 1 FROM dlog_meta m WHERE m.logid=dlog.logid AND "
            + " AND ".join(predicates) + ")", tuple(args))
