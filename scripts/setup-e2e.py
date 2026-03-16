#!/usr/bin/env python3
"""
Setup E2E test environment for Project Studio.

Creates testuser in the database if it doesn't exist.
This is needed for Playwright E2E tests.
"""

import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

# Add API directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "memogarden-api"))

from api.middleware.service import hash_password
from utils import hash_chain, isodatetime


def get_db_path():
    """Get the database path from env or use default."""
    import os
    return os.getenv("MEMOGARDEN_CORE_DB", "core.db")


def create_testuser():
    """Create testuser in the database."""
    db_path = get_db_path()
    print(f"Using database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        # Check if testuser already exists
        existing = conn.execute(
            "SELECT * FROM users WHERE username = ?", ("testuser",)
        ).fetchone()

        if existing:
            print("✓ testuser already exists")
            return True

        # Create the user
        user_id = str(uuid.uuid4())
        username = "testuser"
        password = "TestPass123"
        password_hash = hash_password(password)
        now = isodatetime.now()

        # Compute entity hash
        entity_hash = hash_chain.compute_entity_hash(
            entity_type="User",
            created_at=now,
            updated_at=now,
            previous_hash=None
        )

        # Create entity for user (links to users table)
        conn.execute(
            """INSERT INTO entity (uuid, type, hash, version, created_at, updated_at, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, "User", entity_hash, 1, now, now, json.dumps({}))
        )

        # Create user (id references entity.uuid)
        conn.execute(
            """INSERT INTO users (id, username, password_hash, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (user_id, username, password_hash, 0, now)  # is_admin=0 for non-admin
        )
        conn.commit()

        print(f"✓ Created testuser (id: {user_id[:8]}...)")
        print(f"  Username: {username}")
        print(f"  Password: {password}")
        return True

    except sqlite3.IntegrityError as e:
        print(f"⚠ User creation failed: {e}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        conn.close()


if __name__ == "__main__":
    success = create_testuser()
    sys.exit(0 if success else 1)
