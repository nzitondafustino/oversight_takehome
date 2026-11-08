"""Runs the cases in cases/classification, and checks the classifier against the real taxonomy, data and engine."""

import ast
import json
import re
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from openrouter import OpenRouter
from pydantic import SecretStr
from yaml_cases import exact, load_cases

import expense_validator
from expense_validator.classification import ClassifiedLine, build_classifier, classify_receipt, load_taxonomy, load_vocabulary
from expense_validator.classification.backends import llm as classifier_module
from expense_validator.classification.core.common import UNIDENTIFIED_RECIPIENT, line_input, receipt_language
from expense_validator.classification.backends.llm import CONFIDENCE_LEVELS, build_prompt, openrouter_completion, parse_answer, response_schema
from expense_validator.classification.core.recipients import names_a_recipient
from expense_validator.exceptions import ClassificationError
from expense_validator.evaluation.classification import line_prediction, score_lines
from expense_validator.compiler.llm_extract import cached
from expense_validator.compiler.validate import read_fields
from expense_validator.config import ROOT, load_config
from expense_validator.engine.inputs import allowed_values, classification_state
from expense_validator.engine.evaluation import evaluate_receipts
from expense_validator.engine.models import CLASSIFIED_FIELDS, Receipt
from expense_validator.ingestion import IngestionMode, iter_jsonl
from expense_validator.normalization import NoOpTranslator, TranslationError, normalize_receipt
from expense_validator.evaluation.predictions import PredictionSet, Source, apply_predictions, load_predictions
from expense_validator.policy.schema import RuleSet

CONFIG = load_config()
VOCABULARY = load_vocabulary(CONFIG.paths.taxonomy, CONFIG.paths.rule_fields)
FIELDS = read_fields(CONFIG.paths.rule_fields.read_text())
INGESTED = list(iter_jsonl(ROOT / "data" / "receipts.jsonl", IngestionMode.EVALUATION))
ONE_RECEIPT = normalize_receipt(INGESTED[0].receipt)
SOURCE = Path(expense_validator.__file__).parent
LINE_INPUT_FIELDS = {"original_description", "description_language", "english_translation", "receipt_language", "merchant_name", "merchant_category", "itemized_receipt", "quantity", "unit_price", "currency"}
GOOD_ANSWER = '{"category": "other", "confidence": 0.7, "recipient_name": null, "recipient_type": null, "venue_type": null, "item_kind": null, "essential_toiletry": null}'


class TableTranslator:
    """Answers from a table of text to translation, or to {raises: message}."""

    def __init__(self, table):
        self.table = table

    def translate(self, text, source_language):
        answer = self.table[text]
        if isinstance(answer, dict):
            raise TranslationError(answer["raises"])
        return answer


def _answer_body(content):
    message = {"role": "assistant", "content": content}
    return {"id": "x", "object": "chat.completion", "created": 1, "model": "m", "system_fingerprint": None, "choices": [{"index": 0, "finish_reason": "stop", "message": message}]}


def _provider(status, body, sent=None, vocabulary=VOCABULARY):
    """The real SDK, with only the network replaced. Retries are off, so an error returns at once."""

    def handler(request):
        if sent is not None:
            sent.append(request)
        return httpx.Response(status, json=body)

    client = OpenRouter(api_key="very-secret", client=httpx.Client(transport=httpx.MockTransport(handler)), retry_config=None)
    return openrouter_completion(client, "some/model", 0, vocabulary)


def _answer_text(case):
    return case["answer"] if isinstance(case["answer"], str) else json.dumps(case["answer"])


def _one_line_receipt():
    receipt = INGESTED[0].receipt
    line = receipt.line_items[0]
    return normalize_receipt(Receipt.model_validate({**receipt.model_dump(), "line_items": [line], "total_amount": line.amount, "total_amount_usd": line.amount_usd}))


def _receipt_with(description, translator=None, language="en"):
    receipt = _one_line_receipt().receipt
    line = {**receipt.line_items[0].model_dump(), "description_raw": description, "language": language}
    return normalize_receipt(Receipt.model_validate({**receipt.model_dump(), "line_items": [line]}), translator or NoOpTranslator())


# --- Answers from the model ---


