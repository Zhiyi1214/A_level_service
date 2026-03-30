from config import settings

if settings.USE_POSTGRES:
    from storage.postgres import PostgresStore

    store = PostgresStore(settings.DATABASE_URL)
else:
    from storage.sqlite import SQLiteStore

    store = SQLiteStore(settings.DATABASE_PATH)
