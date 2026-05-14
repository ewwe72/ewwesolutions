"""Claude Code PreToolUse hook — block git commits/pushes that would leak secrets.

Wired in .claude/settings.json on the `Bash` tool. Reads the tool input as JSON
on stdin, inspects the command, and if it looks like a git mutation that could
push tracked content (git add/commit/push), runs scripts/audit_secrets.py against
the staged set. Non-zero audit exit blocks the tool call.

Hook contract (Claude Code):
  stdin:  {"tool_name": "Bash", "tool_input": {"command": "...", ...}, ...}
  stdout: ignored (printed to user)
  exit 0: allow the tool call
  exit 2: block the tool call, message printed to the model

We deliberately fail-open on unrelated commands so this hook does NOT interfere
with non-git Bash use. The git pre-commit hook (.githooks/pre-commit) is the
authoritative second layer.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


# Trigger when the bash command contains any of these (whole-word-ish).
_GIT_MUTATIONS = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?"
    r"(?:add|commit|push|stash\s+push)\b",
    re.IGNORECASE,
)

# Find a working dir hint to scope the audit:
#   "cd <path> && git ..."   -> use <path>
#   "git -C <path> ..."      -> use <path>
# Otherwise: cwd of the hook.
_CD_HINT = re.compile(r"cd\s+\"([^\"]+)\"|cd\s+'([^']+)'|cd\s+(\S+)")
_GIT_C_HINT = re.compile(r"git\s+-C\s+\"([^\"]+)\"|git\s+-C\s+'([^']+)'|git\s+-C\s+(\S+)")


def _extract_workdir(cmd: str) -> str | None:
    for pat in (_GIT_C_HINT, _CD_HINT):
        m = pat.search(cmd)
        if m:
            for g in m.groups():
                if g:
                    return g
    return None


def _find_audit_script(start: Path) -> Path | None:
    """Walk upward looking for scripts/audit_secrets.py."""
    cur = start.resolve()
    for _ in range(6):
        cand = cur / "scripts" / "audit_secrets.py"
        if cand.is_file():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0  # malformed — don't block

    tool = payload.get("tool_name") or payload.get("tool") or ""
    tool_input = payload.get("tool_input") or payload.get("input") or {}
    if tool != "Bash":
        return 0

    cmd = tool_input.get("command") or ""
    if not _GIT_MUTATIONS.search(cmd):
        return 0  # not a git mutation we care about

    workdir = _extract_workdir(cmd) or os.getcwd()
    workdir_path = Path(workdir)
    if not workdir_path.is_absolute():
        workdir_path = Path(os.getcwd()) / workdir_path

    audit = _find_audit_script(workdir_path)
    if audit is None:
        # Audit script missing — fail closed for git mutations, since the
        # whole point of this hook is that the operator wants paranoia.
        print(
            "claude_hook_block_secrets: cannot locate scripts/audit_secrets.py — "
            "refusing the git mutation. Install the audit script or remove this hook.",
            file=sys.stderr,
        )
        return 2

    # Run the audit. --staged-only mirrors the pre-commit hook scope.
    result = subprocess.run(
        [sys.executable, str(audit), "--staged-only", "--quiet"],
        cwd=str(workdir_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode == 0:
        return 0

    print(
        "claude_hook_block_secrets: BLOCKED — secret/PII/forbidden-path detected "
        "in staged files.\n"
        f"  workdir: {workdir_path}\n"
        f"  audit:   {audit}\n"
        "  ---\n"
        f"{result.stdout}{result.stderr}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
