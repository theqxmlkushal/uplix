"""Tiny database helper. One connection per request, which is what a serverless host needs.

prepare_threshold=None turns off prepared statements: Supabase's transaction pooler (the one meant
for serverless) does not support them.
"""
import os

import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None, row_factory=dict_row, connect_timeout=10)


def query(sql, params=(), one=False):
    """Run a SELECT and return a list of dicts (or one dict / None when one=True)."""
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE in its own transaction."""
    with connect() as conn:
        conn.execute(sql, params)
