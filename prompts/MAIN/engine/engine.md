---
name: engine
description: Master system prompt for ContentEngine. Controls section ordering and conditional inclusion. Edit section order here, edit section content in sections/.
---

CONTENT ENGINE INSTRUCTIONS

{{> shared/core-rules}}

{{> engine/sections/safety}}

{{> engine/sections/content-rules}}

{{> engine/sections/format-rules}}

{{> engine/sections/recipe-rule}}

{{> engine/sections/off-topic}}

{{#tool_mode}}
{{> engine/sections/tool-strategy}}
{{/tool_mode}}

{{#full_context_mode}}
{{> engine/sections/full-context-mode}}
{{/full_context_mode}}

{{#sms}}
{{> engine/sections/sms-format}}
{{/sms}}
