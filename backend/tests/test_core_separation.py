"""The core library stays independent of the API: it never imports it, FastAPI or MongoDB."""

import re
import subprocess
import sys
import tomllib

from conftest import REPO

IMPORT = re.compile(r"^\s*(?:from|import)\s+(expense_api|fastapi|starlette|pymongo|bson|motor|mongomock)\b", re.MULTILINE)


def test_no_core_module_imports_the_api_or_a_database():
    offenders = [str(path.relative_to(REPO)) for path in (REPO / "src" / "expense_validator").rglob("*.py") if IMPORT.search(path.read_text())]
    assert offenders == []


def test_the_core_package_depends_on_no_web_framework_or_database():
    dependencies = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    assert not [name for name in dependencies if re.match(r"(fastapi|starlette|pymongo|motor|uvicorn)\b", name)]


def test_importing_the_core_loads_nothing_of_the_api():
    code = "import sys, expense_validator.validator; print(sorted(m for m in sys.modules if m.split('.')[0] in {'expense_api', 'fastapi', 'pymongo'}))"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip() == "[]"
