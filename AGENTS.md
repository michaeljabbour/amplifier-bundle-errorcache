# Repository guidance

Preserve existing module IDs, imports and public entry points. Run the relevant offline tests after code changes; distinguish source checks from live provider or service acceptance. Never publish credentials, private session data or local research.

## Local market research

At session start, inspect `.research/market/INDEX.json` and its source review dates. Keep this directory ignored by Git. If missing, create a local source inventory with cited primary URLs, retrieval dates, evidence and explicit unanswered questions. Before positioning, launch, naming or adoption decisions, and whenever sources are more than 30 days old, refresh the relevant research and record the change in `.research/market/CHANGELOG.md`. A seeded link list is not a completed comparison. Never imply fresh content review from a metadata-only refresh. Keep useful findings local unless the owner explicitly approves a sanitized public summary.

Do not add a third-party capability, contact maintainers or create service credentials without the owner's authorization.
