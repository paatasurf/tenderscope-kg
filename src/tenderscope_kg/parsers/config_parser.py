"""
Config file parser: JSON, YAML, TOML, .env, Dockerfile, CI pipelines.
Extracts: config_file entities, config_key entities, pipeline/stage entities.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..db import make_entity_id, make_relation_id
from ..models import Entity, EntityKind, Relation, RelationKind
from .base import BaseParser, ParseResult

_CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".env", ".ini", ".cfg"}
_CONFIG_NAMES = {
    "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env", ".env.example", ".env.local",
    "makefile", "justfile",
}
_CI_NAMES = {".github", "workflows"}  # path segment markers
_RE_ENV_KEY = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)
_RE_TOML_TABLE = re.compile(r"^\[([^\]]+)\]", re.MULTILINE)
_RE_TOML_KEY = re.compile(r"^(\w[\w.-]*)\s*=", re.MULTILINE)
_RE_YAML_KEY = re.compile(r"^( *)(\w[\w-]*):", re.MULTILINE)
_RE_GH_JOB = re.compile(r"^  (\w[\w-]*):\s*$", re.MULTILINE)
_RE_GH_STEP = re.compile(r"^\s+-\s+(?:name|uses):\s+(.+)$", re.MULTILINE)


class ConfigParser(BaseParser):
    def __init__(self, file_path: str, source: str):
        super().__init__(file_path, source)
        self.language = "config"

    def can_parse(self) -> bool:
        name = Path(self.file_path).name.lower()
        if name in _CONFIG_NAMES:
            return True
        if self.ext in _CONFIG_EXTS:
            return True
        # GitHub Actions workflows
        parts = Path(self.file_path).parts
        if ".github" in parts and self.ext in {".yml", ".yaml"}:
            return True
        return False

    def parse(self) -> ParseResult:
        entities: list[Entity] = []
        relations: list[Relation] = []
        fp = self.file_path
        src = self.source
        name = Path(fp).name.lower()
        lines = src.splitlines()

        file_eid = make_entity_id(EntityKind.CONFIG_FILE, fp)
        entities.append(Entity(
            id=file_eid,
            kind=EntityKind.CONFIG_FILE,
            name=Path(fp).name,
            qualified_name=fp,
            file_path=fp,
            line_start=1,
            line_end=len(lines),
            language="config",
        ))

        # .env files
        if name.startswith(".env") or self.ext == ".env":
            for m in _RE_ENV_KEY.finditer(src):
                key = m.group(1)
                eid = make_entity_id(EntityKind.CONFIG_KEY, f"{fp}#{key}")
                line = src[:m.start()].count("\n") + 1
                entities.append(Entity(
                    id=eid,
                    kind=EntityKind.CONFIG_KEY,
                    name=key,
                    qualified_name=f"{fp}#{key}",
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language="config",
                    extra={"env_var": True},
                ))
                relations.append(Relation(
                    id=make_relation_id(file_eid, RelationKind.CONTAINS, eid),
                    source_id=file_eid,
                    target_id=eid,
                    kind=RelationKind.CONTAINS,
                    file_path=fp,
                    line=line,
                ))
            return ParseResult(entities=entities, relations=relations)

        # JSON
        if self.ext == ".json":
            try:
                obj = json.loads(src)
                if isinstance(obj, dict):
                    for key in list(obj.keys())[:50]:
                        eid = make_entity_id(EntityKind.CONFIG_KEY, f"{fp}#{key}")
                        entities.append(Entity(
                            id=eid,
                            kind=EntityKind.CONFIG_KEY,
                            name=str(key),
                            qualified_name=f"{fp}#{key}",
                            file_path=fp,
                            line_start=1,
                            line_end=1,
                            language="config",
                        ))
                        relations.append(Relation(
                            id=make_relation_id(file_eid, RelationKind.CONTAINS, eid),
                            source_id=file_eid,
                            target_id=eid,
                            kind=RelationKind.CONTAINS,
                            file_path=fp,
                        ))
            except json.JSONDecodeError:
                pass
            return ParseResult(entities=entities, relations=relations)

        # TOML
        if self.ext == ".toml":
            for m in _RE_TOML_TABLE.finditer(src):
                section = m.group(1)
                eid = make_entity_id(EntityKind.CONFIG_KEY, f"{fp}#{section}")
                line = src[:m.start()].count("\n") + 1
                entities.append(Entity(
                    id=eid,
                    kind=EntityKind.CONFIG_KEY,
                    name=section,
                    qualified_name=f"{fp}#{section}",
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language="config",
                ))
                relations.append(Relation(
                    id=make_relation_id(file_eid, RelationKind.CONTAINS, eid),
                    source_id=file_eid,
                    target_id=eid,
                    kind=RelationKind.CONTAINS,
                    file_path=fp,
                    line=line,
                ))
            return ParseResult(entities=entities, relations=relations)

        # GitHub Actions YAML workflows
        parts = Path(fp).parts
        if ".github" in parts and self.ext in {".yml", ".yaml"}:
            return self._parse_gh_workflow(file_eid, entities, relations)

        # Generic YAML: top-level keys only
        if self.ext in {".yaml", ".yml"}:
            seen: set[str] = set()
            for m in _RE_YAML_KEY.finditer(src):
                indent = len(m.group(1))
                if indent > 0:
                    continue
                key = m.group(2)
                if key in seen:
                    continue
                seen.add(key)
                eid = make_entity_id(EntityKind.CONFIG_KEY, f"{fp}#{key}")
                line = src[:m.start()].count("\n") + 1
                entities.append(Entity(
                    id=eid,
                    kind=EntityKind.CONFIG_KEY,
                    name=key,
                    qualified_name=f"{fp}#{key}",
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language="config",
                ))
                relations.append(Relation(
                    id=make_relation_id(file_eid, RelationKind.CONTAINS, eid),
                    source_id=file_eid,
                    target_id=eid,
                    kind=RelationKind.CONTAINS,
                    file_path=fp,
                    line=line,
                ))

        return ParseResult(entities=entities, relations=relations)

    def _parse_gh_workflow(
        self,
        file_eid: str,
        entities: list[Entity],
        relations: list[Relation],
    ) -> ParseResult:
        src = self.source
        fp = self.file_path

        # Pipeline entity for the whole workflow
        wf_name = Path(fp).stem
        pipeline_eid = make_entity_id(EntityKind.PIPELINE, fp)
        entities.append(Entity(
            id=pipeline_eid,
            kind=EntityKind.PIPELINE,
            name=wf_name,
            qualified_name=fp,
            file_path=fp,
            line_start=1,
            line_end=len(src.splitlines()),
            language="config",
        ))
        relations.append(Relation(
            id=make_relation_id(file_eid, RelationKind.CONTAINS, pipeline_eid),
            source_id=file_eid,
            target_id=pipeline_eid,
            kind=RelationKind.CONTAINS,
            file_path=fp,
        ))

        # Jobs as pipeline stages
        prev_stage_eid: str | None = None
        for m in _RE_GH_JOB.finditer(src):
            job_name = m.group(1)
            stage_qname = f"{fp}#{job_name}"
            stage_eid = make_entity_id(EntityKind.PIPELINE_STAGE, stage_qname)
            line = src[:m.start()].count("\n") + 1
            entities.append(Entity(
                id=stage_eid,
                kind=EntityKind.PIPELINE_STAGE,
                name=job_name,
                qualified_name=stage_qname,
                file_path=fp,
                line_start=line,
                line_end=line,
                language="config",
            ))
            relations.append(Relation(
                id=make_relation_id(pipeline_eid, RelationKind.PIPELINE_STEP, stage_eid),
                source_id=pipeline_eid,
                target_id=stage_eid,
                kind=RelationKind.PIPELINE_STEP,
                file_path=fp,
                line=line,
            ))
            if prev_stage_eid:
                relations.append(Relation(
                    id=make_relation_id(prev_stage_eid, RelationKind.DEPENDS_ON, stage_eid),
                    source_id=prev_stage_eid,
                    target_id=stage_eid,
                    kind=RelationKind.DEPENDS_ON,
                    file_path=fp,
                    line=line,
                ))
            prev_stage_eid = stage_eid

        return ParseResult(entities=entities, relations=relations)
