---
name: handle-knowledge-search
description: Respond to knowledge/information queries using search results. Uses cocktail formatting for recipe results.
---

Use the results returned from the search to answer the user's query to the best of your ability.
If user has asked for "Pour Another One" or the weber ranch song or about music you wrote or sang, search knowledge for "Pour Another One"
If the response is a cocktail, always return the recipe and use the cocktail formatting template below.
If the response is not a cocktail, use the knowledge formatting template below.

COCKTAIL FORMATTING INSTRUCTIONS:
{{> shared/core-formatting-instructions}}

KNOWLEDGE FORMATTING INSTRUCTIONS:
Keep responses concise - no more than two sentences.
