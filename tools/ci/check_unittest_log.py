"""Check a verbose unittest log against the documented expected failures.

Usage: python tools/ci/check_unittest_log.py LOG --expected-failures N --expected-modules PREFIX

unittest already exits non-zero on a failure, an error or an unexpected success. This check adds
what it does not: the run must end in OK, report exactly N expected failures, and every expected
failure must belong to a module starting with PREFIX (the documented Lot 4 residuals). A new
expectedFailure elsewhere, or a residual that silently disappears, fails the check until the count
is updated by a reviewed change. Exit 0 when the log matches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter

SUMMARY = re.compile(r"^(OK|FAILED)(?: \((.*)\))?$")
RAN = re.compile(r"^Ran (\d+) tests? in ")
EXPECTED_LINE = re.compile(r"\((test_[A-Za-z0-9_]+)\.[^)]*\) \.\.\. expected failure$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log")
    parser.add_argument("--expected-failures", type=int, required=True)
    parser.add_argument("--expected-modules", required=True, help="module prefix every expected failure must have")
    args = parser.parse_args()
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        lines = [line.rstrip("\n") for line in handle]

    ran = [int(m.group(1)) for line in lines if (m := RAN.match(line))]
    summaries = [m for line in lines if (m := SUMMARY.match(line))]
    if not ran or not summaries:
        print("no unittest summary found in the log: the run did not finish")
        return 1
    status, detail = summaries[-1].group(1), summaries[-1].group(2) or ""
    counts = dict(item.split("=") for item in detail.split(", ") if "=" in item)
    by_module = Counter(m.group(1) for line in lines if (m := EXPECTED_LINE.search(line)))
    reported = int(counts.get("expected failures", 0))

    problems = []
    if status != "OK":
        problems.append(f"run ended {status} ({detail})")
    if reported != args.expected_failures:
        problems.append(f"{reported} expected failures reported, {args.expected_failures} documented")
    if sum(by_module.values()) != reported:
        problems.append(f"{sum(by_module.values())} expected-failure lines found, {reported} in the summary")
    outside = {module: n for module, n in by_module.items() if not module.startswith(args.expected_modules)}
    if outside:
        problems.append(f"expected failures outside {args.expected_modules}*: {outside}")

    print(f"tests run: {ran[-1]}; status: {status}; summary: {detail or 'none'}")
    print("expected failures by module: " + (", ".join(f"{m}={n}" for m, n in sorted(by_module.items())) or "none"))
    for problem in problems:
        print(f"CHECK FAILED: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
