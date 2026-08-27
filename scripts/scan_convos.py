#!/usr/bin/env python3
"""Scan exported conversation markdown for secrets and private information.

Findings are graded so that the objective and the subjective stay separate:

    BLOCK   Credentials and keys. Never publish these — treat as compromised
            and rotate them. `publish_convos.py` refuses to allow such a file.
    REVIEW  Personal or identifying information. Only you can judge whether a
            given email, postcode or salary is fine to make public.
    NOTE    Low-signal leakage, e.g. your username in an absolute path.

Usage:
    python scan_convos.py                        # scan ./llm_convos
    python scan_convos.py path/to/convos         # scan a folder
    python scan_convos.py a.md b.md              # scan specific files
    python scan_convos.py --severity BLOCK       # only show the serious stuff
    python scan_convos.py --json                 # machine-readable output

Exit codes: 0 clean, 1 findings at or above --fail-on (default BLOCK), 2 error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

SEVERITIES = ("NOTE", "REVIEW", "BLOCK")

# Matches that are obviously placeholders rather than real values.
PLACEHOLDER_RE = re.compile(
    r"(?i)\b(example|sample|placeholder|dummy|redacted|your[-_]?|my[-_]?|xxx+|"
    r"foo|bar|test|fake|changeme|insert[-_]?|<[^>]*>|\.\.\.|abc123|first\.?name|last\.?name|user@|name@)"
)

# Domains that are conventionally fake, or are the tool's own noise.
BENIGN_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "test.com",
    "email.com", "domain.com", "noreply.github.com", "anthropic.com",
}

CARD_IIN_RE = re.compile(r"(?:4|5[1-5]|2[2-7]|3[47]|6(?:011|5))")

SALARY_WORD_RE = re.compile(
    r"(?i)\b(?:salary|salaries|remuneration|pay\s*(?:scale|band|rate|grade)|"
    r"day\s*rate|per\s*annum|p\.?a\.?\b|pro\s*rata|FTE|take[- ]home|wage)"
)

PRIVATE_IP_RE = re.compile(
    r"^(?:10\.|127\.|0\.|169\.254\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.|"
    r"22[4-9]\.|2[3-5]\d\.)"
)


class Rule:
    def __init__(self, rule_id, severity, description, pattern, validator=None,
                 flags=0, redact=True):
        self.id = rule_id
        self.severity = severity
        self.description = description
        self.regex = re.compile(pattern, flags)
        self.validator = validator
        self.redact = redact


def _luhn_ok(digits):
    """Luhn checksum — keeps random long numbers out of the card-number rule."""
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _token_around(line, start, end):
    """The whitespace-delimited token containing a match."""
    left = line.rfind(" ", 0, start) + 1
    right = line.find(" ", end)
    return line[left: right if right != -1 else len(line)]


def _valid_card(match, line):
    """Reject ISBNs, floats and version strings that happen to pass Luhn."""
    start, end = match.span()
    if start > 0 and (line[start - 1].isdigit() or line[start - 1] in "._-"):
        return False
    if end < len(line) and (line[end].isdigit() or line[end] in "_-"):
        return False
    if end + 1 < len(line) and line[end] == "." and line[end + 1].isdigit():
        return False
    digits = re.sub(r"\D", "", match.group(0))
    if len(digits) not in (15, 16):
        return False
    if digits.startswith(("978", "979")):        # ISBN-13
        return False
    if not CARD_IIN_RE.match(digits):            # must look like a real issuer
        return False
    return _luhn_ok(digits)


def _real_email(match, line):
    domain = match.group(0).rsplit("@", 1)[-1].lower()
    return domain not in BENIGN_EMAIL_DOMAINS and not PLACEHOLDER_RE.search(match.group(0))


def _public_ip(match, line):
    text = match.group(0)
    if any(not o.isdigit() or int(o) > 255 for o in text.split(".")):
        return False
    if PRIVATE_IP_RE.match(text):
        return False
    # A dotted quad inside a token with letters is a version, not an address
    return not re.search(r"[A-Za-z]", _token_around(line, *match.span()))


def _salary_context(match, line):
    """A currency figure only matters here if pay is being discussed nearby."""
    start, end = match.span()
    window = line[max(0, start - 150): end + 150]
    return bool(SALARY_WORD_RE.search(window))


# Words that, as a whole credential value, mean "fill this in yourself".
_PLACEHOLDER_EXACT = re.compile(
    r"(?i)^(?:pass(?:word|wd)?|passw0rd|secret|token|key|api[-_]?key|"
    r"user(?:name)?|admin|root|localhost|hostname|dbname|database|"
    r"1234\d*|x+|\.+|-+)$"
)
_PLACEHOLDER_PREFIX = ("your", "my", "test", "fake", "sample", "insert", "abc123")
_PLACEHOLDER_CONTAINS = ("example", "placeholder", "changeme", "redacted", "dummy")


def _looks_placeholder(value):
    value = value.strip().strip("\"'")
    if not value:
        return True
    if value[0] in "<{$[":                       # <your_key>, ${VAR}, {{token}}
        return True
    if _PLACEHOLDER_EXACT.match(value):
        return True
    norm = re.sub(r"[^a-z0-9]", "", value.lower())
    if any(norm.startswith(t) for t in _PLACEHOLDER_PREFIX):
        return True
    return any(t in norm for t in _PLACEHOLDER_CONTAINS)


def _not_placeholder(match, line):
    """Test the captured secret value where the rule captures one."""
    value = match.group(match.lastindex) if match.lastindex else match.group(0)
    return not _looks_placeholder(value)


RULES = [
    # ---- BLOCK: credentials, keys, tokens -------------------------------
    Rule("anthropic-key", "BLOCK", "Anthropic API key",
         r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    Rule("openai-key", "BLOCK", "OpenAI API key",
         r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    Rule("aws-access-key", "BLOCK", "AWS access key ID",
         r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    Rule("aws-secret", "BLOCK", "AWS secret access key",
         r"(?i)aws_secret_access_key\s*[=:]\s*[\"']?([A-Za-z0-9/+=]{40})"),
    Rule("github-token", "BLOCK", "GitHub token",
         r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    Rule("slack-token", "BLOCK", "Slack token",
         r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"),
    Rule("google-key", "BLOCK", "Google API key",
         r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    Rule("stripe-key", "BLOCK", "Stripe live key",
         r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{20,}\b"),
    Rule("private-key", "BLOCK", "Private key block",
         r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"),
    Rule("ssh-key", "BLOCK", "SSH private/public key material",
         r"\bssh-(?:rsa|dss|ed25519) AAAA[0-9A-Za-z+/]{50,}"),
    Rule("jwt", "BLOCK", "JSON Web Token",
         r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    Rule("bearer-token", "BLOCK", "Authorization header with token",
         r"(?i)authorization\s*[:=]\s*[\"']?(?:bearer|token)\s+([A-Za-z0-9._\-]{16,})",
         validator=_not_placeholder),
    Rule("db-url-creds", "BLOCK", "Database URL with embedded password",
         r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://"
         r"[^\s:/@]+:([^\s:/@]+)@[^\s/]+",
         validator=_not_placeholder),
    Rule("secret-assignment", "BLOCK", "Secret assigned in config or shell",
         r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|"
         r"client[_-]?secret|password|passwd|db[_-]?pass)\s*[=:]\s*"
         r"[\"']?([A-Za-z0-9/+=_\-]{12,})[\"']?",
         validator=_not_placeholder),

    # ---- REVIEW: personal / identifying ---------------------------------
    Rule("email", "REVIEW", "Email address",
         r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
         validator=_real_email, redact=False),
    Rule("uk-phone", "REVIEW", "UK phone number",
         r"\b(?:\+44\s?\d{4}|\(?0\d{4}\)?)\s?\d{3}\s?\d{3,4}\b"),
    Rule("intl-phone", "REVIEW", "International phone number",
         r"\+(?!44)\d{1,3}[\s\-]?\d{3}[\s\-]?\d{3,4}[\s\-]?\d{3,4}\b"),
    Rule("uk-postcode", "REVIEW", "UK postcode",
         r"\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b", redact=False),
    Rule("uk-ni-number", "REVIEW", "UK National Insurance number",
         r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b"),
    Rule("nhs-number", "REVIEW", "NHS number",
         r"(?i)\bNHS\s*(?:number|no\.?)\s*[:#]?\s*\d{3}\s?\d{3}\s?\d{4}\b"),
    Rule("card-number", "REVIEW", "Payment card number",
         r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{3,4}\b",
         validator=_valid_card),
    Rule("uk-sort-code", "REVIEW", "UK bank sort code",
         r"(?i)\bsort\s*code\s*[:#]?\s*\d{2}[\-\s]?\d{2}[\-\s]?\d{2}\b"),
    Rule("iban", "REVIEW", "IBAN",
         r"\b[A-Z]{2}\d{2}\s?(?:[A-Z0-9]{4}\s?){3,7}[A-Z0-9]{1,4}\b"),
    Rule("salary", "REVIEW", "Pay figure in a salary context",
         r"[£$€]\s?\d[\d,]{3,}(?:\.\d{2})?(?:\s?[km]\b)?",
         validator=_salary_context, redact=False),
    Rule("personal-doc", "REVIEW", "Reference to an identity document",
         r"(?i)\b(?:passport\s*(?:number|no\.?)|driving\s*licence\s*(?:number|no\.?)|"
         r"date\s*of\s*birth|d\.?o\.?b\.?)\b\s*[:#]?", redact=False),

    # ---- NOTE: low-signal leakage ---------------------------------------
    Rule("home-path", "NOTE", "Absolute home path (reveals username)",
         r"(?:/Users/|/home/|[A-Z]:\\Users\\)([A-Za-z0-9._\-]+)", redact=False),
    Rule("public-ip", "NOTE", "Public IP address",
         r"\b(?:\d{1,3}\.){3}\d{1,3}\b", validator=_public_ip, redact=False),
]


class Finding:
    def __init__(self, path, line_no, rule, text):
        self.path = path
        self.line_no = line_no
        self.rule = rule
        self.text = text

    @property
    def display(self):
        if not self.rule.redact:
            return self.text if len(self.text) <= 80 else self.text[:77] + "..."
        return redact(self.text)

    def to_dict(self):
        return {
            "file": str(self.path),
            "line": self.line_no,
            "rule": self.rule.id,
            "severity": self.rule.severity,
            "description": self.rule.description,
            "match": self.display,
        }


def redact(text):
    """Show just enough of a match to find it, never enough to use it."""
    text = text.strip()
    if len(text) <= 12:
        return text[:3] + "*" * max(0, len(text) - 3)
    return f"{text[:8]}...{text[-4:]} ({len(text)} chars)"


def scan_text(text, path="<text>"):
    """Return findings for a block of text, one per match, deduplicated."""
    findings = []
    seen = set()
    for line_no, line in enumerate(text.splitlines(), 1):
        for rule in RULES:
            for match in rule.regex.finditer(line):
                if rule.validator and not rule.validator(match, line):
                    continue
                value = match.group(0)
                key = (rule.id, value)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(path, line_no, rule, value))
    return findings


def scan_file(path):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"Could not read {path}: {exc}", file=sys.stderr)
        return []
    return scan_text(text, path)


def collect_files(targets):
    """Expand folders to their .md files; keep explicit files as given."""
    files = []
    for target in targets:
        p = Path(target).expanduser()
        if p.is_dir():
            files.extend(sorted(p.glob("*.md")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"Not found: {p}", file=sys.stderr)
    return files


def worst_severity(findings):
    """Return the highest severity present, or None."""
    present = {f.rule.severity for f in findings}
    for sev in reversed(SEVERITIES):
        if sev in present:
            return sev
    return None


def summarise(findings):
    """Count findings per severity."""
    counts = dict.fromkeys(SEVERITIES, 0)
    for f in findings:
        counts[f.rule.severity] += 1
    return counts


def _min_index(severity):
    return SEVERITIES.index(severity)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Scan exported conversations for secrets and private information.",
    )
    parser.add_argument(
        "targets", nargs="*", default=None,
        help="Files or folders to scan (default: ./llm_convos)",
    )
    parser.add_argument(
        "--severity", choices=SEVERITIES, default="NOTE",
        help="Minimum severity to report (default: NOTE)",
    )
    parser.add_argument(
        "--fail-on", choices=SEVERITIES + ("NEVER",), default="BLOCK",
        help="Exit non-zero if findings reach this severity (default: BLOCK)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument(
        "--quiet", action="store_true", help="Only print the per-file summary",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    targets = args.targets or ["llm_convos"]
    files = collect_files(targets)
    if not files:
        print("No files to scan.", file=sys.stderr)
        return 2

    threshold = _min_index(args.severity)
    all_findings = []
    per_file = {}

    for path in files:
        findings = [f for f in scan_file(path)
                    if _min_index(f.rule.severity) >= threshold]
        per_file[path] = findings
        all_findings.extend(findings)

    if args.json:
        print(json.dumps(
            {
                "files": {
                    str(p): [f.to_dict() for f in fs] for p, fs in per_file.items()
                },
                "summary": summarise(all_findings),
            },
            indent=2,
        ))
    else:
        for path, findings in per_file.items():
            if not findings:
                if not args.quiet:
                    print(f"\n\033[32m✓\033[0m {path.name} — clean")
                continue
            counts = summarise(findings)
            worst = worst_severity(findings)
            colour = {"BLOCK": "31", "REVIEW": "33", "NOTE": "36"}[worst]
            tally = "  ".join(f"{k}:{v}" for k, v in counts.items() if v)
            print(f"\n\033[{colour}m●\033[0m {path.name} — {tally}")
            if not args.quiet:
                for f in sorted(findings, key=lambda f: (-_min_index(f.rule.severity), f.line_no)):
                    print(f"    {f.rule.severity:<6} line {f.line_no:<5} "
                          f"{f.rule.description}: {f.display}")

        counts = summarise(all_findings)
        print(f"\n{len(files)} file(s) scanned — "
              + ", ".join(f"{v} {k}" for k, v in counts.items()))

    if args.fail_on != "NEVER":
        limit = _min_index(args.fail_on)
        if any(_min_index(f.rule.severity) >= limit for f in all_findings):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
