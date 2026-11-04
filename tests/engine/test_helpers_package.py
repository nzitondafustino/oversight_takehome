"""Runs cases/engine/helpers_package.yaml: the layout of the helpers package, not what it decides."""

import ast
import importlib
import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_helpers import CONTEXT, FIELDS, RULES, RULESET
from yaml_cases import load_cases

from expense_validator import engine
from expense_validator.engine import helpers
from expense_validator.engine.helpers import _Job
from expense_validator.engine.helpers.context import HelperJob
from expense_validator.engine.helpers.gifts import is_client_gift
from expense_validator.engine.helpers.meals import MealPolicy
from expense_validator.policy.vocabulary import HELPER_CONTRACTS

PACKAGE = helpers.__name__
SRC = Path(engine.__file__).parent.parent.parent
MODULES = sorted(info.name for info in pkgutil.iter_modules(helpers.__path__))
# The one helper that runs on a carve-out rule, so the carve-out step calls it rather than the registry.
NOT_DISPATCHED = {"baggage_allowance"}


def absolute_imports(module):
    """The modules this module imports, at any depth in its source."""
    tree = ast.parse(Path(importlib.import_module(f"{PACKAGE}.{module}").__file__).read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{module} uses a relative import"
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize("case", load_cases("helpers", "dispatch"))
def test_a_helper_name_dispatches_to_the_function_its_domain_owns(case):
    function = helpers.HELPERS[case["helper"]]
    assert function.__module__ == f"{PACKAGE}.{case['expected']['module']}"
    assert function.__name__ == case["expected"]["function"]


def test_the_registry_holds_every_helper_in_the_vocabulary():
    assert set(helpers.HELPERS) == set(HELPER_CONTRACTS) - NOT_DISPATCHED
    assert {case.values[0]["helper"] for case in load_cases("helpers", "dispatch")} == set(helpers.HELPERS)


@pytest.mark.parametrize("case", load_cases("helpers", "names"))
def test_a_name_stays_importable_from_the_package(case):
    home = importlib.import_module(f"{PACKAGE}.{case['expected']['module']}")
    assert getattr(helpers, case["name"]) is getattr(home, case["name"])
    assert case["name"] in helpers.__all__


def test_every_exported_name_is_listed():
    assert set(helpers.__all__) == {case.values[0]["name"] for case in load_cases("helpers", "names")}


@pytest.mark.parametrize("case", load_cases("helpers", "modules"))
def test_a_module_reads_only_what_its_place_in_the_package_allows(case):
    imports = {name for name in absolute_imports(case["module"]) if name.startswith("expense_validator.")}
    siblings = {name.removeprefix(f"{PACKAGE}.") for name in imports if name.startswith(f"{PACKAGE}.")}
    prefix = f"{engine.__name__}."
    outside = {name.removeprefix(prefix) for name in imports if name.startswith(prefix) and not name.startswith(PACKAGE)}
    assert siblings <= set(case["expected"]["siblings"])
    assert outside <= set(case["expected"]["engine"])
    assert PACKAGE not in imports, "a module in the package must not import the package itself"


def test_every_module_in_the_package_is_listed():
    assert {case.values[0]["module"] for case in load_cases("helpers", "modules")} == set(MODULES)


@pytest.mark.parametrize("name", [f"{PACKAGE}.{name}" for name in MODULES] + [
    f"{engine.__name__}.{info.name}" for info in pkgutil.iter_modules(engine.__path__)
])
def test_a_module_can_be_the_first_one_imported(name):
    """In a fresh interpreter, as a circular import only shows when its cycle is entered from one side."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        env={**os.environ, "PYTHONPATH": str(SRC)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("receipt", CONTEXT.receipts, ids=lambda receipt: receipt.receipt_id)
def test_the_earlier_job_gives_the_same_answers_as_the_modules_that_now_own_them(receipt):
    rule = RULES["MEAL-01"]
    job = _Job(rule, receipt, CONTEXT, RULESET, FIELDS)
    policy = MealPolicy(HelperJob(rule, receipt, CONTEXT, RULESET, FIELDS))
    assert job.is_meal(receipt) is policy.is_meal(receipt)
    assert job.per_person(receipt) == policy.per_person(receipt)
    assert job.spend_bounds(receipt) == policy.spend_bounds(receipt)
    assert job.meal_cap(rule, [receipt]) == policy.meal_cap(rule, [receipt])
    for line in receipt.line_items:
        assert job.is_gift(line) is is_client_gift(line, receipt, FIELDS)
