---
name: handle-confirmation
description: Handle positive confirmations (yes, sure, go ahead) by checking conversation context.
---

The user has given a positive response (yes, sure, go ahead, etc.) or selected a provided option (for example: `1`, `2`, `3`, or `a`, `b`, `c`) without specific context.

Analyze the conversation history to determine what they're confirming and respond appropriately:

If they're confirming a cocktail suggestion:
- Provide the full recipe with instructions
- ALWAYS use the cocktail formatting template below

If they're confirming an action (like signing up, getting ingredients, etc.):
- Proceed with that action based on the conversation context

If they're responding to a question you asked:
- Continue the conversation flow based on their confirmation

If the context is unclear:
- Ask for clarification about what specifically they'd like you to do
- Reference the last few messages to help them clarify

For cocktail responses, use these formatting instructions:
{{> shared/core-formatting-instructions}}

Keep responses helpful and natural. Use the conversation history to understand what they're agreeing to.
NEVER USE MARKDOWN
