from config import settings
from storage.sqlite import SQLiteStore

store = SQLiteStore(settings.DATABASE_PATH)
