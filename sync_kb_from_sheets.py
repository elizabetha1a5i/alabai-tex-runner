"""
sync_kb_from_sheets.py
Run once to export Google Sheets KB data into local kb/ JSON files.
After this, edit kb/*.json in VS Code directly — no Sheets access needed.

Usage:
    python sync_kb_from_sheets.py
"""
import json
import os
import re
import sys

KB_DIR = os.path.join(os.path.dirname(__file__), "kb")
os.makedirs(KB_DIR, exist_ok=True)

# Import the loaders from the prod runner (reuses the same Google auth)
sys.path.insert(0, os.path.dirname(__file__))

try:
    from tex_qa_test_runner_prod import get_google_services, load_recipes, load_brand_facts, load_ingredients
except ImportError as e:
    print(f"❌ Could not import from tex_qa_test_runner_prod: {e}")
    sys.exit(1)


def main():
    print("🔑 Connecting to Google Sheets...")
    _, sheets_service = get_google_services()
    if not sheets_service:
        print("❌ Could not connect to Google Sheets. Make sure token.json is present.")
        sys.exit(1)

    print("\n📖 Loading recipes...")
    recipes = load_recipes(sheets_service)
    recipes_path = os.path.join(KB_DIR, "recipes.json")
    with open(recipes_path, "w", encoding="utf-8") as f:
        json.dump(recipes, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Saved {len(recipes)} recipes → kb/recipes.json")

    print("\n📖 Loading brand facts...")
    facts = load_brand_facts(sheets_service)
    facts_path = os.path.join(KB_DIR, "brand_facts.json")
    with open(facts_path, "w", encoding="utf-8") as f:
        json.dump(facts, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Saved {len(facts)} brand facts → kb/brand_facts.json")

    print("\n📖 Loading ingredients...")
    ingredients = load_ingredients(sheets_service)
    ingr_path = os.path.join(KB_DIR, "ingredients.json")
    with open(ingr_path, "w", encoding="utf-8") as f:
        json.dump(ingredients, f, indent=2, ensure_ascii=False)
    print(f"  ✅ Saved {len(ingredients)} ingredients → kb/ingredients.json")

    print("\n✅ Done! Edit kb/*.json in VS Code to add or update data.")
    print("   Run this script again anytime to re-sync from Google Sheets.")


if __name__ == "__main__":
    main()
