"""Language parser registry."""
from .base import BaseParser
from .python_parser import PythonParser
from .js_parser import JavaScriptParser
from .sql_parser import SQLParser
from .config_parser import ConfigParser

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
