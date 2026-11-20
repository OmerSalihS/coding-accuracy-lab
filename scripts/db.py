"""Shared database helpers used by every script."""
import os

import psycopg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://charta:charta@localhost:5433/charta"
)


def connect():
    return psycopg.connect(DATABASE_URL)


def normalize_code(code: str) -> str:
    """'m54.50 ' -> 'M5450'. Codes are stored without dots."""
    return code.strip().upper().replace(".", "")


def display_code(code: str) -> str:
    """'M5450' -> 'M54.50'. ICD-10-CM puts the dot after the 3rd character."""
    return code if len(code) <= 3 else f"{code[:3]}.{code[3:]}"
