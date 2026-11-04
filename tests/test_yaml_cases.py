"""Guard the data-driven runner against silently skipped or ambiguous cases."""

import pytest

from yaml_cases import discover_cases


@pytest.mark.parametrize("document, message", [
    ("suite: typo\ncases: []", "suite must be"),
    ("suite: rules\ncase: []", "sections must be"),
    ("suite: rules\naccepted: []", "nonempty list"),
    ("suite: rules\nsuite: rulesets", "duplicate YAML key"),
    ("suite: rules\nrefused:\n  - {id: example, rule: {}, expected: x, typo: true}", "case keys"),
    ("suite: rules\naccepted:\n  - {id: example, rule: {}, expected: wrong}", "nonempty mapping"),
    ("suite: rules\nrefused:\n  - {id: example, rule: {}, expected: '['}", "unterminated character set"),
])
def test_invalid_case_file_fails_with_path(tmp_path, document, message):
    path = tmp_path / "broken.yaml"
    path.write_text(document)
    with pytest.raises(ValueError, match=message) as error:
        discover_cases(tmp_path)
    assert str(path) in str(error.value)


def test_discovers_nested_files_and_rejects_duplicate_ids(tmp_path):
    document = "suite: rules\nrefused:\n  - {id: example, rule: {}, expected: x}"
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "extra.yml").write_text(document)
    catalog = discover_cases(tmp_path)
    assert len(catalog["rules", "refused"]) == 1
    assert catalog["rules", "refused"][0].id == "nested/extra.yml::example"
    (tmp_path / "duplicate.yaml").write_text(document)
    with pytest.raises(ValueError, match="duplicate case id"):
        discover_cases(tmp_path)


def test_missing_cases_fail(tmp_path):
    with pytest.raises(ValueError, match="no YAML cases"):
        discover_cases(tmp_path)
