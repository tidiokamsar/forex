"""Secret scan for ASTRA: every tracked file, plus every line added in the history of HEAD.

Usage: python tools/ci/secret_scan.py [--repo PATH] [--no-history]

Exit 0 when nothing suspicious is found, 1 when there is at least one hit, 2 on a usage or git
error. Every hit is printed with its location; a matched value is shown truncated, never in full.
Standard library only, so CI and local runs need no extra tool.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# File names that must never be tracked (local state, keys, databases, logs, credential files).
FORBIDDEN_PATH = re.compile(
    r"(^|/)(\.env(\..*)?|\.netrc|id_rsa.*|id_ed25519.*|credentials.*\.json|.*\.(pem|key|p12|pfx|sqlite|sqlite3|db|jsonl|log))$",
    re.I,
)
ALLOWED_PATHS = {".env.example"}

PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
TOKEN_FORMATS = {
    "aws_access_key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}
# A credential-looking name assigned a long literal.
ASSIGNMENT = re.compile(
    r"(api[_-]?key|api[_-]?secret|secret|token|password|passwd|client_secret|access_token)\s*[=:]\s*['\"]([^'\"\s]{16,})['\"]",
    re.I,
)
# Credentials embedded in a URL: scheme://user:password@host with a password of 6+ characters.
URL_CREDENTIALS = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:([^/\s@'\"]{6,})@", re.I)
# A value that is itself an environment-variable NAME (e.g. "ASTRA_SECRET_BINANCE_API_KEY") names where a
# secret is read from; it is not secret material.
ENV_VAR_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
# Test fixtures use clearly fake values.
FAKE_MARKERS = ("test-", "fake", "dummy", "example", "wrong-", "placeholder", "AAAAAAAA", "BBBBBBBB", "CCCCCCCC",
                "not-a-real")


def git(repo: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return result.stdout


def shown(value: str) -> str:
    return value[:4] + "..." if len(value) > 4 else value


def line_hits(text: str) -> list[str]:
    hits = []
    if PRIVATE_KEY.search(text):
        hits.append("private key block")
    for name, pattern in TOKEN_FORMATS.items():
        for match in pattern.finditer(text):
            hits.append(f"{name} {shown(match.group(0))}")
    for match in ASSIGNMENT.finditer(text):
        value = match.group(2)
        if ENV_VAR_NAME.fullmatch(value) or any(marker.lower() in value.lower() for marker in FAKE_MARKERS):
            continue
        hits.append(f"credential-like literal for {match.group(1)}: {shown(value)}")
    for match in URL_CREDENTIALS.finditer(text):
        if not any(marker.lower() in match.group(1).lower() for marker in FAKE_MARKERS):
            hits.append(f"credentials in URL: {shown(match.group(1))}")
    return hits


def scan_tree(repo: str) -> list[str]:
    hits = []
    for path in git(repo, "ls-files").splitlines():
        if FORBIDDEN_PATH.search(path) and path not in ALLOWED_PATHS:
            hits.append(f"tracked sensitive file: {path}")
            continue
        try:
            with open(f"{repo}/{path}", encoding="utf-8", errors="replace") as handle:
                for number, text in enumerate(handle, 1):
                    hits.extend(f"{path}:{number}: {hit}" for hit in line_hits(text))
        except (IsADirectoryError, FileNotFoundError):
            continue
    with open(f"{repo}/.env.example", encoding="utf-8") as handle:
        for number, text in enumerate(handle, 1):
            name, _, value = text.partition("=")
            if not text.lstrip().startswith("#") and "SECRET" in name.upper() and value.strip():
                hits.append(f".env.example:{number}: secret variable with a value")
    return hits


def scan_history(repo: str) -> list[str]:
    hits = []
    commit = path = ""
    log = git(repo, "log", "-p", "--no-color", "--no-ext-diff", "--format=commit %H", "HEAD")
    for text in log.splitlines():
        if text.startswith("commit "):
            commit = text[7:19]
        elif text.startswith("+++ b/"):
            path = text[6:]
            if FORBIDDEN_PATH.search(path) and path not in ALLOWED_PATHS:
                hits.append(f"{commit} added sensitive file: {path}")
        elif text.startswith("+") and not text.startswith("+++"):
            hits.extend(f"{commit} {path}: {hit}" for hit in line_hits(text[1:]))
    return sorted(set(hits))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=".")
    parser.add_argument("--no-history", action="store_true", help="scan only the tracked tree")
    args = parser.parse_args()
    head = git(args.repo, "rev-parse", "HEAD").strip()
    hits = scan_tree(args.repo)
    commits = "0"
    if not args.no_history:
        commits = git(args.repo, "rev-list", "--count", "HEAD").strip()
        hits += scan_history(args.repo)
    print(f"secret scan of {head}: tracked tree" + ("" if args.no_history else f" and {commits} commits of history"))
    print("\n".join(hits) if hits else "no secret found")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
