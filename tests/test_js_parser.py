"""Tests for the JavaScript/TypeScript parser."""
import pytest
from tenderscope_kg.parsers.js_parser import JavaScriptParser
from tenderscope_kg.models import EntityKind, RelationKind

TS_SOURCE = """\
import { Request, Response } from 'express';
import type { User } from './models';
import { db } from '../database';

export interface UserPayload {
  id: number;
  email: string;
}

export type UserId = number;

export enum Status {
  Active = 'active',
  Inactive = 'inactive',
}

export class UserController {
  private db: any;

  constructor(db: any) {
    this.db = db;
  }

  async getUser(req: Request, res: Response): Promise<void> {
    const id = req.params.id;
    res.json({ id });
  }
}

export async function createUser(email: string): Promise<User> {
  return db.insert({ email });
}

const deleteUser = (id: number) => {
  db.delete(id);
};

app.get('/users/:id', UserController.prototype.getUser);
app.post('/users', createUser);
"""


@pytest.fixture()
def result():
    p = JavaScriptParser("src/controllers/users.ts", TS_SOURCE)
    assert p.can_parse()
    return p.parse()


def test_file_entity(result):
    files = [e for e in result.entities if e.kind == EntityKind.FILE]
    assert len(files) == 1


def test_class_extracted(result):
    classes = [e for e in result.entities if e.kind == EntityKind.CLASS]
    assert any(e.name == "UserController" for e in classes)


def test_function_extracted(result):
    funcs = [e for e in result.entities if e.kind == EntityKind.FUNCTION]
    names = {e.name for e in funcs}
    assert "createUser" in names


def test_arrow_function_extracted(result):
    funcs = [e for e in result.entities if e.kind == EntityKind.FUNCTION]
    names = {e.name for e in funcs}
    assert "deleteUser" in names


def test_interface_extracted(result):
    ifaces = [e for e in result.entities if e.kind == EntityKind.INTERFACE]
    assert any(e.name == "UserPayload" for e in ifaces)


def test_type_alias_extracted(result):
    types = [e for e in result.entities if e.kind == EntityKind.TYPE_ALIAS]
    assert any(e.name == "UserId" for e in types)


def test_enum_extracted(result):
    enums = [e for e in result.entities if e.kind == EntityKind.ENUM]
    assert any(e.name == "Status" for e in enums)


def test_api_routes_extracted(result):
    routes = [e for e in result.entities if e.kind == EntityKind.API_ROUTE]
    assert len(routes) >= 2
    paths = {e.extra.get("path") for e in routes}
    assert "/users/:id" in paths
    assert "/users" in paths


def test_import_relations(result):
    rels = [r for r in result.relations if r.kind == RelationKind.IMPORTS]
    targets = {r.extra.get("unresolved_target") for r in rels}
    assert "express" in targets
    assert "./models" in targets
