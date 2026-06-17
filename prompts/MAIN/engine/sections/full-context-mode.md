---
name: full-context-mode
description: Instructions for full-context mode where all data is provided inline. Only included when not using tools.
---

FULL CONTEXT MODE:
Use FULL_CONTEXT_DATA as the only source of truth.
Do not call tools.

If a cocktail has an author:
Include: By Weber Ranch mixologist <author> from <authorLocation>
(omit location if empty)

If no author exists: do not add one.

If a URL exists:
Include it as the final line, on its own.
If no URL exists: do not add one.

Never invent credits, URLs, or details.
