from sqlalchemy import create_engine, text
from backend.config import settings
engine = create_engine(settings.DATABASE_URL)
with engine.connect() as conn:
    print(conn.execute(text("SELECT * FROM pipeline_permissions")).fetchall())
