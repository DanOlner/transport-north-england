#!/usr/bin/env python3
"""Find and export all Claude Code conversations for a project to markdown.

Usage:
    python export_all_convos.py                      # auto-detect project from cwd
    python export_all_convos.py /path/to/project     # specify project root
    python export_all_convos.py --convo-dir ~/.claude/projects/my-project
    python export_all_convos.py --output-dir ~/notes/convos
    python export_all_convos.py --list               # show projects with transcripts

Outputs markdown files into <project_root>/llm_convos/ (created if missing).

Honours CLAUDE_CONFIG_DIR if you have relocated ~/.claude.
"""

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime as _dt
from datetime import datetime
from pathlib import Path

# Import the converter from the sibling script
sys.path.insert(0, str(Path(__file__).resolve().parent))
from jsonl_to_markdown import convert, first_human_text, iter_messages
from redact_convos import redact_file
from publish_convos import ensure_gitignore, sync

DEFAULT_OUTPUT_DIRNAME = "llm_convos"

# Claude Code deletes transcripts older than cleanupPeriodDays at startup, with
# unlink() rather than a trash folder. The documented default is 30 days, so an
# archive tool that says nothing about it is watching its own source disappear.
DEFAULT_RETENTION_DAYS = 30
RETENTION_KEY = "cleanupPeriodDays"
RETENTION_ADVISED = 365


def get_claude_settings_path():
    """Path to the user-level settings.json, honouring CLAUDE_CONFIG_DIR."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return base / "settings.json"


def read_retention():
    """Return the configured retention in days, or None if unset."""
    path = get_claude_settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get(RETENTION_KEY) if isinstance(data, dict) else None
    return value if isinstance(value, int) else None


def warn_retention(fresh=False):
    """Report transcript retention, since exports are only as durable as it is.

    Always reported when setting a folder up for the first time -- that is when
    you would want to know. After that it stays quiet unless the period is short
    enough to be losing conversations.
    """
    days = read_retention()
    healthy = days is not None and days >= RETENTION_ADVISED

    if healthy and not fresh:
        return

    if healthy:
        years = days / 365
        print(f"\n  Transcript retention: {RETENTION_KEY} = {days} days "
              f"(~{years:.0f} years), from {get_claude_settings_path()}.",
              file=sys.stderr)
        return

    shown = days if days is not None else DEFAULT_RETENTION_DAYS
    how = "set to" if days is not None else "unset, so the default applies:"
    print(f"\n  \033[33mNote:\033[0m {RETENTION_KEY} is {how} {shown} days.",
          file=sys.stderr)
    print("  Claude Code deletes older transcripts at startup. To keep them:",
          file=sys.stderr)
    print("      python3 scripts/export_all_convos.py --set-retention 3650",
          file=sys.stderr)


def set_retention(days):
    """Write cleanupPeriodDays into settings.json, touching nothing else."""
    if days < 1:
        print("Refusing to set a retention below 1 day. Anthropic does not "
              "document 0 as 'never delete', and it may mean 'keep nothing'.",
              file=sys.stderr)
        return 1

    path = get_claude_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file():
        original = path.read_text(encoding="utf-8")
        try:
            json.loads(original)
        except ValueError as exc:
            print(f"{path} is not valid JSON ({exc}). Not touching it.",
                  file=sys.stderr)
            return 1
        backup = path.with_name(
            f"{path.name}.bak-{_dt.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)

        # Surgical edit: replace the value in place, or insert one line, so the
        # rest of a hand-maintained file survives byte for byte.
        pattern = re.compile(rf'("{RETENTION_KEY}"\s*:\s*)\d+')
        if pattern.search(original):
            updated = pattern.sub(rf'\g<1>{days}', original, count=1)
        else:
            updated = re.sub(r"^\{", '{\n  "%s": %d,' % (RETENTION_KEY, days),
                             original, count=1)
    else:
        original, backup = None, None
        updated = json.dumps({RETENTION_KEY: days}, indent=2) + "\n"

    try:
        json.loads(updated)
    except ValueError as exc:
        print(f"Edit would have produced invalid JSON ({exc}). Nothing written.",
              file=sys.stderr)
        return 1

    path.write_text(updated, encoding="utf-8")
    print(f"{RETENTION_KEY} = {days} in {path}")
    if backup:
        print(f"  backup: {backup}")
    print("  Takes effect next time Claude Code starts — which is also the next "
          "moment cleanup would run.")
    return 0


def get_claude_projects_dir():
    """Return the Claude Code projects directory.

    Same location on macOS, Linux and Windows (~/.claude/projects), but
    CLAUDE_CONFIG_DIR overrides the ~/.claude part if it is set.
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir).expanduser() if config_dir else Path.home() / ".claude"
    return base / "projects"


