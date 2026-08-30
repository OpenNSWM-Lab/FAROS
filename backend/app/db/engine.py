"""
Database Engine - SQLite connection and session management.

Designed for easy migration to PostgreSQL later.
"""

import os
import logging
from typing import Generator, Optional, Dict, Any
from contextlib import contextmanager

from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.core.paths import get_data_dir

logger = logging.getLogger(__name__)

# Database path
_DATA_DIR = str(get_data_dir())
_DB_PATH = os.path.join(_DATA_DIR, "app.db")

# Database URL (SQLite for now, easily swappable to PostgreSQL)
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{_DB_PATH}")

# Engine singleton
_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """Get or create the database engine."""
    global _engine
    
    if _engine is None:
        # Ensure data directory exists
        os.makedirs(_DATA_DIR, exist_ok=True)
        
        # Create engine with appropriate settings
        connect_args = {}
        if DATABASE_URL.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        
        _engine = create_engine(
            DATABASE_URL,
            echo=os.environ.get("DB_ECHO", "").lower() == "true",
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        
        logger.info(f"Database engine created: {DATABASE_URL}")
    
    return _engine


def get_session() -> Generator[Session, None, None]:
    """Get a database session (for FastAPI dependency injection)."""
    engine = get_engine()
    with Session(engine) as session:
        yield session


@contextmanager
def get_session_context() -> Generator[Session, None, None]:
    """Get a database session as context manager."""
    engine = get_engine()
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def init_db(drop_existing: bool = False) -> None:
    """
    Initialize the database.
    
    Creates all tables defined in models.
    In production, use Alembic migrations instead.
    
    Args:
        drop_existing: If True, drop all existing tables first (DANGEROUS!)
    """
    from . import models  # Import to register models
    
    engine = get_engine()
    
    if drop_existing:
        logger.warning("Dropping all existing tables!")
        SQLModel.metadata.drop_all(engine)
    
    SQLModel.metadata.create_all(engine)
    logger.info("Database tables created/verified")


def test_connection() -> bool:
    """Test database connection."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        return False


def get_migration_status() -> Dict[str, Any]:
    """
    Get current migration status.
    
    Returns dict with:
    - status: "current", "pending", "error"
    - revision: current revision or None
    - error: error message if any
    """
    result = {
        "status": "unknown",
        "revision": None,
        "error": None,
    }
    
    try:
        # Check if alembic_version table exists
        engine = get_engine()
        with engine.connect() as conn:
            # Check for alembic version table
            if DATABASE_URL.startswith("sqlite"):
                check_sql = text(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                )
            else:
                check_sql = text(
                    "SELECT tablename FROM pg_tables WHERE tablename='alembic_version'"
                )
            
            table_exists = conn.execute(check_sql).fetchone() is not None
            
            if not table_exists:
                # No migrations run yet, but tables might exist from init_db
                # Check if any of our tables exist
                if DATABASE_URL.startswith("sqlite"):
                    tables_sql = text(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='code_projects'"
                    )
                else:
                    tables_sql = text(
                        "SELECT tablename FROM pg_tables WHERE tablename='code_projects'"
                    )
                
                has_tables = conn.execute(tables_sql).fetchone() is not None
                
                if has_tables:
                    result["status"] = "initialized_no_migrations"
                    result["revision"] = "init"
                else:
                    result["status"] = "not_initialized"
                return result
            
            # Get current revision
            version_sql = text("SELECT version_num FROM alembic_version")
            row = conn.execute(version_sql).fetchone()
            
            if row:
                result["revision"] = row[0]
                result["status"] = "current"
            else:
                result["status"] = "no_revision"
                
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    
    return result


def close_engine() -> None:
    """Close the database engine."""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None
        logger.info("Database engine closed")
