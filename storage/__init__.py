from config import settings

settings.validate_database_url()

from storage.postgres import PostgresStore

store = PostgresStore(settings.DATABASE_URL)
