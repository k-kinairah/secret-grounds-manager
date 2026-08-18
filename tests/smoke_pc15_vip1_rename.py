"""PC-15's default display name change: "PC 15" -> "VIP 1".

Covers:
- A fresh install seeds PC-15 with display_name "VIP 1" directly.
- An existing shop whose PC-15 row is still the untouched "PC 15" default
  gets renamed to "VIP 1" by rename_untouched_default_pc15_display_name().
- An owner-customized PC-15 display_name is never overwritten.
- pc_name/is_vip and every other identity/routing field are untouched --
  this migration only ever writes display_name.
"""
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        # --- Fresh install: seed_default_pc_layouts writes "VIP 1" directly. ---
        fresh_db_path = Path(temp_directory) / "fresh.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(fresh_db_path)

        from server.database import get_connection, initialize_database

        initialize_database()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT pc_name, display_name, is_vip FROM pc_layouts WHERE pc_name = 'PC-15'"
            ).fetchone()
        assert row is not None
        assert row["pc_name"] == "PC-15", "internal pc_name identifier must remain PC-15"
        assert row["display_name"] == "VIP 1"
        assert row["is_vip"] == 1

        # --- Existing shop, untouched default: "PC 15" -> "VIP 1". ---
        untouched_db_path = Path(temp_directory) / "untouched.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(untouched_db_path)
        initialize_database()

        with get_connection() as connection:
            connection.execute(
                "UPDATE pc_layouts SET display_name = 'PC 15' WHERE pc_name = 'PC-15'"
            )
            connection.commit()

        from server.database import rename_untouched_default_pc15_display_name

        with get_connection() as connection:
            rename_untouched_default_pc15_display_name(connection)
            connection.commit()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT pc_name, display_name, is_vip FROM pc_layouts WHERE pc_name = 'PC-15'"
            ).fetchone()
        assert row["pc_name"] == "PC-15"
        assert row["display_name"] == "VIP 1"
        assert row["is_vip"] == 1

        # Re-running must be a safe no-op (the row no longer reads "PC 15").
        with get_connection() as connection:
            rename_untouched_default_pc15_display_name(connection)
            connection.commit()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT display_name FROM pc_layouts WHERE pc_name = 'PC-15'"
            ).fetchone()
        assert row["display_name"] == "VIP 1"

        # --- Owner-customized display name is never overwritten. ---
        customized_db_path = Path(temp_directory) / "customized.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(customized_db_path)
        initialize_database()

        with get_connection() as connection:
            connection.execute(
                "UPDATE pc_layouts SET display_name = 'Tyrone Station' WHERE pc_name = 'PC-15'"
            )
            connection.commit()

        with get_connection() as connection:
            rename_untouched_default_pc15_display_name(connection)
            connection.commit()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT pc_name, display_name FROM pc_layouts WHERE pc_name = 'PC-15'"
            ).fetchone()
        assert row["pc_name"] == "PC-15", "internal identifier must never change"
        assert row["display_name"] == "Tyrone Station", "an owner-customized display name must never be overwritten"

        # Also confirmed via the full initialize_database() path (not just
        # the migration function called in isolation), since that's how it
        # actually runs in production.
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(customized_db_path)
        initialize_database()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT display_name FROM pc_layouts WHERE pc_name = 'PC-15'"
            ).fetchone()
        assert row["display_name"] == "Tyrone Station"

        print("PC-15 VIP 1 rename smoke test passed.")


if __name__ == "__main__":
    main()
