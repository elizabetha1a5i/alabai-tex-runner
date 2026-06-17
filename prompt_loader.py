"""
Loads Weber Ranch GPT prompt files as the source of truth for QA evaluation.
Production tests use the MAIN repo; staging tests use the STAGING repo.

Lookup order:
  1. prompts/MAIN (or STAGING) bundled inside this runner repo  ← used in CI
  2. Local Bitbucket checkout on disk                           ← used locally
"""
from pathlib import Path

_HERE = Path(__file__).parent

_MAIN_BUNDLED    = _HERE / "prompts" / "MAIN"
_STAGING_BUNDLED = _HERE / "prompts" / "STAGING"

_MAIN_LOCAL    = Path(r"C:\Users\ElizabethAlabi\Downloads\BITBUCKET - REPO\cyphr1-weber-gpt-serverless-MAIN\src\prompts")
_STAGING_LOCAL = Path(r"C:\Users\ElizabethAlabi\Downloads\BITBUCKET - REPO\cyphr1-weber-gpt-serverless-STAGING\src\prompts")

# Applied to every test regardless of category
_CORE_FILES = [
    "shared/core-rules.md",
    "shared/url-formatting.md",
    "engine/sections/format-rules.md",
    "engine/sections/content-rules.md",
    "engine/sections/safety.md",
    "engine/sections/off-topic.md",
]

# Additional files loaded per test category
_CATEGORY_EXTRAS = {
    "Cocktails": [
        "engine/sections/recipe-rule.md",
        "shared/cocktail-template.md",
        "shared/core-formatting-instructions.md",
        "intents/handle-recipe-search.md",
        "intents/handle-confirmation.md",
    ],
    "Recipe_KB": [
        "engine/sections/recipe-rule.md",
        "shared/cocktail-template.md",
        "shared/core-formatting-instructions.md",
        "engine/sections/full-context-mode.md",
        "intents/handle-recipe-search.md",
    ],
    "Custom": [
        "engine/sections/recipe-rule.md",
        "shared/cocktail-template.md",
        "shared/core-formatting-instructions.md",
        "shared/novel-cocktail-instructions.md",
        "intents/handle-novel-cocktail.md",
    ],
    "Safety": [
        "intents/handle-underage.md",
    ],
    "Security": [
        "intents/handle-jailbreak-attempt.md",
    ],
    "Brand": [
        "shared/buy-now-information.md",
    ],
    "Store": [
        "shared/buy-now-information.md",
        "intents/handle-store-locator.md",
        "intents/handle-buy-now.md",
    ],
    "Personas": [],
    "Edge_Cases": [
        "engine/sections/recipe-rule.md",
        "shared/cocktail-template.md",
        "shared/core-formatting-instructions.md",
        "intents/handle-jailbreak-attempt.md",
        "intents/handle-underage.md",
        "intents/handle-off-topic.md",
    ],
}


def _resolve_root(env):
    if env == "staging":
        if _STAGING_BUNDLED.exists():
            return _STAGING_BUNDLED
        if _STAGING_LOCAL.exists():
            return _STAGING_LOCAL
    else:
        if _MAIN_BUNDLED.exists():
            return _MAIN_BUNDLED
        if _MAIN_LOCAL.exists():
            return _MAIN_LOCAL
    return None


def load_prompts_for_category(category, env="production"):
    """
    Returns (prompt_text, error_string).
    prompt_text is a labelled string containing all relevant prompt file contents.
    error_string is None on success, a message string on failure.
    """
    root = _resolve_root(env)
    if root is None:
        return None, (
            f"Prompt files not found for env='{env}'. "
            f"Expected bundled path: prompts/{'STAGING' if env == 'staging' else 'MAIN'}"
        )

    files   = _CORE_FILES + _CATEGORY_EXTRAS.get(category, [])
    parts   = []
    missing = []

    for rel in files:
        fp = root / Path(rel.replace("/", "\\") if "\\" not in rel else rel)
        if not fp.exists():
            fp = root / rel  # try as-is (Linux paths work on CI)
        if fp.exists():
            label = Path(rel).name.replace(".md", "").upper().replace("-", " ")
            parts.append(
                f"=== {label} ({rel}) ===\n{fp.read_text(encoding='utf-8').strip()}"
            )
        else:
            missing.append(rel)

    if missing:
        print(f"  ⚠️  Prompt files not found ({category}): {', '.join(missing)}")

    if not parts:
        return None, f"No prompt files loaded for category '{category}' from {root}"

    return "\n\n".join(parts), None
