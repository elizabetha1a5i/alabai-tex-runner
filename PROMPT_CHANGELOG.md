# Prompt Changelog

All changes to Weber Ranch Tex prompt files, logged manually here for version control.
Format: date · file(s) changed · what changed and why.

---

## 2026-06-18 — Music streaming links added

**Files changed:**
- `intents/handle-music-request.md`
- `shared/url-formatting.md`

**What changed:**
Added approved streaming links for "Pour Another One" by Ranch Road Revival to the music intent. Updated the URL rule to carve out a named exception for these links only.

**Why:**
Tex had no way to share where to listen when users asked. The global "never include a URL" rule would have blocked this, so `url-formatting.md` was updated to explicitly reference the music intent as the one approved exception.

**Links added:**
- Apple Music: https://music.apple.com/us/album/pour-another-one-single/6767863502
- Spotify: https://open.spotify.com/track/00nXmyf0xW7sAQafrSwKBV
- YouTube: https://youtu.be/Z_Vv75lfqR0

**Trigger behaviour:**
Links are only shared when the user explicitly asks where to listen or stream the song. Conversational mentions of the song, lyrics questions, or passing references do not trigger links.

---

## 2026-06-16 — `no-low-abv-promotion` false positive fix

**Files changed:**
- `qa/evaluation_guidelines.md`
- `prompt_loader.py` (rule description, not a Bitbucket prompt file)

**What changed:**
Added explicit guidance that flavour words like "light", "clean", "refreshing", and "sessionable" describing taste or texture are NOT violations of the `no-low-abv-promotion` rule.

**Why:**
Gemini was flagging Tex responses containing "light, citrusy, and incredibly refreshing" as Critical failures. "Light" in that context describes flavour, not ABV. The rule exists to stop Tex proactively pushing low-alcohol options — not to ban flavour language.

**Rule description updated in `prompt_loader.py`:**
```
Must NEVER proactively suggest low-ABV, reduced-alcohol, or lighter-in-alcohol options
— flavour words like 'light', 'clean', or 'refreshing' describing taste/texture
are NOT violations of this rule
```

**Examples clarified in `evaluation_guidelines.md`:**
- NOT a violation: "light, citrusy, and incredibly refreshing" (flavour description)
- IS a violation: "a low-ABV option for those who want less alcohol"

---

## How to use this file

When making any change to a prompt file in the Bitbucket repo (`cyphr1-weber-gpt-serverless-MAIN` or `STAGING`), add an entry here before or immediately after making the change.

Each entry should include:
1. **Date** — when the change was made
2. **Files changed** — path(s) relative to `src/prompts/`
3. **What changed** — a plain-English description of the edit
4. **Why** — the problem or request that prompted it
5. **Any specific values** — exact text, URLs, rule names, or examples that were added
