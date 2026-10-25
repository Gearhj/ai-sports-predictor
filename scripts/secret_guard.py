"""Refuse to commit or push credentials. Dependency-free; prints no secret.

Scans ADDED lines only (plus commit messages), so unchanged history does not
re-trigger. Used by the local git hooks installed by scripts/install-git-hooks.sh:

    python scripts/secret_guard.py --staged          # pre-commit: the index
    python scripts/secret_guard.py --pre-push        # pre-push: refs on stdin
    python scripts/secret_guard.py --range A..B      # any commit range

Findings:
  * a database URL with an inline password (``user:password@``) whose password
    is not a documentation token such as ``<password>`` or ``${VAR}``;
  * an assignment to a credential-named setting (``*_PASSWORD``, ``*_SECRET``,
    ``*_TOKEN``, ``*API_KEY``, ``secret_key``...) with a literal value;
  * well-known token formats (GitHub, OpenAI/Anthropic, AWS, Slack, Google,
    PEM private keys, JWTs);
  * any value from the local ``.env`` (if present), anywhere in an added line.

"change-me" style defaults are findings, not placeholders: one was in live use
as a real password. A genuine test fixture may be accepted line by line with a
trailing ``secret-guard: test-fixture`` comment -- never by path or pattern.
Exit status 1 blocks the commit or push; the report names file, line and rule
but never the value.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ALLOW_MARK = "secret-guard: test-fixture"
URL = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9]+)?://([A-Za-z0-9_.-]+):([^@\s'\"`]+)@")
ASSIGN = re.compile(
    r"(?i)(?:^|[\s,{(])[\"']?([A-Za-z0-9_]*(?:password|passwd|secret|token|api_?key|private_key|access_key))[\"']?"
    r"[ \t]*(?::[ \t]*[A-Za-z_][A-Za-z0-9_\[\], |.]*)?[ \t]*[:=][ \t]*[\"']([^\"'\s#,}]{6,})[\"']")
BARE_ENV = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY))"
    r"[ \t]*=[ \t]*([^\s#\"']{6,})[ \t]*$")
TOKENS = {
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "openai-or-anthropic-key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{32,}\b"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}
INERT = re.compile(r"(?i)^(?:<[a-z0-9_ -]+>|\$\{[^}]*\}?.*|\*+|x+|\.+|redacted(?:-credential)?|none|null|"
                   r"true|false|your[-_][a-z_-]+|not[-_]?set|empty)$")
CODE = re.compile(r"[()\[\]{}]|^(?:self|settings|os|env|config|cls|request|args|kwargs)\b|\.(?:get|environ)\b")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, check=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def local_env_values() -> list[str]:
    env = Path(git("rev-parse", "--show-toplevel").strip()) / ".env"
    if not env.is_file():
        return []
    vals = []
    for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^[ \t]*([A-Za-z0-9_]+)[ \t]*=[ \t]*(.*?)[ \t]*$", line)
        if m and re.search(r"(KEY|SECRET|PASSWORD|TOKEN|DATABASE_URL)$", m.group(1)) and len(m.group(2)) >= 8:
            v = m.group(2).strip("\"'")
            vals.append(v)
            vals += [u.group(2) for u in URL.finditer(v)]
    return [v for v in vals if len(v) >= 8]


def scan_line(path: str, text: str, env_vals: list[str]) -> list[str]:
    if ALLOW_MARK in text:
        return []
    found = []
    for v in env_vals:
        if v in text:
            found.append("value from the local .env")
    for m in URL.finditer(text):
        if not INERT.match(m.group(2)):
            found.append("database URL with an inline password")
    for rx in (ASSIGN, BARE_ENV):
        for m in rx.finditer(text):
            value = m.group(2)
            if not INERT.match(value) and not CODE.search(value):
                found.append(f"literal value assigned to {m.group(1)}")
    for name, rx in TOKENS.items():
        if rx.search(text):
            found.append(name)
    return found


def added_lines(diff: str):
    path, lineno = "?", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            yield path, lineno, raw[1:]
            lineno += 1
        elif not raw.startswith("-"):
            lineno += 1


def report(findings: list[tuple[str, int, str]]) -> int:
    if not findings:
        print("secret-guard: clean")
        return 0
    print("secret-guard: BLOCKED -- possible credentials in added content:")
    for path, line, rule in findings:
        print(f"  {path}:{line}  {rule}")
    print("Remove the value (use .env, which is never committed). A genuine test fixture may be "
          f"accepted with a trailing '{ALLOW_MARK}' comment on that line.")
    return 1


def scan_diff(diff: str, env_vals: list[str], findings: list) -> None:
    for path, line, text in added_lines(diff):
        for rule in scan_line(path, text, env_vals):
            findings.append((path, line, rule))


def main() -> int:
    env_vals = local_env_values()
    findings: list[tuple[str, int, str]] = []
    if "--staged" in sys.argv:
        scan_diff(git("diff", "--cached", "--unified=0", "--no-color", "--no-ext-diff"), env_vals, findings)
        return report(findings)

    ranges: list[str] = []
    if "--range" in sys.argv:
        ranges.append(sys.argv[sys.argv.index("--range") + 1])
    if "--pre-push" in sys.argv:
        for line in sys.stdin.read().splitlines():
            parts = line.split()
            if len(parts) != 4 or set(parts[1]) == {"0"}:
                continue  # deletion
            local_sha, remote_sha = parts[1], parts[3]
            ranges.append(local_sha + " --not --remotes" if set(remote_sha) == {"0"} else f"{remote_sha}..{local_sha}")
    for rng in ranges:
        commits = git("rev-list", *rng.split()).split()
        for c in commits:
            scan_diff(git("show", "--format=", "--unified=0", "--no-color", "--no-ext-diff", c), env_vals, findings)
            msg = git("log", "-1", "--format=%B", c)
            for rule in {r for ln in msg.splitlines() for r in scan_line("(commit message)", ln, env_vals)}:
                findings.append((f"commit {c[:12]} message", 0, rule))
    return report(findings)


if __name__ == "__main__":
    sys.exit(main())
