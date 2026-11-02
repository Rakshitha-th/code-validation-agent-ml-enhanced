import json
from pathlib import Path
from typing import Any


RULES_DIRECTORY = Path(__file__).with_name("validation_rules")
SUPPORTED_RULE_LANGUAGES = {
    "python",
    "java",
    "javascript",
    "typescript",
    "c",
    "cpp",
    "csharp",
    "go",
}
SUPPORTED_MATCHERS = {
    "syntax_error",
    "mutable_default",
    "swallowed_exception",
    "return_in_finally",
    "unmanaged_file_open",
    "unbounded_read",
    "empty_exception_handler",
    "unconditional_loop",
    "unbounded_file_read",
    "unreleased_allocation",
}
SUPPORTED_CATEGORIES = {"bug", "resource"}
SUPPORTED_SEVERITIES = {"low", "medium", "high", "critical"}


def load_language_rules(language: str) -> dict[str, dict[str, Any]]:
    normalized_language = language.strip().lower()
    if normalized_language not in SUPPORTED_RULE_LANGUAGES:
        raise ValueError(f"No validation rule dataset is configured for {language!r}.")

    rules_path = RULES_DIRECTORY / f"{normalized_language}_rules.json"
    try:
        dataset = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Could not load the {normalized_language} validation rule dataset."
        ) from exc

    if (
        not isinstance(dataset, dict)
        or dataset.get("language") != normalized_language
        or dataset.get("version") != 1
        or not isinstance(dataset.get("rules"), list)
    ):
        raise RuntimeError(
            f"The {normalized_language} validation rule dataset has an invalid structure."
        )

    rules = {}
    for rule in dataset["rules"]:
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("id"), str)
            or rule.get("matcher") not in SUPPORTED_MATCHERS
            or rule.get("category") not in SUPPORTED_CATEGORIES
            or rule.get("severity") not in SUPPORTED_SEVERITIES
            or not isinstance(rule.get("description"), str)
            or not isinstance(rule.get("recommendation"), str)
            or not isinstance(rule.get("subcategory"), str)
        ):
            raise RuntimeError(
                f"The {normalized_language} validation rule dataset contains an invalid rule."
            )
        if rule["id"] in rules:
            raise RuntimeError(
                f"The {normalized_language} validation rule dataset contains duplicate IDs."
            )
        rules[rule["id"]] = rule
    return rules
