"""Secret scan for ASTRA: every tracked file, plus every line added in the history of HEAD.

Usage: python tools/ci/secret_scan.py [--repo PATH] [--no-history]

Exit 0 when nothing suspicious is found, 1 when there is at least one hit, 2 on a usage or git
error or a shallow clone (the history would be incomplete). Every hit is printed with its location;
a matched value is shown truncated to 4 characters, never in full. Standard library only, so CI and
local runs need no extra tool.
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

PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY( BLOCK)?-----")
TOKEN_FORMATS = {
    "aws_access_key": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "slack_token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "openai_anthropic_key": re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    "stripe_live_key": re.compile(r"\b(sk|rk)_live_[A-Za-z0-9]{16,}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*"),
}
# A credential-looking name assigned a long quoted literal (code, JSON, YAML, TOML).
ASSIGNMENT = re.compile(
    r"(api[_-]?key|api[_-]?secret|secret|token|password|passwd|client_secret|access_token)\s*[=:]\s*['\"]([^'\"\s]{16,})['\"]",
    re.I,
)
# The same name with an unquoted value alone on a configuration line (YAML, INI, .env): `api_key: abc...`.
# The value must be a bare token up to the end of the line, so code such as `token = make(x)` is not a hit.
CONFIG_ASSIGNMENT = re.compile(
    r"^\s*[\w.-]*(api[_-]?key|api[_-]?secret|secret|token|password|passwd|client_secret|access_token)\s*[=:]\s*"
    r"([A-Za-z0-9+/=_~.-]{16,})\s*$",
    re.I,
)
# Credentials embedded in a URL: scheme, user, colon, a password of 6+ characters, an at sign.
URL_CREDENTIALS = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:([^/\s@'\"]{6,})@", re.I)
# Words that only document the shape of such a URL (exact match, case-insensitive).
URL_PLACEHOLDERS = {"password", "passwd", "secret"}
# A value that is itself an environment-variable NAME (e.g. "ASTRA_SECRET_BINANCE_API_KEY") names where a
# secret is read from; it is not secret material. An underscore is required, so an upper-case key is still a hit.
ENV_VAR_NAME = re.compile(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+)+")
# Test fixtures use clearly fake values.
FAKE_MARKERS = ("test-", "fake", "dummy", "example", "wrong-", "placeholder", "AAAAAAAA", "BBBBBBBB", "CCCCCCCC",
                "not-a-real")


def git(repo: str, *args: str) -> str:
    # core.quotePath=false keeps non-ASCII paths readable instead of quoted and escaped.
    result = subprocess.run(["git", "-C", repo, "-c", "core.quotePath=false", *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return result.stdout


def shown(value: str) -> str:
    return value[:4] + "..." if len(value) > 4 else value


def is_fake(value: str) -> bool:
    return any(marker.lower() in value.lower() for marker in FAKE_MARKERS)


def line_hits(text: str) -> list[str]:
    hits = []
    if PRIVATE_KEY.search(text):
        hits.append("private key block")
    for name, pattern in TOKEN_FORMATS.items():
        for match in pattern.finditer(text):
            if not is_fake(match.group(0)):
                hits.append(f"{name} {shown(match.group(0))}")
    for match in [*ASSIGNMENT.finditer(text), *CONFIG_ASSIGNMENT.finditer(text)]:
        value = match.group(2)
        if ENV_VAR_NAME.fullmatch(value) or is_fake(value):
            continue
        hits.append(f"credential-like literal for {match.group(1)}: {shown(value)}")
    for match in URL_CREDENTIALS.finditer(text):
        password = match.group(1)
        if password.lower() not in URL_PLACEHOLDERS and not is_fake(password):
            hits.append(f"credentials in URL: {shown(password)}")
    return hits


def scan_tree(repo: str) -> list[str]:
    hits = []
    for path in git(repo, "ls-files", "-z").split("\0"):
        if not path:
            continue
        if FORBIDDEN_PATH.search(path) and path not in ALLOWED_PATHS:
            hits.append(f"tracked sensitive file: {path}")
            continue
        try:
            with open(f"{repo}/{path}", encoding="utf-8", errors="replace") as handle:
                for number, text in enumerate(handle, 1):
                    hits.extend(f"{path}:{number}: {hit}" for hit in line_hits(text))
        except FileNotFoundError:
            hits.append(f"tracked file missing from the checkout: {path}")
        except IsADirectoryError:
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
    # --diff-merges=first-parent also shows lines introduced while resolving a merge.
    log = git(repo, "log", "-p", "--no-color", "--no-ext-diff", "--diff-merges=first-parent", "--format=commit %H", "HEAD")
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
        if git(args.repo, "rev-parse", "--is-shallow-repository").strip() == "true":
            print("shallow clone: the history scan would be incomplete; fetch the full history (fetch-depth: 0)")
            return 2
        commits = git(args.repo, "rev-list", "--count", "HEAD").strip()
        hits += scan_history(args.repo)
    print(f"secret scan of {head}: tracked tree" + ("" if args.no_history else f" and {commits} commits of history"))
    print("\n".join(hits) if hits else "no secret found")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