@pytest.mark.parametrize("case", load_cases("classification", "answers"))
def test_a_valid_answer_is_read_exactly(case):
    line = classify_receipt(_receipt_with(case["description"]), lambda prompt, text: _answer_text(case), VOCABULARY)[0]
    assert line.error is None
    found = {**line._asdict(), "confidence": str(line.confidence)}
    assert exact({name: found[name] for name in case["expected"]}) == exact(case["expected"])


@pytest.mark.parametrize("case", load_cases("classification", "answers_refused"))
def test_a_bad_answer_is_an_explicit_failure_with_nothing_made_up(case):
    line = classify_receipt(_receipt_with(case["description"]), lambda prompt, text: _answer_text(case), VOCABULARY)[0]
    assert re.search(case["expected"], line.error)
    assert line._replace(error=None) == ClassifiedLine(line.line_id)


def test_a_recipient_stated_only_in_the_translation_counts_as_stated():
    normalized = _receipt_with("Blumen für J. Kim", TableTranslator({"Blumen für J. Kim": "Flowers for J. Kim"}), "de")
    answer = GOOD_ANSWER.replace('"recipient_name": null', '"recipient_name": "Flowers for J. Kim"')
    assert classify_receipt(normalized, lambda prompt, text: answer, VOCABULARY)[0].recipient_name == "Flowers for J. Kim"


def test_the_merchant_is_not_evidence_of_a_recipient():
    normalized = _receipt_with("Wine")
    answer = GOOD_ANSWER.replace('"recipient_name": null', f'"recipient_name": "{normalized.receipt.merchant_name}"')
    assert "recipient_name is not in the line's text" in classify_receipt(normalized, lambda prompt, text: answer, VOCABULARY)[0].error


def test_a_failure_message_quotes_no_line_text():
    with pytest.raises(ClassificationError) as error:
        parse_answer('{"category": "other", "confidence": 0.5, "recipient_name": "Secret Person", "recipient_type": null, "venue_type": null, "item_kind": null, "essential_toiletry": null}', json.dumps({"original_description": "Sensitive words"}), VOCABULARY)
    assert "Secret Person" not in str(error.value) and "Sensitive" not in str(error.value)


def test_one_failed_line_does_not_stop_the_others():
    answers = iter([GOOD_ANSWER, "not json", GOOD_ANSWER, GOOD_ANSWER])
    results = classify_receipt(ONE_RECEIPT, lambda prompt, text: next(answers), VOCABULARY)
    assert [result.category for result in results] == ["other", None, "other", "other"]
    assert [result.line_id for result in results] == [line.line_id for line in ONE_RECEIPT.lines]


# --- Recipients a line names ---


@pytest.mark.parametrize("case", load_cases("classification", "recipient_cues"))
def test_recipient_evidence_is_read_from_the_lines_words(case):
    assert names_a_recipient(case["text"]) is case["expected"]["named"]


def test_recipient_evidence_in_the_translation_counts():
    assert names_a_recipient("Blumen", "Flowers for J. Kim") and not names_a_recipient("Blumen", None)


@pytest.mark.parametrize("case", load_cases("classification", "recipient_answers"))
def test_an_unread_recipient_is_marked_and_the_rest_of_the_answer_kept(case):
    line = classify_receipt(_receipt_with(case["description"]), lambda prompt, text: _answer_text(case), VOCABULARY)[0]
    assert line.error is None
    found = {**line._asdict(), "confidence": str(line.confidence)}
    assert exact({name: found[name] for name in case["expected"]}) == exact(case["expected"])


def test_the_unread_marker_is_outside_the_catalog_so_the_engine_reads_it_as_unknown():
    assert UNIDENTIFIED_RECIPIENT not in VOCABULARY.recipient_types
    line = INGESTED[0].receipt.line_items[0].with_classification({"category": "food_beverage", "recipient_type": UNIDENTIFIED_RECIPIENT})
    assert classification_state(line, "recipient_type", FIELDS, Decimal("0.5")) == "unknown"


