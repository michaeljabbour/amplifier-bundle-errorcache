"""ErrorCache hook — automatically searches for verified solutions on errors."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from amplifier_core import HookResult, ModuleCoordinator

_UNRESOLVED_VAR = re.compile(r"\$\{.+\}")


def _resolve_env(value: str | None, env_var: str, default: str) -> str:
    """Resolve a config value, falling back to env var then default.

    Handles unresolved ``${VAR:-default}`` patterns that YAML config loaders
    may pass through literally when they don't support shell-style variable
    interpolation.

    Priority: real config value > environment variable > hardcoded default.
    """
    if value and not _UNRESOLVED_VAR.search(value):
        return value
    return os.environ.get(env_var, default)


__amplifier_module_type__ = "hook"

# Patterns that indicate errors in tool output (even when tool "succeeds")
ERROR_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\)", re.IGNORECASE),
    re.compile(r"(?:Error|Exception|FAILED|FATAL):", re.IGNORECASE),
    re.compile(r"error\[E\d+\]", re.IGNORECASE),  # Rust/pyright errors
    re.compile(r"ERR_[A-Z_]+"),  # Node.js error codes
    re.compile(r"ECONNREFUSED|ENOENT|EACCES|EPERM"),  # POSIX errors
    re.compile(r"ModuleNotFoundError|ImportError|SyntaxError|TypeError|ValueError"),
    re.compile(r"error TS\d+:"),  # TypeScript errors
    re.compile(r"FAILED.*tests?|tests? FAILED", re.IGNORECASE),
    re.compile(r"Build failed|Compilation failed|Cannot find module", re.IGNORECASE),
]


def _extract_error_text(output: str) -> str | None:
    """Extract the most relevant error lines from tool output."""
    lines = output.strip().splitlines()
    error_lines = []
    capture = False

    for line in lines:
        if any(p.search(line) for p in ERROR_PATTERNS):
            capture = True
        if capture:
            error_lines.append(line)
            if len(error_lines) >= 10:
                break

    if error_lines:
        return "\n".join(error_lines)

    # Fallback: check if any pattern matches anywhere
    for p in ERROR_PATTERNS:
        m = p.search(output)
        if m:
            # Return context around the match
            start = max(0, m.start() - 200)
            end = min(len(output), m.end() + 500)
            return output[start:end].strip()

    return None


def _get_output_text(result: Any) -> str:
    """Extract text from a tool result."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        # ToolResult.model_dump() shape
        output = result.get("output", "")
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            # Bash tool returns {"stdout": "...", "stderr": "..."}
            parts = []
            if output.get("stdout"):
                parts.append(str(output["stdout"]))
            if output.get("stderr"):
                parts.append(str(output["stderr"]))
            return "\n".join(parts)
        return str(output)
    return str(result)


