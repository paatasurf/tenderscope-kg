"""Language parser registry."""

from .base import BaseParser
from .config_parser import ConfigParser
from .js_parser import JavaScriptParser
from .python_parser import PythonParser
from .sql_parser import SQLParser

PARSERS: list[type[BaseParser]] = [
    PythonParser,
    JavaScriptParser,
    SQLParser,
    ConfigParser,
]


def get_parser(file_path: str, source: str) -> BaseParser | None:
    for cls in PARSERS:
        p = cls(file_path, source)
        if p.can_parse():
            return p
    return None