@pytest.mark.parametrize("case", load_cases("classification", "policy_paths"))
def test_a_named_recipient_never_passes_silently_and_never_makes_a_flag_by_itself(case):
    receipt = Receipt.model_validate(case["receipt"])
    answers = {line_id: json.dumps(answer) for line_id, answer in case["answers"].items()}
    normalized = normalize_receipt(receipt)
    texts = {line_input(normalized, line): line.line_id for line in normalized.lines}
    classified = classify_receipt(normalized, lambda prompt, text: answers[texts[text]], VOCABULARY)
    outputs = {line.line_id: {name: value for name, value in line._asdict().items() if name in CLASSIFIED_FIELDS and value is not None} for line in classified}
    ruleset = RuleSet.from_json(CONFIG.paths.ruleset.read_text())
    result = evaluate_receipts([receipt.with_classifications(outputs)], ruleset, FIELDS, complete=True).results[0]
    found = {
        "disposition": result.disposition,
        "decided": result.decided,
        "exceptions": sorted(group.rule_id for group in result.active_exceptions),
        "open": sorted({question.rule_id for question in result.report.open_questions}),
        "uncertainty": sorted([item.line_id, item.field, item.state] for item in (result.uncertainty.uncertainty if result.uncertainty else ())),
        "cited": {group.rule_id: sorted(group.line_ids) for group in result.active_exceptions},
    }
    assert exact({name: found[name] for name in case["expected"]}) == exact(case["expected"])
    assert "GIFT-04" not in found["exceptions"] or any(answer.get("recipient_type") == "government_official" for answer in case["answers"].values())


# --- Confidence, and what the engine makes of it ---


@pytest.mark.parametrize("case", load_cases("classification", "thresholds"))
def test_the_engine_reads_a_classifier_confidence_by_its_own_threshold(case):
    ruleset = RuleSet.from_json(CONFIG.paths.ruleset.read_text())
    threshold = next(rule for rule in ruleset.rules if rule.rule_id == "EXC-03").params["min_confidence"].value
    assert threshold == Decimal("0.5")
    confidence = case["confidence"]
    answer = {"category": "alcohol", "confidence": None if confidence is None else Decimal(confidence)}
    line = INGESTED[0].receipt.line_items[0].with_classification(answer)
    assert classification_state(line, "category", FIELDS, threshold) == case["expected"]["state"]


def test_levels_are_coarse_and_none_claims_certainty():
    assert all(0 < level < 1 for level in CONFIDENCE_LEVELS)
    assert Decimal("0.5") in {Decimal(str(level)) for level in CONFIDENCE_LEVELS}


def test_the_model_gets_the_same_levels_as_the_check():
    schema = response_schema(VOCABULARY)
    assert schema["properties"]["confidence"]["enum"] == list(CONFIDENCE_LEVELS)
    assert all(str(level) in build_prompt(VOCABULARY) for level in CONFIDENCE_LEVELS)


# --- Into the existing prediction contract ---


def test_a_classified_line_survives_the_prediction_file_and_reaches_the_engine(tmp_path):
    line_id = INGESTED[0].receipt.line_items[0].line_id
    classified = ClassifiedLine(line_id, "jewelry", True, Decimal("0.7"), "A. Moreau", "client", essential_toiletry=False)
    source = Source(kind="actual", name="n", description="d", model="m")
    path = tmp_path / "predictions.json"
    path.write_text(PredictionSet(source=source, predictions=(line_prediction(classified),)).model_dump_json(exclude_none=True))
    loaded = load_predictions(path)
    line = apply_predictions([INGESTED[0].receipt], loaded)[0].line_items[0]
    assert (line.category, line.restricted, line.confidence) == ("jewelry", True, Decimal("0.7"))
    assert (line.recipient_name, line.recipient_type, line.venue_type, line.item_kind) == ("A. Moreau", "client", None, None)
    assert line.essential_toiletry is False


def test_a_failed_line_reaches_the_engine_unclassified_and_with_no_confidence():
    receipt = INGESTED[0].receipt
    failed = classify_receipt(ONE_RECEIPT, lambda prompt, text: "not json", VOCABULARY)
    predictions = tuple(line_prediction(line) for line in failed if line.error is None)
    source = Source(kind="actual", name="n", description="d", model="m")
    line = apply_predictions([receipt], PredictionSet(source=source, predictions=predictions))[0].line_items[0]
    assert (line.category, line.restricted, line.confidence) == (None, None, None)


# --- Responses from the provider ---


@pytest.mark.parametrize("case", load_cases("classification", "provider"))
def test_a_provider_response_is_classified(case):
    result = classify_receipt(ONE_RECEIPT, _provider(case["status"], case["body"]), VOCABULARY)[0]
    wanted = case["expected"]
    assert (result.category, result.restricted, str(result.confidence)) == (wanted["category"], wanted["restricted"], wanted["confidence"])