class ErrorCacheClient:
    """Minimal HTTP client for the ErrorCache API."""

    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def search(self, error_text: str, limit: int = 3) -> list[dict]:
        """Search ErrorCache for solutions. Returns list of questions with answers."""
        try:
            sig = urllib.parse.quote(error_text[:500])
            url = f"{self.api_url}/search/similar?error_signature={sig}&limit={limit}"
            req = urllib.request.Request(url, headers=self._headers(), method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                results = data if isinstance(data, list) else data.get("data", [])
                return results if isinstance(results, list) else []
        except Exception:
            return []

    def submit(
        self,
        title: str,
        error_signature: str,
        root_cause: str,
        fix_approach: str,
        environment: dict | None = None,
    ) -> bool:
        """Submit an error + solution to ErrorCache."""
        try:
            # Submit question
            q_body = json.dumps(
                {
                    "title": title[:300],
                    "error_signature": error_signature,
                    "error_category": "other",
                    "environment": environment or {},
                }
            ).encode()
            q_req = urllib.request.Request(
                f"{self.api_url}/questions",
                data=q_body,
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(q_req, timeout=10) as resp:
                q_data = json.loads(resp.read())
                question_id = q_data.get("data", {}).get("id")

            if not question_id:
                return False

            # Submit answer
            a_body = json.dumps(
                {
                    "root_cause": root_cause,
                    "fix_approach": fix_approach,
                }
            ).encode()
            a_req = urllib.request.Request(
                f"{self.api_url}/questions/{question_id}/answers",
                data=a_body,
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(a_req, timeout=10):
                return True
        except Exception:
            return False


class ErrorCacheHook:
    """Watches for errors, searches ErrorCache, tracks fixes."""

    def __init__(self, client: ErrorCacheClient, auto_search: bool, auto_submit: bool):
        self.client = client
        self.auto_search = auto_search
        self.auto_submit = auto_submit
        self.tracked_errors: dict[
            str, dict
        ] = {}  # error_key -> {error_text, tool_name, ...}
        self.applied_solutions: dict[str, dict] = {}  # error_key -> solution info

    def _error_key(self, text: str) -> str:
        """Create a dedup key from error text."""
        # Strip file paths, line numbers, etc. for matching
        cleaned = re.sub(
            r"/[\w./\\-]+\.(py|js|ts|rs|go|rb|java|c|cpp|h)", "<FILE>", text
        )
        cleaned = re.sub(r":\d+:\d+", ":<N>", cleaned)
        cleaned = re.sub(r"line \d+", "line <N>", cleaned, flags=re.IGNORECASE)
        return cleaned[:200].strip().lower()

    async def handle_tool_error(self, event: str, data: dict[str, Any]) -> HookResult:
        """tool:error — a tool raised an exception."""
        if not self.auto_search:
            return HookResult(action="continue")

        error_info = data.get("error", {})
        error_text = f"{error_info.get('type', 'Error')}: {error_info.get('msg', '')}"
        tool_name = data.get("tool_name", "unknown")

        return await self._search_and_inject(error_text, tool_name)

    async def handle_tool_post(self, event: str, data: dict[str, Any]) -> HookResult:
        """tool:post — a tool completed. Check for errors in output or resolved errors."""
        tool_name = data.get("tool_name", "")
        result = data.get("result", {})
        output_text = _get_output_text(result)

        # Check if a tracked error was resolved
        if self.auto_submit and self.tracked_errors:
            success = True
            if isinstance(result, dict):
                success = result.get("success", True)
            if success:
                resolved = []
                for key, err_info in list(self.tracked_errors.items()):
                    if err_info.get("tool_name") == tool_name:
                        # Same tool now succeeds — error likely resolved
                        resolved.append((key, err_info))

                for key, err_info in resolved:
                    del self.tracked_errors[key]
                    # We could auto-submit here, but we need the fix context
                    # which we don't have from just the tool output.
                    # Leave this for the tool-errorcache manual submit.

        # Check for errors in "successful" tool output
        if not self.auto_search:
            return HookResult(action="continue")

        if tool_name not in ("bash", "Bash", "shell", "run_command"):
            return HookResult(action="continue")

        if not output_text or len(output_text) < 20:
            return HookResult(action="continue")

        error_text = _extract_error_text(output_text)
        if not error_text:
            return HookResult(action="continue")

        return await self._search_and_inject(error_text, tool_name)

    async def _search_and_inject(self, error_text: str, tool_name: str) -> HookResult:
        """Search ErrorCache and inject results into context."""
        key = self._error_key(error_text)

        # Don't search for the same error twice in one session
        if key in self.tracked_errors:
            return HookResult(action="continue")

        # Track this error
        self.tracked_errors[key] = {
            "error_text": error_text,
            "tool_name": tool_name,
        }

        solutions = self.client.search(error_text, limit=3)

        if not solutions:
            return HookResult(action="continue")

        # Format solutions for context injection
        lines = [
            "## ErrorCache: Verified Solutions Found",
            "",
            f"Found {len(solutions)} solution(s) for this error.",
            "Review these before debugging independently:",
            "",
        ]

        for i, q in enumerate(solutions[:3], 1):
            title = q.get("title", "Untitled")
            verifications = q.get("verification_count", 0)
            status = q.get("status", "open")
            answer_count = q.get("answer_count", 0)
            q_id = q.get("id", "")

            lines.append(f"### [{i}] {title}")
            lines.append(
                f"Status: {status} | Answers: {answer_count} | Verifications: {verifications}"
            )

            if q.get("best_answer"):
                ba = q["best_answer"]
                if ba.get("root_cause"):
                    lines.append(f"Root cause: {ba['root_cause']}")
                if ba.get("fix_approach"):
                    lines.append(f"Fix: {ba['fix_approach']}")
                if ba.get("patch_or_commands"):
                    cmds = ba["patch_or_commands"]
                    if isinstance(cmds, list):
                        lines.append(f"Commands: {' && '.join(cmds)}")

            if q_id:
                lines.append(f"Link: https://errorcache.com/questions/{q_id}")
            lines.append("")

        lines.append(
            "If you apply one of these fixes, use `errorcache verify_solution` "
            "to confirm it worked."
        )

        return HookResult(
            action="inject_context",
            context_injection="\n".join(lines),
            context_injection_role="system",
            ephemeral=True,
            user_message=f"ErrorCache: Found {len(solutions)} verified solution(s)",
            user_message_level="info",
        )


async def mount(
    coordinator: ModuleCoordinator,
    config: dict[str, Any] | None = None,
) -> Callable | None:
    """Mount the ErrorCache hook."""
    config = config or {}
    api_url = _resolve_env(
        config.get("api_url"), "ERRORCACHE_API_URL", "https://api.errorcache.com/api/v1"
    )
    api_key = _resolve_env(config.get("api_key"), "ERRORCACHE_API_KEY", "")
    auto_search = config.get("auto_search", True)
    auto_submit = config.get("auto_submit", True)

    client = ErrorCacheClient(api_url=api_url, api_key=api_key)
    hook = ErrorCacheHook(
        client=client, auto_search=auto_search, auto_submit=auto_submit
    )

    unreg_error = coordinator.hooks.register(
        "tool:error",
        hook.handle_tool_error,
        priority=10,
        name="errorcache-tool-error",
    )
    unreg_post = coordinator.hooks.register(
        "tool:post",
        hook.handle_tool_post,
        priority=50,
        name="errorcache-tool-post",
    )

    def cleanup():
        unreg_error()
        unreg_post()

    return cleanup
