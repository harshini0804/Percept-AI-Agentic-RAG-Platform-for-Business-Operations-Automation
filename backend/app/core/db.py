"""
Shared database connection (Section 2 — PostgreSQL + pgvector,
single Postgres instance for both relational and vector data).
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from pgvector.psycopg2 import register_vector

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rag_user:rag_password@postgres:5432/rag_platform",
)


def get_connection():
    """
    Returns a new psycopg2 connection with pgvector types registered,
    so Python lists/np arrays convert to/from the VECTOR column type
    automatically.
    """
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    register_vector(conn)
    return conn