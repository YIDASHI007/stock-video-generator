from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path


def _detect_old_root(connection: sqlite3.Connection) -> Path | None:
    candidates = [
        ("outputs", "video_path", "\\data\\outputs\\"),
        ("publish_jobs", "manifest_path", "\\data\\publishes\\"),
        ("publish_accounts", "browser_profile_dir", "\\data\\publish-accounts\\"),
    ]
    for table, column, marker in candidates:
        try:
            row = connection.execute(
                f'SELECT "{column}" FROM "{table}" '
                f'WHERE "{column}" IS NOT NULL AND "{column}" != "" LIMIT 1'
            ).fetchone()
        except sqlite3.OperationalError:
            continue
        if not row or not isinstance(row[0], str):
            continue
        value = row[0]
        normalized = value.replace("/", "\\")
        index = normalized.lower().find(marker.lower())
        if index >= 0:
            return Path(normalized[:index]).resolve()
    return None


def _replacement_pairs(old_root: Path, new_root: Path) -> list[tuple[str, str]]:
    old_windows = str(old_root)
    new_windows = str(new_root)
    old_posix = old_root.as_posix()
    new_posix = new_root.as_posix()
    return [
        (old_windows.replace("\\", "\\\\"), new_windows.replace("\\", "\\\\")),
        (old_windows, new_windows),
        (old_posix, new_posix),
    ]


def _relocate_database(
    database_path: Path,
    replacements: list[tuple[str, str]],
) -> int:
    backup_path = database_path.with_suffix(".before-relocation.bak")
    shutil.copy2(database_path, backup_path)
    connection = sqlite3.connect(database_path)
    changed = 0
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        connection.execute("BEGIN IMMEDIATE")
        for table in tables:
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            for column in columns:
                name = column[1]
                declared_type = str(column[2]).upper()
                if not any(token in declared_type for token in ("CHAR", "TEXT", "CLOB")):
                    continue
                for old_value, new_value in replacements:
                    if not old_value or old_value == new_value:
                        continue
                    cursor = connection.execute(
                        f'UPDATE "{table}" '
                        f'SET "{name}" = replace("{name}", ?, ?) '
                        f'WHERE instr("{name}", ?) > 0',
                        (old_value, new_value, old_value),
                    )
                    changed += max(0, cursor.rowcount)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return changed


def _relocate_json_files(
    data_dir: Path,
    replacements: list[tuple[str, str]],
) -> int:
    changed = 0
    for path in data_dir.rglob("*.json"):
        if "publish-accounts" in path.parts:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        updated = original
        for old_value, new_value in replacements:
            if old_value and old_value != new_value:
                updated = updated.replace(old_value, new_value)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rewrite absolute project paths after moving the project."
    )
    parser.add_argument(
        "--old-root",
        type=Path,
        default=None,
        help="Original project root. Auto-detected from SQLite when omitted.",
    )
    parser.add_argument(
        "--new-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="New project root. Defaults to the directory containing scripts/.",
    )
    args = parser.parse_args()

    new_root = args.new_root.resolve()
    database_path = new_root / "data" / "database" / "stock_video.db"
    if not database_path.is_file():
        print("No migrated SQLite database found; path relocation is not required.")
        return 0

    connection = sqlite3.connect(database_path)
    try:
        old_root = args.old_root.resolve() if args.old_root else _detect_old_root(connection)
    finally:
        connection.close()
    if old_root is None:
        raise RuntimeError(
            "Could not detect the original project path. "
            "Run again with --old-root 'X:\\\\old\\\\project'."
        )
    if old_root == new_root:
        print(f"Project path is unchanged: {new_root}")
        return 0

    replacements = _replacement_pairs(old_root, new_root)
    database_changes = _relocate_database(database_path, replacements)
    json_changes = _relocate_json_files(new_root / "data", replacements)
    (new_root / "data" / "publish-accounts").mkdir(parents=True, exist_ok=True)
    (new_root / "logs").mkdir(parents=True, exist_ok=True)

    print(f"Relocated project: {old_root} -> {new_root}")
    print(f"Updated SQLite values: {database_changes}")
    print(f"Updated JSON files: {json_changes}")
    print(
        "A database backup was saved as "
        f"{database_path.with_suffix('.before-relocation.bak').name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
