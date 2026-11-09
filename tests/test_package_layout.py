"""Where models and exceptions live, and that every older import path gives the same objects."""

import os
import subprocess
import sys

import pytest

import expense_validator
from expense_validator import errors, exceptions, models, results, validator
from expense_validator.classification.backends import llm
from expense_validator.compiler import llm_extract
from expense_validator.config import ROOT
from expense_validator.ingestion import IngestionError as ingestion_package_error
from expense_validator.ingestion import models as ingestion_models
from expense_validator.models import results as model_results
from expense_validator.models import settings
from expense_validator.normalization import translation

PUBLIC_ERRORS = ("ValidatorError", "ConfigurationError", "InvalidInputError", "ReplayAnswerMissing", "EvidenceError")
COMPILER_MODELS = ("Approval", "CandidateRuleset", "DocumentReading", "ExtractionResult", "PolicyDocumentInput", "PublicationResult", "Resolution", "ReviewItem")
RESULTS = ("BatchResult", "DispositionReasonResult", "ExceptionResult", "LineResult", "Metadata", "Mode", "OpenQuestionResult", "Provenance", "ReceiptResult", "ReviewSignalResult", "UncertaintyResult")


def test_every_public_name_comes_from_its_new_home():
    for name in PUBLIC_ERRORS:
        assert getattr(expense_validator, name) is getattr(exceptions, name)
    for name in RESULTS:
        assert getattr(expense_validator, name) is getattr(model_results, name) is getattr(models, name)
    assert expense_validator.ValidatorSettings is settings.ValidatorSettings is models.ValidatorSettings
    assert expense_validator.ExpenseValidator is validator.ExpenseValidator
    assert expense_validator.Receipt is models.Receipt
    from expense_validator import policy_compiler
    from expense_validator.compiler import agreement, approval
    from expense_validator.models import compiler

    for name in COMPILER_MODELS:
        assert getattr(expense_validator, name) is getattr(models, name) is getattr(compiler, name)
    assert models.Approval is approval.Approval and models.Resolution is agreement.Resolution
    assert expense_validator.PolicyCompiler is policy_compiler.PolicyCompiler
    assert expense_validator.ApprovalRefused is exceptions.ApprovalRefused
    assert expense_validator.CompilerSettings is settings.CompilerSettings is models.CompilerSettings


def test_older_import_paths_give_the_same_objects():
    for name in PUBLIC_ERRORS:
        assert getattr(errors, name) is getattr(exceptions, name)
    for name in RESULTS:
        assert getattr(results, name) is getattr(model_results, name)
    assert validator.ValidatorSettings is settings.ValidatorSettings and validator.CONFIG_CACHE is settings.CONFIG_CACHE
    assert llm.ClassificationError is exceptions.ClassificationError
    assert translation.TranslationError is exceptions.TranslationError
    assert ingestion_models.IngestionError is ingestion_package_error is exceptions.IngestionError
    assert llm_extract.RecordingMissing is exceptions.RecordingMissing


@pytest.mark.parametrize(
    "name, bases",
    [
        ("ValidatorError", ("Exception",)),
        ("ConfigurationError", ("ValidatorError", "ValueError")),
        ("InvalidInputError", ("ValidatorError", "ValueError")),
        ("ReplayAnswerMissing", ("ValidatorError", "LookupError")),
        ("EvidenceError", ("ValidatorError", "ValueError")),
        ("ApprovalRefused", ("ValidatorError", "ValueError")),
        ("ClassificationError", ("Exception",)),
        ("TranslationError", ("Exception",)),
        ("IngestionError", ("ValueError",)),
        ("RecordingMissing", ("LookupError",)),
    ],
)
def test_exception_inheritance_is_unchanged(name, bases):
    error = getattr(exceptions, name)
    assert tuple(base.__name__ for base in error.__bases__) == bases
    assert error.__module__ == "expense_validator.exceptions"


def test_exception_messages_and_attributes_are_unchanged():
    missing = exceptions.ReplayAnswerMissing(["a", "b"])
    assert str(missing) == "2 answers are not in the replay cache, and replaying never calls a model" and missing.missing == ("a", "b")
    refused = exceptions.IngestionError("bad", receipt_id="R-1", source="f.jsonl", line_number=3)
    assert str(refused) == "f.jsonl:3: R-1: bad" and (refused.reason, refused.receipt_id, refused.line_number) == ("bad", "R-1", 3)