@pytest.mark.parametrize("case", load_cases("classification", "provider_refused"))
def test_a_bad_provider_response_fails_the_line_and_keeps_the_key_out_of_the_error(case):
    result = classify_receipt(ONE_RECEIPT, _provider(case["status"], case["body"]), VOCABULARY)[0]
    assert re.search(case["expected"], result.error)
    assert result._replace(error=None) == ClassifiedLine(result.line_id)
    assert "very-secret" not in result.error


@pytest.mark.parametrize("case", load_cases("classification", "provider_refused"))
def test_a_bad_provider_response_is_not_cached_and_is_asked_again(case, tmp_path):
    sent = []
    complete = cached(_provider(case["status"], case["body"], sent), "some/model", tmp_path)
    for _ in range(2):
        classify_receipt(_one_line_receipt(), complete, VOCABULARY)
    assert len(sent) == 2
    assert list(tmp_path.iterdir()) == []


def test_the_request_asks_for_the_contract_and_not_the_flag():
    sent = []
    classify_receipt(ONE_RECEIPT, _provider(200, _answer_body(GOOD_ANSWER), sent), VOCABULARY)
    assert len(sent) == len(ONE_RECEIPT.lines)
    request = json.loads(sent[0].content)
    schema = request["response_format"]["json_schema"]["schema"]
    assert (request["model"], request["temperature"], request["stream"]) == ("some/model", 0, False)
    assert set(schema["properties"]) == set(schema["required"]) == {"category", "confidence", "recipient_name", "recipient_type", "venue_type", "item_kind", "essential_toiletry"}
    assert schema["properties"]["category"]["enum"] == list(VOCABULARY.categories)
    assert [message["role"] for message in request["messages"]] == ["system", "user"]


# --- What the model reads ---


@pytest.mark.parametrize("case", load_cases("classification", "line_inputs"))
def test_a_line_input_holds_what_the_model_may_read(case):
    table = case["translations"]
    translator = NoOpTranslator() if table is None else TableTranslator(table)
    normalized = normalize_receipt(Receipt.model_validate(case["receipt"]), translator)
    found = [json.loads(line_input(normalized, line)) for line in normalized.lines]
    assert exact(found) == exact(case["expected"]["fields"])


@pytest.mark.parametrize("case", load_cases("classification", "receipt_languages"))
def test_the_receipt_language_is_the_language_of_most_lines(case):
    assert receipt_language(case["languages"]) == case["expected"]["language"]


def test_the_lines_own_words_come_first_and_a_translation_follows_them():
    normalized = _receipt_with("Blumen", TableTranslator({"Blumen": "Flowers"}), "de")
    assert list(json.loads(line_input(normalized, normalized.lines[0])))[:3] == ["original_description", "description_language", "english_translation"]


def test_the_prompt_sets_the_order_of_evidence_and_calls_both_texts_what_they_are():
    prompt = build_prompt(VOCABULARY)
    flat = " ".join(prompt.split())
    assert "after deterministic normalization" in flat and "not necessarily character for character as printed" in flat
    assert "exactly as printed" not in flat
    assert "receipt_language is inferred from the languages of the receipt's lines, not recorded on the receipt" in flat
    assert "do not assume original_description is written in it" in flat
    order = [prompt.index(name) for name in ("1. original_description", "2. english_translation", "3. Receipt context")]
    assert order == sorted(order)
    assert "never choose a category from the merchant or the price alone" in prompt
    assert "go by the original and lower your confidence" in prompt


def test_the_prompt_keeps_an_items_category_apart_from_its_purpose():
    flat = " ".join(build_prompt(VOCABULARY).split())
    assert "An item purchased as a gift or award retains the category of what the item physically is." in flat
    assert "a non-electronic watch or bracelet is `jewelry` even when purchased for an employee or client" in flat
    assert "such as gift baskets, flowers or greeting cards" in flat


def test_the_prompt_lets_only_venue_type_use_the_merchant_and_only_after_the_line_shows_an_event():
    flat = " ".join(build_prompt(VOCABULARY).split())
    assert "never guess, and do not work these out from the receipt context, except venue_type as described below" in flat
    assert "come from those words alone" not in flat, "the stricter wording that cost recipients and medication kinds must not return"
    assert "The line's own words must show an event or venue" in flat
    assert "merchant_name and merchant_category may then decide which kind it is" in flat
    assert "With no event or venue in the line's words, or when its kind cannot be told, use null, whatever the merchant" in flat


