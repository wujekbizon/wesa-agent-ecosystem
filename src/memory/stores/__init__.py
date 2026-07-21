"""Memory stores — one module per table. Each store wraps a psycopg3
connection and exposes typed read/write methods.

Level 1: policy, preference. Level 2 adds fact, episodic, trace.
"""