def project_path_to_folder_name(project_path):
    """Convert an absolute project path to the Claude folder name convention.

    e.g. /Users/danolner/Code/My_Project -> -Users-danolner-Code-My-Project

    Claude Code replaces path separators, underscores and dots with hyphens,
    and on Windows the drive colon goes too.
    """
    resolved = Path(project_path).expanduser().resolve()
    return re.sub(r"[/\\_.:]", "-", str(resolved))


def _convo_dir_cwd(convo_dir):
    """Read the project cwd recorded inside a convo dir's transcripts."""
    for jsonl_path in sorted(convo_dir.glob("*.jsonl")):
        try:
            for msg in iter_messages(jsonl_path):
                cwd = msg.get("cwd")
                if cwd:
                    return Path(cwd)
        except OSError:
            continue
    return None


def iter_convo_dirs(projects_dir):
    """Yield (convo_dir, recorded_project_path_or_None) for dirs holding transcripts."""
    if not projects_dir.is_dir():
        return
    for d in sorted(projects_dir.iterdir()):
        if d.is_dir() and any(d.glob("*.jsonl")):
            yield d, _convo_dir_cwd(d)


def _loosen(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _match_exact(projects_dir, root, convo_dirs):
    """Find the convo dir for exactly this path, or None."""
    convo_dir = projects_dir / project_path_to_folder_name(root)
    if convo_dir.is_dir() and any(convo_dir.glob("*.jsonl")):
        return convo_dir

    # Authoritative: match on the cwd recorded inside the transcripts, which
    # does not depend on guessing how Claude Code encodes paths into names.
    for d, recorded in convo_dirs:
        if recorded is not None and recorded.expanduser().resolve() == root:
            return d

    # Last resort: loose name match, ignoring separator style and case
    target = _loosen(root)
    for d, _ in convo_dirs:
        if _loosen(d.name) == target:
            return d

    return None


def find_convo_dir(project_root):
    """Find the conversation directory for a project root.

    Returns (convo_dir, matched_root) or (None, None). If the given path is a
    subdirectory of the project Claude was run in, walks up to find the match,
    so the tool works from anywhere inside a project tree.
    """
    projects_dir = get_claude_projects_dir()
    root = Path(project_root).expanduser().resolve()
    convo_dirs = list(iter_convo_dirs(projects_dir))

    for candidate in [root, *root.parents]:
        found = _match_exact(projects_dir, candidate, convo_dirs)
        if found is not None:
            return found, candidate

    return None, None


def get_convo_timestamp(jsonl_path):
    """Extract the earliest timestamp from a conversation file for naming."""
    try:
        for msg in iter_messages(jsonl_path):
            ts = msg.get("timestamp")
            if not ts:
                continue
            try:
                # Handle ISO format or epoch
                if isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    dt = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
            except (ValueError, TypeError, OSError):
                continue
            return dt.strftime("%Y-%m-%d_%H%M")
    except OSError:
        pass
    return None


def make_label(jsonl_path):
    """Build a short filename-safe label from the first human message."""
    text = first_human_text(jsonl_path)
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)          # drop any stray tags
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    words = text.split()[:6]
    label = "_".join(words)[:50].strip("_ .")
    return label or None


def unique_path(output_dir, stem, used):
    """Return a collision-free output path for `stem` within output_dir."""
    candidate = stem
    n = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{n}"
        n += 1
    used.add(candidate.lower())
    return output_dir / f"{candidate}.md"