def test_a_venue_with_no_event_in_the_line_is_still_accepted_as_written():
    """The contract checks a venue against its category, and leaves its evidence to the prompt: this says so."""
    answer = GOOD_ANSWER.replace('"venue_type": null', '"venue_type": "sporting_event"')
    line = classify_receipt(_receipt_with("Sandwich"), lambda prompt, text: answer, VOCABULARY)[0]
    assert (line.category, line.venue_type, line.error) == ("other", "sporting_event", None)


def test_on_the_real_data_one_request_per_line_holds_no_answers():
    seen = []

    def complete(prompt, text):
        seen.append((prompt, text))
        return GOOD_ANSWER

    for item in INGESTED:
        classify_receipt(normalize_receipt(item.receipt), complete, VOCABULARY)
    assert len(seen) == 79
    assert {key for _, text in seen for key in json.loads(text)} <= LINE_INPUT_FIELDS
    notes = [item.ground_truth.notes for item in INGESTED if item.ground_truth.notes]
    assert notes and not any(note in prompt or note in text for note in notes for prompt, text in seen)
    sent = [json.loads(text) for _, text in seen]
    assert not [line for line in sent if "submitted_expense_type" in line or "labels" in line or "category" in line]
    by_text = {line["original_description"]: line for line in sent}
    assert by_text["Mojito sin alcohol"]["description_language"] == "es" and by_text["Mojito sin alcohol"]["receipt_language"] == "en"
    assert by_text["Vino tinto Rioja Reserva 750ml"]["receipt_language"] is None


def test_the_prompt_lists_every_value_and_never_asks_for_a_judgement():
    prompt = build_prompt(VOCABULARY)
    assert all(f"- {name}: " in prompt for name in VOCABULARY.categories)
    assert all(value in prompt for value in (*VOCABULARY.recipient_types, *VOCABULARY.venue_types, *VOCABULARY.item_kinds))
    assert not re.search(r"restricted|reimburs|policy|exception|violation", prompt, re.IGNORECASE)


# --- The vocabulary ---


def test_the_taxonomy_has_21_categories_and_matches_the_rule_catalog():
    assert len(VOCABULARY.categories) == 21
    assert list(VOCABULARY.categories) == allowed_values(FIELDS, "item.category")


def test_the_attribute_values_are_the_engines():
    assert list(VOCABULARY.recipient_types) == allowed_values(FIELDS, "item.recipient_type")
    assert list(VOCABULARY.venue_types) == allowed_values(FIELDS, "item.venue_type")
    assert list(VOCABULARY.item_kinds) == allowed_values(FIELDS, "item.item_kind")


def test_the_flag_from_the_taxonomy_matches_every_answer_in_the_data():
    for item in INGESTED:
        for labels in item.ground_truth.line_labels.values():
            assert VOCABULARY.categories[labels.category].restricted is labels.restricted


def test_a_file_with_no_taxonomy_table_is_refused(tmp_path):
    path = tmp_path / "taxonomy.md"
    path.write_text("# No table here\n")
    with pytest.raises(ValueError, match="no taxonomy table"):
        load_taxonomy(path)


# --- Scores ---


@pytest.mark.parametrize("case", load_cases("classification", "scores"))
def test_scores_count_failures_as_wrong(case):
    scores = score_lines(case["lines"])
    found = {
        "category_right": scores["accuracy"]["numerator"],
        "restricted_right": scores["restricted_accuracy"]["numerator"],
        "failures": scores["failures"]["numerator"],
        "total": scores["lines"],
    }
    assert found == case["expected"]


# --- Settings and the cache ---


def _config(tmp_path, *, model="some/model", key="secret"):
    return CONFIG.model_copy(
        update={
            "classification": CONFIG.classification.model_copy(update={"model": model}),
            "paths": CONFIG.paths.model_copy(update={"llm_cache": tmp_path}),
            "openrouter_api_key": SecretStr(key) if key else None,
        }
    )


def _counting_client(sent, built=None):
    def client(**settings):
        if built is not None:
            built.update(settings)

        def handler(request):
            sent.append(request)
            return httpx.Response(200, json=_answer_body(GOOD_ANSWER))

        return OpenRouter(api_key="k", client=httpx.Client(transport=httpx.MockTransport(handler)), retry_config=None)

    return client


