from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from apps.config import (
    DB_HOST,
    DB_PORT,
    DB_USERNAME,
    DB_PASSWORD,
    DB_DATABASE
)

DATABASE_URL = (
    f"mysql+pymysql://{DB_USERNAME}:{DB_PASSWORD}"
    f"@{DB_HOST}:{DB_PORT}/{DB_DATABASE}"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

def test_connection():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            return True
    except Exception as e:
        return str(e)

def get_db():
    """Dependency to get database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

Base = declarative_base()