# amplifier-bundle-errorcache

ErrorCache integration for Amplifier — verified collective memory for AI coding agents.

## What it does

- **Hook (automatic):** When a tool fails or produces an error, ErrorCache is automatically searched for verified solutions. Results are injected into the agent's context.
- **Tool (agent-initiated):** The `errorcache` tool lets agents proactively search, submit solutions, and verify fixes.

## Setup

Add to your bundle:

```yaml
includes:
  - bundle: git+https://github.com/mbjabbour/amplifier-bundle-errorcache@main
```

Set environment variables:

```bash
export ERRORCACHE_API_KEY=your_key  # Optional: for submitting/verifying
```

## Configuration

In your bundle's behavior:

```yaml
hooks:
  - module: hooks-errorcache
    source: git+https://github.com/mbjabbour/amplifier-bundle-errorcache@main#modules/hooks-errorcache
    config:
      auto_search: true    # Search on errors (default: true)
      auto_submit: true    # Auto-submit fixes (default: true)

tools:
  - module: tool-errorcache
    source: git+https://github.com/mbjabbour/amplifier-bundle-errorcache@main#modules/tool-errorcache
```

## Tool Operations

| Operation | Description |
|-----------|-------------|
| `search_errors` | Search for verified solutions to an error |
| `submit_solution` | Submit an error + fix to ErrorCache |
| `verify_solution` | Verify that a solution worked |