def _loaded_after(module):
    code = f"import sys, {module}; print(sorted(name for name in sys.modules if name.startswith('expense_validator')))"
    found = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=True)
    return set(eval(found.stdout))


def test_exceptions_depend_on_nothing_in_the_package():
    assert _loaded_after("expense_validator.exceptions") == {"expense_validator", "expense_validator.exceptions"}


def test_models_load_no_implementation():
    loaded = _loaded_after("expense_validator.models")
    assert not {name for name in loaded if name.startswith(("expense_validator.validator", "expense_validator.classification", "expense_validator.compiler", "expense_validator.normalization", "expense_validator.engine.evaluation"))}


# --- Evaluation ---

EVALUATION = ROOT / "src" / "expense_validator" / "evaluation"


def _imported(path):
    import ast

    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    return found


def test_evaluation_computation_imports_no_command_line():
    computation = [path for path in EVALUATION.glob("*.py")]
    assert {path.name for path in computation} == {"__init__.py", "classification.py", "coverage.py", "dataset.py", "detection.py", "error_analysis.py", "keyword_baseline.py", "metrics.py", "policy_outputs.py", "predictions.py", "report.py", "robustness.py", "scenarios.py", "texts.py"}
    for path in computation:
        names = _imported(path)
        assert not {"argparse", "sys", "expense_validator.run_receipts"} & names, path.name
        assert not [name for name in names if name.startswith("expense_validator.evaluation.cli") or name.endswith("__main__")], path.name
    assert not _imported(EVALUATION / "__init__.py")


def test_only_the_launchers_reach_the_evaluation_command_lines():
    package = ROOT / "src" / "expense_validator"
    reaching = {str(path.relative_to(package)) for path in package.rglob("*.py") if any(name.startswith("expense_validator.evaluation.cli") for name in _imported(path))}
    assert reaching == {"offline_eval/__main__.py", "classification/evaluate.py", "classification/robustness.py"}


def test_evaluation_is_callable_from_python():
    from expense_validator.evaluation import classification, policy_outputs, robustness

    for module, names in (
        (policy_outputs, ("evaluate_predictions", "evaluate_scenarios", "baseline_comparison")),
        (classification, ("classification_metrics", "classification_predictions", "score_lines")),
        (robustness, ("score_rows", "classify_cases", "classify_receipts", "score_receipts", "check_labels")),
    ):
        assert all(callable(getattr(module, name)) for name in names), module.__name__


def test_langgraph_loads_only_when_chosen():
    """The core install has no langgraph: nothing but the orchestrator imports it, and only a langgraph request loads that."""
    code = (
        "import sys\n"
        "from expense_validator import ExpenseValidator\n"
        "from expense_validator.config import ROOT\n"
        "ExpenseValidator.from_config(ROOT / 'config.yaml', mode='deterministic').validate_batch([], context_complete=False)\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('langgraph', 'langchain_core', 'langsmith') or m.endswith('orchestrator')))"
    )
    loaded = subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=True).stdout
    assert loaded.strip() == "[]"
    package = ROOT / "src" / "expense_validator"
    users = sorted(str(path.relative_to(package)) for path in package.rglob("*.py") if any(name.split(".")[0] == "langgraph" for name in _imported(path)))
    assert users == ["classification/orchestrator.py"]


# --- Installation ---


def test_every_console_script_resolves_to_a_callable():
    import importlib
    import tomllib

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts, "pyproject.toml declares the command lines"
    for name, target in scripts.items():
        module, function = target.split(":")
        assert callable(getattr(importlib.import_module(module), function)), name


def test_the_project_root_is_the_checkout_or_the_one_named_in_the_environment(tmp_path):
    code = "from expense_validator.config import ROOT; print(ROOT)"
    run = lambda env: subprocess.run([sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(ROOT / "src"), **env}, cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()
    assert run({}) == str(ROOT), "a source checkout is its own project folder, wherever the command runs from"
    assert run({"EXPENSE_VALIDATOR_ROOT": str(tmp_path)}) == str(tmp_path.resolve())


def test_the_package_ships_its_typing_marker():
    assert (ROOT / "src" / "expense_validator" / "py.typed").is_file()
