"""Tiny database helper. One connection per request, which is what a serverless host needs.

The connection is cached on Flask's `g` for the duration of a request and closed in the app's
teardown hook (see app.py). Outside a request (scripts, tests) every call opens and closes its
own connection, so nothing is left behind.

prepare_threshold=None turns off prepared statements: Supabase's transaction pooler (the one
meant for serverless) does not support them.
"""
import os
from contextlib import contextmanager

import psycopg
from flask import g, has_app_context
from psycopg.rows import dict_row


def _open():
    return psycopg.connect(os.environ["DATABASE_URL"], prepare_threshold=None,
                           row_factory=dict_row, connect_timeout=10)


def connect():
    """The request's connection, opened once and reused by query()/execute()."""
    if has_app_context():
        conn = getattr(g, "_db_conn", None)
        if conn is None or conn.closed:
            conn = _open()
            g._db_conn = conn
        return conn
    return _open()


def close_connection(exc=None):
    """Commit or discard the request's transaction, then close it."""
    if not has_app_context():
        return
    conn = g.pop("_db_conn", None)
    if conn is None or conn.closed:
        return
    try:
        if exc is None:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()


def query(sql, params=(), one=False):
    """Run a SELECT and return a list of dicts (or one dict / None when one=True)."""
    shared = has_app_context()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except BaseException:
        # A failed statement aborts the request's transaction. Roll it back so a view that
        # catches the error (the duplicate order_key case) can keep using the connection.
        if not conn.closed:
            conn.rollback()
        if not shared and not conn.closed:
            conn.close()
        raise
    if not shared:
        conn.close()
    return (rows[0] if rows else None) if one else rows


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE and commit it."""
    shared = has_app_context()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except BaseException:
        if not conn.closed:
            conn.rollback()
        if not shared and not conn.closed:
            conn.close()
        raise
    if not shared:
        conn.close()


@contextmanager
def transaction():
    """Several statements as one transaction: either all of them land or none do."""
    shared = has_app_context()
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        if not shared:
            conn.close()
