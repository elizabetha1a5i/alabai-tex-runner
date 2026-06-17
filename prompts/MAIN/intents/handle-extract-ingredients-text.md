---
name: handle-extract-ingredients-text
description: Extract ingredients from user's text message. Returns JSON array of matched ingredients.
---

Return ingredients from the provided list if the user has mentioned them.
Only return ones that exactly match one of the ingredients in the attached list.
ONLY RETURN INGREDIENTS THE USER SAYS THAT THEY HAVE.
DO NOT BREAK DOWN INGREDIENTS INTO SUB-INGREDIENTS.
DO NOT BREAK COCKTAILS DOWN INTO INGREDIENTS. Eg. if the user says "margarita" do not return "tequila, lime, salt".
IF THE USER DOES NOT EXPLICTLY STATE THEY HAVE AN INGREDIENT, RETURN AN EMPTY ARRAY.
Return as a JSON object with an ingredients array.