def list_projects():
    projects_dir = get_claude_projects_dir()
    rows = list(iter_convo_dirs(projects_dir))
    if not rows:
        print(f"No conversation transcripts found under {projects_dir}", file=sys.stderr)
        return 1
    print(f"Projects with transcripts in {projects_dir}:\n", file=sys.stderr)
    for d, recorded in rows:
        count = len(list(d.glob("*.jsonl")))
        print(f"  {count:>3} convo(s)  {recorded or d.name}", file=sys.stderr)
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Export Claude Code conversations for a project to markdown.",
    )
    parser.add_argument(
        "project_root", nargs="?", default=None,
        help="Project root to export (default: current directory)",
    )
    parser.add_argument(
        "--convo-dir",
        help="Point directly at a ~/.claude/projects/<name> folder",
    )
    parser.add_argument(
        "--output-dir",
        help=f"Where to write markdown (default: <project_root>/{DEFAULT_OUTPUT_DIRNAME})",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List projects that have transcripts, then exit",
    )
    parser.add_argument(
        "--set-retention", type=int, metavar="DAYS",
        help=f"Set {RETENTION_KEY} in settings.json (e.g. 3650) and exit. "
             "Applies to all projects, not just this one",
    )
    parser.add_argument(
        "--no-list", dest="build_list", action="store_false",
        help="Do not build the publish list; write only the deny-all stub",
    )
    parser.add_argument(
        "--no-scan", dest="scan", action="store_false",
        help="Build the publish list without re-scanning for risk tags",
    )
    parser.add_argument(
        "--redact", action="store_true",
        help="Replace home directory paths with ~ in the output "
             "(see redact_convos.py)",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.set_retention is not None:
        return set_retention(args.set_retention)

    if args.list:
        return list_projects()

    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else Path.cwd()

    # Find conversation files
    if args.convo_dir:
        convo_dir = Path(args.convo_dir).expanduser()
        if not convo_dir.is_dir():
            print(f"Error: specified convo dir does not exist: {convo_dir}", file=sys.stderr)
            return 1
    else:
        convo_dir, matched_root = find_convo_dir(project_root)
        if matched_root is not None and matched_root != project_root:
            print(f"Note: using project root {matched_root}", file=sys.stderr)
            project_root = matched_root
        if convo_dir is None:
            print(f"Error: no Claude Code conversations found for project: {project_root}", file=sys.stderr)
            print(f"  Looked in: {get_claude_projects_dir()}", file=sys.stderr)
            print(f"  Expected folder: {project_path_to_folder_name(project_root)}", file=sys.stderr)
            print("\n  Run with --list to see projects that do have transcripts,", file=sys.stderr)
            print("  or use --convo-dir to point to the folder directly.", file=sys.stderr)
            return 1

    jsonl_files = sorted(convo_dir.glob("*.jsonl"))
    if not jsonl_files:
        print(f"No .jsonl files found in {convo_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(jsonl_files)} conversation(s) in:", file=sys.stderr)
    print(f"  {convo_dir}", file=sys.stderr)

    # Create output directory (including any missing parents)
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser().resolve()
    else:
        output_dir = project_root / DEFAULT_OUTPUT_DIRNAME
    first_run = not output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Deny-all from the moment the folder exists, before anything is written
    ensure_gitignore(output_dir)

    # Process each conversation
    used = set()
    written = 0
    redacted = 0
    for jsonl_path in jsonl_files:
        timestamp = get_convo_timestamp(jsonl_path) or "unknown"
        label = make_label(jsonl_path) or jsonl_path.stem[:8]

        output_path = unique_path(output_dir, f"{timestamp}_{label}", used)

        try:
            if convert(str(jsonl_path), str(output_path), verbose=False) == 0:
                print(f"  {jsonl_path.name} -> skipped (no readable messages)",
                      file=sys.stderr)
                continue
            print(f"  {jsonl_path.name} -> {output_path.name}", file=sys.stderr)
            if args.redact:
                redacted += sum(redact_file(output_path).values())
            written += 1
        except OSError as exc:
            print(f"    skipped ({exc})", file=sys.stderr)

    print(f"\nDone. {written} file(s) written to {output_dir}", file=sys.stderr)
    if args.redact:
        print(f"Redacted {redacted} path reference(s).", file=sys.stderr)

    if args.build_list:
        print(file=sys.stderr)
        sys.stderr.flush()
        report = sync(output_dir, scan=args.scan)
        sys.stdout.flush()
        if args.scan:
            counts = {}
            for _, tag, _, _ in report:
                counts[tag] = counts.get(tag, 0) + 1
            summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
            print(f"  scanned: {summary}", file=sys.stderr)
        print(f"\nUncomment entries in {output_dir / '.gitignore'} to publish them.",
              file=sys.stderr)

    warn_retention(fresh=first_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