def test_the_committed_settings_name_a_classification_model_of_its_own():
    assert CONFIG.classification.model and CONFIG.classification.model != CONFIG.translation.model


def test_settings_build_a_cached_classifier_for_the_classification_model(tmp_path, monkeypatch):
    sent, built = [], {}
    monkeypatch.setattr(classifier_module, "OpenRouter", _counting_client(sent, built))
    complete = build_classifier(_config(tmp_path), VOCABULARY)
    assert [complete("prompt", _line()), complete("prompt", _line())] == [GOOD_ANSWER] * 2
    assert len(sent) == 1
    assert json.loads(sent[0].content)["model"] == "some/model"
    assert built["timeout_ms"] == CONFIG.llm.timeout_seconds * 1000


def _line():
    return json.dumps({"original_description": "Item"})


def test_a_new_prompt_is_not_answered_from_the_cache(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(classifier_module, "OpenRouter", _counting_client(sent))
    complete = build_classifier(_config(tmp_path), VOCABULARY)
    complete("one prompt", _line())
    complete("another prompt", _line())
    assert len(sent) == 2


def test_a_new_output_contract_is_not_answered_from_the_cache_even_with_the_same_prompt(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(classifier_module, "OpenRouter", _counting_client(sent))
    build_classifier(_config(tmp_path), VOCABULARY)("prompt", _line())
    changed = VOCABULARY._replace(venue_types=(*VOCABULARY.venue_types, "museum"))
    build_classifier(_config(tmp_path), changed)("prompt", _line())
    assert len(sent) == 2


def test_an_old_answer_found_in_the_cache_is_still_checked():
    old = classify_receipt(ONE_RECEIPT, lambda prompt, text: '{"category": "alcohol"}', VOCABULARY)
    assert all(line.error and line.confidence is None for line in old)


@pytest.mark.parametrize("settings", [{"model": None}, {"key": None}])
def test_a_classifier_with_no_way_to_run_is_refused(tmp_path, settings):
    with pytest.raises(ValueError, match="classification needs"):
        build_classifier(_config(tmp_path, **settings), VOCABULARY)


# --- Boundaries ---


def _imports(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    return found


CLASSIFICATION = SOURCE / "classification"
CORE = ("core/__init__.py", "core/models.py", "core/common.py", "core/recipients.py")
BACKENDS = ("backends/__init__.py", "backends/llm.py", "backends/jev.py", "backends/registry.py")
LAUNCHERS = ("evaluate.py", "robustness.py")  # kept only as the commands they were


def test_the_production_classifiers_cannot_reach_answers_or_the_engine():
    for module in ("__init__.py", "orchestrator.py", *CORE, *BACKENDS):
        for name in _imports(CLASSIFICATION / module):
            assert not name.startswith(("expense_validator.evaluation", "expense_validator.offline_eval", "expense_validator.ingestion", "expense_validator.engine")), (module, name)


def test_backends_are_independent_and_the_shared_core_knows_none():
    for module in CORE:
        assert not [name for name in _imports(CLASSIFICATION / module) if name.startswith("expense_validator.classification.backends")], module
    # A backend builds on the core alone: never on another backend, nor on the registry.
    for module in ("backends/llm.py", "backends/jev.py"):
        classification = {name for name in _imports(CLASSIFICATION / module) if name.startswith("expense_validator.classification")}
        assert classification <= {"expense_validator.classification.core.common", "expense_validator.classification.core.models"}, (module, classification)
    # The subpackages import nothing, so that importing one backend never loads another.
    assert not _imports(CLASSIFICATION / "backends" / "__init__.py") and not _imports(CLASSIFICATION / "core" / "__init__.py")
    # The orchestrator knows no backend: it runs whichever the validator hands it, through the shared core.
    orchestrator = {name for name in _imports(CLASSIFICATION / "orchestrator.py") if name.startswith("expense_validator.classification")}
    assert orchestrator == {"expense_validator.classification.core.common", "expense_validator.classification.core.models"}
    # The validator reaches a backend only through the registry, the shared core and the orchestrator.
    validator = {name for name in _imports(SOURCE / "validator.py") if name.startswith("expense_validator.classification")}
    assert validator == {"expense_validator.classification.backends.registry", "expense_validator.classification.core.common", "expense_validator.classification.core.models", "expense_validator.classification.orchestrator"}


def test_every_registered_backend_has_the_same_interface():
    from expense_validator.classification.backends.registry import BACKENDS
    from expense_validator.classification.core.models import Backend

    assert list(BACKENDS) == ["chat_completions", "decisions"]  # adapters, named for their protocol: never a provider or model
    assert all(isinstance(backend, Backend) and all(callable(f) for f in backend if not isinstance(f, str)) for backend in BACKENDS.values())
    assert {name: backend.cache_folder for name, backend in BACKENDS.items()} == {"chat_completions": "", "decisions": "jev"}  # the folders recordings were made in


def test_no_model_comparison_lives_in_the_application():
    for path in CLASSIFICATION.rglob("*.py"):
        assert not {"comparison", "contract"} & {name.rsplit(".", 1)[-1] for name in _imports(path)}, path
        assert path.stem not in {"comparison", "contract"}, path


def test_no_other_package_imports_classification_and_its_package_leaves_out_evaluation():
    for package in ("engine", "policy", "compiler", "ingestion", "normalization", "offline_eval"):
        for path in (SOURCE / package).rglob("*.py"):
            assert not [name for name in _imports(path) if name.startswith("expense_validator.classification")], path
    for path in CLASSIFICATION.rglob("*.py"):
        if str(path.relative_to(CLASSIFICATION)) not in LAUNCHERS:
            assert not [name for name in _imports(path) if name.startswith("expense_validator.evaluation")], path


def test_the_evaluation_launchers_only_run_their_command():
    for module in LAUNCHERS:
        tree = ast.parse((CLASSIFICATION / module).read_text())
        body = [node for node in tree.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
        assert len(body) == 1 and isinstance(body[0], ast.If) and ast.unparse(body[0].test) == "__name__ == '__main__'", module


def test_a_new_backend_needs_only_its_module_and_registration(monkeypatch, tmp_path):
    """Verify a registered backend reuses validator caching, replay, and evaluation."""
    import json
    from expense_validator import ExpenseValidator
    from expense_validator.classification.backends import registry
    from expense_validator.classification.core import common, models
    from expense_validator.models import ValidatorSettings
    from expense_validator.run_receipts import load_receipts

    def classify_receipt(normalized, complete, settings, vocabulary):
        lines = []
        for line in normalized.lines:
            answer = json.loads(complete("stand-in instructions", common.line_input(normalized, line)))
            result = models.ClassifiedLine(line.line_id, category=answer["category"], restricted=vocabulary.categories[answer["category"]].restricted, confidence=Decimal("0.7"))
            lines.append(common.finalize(result, line, vocabulary))
        return tuple(lines)

    def connect(client, settings, vocabulary):
        return lambda prompt, text: json.dumps({"category": "food_beverage"})  # a real backend would call its model here

    stand_in = models.Backend(check_model=lambda model: None, prompt=lambda vocabulary: "stand-in instructions", cache_key=lambda model, vocabulary: model,
                              cache_folder="stand-in", connect=connect, classify_receipt=classify_receipt)
    monkeypatch.setitem(registry.BACKENDS, "stand-in", stand_in)
    receipts = load_receipts()[:2]
    settings = ValidatorSettings.from_config(ROOT / "config.yaml", mode="live", cache_dir=tmp_path, classification_backend="stand-in", classification_model="stand-in-model")
    live = ExpenseValidator(settings.model_copy(update={"translation_enabled": False, "openrouter_api_key": SecretStr("sk-or-placeholder")})).validate_batch(receipts, context_complete=False)
    assert all(line.category == "food_beverage" and line.confidence == 0.7 for result in live.results for line in result.line_items)
    assert list((tmp_path / "stand-in").iterdir()) and live.results[0].provenance.classification_backend == "stand-in"
    replay = ExpenseValidator(settings.model_copy(update={"mode": "replay", "translation_enabled": False})).validate_batch(receipts, context_complete=False)
    assert [r.to_record()["exceptions"] for r in replay.results] == [r.to_record()["exceptions"] for r in live.results]


@pytest.mark.parametrize("case", load_cases("classification", "adapters"))
def test_the_model_decides_which_adapter_runs_it(case):
    from expense_validator.classification.backends.registry import ADAPTERS, BACKENDS, backends_for

    assert backends_for(case["model"]) == case["expected"]["backends"]
    assert set(ADAPTERS) == set(BACKENDS) and {adapter.provider for adapter in ADAPTERS.values()} == {"openrouter"}
