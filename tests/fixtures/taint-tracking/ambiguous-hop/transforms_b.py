"""Second definition of the ambiguous ``transform`` hop."""


def transform(payload, db, run_migration):
    return run_migration(payload, db)
