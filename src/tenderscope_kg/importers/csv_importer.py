"""
Generic CSV importer.

Column mapping is driven by a schema dict passed at construction time,
so the same importer handles any CSV shape without subclassing.

Schema format
-------------
{
    "entity_kind": "company",          # required
    "name_column": "company_name",     # required — field used as the entity name
    "attribute_columns": [             # optional — additional fields stored as attributes
        "address", "city", "phone", "email", ...
    ],
    "relation_columns": [              # optional — columns that create relations
        {
            "column": "parent_company",
            "relation_kind": "subsidiary_of",
            "target_kind": "company"
        }
    ],
    "confidence": 0.9                  # optional default confidence
}

Example usage
-------------
    importer = CSVImporter(
        repo,
        path="companies.csv",
        schema={
            "entity_kind": "company",
            "name_column": "legal_name",
            "attribute_columns": ["address", "city", "phone"],
        },
    )
    result = importer.run()
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

from ..domain import BizEntityKind, BizRelationKind
from ..domain.results import ImportResult
from ..repository._base import BizRepository
from .base import BaseImporter


class CSVImporter(BaseImporter):
    name = "csv"

    def __init__(
        self,
        repo: BizRepository,
        path: str,
        schema: dict[str, Any],
        source_tag: str = "csv",
        encoding: str = "utf-8-sig",
    ) -> None:
        super().__init__(repo)
        self.path = Path(path)
        self.schema = schema
        self.source_tag = source_tag
        self.encoding = encoding

    def run(self) -> ImportResult:
        result = self._make_result()
        result.importer = f"csv:{self.path.name}"
        t0 = time.perf_counter()

        entity_kind_str = self.schema.get("entity_kind")
        if not entity_kind_str:
            result.errors.append("schema missing 'entity_kind'")
            return result
        try:
            entity_kind = BizEntityKind(entity_kind_str)
        except ValueError:
            result.errors.append(f"Unknown entity_kind: {entity_kind_str}")
            return result

        name_col = self.schema.get("name_column")
        if not name_col:
            result.errors.append("schema missing 'name_column'")
            return result

        attr_cols: list[str] = self.schema.get("attribute_columns", [])
        rel_cols: list[dict] = self.schema.get("relation_columns", [])
        default_confidence: float = float(self.schema.get("confidence", 1.0))

        if not self.path.exists():
            result.errors.append(f"File not found: {self.path}")
            return result

        with open(self.path, encoding=self.encoding, newline="") as fh:
            reader = csv.DictReader(fh)
            for row_num, row in enumerate(reader, start=2):
                name = (row.get(name_col) or "").strip()
                if not name:
                    result.warnings.append(f"Row {row_num}: empty name column '{name_col}', skipping")
                    continue

                attrs: dict[str, Any] = {}
                for col in attr_cols:
                    val = (row.get(col) or "").strip()
                    if val:
                        attrs[col] = val

                try:
                    entity, created = self.repo.put_entity(
                        kind=entity_kind,
                        name=name,
                        attributes=attrs,
                        source=self.source_tag,
                        confidence=default_confidence,
                    )
                except Exception as exc:
                    result.errors.append(f"Row {row_num}: {exc}")
                    continue

                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1

                # Process relation columns
                for rel_spec in rel_cols:
                    col = rel_spec.get("column", "")
                    rel_kind_str = rel_spec.get("relation_kind", "")
                    tgt_kind_str = rel_spec.get("target_kind", "company")
                    target_name = (row.get(col) or "").strip()
                    if not target_name or not rel_kind_str:
                        continue
                    try:
                        rel_kind = BizRelationKind(rel_kind_str)
                        tgt_kind = BizEntityKind(tgt_kind_str)
                    except ValueError as exc:
                        result.warnings.append(f"Row {row_num}: bad relation spec — {exc}")
                        continue
                    try:
                        target, tgt_was_created = self.repo.put_entity(
                            kind=tgt_kind,
                            name=target_name,
                            source=self.source_tag,
                        )
                        if tgt_was_created:
                            result.entities_created += 1
                        else:
                            result.entities_updated += 1
                        _, rel_created = self.repo.put_relation(
                            source_uid=entity.uid,
                            kind=rel_kind,
                            target_uid=target.uid,
                            source=self.source_tag,
                        )
                        if rel_created:
                            result.relations_created += 1
                        else:
                            result.relations_updated += 1
                    except Exception as exc:
                        result.warnings.append(f"Row {row_num} relation: {exc}")

        self.repo.rebuild_fts()
        result.elapsed_s = time.perf_counter() - t0
        return result
