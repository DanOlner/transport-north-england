#!/usr/bin/env python3
"""Strip identifying detail out of exported conversation markdown.

By default this only rewrites filesystem paths, which is the leak that appears
in essentially every export: the `*Source:*` header and every tool call carry
your home directory, and the Claude project folders carry it again in hyphenated
form (`-Users-yourname-Code-project`). Both become `~` and `-Users-user-`.

    /Users/dan/Code/thing/main.py                 ->  ~/Code/thing/main.py
    ~/.claude/projects/-Users-dan-Code-thing/x     ->  ~/.claude/projects/-Users-user-Code-thing/x

Emails and credentials are left alone unless you ask for them, because those
are judgement calls — see scan_convos.py for what is in a file.

    A redacted credential is still a leaked credential. Rotate it.

Usage:
    python redact_convos.py                      # rewrite ./llm_convos in place
    python redact_convos.py path/to/convos       # a different folder
    python redact_convos.py --dry-run            # show what would change
    python redact_convos.py --emails             # also mask email addresses
    python redact_convos.py --personal           # also mask phones, postcodes, cards
    python redact_convos.py --secrets            # also mask BLOCK-level matches
    python redact_convos.py --output-dir DIR     # write copies instead

Rewriting is idempotent: running it twice changes nothing the second time.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scan_convos import RULES

DEFAULT_DIR = "llm_convos"

# Home directories that are conventions rather than people
SHARED_ACCOUNTS = ("Shared", "Public", "Default", "All Users", "user")

# System accounts carry no identity and are meaningful in permission output
SYSTEM_ACCOUNTS = {"root", "daemon", "nobody", "wheel", "staff", "admin",
                   "user", "www-data", "systemd-network"}

# The owner column of `ls -l`, which appears throughout captured tool output:
#   -rw-r--r--@ 1 name  staff   23722 Jul 16 13:04 report.md
LS_OWNER_RE = re.compile(
    r"([-dlbcps][-rwxsStT]{9}[@+.]?\s+\d+\s+)([A-Za-z_][A-Za-z0-9_.-]*)(\s)"
)


def _mask_ls_owner(match):
    owner = match.group(2)
    if owner.lower() in SYSTEM_ACCOUNTS or owner.startswith("_"):
        return match.group(0)
    return f"{match.group(1)}user{match.group(3)}"


def path_rules(home=None):
    """Substitutions that remove a username from a filesystem path.

    The real home directory is handled first and most precisely; the generic
    patterns then catch any other account that appears.
    """
    home = str(home or Path.home())
    skip = "|".join(re.escape(a) for a in SHARED_ACCOUNTS)
    return [
        # The actual home directory, longest and most specific match first
        (re.compile(re.escape(home) + r"(?![A-Za-z0-9._-])"), "~"),
        # Any other Unix-style home
        (re.compile(rf"(?<![A-Za-z0-9._-])/Users/(?!(?:{skip})(?![A-Za-z0-9._-]))"
                    r"[A-Za-z0-9._-]+"), "~"),
        (re.compile(rf"(?<![A-Za-z0-9._-])/home/(?!(?:{skip})(?![A-Za-z0-9._-]))"
                    r"[A-Za-z0-9._-]+"), "~"),
        # Windows
        (re.compile(rf"(?i)[A-Z]:\\Users\\(?!(?:{skip})(?![A-Za-z0-9._-]))"
                    r"[A-Za-z0-9._-]+"), "~"),
        # Claude's own hyphenated project-folder encoding of the same path.
        # No trailing "-" required: these also appear as "-Users-name/..." and
        # truncated at the end of a captured tool result.
        (re.compile(r"(-Users-)[A-Za-z0-9._]+"), r"\1user"),
        (re.compile(r"(-home-)[A-Za-z0-9._]+"), r"\1user"),
    ]


def rule_by_id(rule_id):
    for rule in RULES:
        if rule.id == rule_id:
            return rule
    return None


def redact_text(text, home=None, emails=False, secrets=False, personal=False):
    """Return (redacted_text, {label: count})."""
    counts = {}

    def bump(label, n):
        if n:
            counts[label] = counts.get(label, 0) + n

    for pattern, replacement in path_rules(home):
        text, n = pattern.subn(replacement, text)
        bump("home path", n)

    text, n = LS_OWNER_RE.subn(_mask_ls_owner, text)
    bump("file owner", n)

    def apply(rule):
        """Replace a rule's matches, honouring its own validator."""
        def mask(match):
            if rule.validator and not rule.validator(match, match.string):
                return match.group(0)
            return f"<redacted: {rule.description}>"
        return rule.regex.subn(mask, text)

    # Credentials first: a DB URL carries a password that also looks like an
    # email address, and masking the email first would hide the real finding.
    if secrets:
        for rule in RULES:
            if rule.severity == "BLOCK":
                text, n = apply(rule)
                bump(rule.description, n)

    if personal:
        for rule in RULES:
            if rule.severity == "REVIEW" and rule.id != "email":
                text, n = apply(rule)
                bump(rule.description, n)

    if emails:
        text, n = apply(rule_by_id("email"))
        bump("Email address", n)

    return text, counts


def redact_file(path, out_path=None, dry_run=False, **kwargs):
    """Redact one file. Returns the counts of what changed."""
    original = Path(path).read_text(encoding="utf-8", errors="replace")
    redacted, counts = redact_text(original, **kwargs)

    if not counts or redacted == original:
        if out_path and not dry_run:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_text(original, encoding="utf-8")
        return {}

    if not dry_run:
        target = Path(out_path or path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(redacted, encoding="utf-8")
    return counts


def collect_files(targets):
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Remove usernames and other identifying detail from exports.",
    )
    parser.add_argument("targets", nargs="*",
                        help=f"Files or folders (default: ./{DEFAULT_DIR})")
    parser.add_argument("--output-dir",
                        help="Write redacted copies here instead of rewriting")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change, write nothing")
    parser.add_argument("--emails", action="store_true",
                        help="Also mask email addresses")
    parser.add_argument("--personal", action="store_true",
                        help="Also mask phone numbers, postcodes, cards, pay figures")
    parser.add_argument("--secrets", action="store_true",
                        help="Also mask credentials (rotate them anyway)")
    parser.add_argument("--home", help="Treat this path as the home directory")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    files = collect_files(args.targets or [DEFAULT_DIR])
    if not files:
        print("Nothing to redact.", file=sys.stderr)
        return 2

    out_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    total = {}
    changed = 0

    for path in files:
        out_path = (out_dir / path.name) if out_dir else None
        counts = redact_file(
            path, out_path, dry_run=args.dry_run, home=args.home,
            emails=args.emails, secrets=args.secrets, personal=args.personal,
        )
        if counts:
            changed += 1
            detail = ", ".join(f"{k} x{v}" for k, v in sorted(counts.items()))
            print(f"  {path.name}: {detail}")
            for k, v in counts.items():
                total[k] = total.get(k, 0) + v

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{changed} of {len(files)} file(s) {verb}"
          + (f" — {', '.join(f'{k} x{v}' for k, v in sorted(total.items()))}"
             if total else ""))
    if args.secrets and total:
        print("\nRedaction does not un-leak a credential. Rotate anything exposed.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
