## Summary

<!-- 1-3 sentences explaining what this PR does and why -->

## Changes

<!-- List the main changes, grouped logically -->

-

## Verification

<!-- Check the items you have completed -->

- [ ] Import check passes: `uv run python -c "import xyz_agent_context.module; import xyz_agent_context.narrative; import xyz_agent_context.services; print('OK')"`
- [ ] Frontend build passes (if frontend changed): `cd frontend && npm run build`
- [ ] Table sync dry-run passes (if schema changed): `uv run python sync_all_tables.py --dry-run`
- [ ] No secrets committed (`.env`, API keys, credentials, etc.)

## Related

<!-- Related issues or PRs, e.g.: Closes #123 -->
