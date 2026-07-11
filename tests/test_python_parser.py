"""Tests for the Python parser."""

import pytest

from tenderscope_kg.models import EntityKind, RelationKind
from tenderscope_kg.parsers.python_parser import PythonParser

SOURCE = '''\
"""Module docstring."""
import os
from pathlib import Path
from typing import Optional


class Animal:
    """Base animal class."""

    def __init__(self, name: str) -> None:
        self.name = name

    def speak(self) -> str:
        return "..."


class Dog(Animal):
    """A dog."""

    def speak(self) -> str:
        return f"Woof, I am {self.name}"

    def fetch(self, item: str) -> None:
        print(item)


def train(dog: Dog, command: str) -> bool:
    """Train a dog."""
    dog.speak()
    return True
'''


@pytest.fixture()
def result():
    p = PythonParser("zoo/animals.py", SOURCE)
    assert p.can_parse()
    return p.parse()


def test_file_entity(result):
    files = [e for e in result.entities if e.kind == EntityKind.FILE]
    assert len(files) == 1
    assert files[0].file_path == "zoo/animals.py"


def test_module_entity(result):
    mods = [e for e in result.entities if e.kind == EntityKind.MODULE]
    assert any("animals" in e.name for e in mods)


def test_classes_extracted(result):
    classes = [e for e in result.entities if e.kind == EntityKind.CLASS]
    names = {e.name for e in classes}
    assert "Animal" in names
    assert "Dog" in names


def test_methods_extracted(result):
    methods = [e for e in result.entities if e.kind == EntityKind.METHOD]
    names = {e.name for e in methods}
    assert "speak" in names
    assert "__init__" in names
    assert "fetch" in names


def test_function_extracted(result):
    funcs = [e for e in result.entities if e.kind == EntityKind.FUNCTION]
    assert any(e.name == "train" for e in funcs)


def test_train_has_signature(result):
    train = next(e for e in result.entities if e.name == "train")
    assert train.signature is not None
    assert "dog" in train.signature
    assert "command" in train.signature


def test_inheritance_relation(result):
    rels = [r for r in result.relations if r.kind == RelationKind.INHERITS]
    assert any("Animal" in r.extra.get("unresolved_target", "") for r in rels)


def test_import_relations(result):
    rels = [r for r in result.relations if r.kind == RelationKind.IMPORTS]
    targets = {r.extra.get("unresolved_target") for r in rels}
    assert "os" in targets
    assert "pathlib" in targets


def test_call_relations(result):
    rels = [r for r in result.relations if r.kind == RelationKind.CALLS]
    targets = {r.extra.get("unresolved_target", "") for r in rels}
    assert any("speak" in t for t in targets)


def test_docstrings(result):
    animal = next(e for e in result.entities if e.name == "Animal")
    assert animal.docstring == "Base animal class."

    train_fn = next(e for e in result.entities if e.name == "train")
    assert train_fn.docstring == "Train a dog."
