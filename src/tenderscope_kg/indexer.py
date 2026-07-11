"""
Repository indexer.
Walks the repo, runs parsers, writes entities/relations to the graph DB,
then runs a post-index resolution pass to link unresolved targets.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pathspec

from .db import SCHEMA_VERSION, GraphDB, make_relation_id
from .models import RelationKind
from .parsers import get_parser

# Files / directories to always skip
_DEFAULT_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    ".tox",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    ".eggs",
    "*.egg-info",
}
_MAX_FILE_BYTES = 512 * 1024  # skip files > 512 KB


class Indexer:
    def __init__(
        self,
        db: GraphDB,
        repo_root: str,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
        incremental: bool = True,
    ):
        self.db = db
        self.repo_root = Path(repo_root).resolve()
        self.progress_cb = progress_cb
        self.incremental = incremental
        self._ignore_spec: Optional[pathspec.PathSpec] = None
        self._file_hashes: dict[str, str] = {}

    # ── Public API ────────────────────────────────────────────────────────

    def run(self) -> dict:
        t0 = time.perf_counter()
        self._load_gitignore()
        self._load_existing_hashes()

        files = self._collect_files()
        total = len(files)
        new_entities = 0
        new_relations = 0
        languages: dict[str, int] = {}

        for idx, fp in enumerate(files):
            if self.progress_cb:
                self.progress_cb(str(fp), idx + 1, total)

            rel_path = str(fp.relative_to(self.repo_root)).replace("\\", "/")
            content = self._read_file(fp)
            if content is None:
                continue

            # Incremental: skip unchanged files
            file_hash = hashlib.md5(content.encode()).hexdigest()
            if self.incremental and self._file_hashes.get(rel_path) == file_hash:
                continue

            parser = get_parser(rel_path, content)
            if parser is None:
                continue

            result = parser.parse()
            if not result.entities:
                continue

            # Delete stale data for this file
            self.db.delete_by_file(rel_path)

            ne = self.db.upsert_entities(result.entities)
            nr = self.db.upsert_relations(result.relations)
            new_entities += ne
            new_relations += nr

            lang = parser.language
            languages[lang] = languages.get(lang, 0) + 1
            self._file_hashes[rel_path] = file_hash

        # Post-index: rebuild FTS once for correct rowid alignment, then resolve relations
        self.db.rebuild_fts()
        resolved = self._resolve_relations()

        elapsed = time.perf_counter() - t0
        now = datetime.now(timezone.utc).isoformat()
        self.db.set_meta("repo_root", str(self.repo_root))
        self.db.set_meta("last_updated", now)
        self.db.set_meta("schema_version", SCHEMA_VERSION)
        self.db.set_meta("file_hashes", str(self._file_hashes))

        stats = self.db.get_stats()
        stats["new_entities"] = new_entities
        stats["new_relations"] = new_relations
        stats["resolved_relations"] = resolved
        stats["elapsed_s"] = round(elapsed, 2)
        stats["files_scanned"] = total
        return stats

    # ── Private helpers ───────────────────────────────────────────────────

    def _load_gitignore(self) -> None:
        gitignore = self.repo_root / ".gitignore"
        if gitignore.exists():
            lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
            self._ignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)

    def _load_existing_hashes(self) -> None:
        raw = self.db.get_meta("file_hashes")
        if raw:
            try:
                import ast as _ast

                self._file_hashes = _ast.literal_eval(raw)
            except Exception:
                self._file_hashes = {}

    def _collect_files(self) -> list[Path]:
        result: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dp = Path(dirpath)
            rel_dir = dp.relative_to(self.repo_root)

            # Prune ignored directories in-place
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _DEFAULT_IGNORE_DIRS
                and not d.startswith(".")
                and not (self._ignore_spec and self._ignore_spec.match_file(str(rel_dir / d) + "/"))
            ]

            for fname in filenames:
                fp = dp / fname
                rel_fp = str(fp.relative_to(self.repo_root)).replace("\\", "/")
                if self._ignore_spec and self._ignore_spec.match_file(rel_fp):
                    continue
                if fp.stat().st_size > _MAX_FILE_BYTES:
                    continue
                result.append(fp)

        result.sort()
        return result

    def _read_file(self, fp: Path) -> Optional[str]:
        try:
            return fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None

    def _resolve_relations(self) -> int:
        """
        Walk all relations that have unresolved_target in extra.
        For each, try to find a matching entity by qualified_name or name,
        then repoint the relation's target_id to the real entity id.
        This is a best-effort pass — unmatched relations stay as-is.
        """
        assert self.db._conn
        conn = self.db._conn

        rows = conn.execute(
            "SELECT id, source_id, target_id, kind, file_path, line, weight, extra "
            "FROM relations WHERE extra LIKE '%unresolved_target%'"
        ).fetchall()

        resolved = 0
        updates: list[tuple[str, str, str]] = []  # (new_target_id, new_rel_id, old_rel_id)

        for row in rows:
            import json as _json

            extra = _json.loads(row["extra"])
            unresolved = extra.get("unresolved_target")
            if not unresolved:
                continue

            # Try exact qualified_name match first, then suffix match
            candidate = conn.execute(
                "SELECT id FROM entities WHERE qualified_name = ? LIMIT 1",
                (unresolved,),
            ).fetchone()

            if not candidate:
                # Try matching by last segment (name)
                short = unresolved.split(".")[-1]
                candidate = conn.execute(
                    "SELECT id FROM entities WHERE name = ? LIMIT 1",
                    (short,),
                ).fetchone()

            if candidate:
                new_target_id = candidate["id"]
                new_rel_id = make_relation_id(
                    row["source_id"],
                    RelationKind(row["kind"]),
                    new_target_id,
                )
                updates.append((new_target_id, new_rel_id, row["id"]))
                resolved += 1

        if updates:
            with self.db.transaction() as c:
                for new_target_id, new_rel_id, old_id in updates:
                    # Fetch full row before deleting it
                    row = c.execute("SELECT * FROM relations WHERE id = ?", (old_id,)).fetchone()
                    if not row:
                        continue
                    c.execute("DELETE FROM relations WHERE id = ?", (old_id,))
                    # Re-insert with resolved target; ignore if a duplicate already exists
                    c.execute(
                        """INSERT OR IGNORE INTO relations
                           (id, source_id, target_id, kind, file_path, line, weight, extra)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            new_rel_id,
                            row["source_id"],
                            new_target_id,
                            row["kind"],
                            row["file_path"],
                            row["line"],
                            row["weight"],
                            row["extra"],
                        ),
                    )

        return resolved
