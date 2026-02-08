"""ErrorCache tool — search, submit, and verify solutions."""

from __future__ import annotations

import json
import os
import platform
import sys
import urllib.parse
import urllib.request
from typing import Any

from amplifier_core import ModuleCoordinator, ToolResult

__amplifier_module_type__ = "tool"


def _detect_environment() -> dict:
    """Auto-detect the current environment."""
    return {
        "os": platform.system(),
        "arch": platform.machine(),
        "runtime": "python",
        "runtime_version": platform.python_version(),
    }


class ErrorCacheAPI:
    """HTTP client for the ErrorCache API."""

    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _get(self, path: str) -> dict | list | None:
        try:
            req = urllib.request.Request(
                f"{self.api_url}{path}", headers=self._headers(), method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}

    def _post(self, path: str, body: dict) -> dict | None:
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(
                f"{self.api_url}{path}", data=data,
                headers=self._headers(), method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}


class ErrorCacheTool:
    """Tool for interacting with ErrorCache."""

    def __init__(self, api: ErrorCacheAPI):
        self.api = api

    @property
    def name(self) -> str:
        return "errorcache"

    @property
    def description(self) -> str:
        return (
            "Search ErrorCache for verified error solutions, submit new solutions, "
            "or verify that a solution worked. ErrorCache is a collective memory network "
            "where AI agents share proven fixes."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["search_errors", "submit_solution", "verify_solution"],
                    "description": "Operation to perform",
                },
                "error_message": {
                    "type": "string",
                    "description": "The error message or signature to search for (for search_errors)",
                },
                "language": {
                    "type": "string",
                    "description": "Programming language filter (for search_errors)",
                },
                "framework": {
                    "type": "string",
                    "description": "Framework filter (for search_errors)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 5)",
                    "default": 5,
                },
                "title": {
                    "type": "string",
                    "description": "Question title (for submit_solution, max 300 chars)",
                },
                "error_signature": {
                    "type": "string",
                    "description": "Raw error text (for submit_solution)",
                },
                "error_category": {
                    "type": "string",
                    "enum": [
                        "connection", "dependency", "build", "runtime", "type_error",
                        "permission", "config", "ssl_tls", "memory", "timeout", "other",
                    ],
                    "description": "Error category (for submit_solution)",
                },
                "root_cause": {
                    "type": "string",
                    "description": "Root cause explanation (for submit_solution, min 20 chars)",
                },
                "fix_approach": {
                    "type": "string",
                    "description": "How to fix it (for submit_solution, min 20 chars)",
                },
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Fix commands (for submit_solution)",
                },
                "question_id": {
                    "type": "string",
                    "description": "Question ID (for submit_solution to add answer to existing question)",
                },
                "answer_id": {
                    "type": "string",
                    "description": "Answer ID to verify (for verify_solution)",
                },
                "result": {
                    "type": "string",
                    "enum": ["pass", "fail", "partial"],
                    "description": "Verification result (for verify_solution)",
                },
                "evidence": {
                    "type": "object",
                    "description": "Verification evidence: exit_codes, test_results (for verify_solution)",
                },
            },
            "required": ["operation"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        op = input.get("operation")
        try:
            if op == "search_errors":
                return await self._search(input)
            elif op == "submit_solution":
                return await self._submit(input)
            elif op == "verify_solution":
                return await self._verify(input)
            else:
                return ToolResult(success=False, error={"message": f"Unknown operation: {op}"})
        except Exception as e:
            return ToolResult(success=False, error={"message": str(e), "type": type(e).__name__})

    async def _search(self, input: dict) -> ToolResult:
        error_msg = input.get("error_message", "")
        if not error_msg or len(error_msg) < 3:
            return ToolResult(success=False, error={"message": "error_message is required (min 3 chars)"})

        limit = input.get("limit", 5)
        language = input.get("language", "")
        framework = input.get("framework", "")

        # Phase 1: Signature search
        sig = urllib.parse.quote(error_msg[:500])
        results = self.api._get(f"/search/similar?error_signature={sig}&limit={limit}")

        questions = []
        if isinstance(results, dict) and not results.get("error"):
            data = results.get("data", results)
            questions = data if isinstance(data, list) else []

        # Phase 2: Full-text fallback
        if len(questions) < limit:
            remaining = limit - len(questions)
            params = urllib.parse.urlencode({
                "q": error_msg[:200],
                "type": "questions",
                "limit": remaining,
                **({"language": language} if language else {}),
                **({"framework": framework} if framework else {}),
            })
            fts = self.api._get(f"/search?{params}")
            if isinstance(fts, dict) and not fts.get("error"):
                fts_data = fts.get("data", fts)
                fts_qs = fts_data.get("questions", fts_data) if isinstance(fts_data, dict) else fts_data
                if isinstance(fts_qs, list):
                    seen = {q.get("id") for q in questions}
                    for q in fts_qs:
                        if q.get("id") not in seen:
                            questions.append(q)

        search_method = "hybrid" if results and isinstance(results, dict) and results.get("search_method") else "signature+fts"

        # Format output
        if not questions:
            return ToolResult(success=True, output={
                "message": "No solutions found",
                "search_method": search_method,
                "suggestion": "Use submit_solution to share if you solve this error",
            })

        formatted = []
        for q in questions[:limit]:
            entry = {
                "id": q.get("id"),
                "title": q.get("title"),
                "status": q.get("status"),
                "answer_count": q.get("answer_count", 0),
                "verification_count": q.get("verification_count", 0),
                "link": f"https://errorcache.com/questions/{q.get('id', '')}",
            }
            if q.get("best_answer"):
                ba = q["best_answer"]
                entry["best_answer"] = {
                    "id": ba.get("id"),
                    "root_cause": ba.get("root_cause"),
                    "fix_approach": ba.get("fix_approach"),
                    "commands": ba.get("patch_or_commands"),
                    "verification_count": ba.get("verification_count", 0),
                    "success_rate": ba.get("success_rate"),
                }
            formatted.append(entry)

        return ToolResult(success=True, output={
            "results": formatted,
            "count": len(formatted),
            "search_method": search_method,
        })

    async def _submit(self, input: dict) -> ToolResult:
        title = input.get("title", "")
        error_sig = input.get("error_signature", "")
        root_cause = input.get("root_cause", "")
        fix_approach = input.get("fix_approach", "")
        question_id = input.get("question_id")

        if not root_cause or len(root_cause) < 20:
            return ToolResult(success=False, error={"message": "root_cause required (min 20 chars)"})
        if not fix_approach or len(fix_approach) < 20:
            return ToolResult(success=False, error={"message": "fix_approach required (min 20 chars)"})

        # If no question_id, create a new question first
        if not question_id:
            if not title or not error_sig:
                return ToolResult(success=False, error={
                    "message": "title and error_signature required when not providing question_id"
                })
            q_resp = self.api._post("/questions", {
                "title": title[:300],
                "error_signature": error_sig,
                "error_category": input.get("error_category", "other"),
                "environment": _detect_environment(),
            })
            if not q_resp or q_resp.get("error"):
                return ToolResult(success=False, error={
                    "message": f"Failed to create question: {q_resp}"
                })
            question_id = q_resp.get("data", {}).get("id")
            if not question_id:
                return ToolResult(success=False, error={"message": "No question ID returned"})

        # Submit answer
        answer_body = {
            "root_cause": root_cause,
            "fix_approach": fix_approach,
        }
        if input.get("commands"):
            answer_body["patch_or_commands"] = input["commands"]

        a_resp = self.api._post(f"/questions/{question_id}/answers", answer_body)
        if not a_resp or a_resp.get("error"):
            return ToolResult(success=False, error={
                "message": f"Failed to submit answer: {a_resp}"
            })

        answer_id = a_resp.get("data", {}).get("id", "unknown")
        return ToolResult(success=True, output={
            "message": "Solution submitted to ErrorCache",
            "question_id": question_id,
            "answer_id": answer_id,
            "link": f"https://errorcache.com/questions/{question_id}",
        })

    async def _verify(self, input: dict) -> ToolResult:
        answer_id = input.get("answer_id", "")
        result = input.get("result", "")

        if not answer_id:
            return ToolResult(success=False, error={"message": "answer_id is required"})
        if result not in ("pass", "fail", "partial"):
            return ToolResult(success=False, error={"message": "result must be pass, fail, or partial"})

        body = {
            "result": result,
            "environment": _detect_environment(),
        }
        if input.get("evidence"):
            body["evidence"] = input["evidence"]

        resp = self.api._post(f"/answers/{answer_id}/verify", body)
        if not resp or resp.get("error"):
            return ToolResult(success=False, error={
                "message": f"Verification failed: {resp}"
            })

        return ToolResult(success=True, output={
            "message": f"Verification recorded: {result}",
            "answer_id": answer_id,
        })


async def mount(
    coordinator: ModuleCoordinator,
    config: dict[str, Any] | None = None,
) -> None:
    """Mount the ErrorCache tool."""
    config = config or {}
    api_url = config.get("api_url", "https://api.errorcache.com/api/v1")
    api_key = config.get("api_key", "")

    api = ErrorCacheAPI(api_url=api_url, api_key=api_key)
    tool = ErrorCacheTool(api=api)
    coordinator.mount_points["tools"][tool.name] = tool
