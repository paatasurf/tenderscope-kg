"""
JSON importer for TenderScope Intelligence Engine.

Imports entities and relations from a generic JSON structure.
Supports two formats:
  - List of entity objects: [{"kind": "company", "name": "...", "attrs": {...}}, ...]
  - Dict with "entities" and optional "relations" keys.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import BaseImporter
from ..domain.results import ImportResult
from ..domain import BizEntityKind, BizRelationKind
from ..repository._base import BizRepository


class JSONImporter(BaseImporter):
    """Import entities (and optionally relations) from a JSON file."""

    name = "json"

    def __init__(
        self,
        repo: BizRepository,
        file_path: str | Path,
        source_tag: str = "json_import",
        **options: Any,
    ) -> None:
        super().__init__(repo, source_tag=source_tag, **options)
        self.file_path = Path(file_path)

    def run(self) -> ImportResult:
        result = self._make_result()

        try:
            with open(self.file_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            result.errors.append(f"File not found: {self.file_path}")
            return result
        except json.JSONDecodeError as exc:
            result.errors.append(f"JSON parse error: {exc}")
            return result

        # Normalise to {entities: [...], relations: [...]}
        if isinstance(data, list):
            entities_raw = data
            relations_raw: list = []
        elif isinstance(data, dict):
            entities_raw = data.get("entities") or []
            relations_raw = data.get("relations") or []
        else:
            result.errors.append(f"Unsupported JSON root type: {type(data)}")
            return result

        # Import entities
        for raw in entities_raw:
            if not isinstance(raw, dict):
                result.errors.append(f"Skipping non-dict entity: {raw!r}")
                continue
            kind_str = str(raw.get("kind") or "").lower()
            name = raw.get("name") or raw.get("canonical_name") or ""
            attrs = raw.get("attrs") or raw.get("attributes") or {}
            if not name:
                result.errors.append(f"Skipping entity with no name: {raw!r}")
                continue
            try:
                kind = BizEntityKind(kind_str)
            except ValueError:
                result.errors.append(f"Unknown entity kind '{kind_str}' — skipping.")
                continue
            _, created = self.repo.put_entity(kind, name, attrs,
                                              source=self.source_tag)
            if created:
                result.entities_created += 1
            else:
                result.entities_updated += 1

        # Import relations
        for raw in relations_raw:
            if not isinstance(raw, dict):
                continue
            kind_raw = str(raw.get("kind") or "").strip()
            if not kind_raw:
                continue
            # Try lowercase value first (e.g. "submitted_bid"), then uppercase name
            try:
                kind = BizRelationKind(kind_raw.lower())
            except ValueError:
                try:
                    kind = BizRelationKind[kind_raw.upper()]
                except KeyError:
                    result.errors.append(f"Unknown relation kind '{kind_raw}' — skipping.")
                    continue

            # Resolve source endpoint: prefer direct uid, fall back to name+kind lookup
            src = raw.get("source") or raw.get("source_uid") or ""
            if not src:
                src_name = raw.get("source_name") or ""
                src_kind_str = str(raw.get("source_kind") or "").lower()
                if src_name and src_kind_str:
                    try:
                        src_kind = BizEntityKind(src_kind_str)
                        match = self.repo.find_by_canonical(src_kind, src_name.lower())
                        if match:
                            src = match.uid
                    except ValueError:
                        pass

            # Resolve target endpoint
            tgt = raw.get("target") or raw.get("target_uid") or ""
            if not tgt:
                tgt_name = raw.get("target_name") or ""
                tgt_kind_str = str(raw.get("target_kind") or "").lower()
                if tgt_name and tgt_kind_str:
                    try:
                        tgt_kind = BizEntityKind(tgt_kind_str)
                        match = self.repo.find_by_canonical(tgt_kind, tgt_name.lower())
                        if match:
                            tgt = match.uid
                    except ValueError:
                        pass

            if not (src and tgt):
                result.errors.append(
                    f"Could not resolve endpoints for relation {raw!r} — skipping."
                )
                continue
            self.repo.put_relation(src, kind, tgt, source=self.source_tag)
            result.relations_created += 1

        return result
