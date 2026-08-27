#!/usr/bin/env python3
"""Convert a Claude Code conversation .jsonl file to readable markdown.

Usage:
    python jsonl_to_markdown.py <input.jsonl> [output.md]

If no output path is given, writes to stdout.

E.g.

Print to stdout
python3 scripts/jsonl_to_markdown.py ~/.claude/projects/-Users-danolner-thevault/6544d6a2.jsonl

Write to a file (parent folders are created if needed)
python3 scripts/jsonl_to_markdown.py ~/.claude/projects/-Users-danolner-thevault/6544d6a2.jsonl out/convo.md

Pipe to less for browsing
python3 scripts/jsonl_to_markdown.py <file>.jsonl | less
"""

import json
import re
import sys
from pathlib import Path

# Wrapper blocks Claude Code injects into user turns that are not human prose.
# Stripped wholesale (tags *and* their contents) before anything is rendered.
NOISE_BLOCK_RE = re.compile(
    r"<(system-reminder|local-command-caveat|task-notification|ide_selection"
    r"|local-command-stdout)>.*?</\1>",
    re.DOTALL,
)

IDE_OPENED_RE = re.compile(r"<ide_opened_file>(.*?)</ide_opened_file>", re.DOTALL)
COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.DOTALL)
COMMAND_WRAPPER_RE = re.compile(
    r"<(command-name|command-message|command-args)>.*?</\1>", re.DOTALL
)


def _block_text(block):
    """Return the text of a content block, tolerating raw strings and odd shapes."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and block.get("type") == "text":
        return block.get("text", "") or ""
    return ""


def strip_noise(text):
    """Remove injected wrapper blocks, leaving only what the human actually wrote."""
    text = NOISE_BLOCK_RE.sub("", text)
    text = IDE_OPENED_RE.sub("", text)
    text = COMMAND_WRAPPER_RE.sub("", text)
    return text.strip()


def extract_user_text(content):
    """Extract readable text from user message content."""
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        btype = block.get("type", "text") if isinstance(block, dict) else "text"

        if btype == "text":
            text = _block_text(block)
            if not text:
                continue

            # Note which file the IDE had open, then drop the tag itself
            for opened in IDE_OPENED_RE.findall(text):
                path = opened.split("opened the file ")[-1].split(" in the IDE")[0]
                parts.append(f"*[Opened file: {path}]*")

            # Surface slash commands as a readable line
            for cmd in COMMAND_NAME_RE.findall(text):
                cmd = cmd.strip()
                if cmd:
                    parts.append(f"*[Slash command: {cmd}]*")

            cleaned = strip_noise(text)
            if cleaned:
                parts.append(cleaned)

        elif btype == "tool_result":
            # Summarise tool results compactly
            result_content = block.get("content", "")
            if isinstance(result_content, str):
                preview = result_content[:200].replace("\n", " ")
                if len(result_content) > 200:
                    preview += "..."
                parts.append(f"> **Tool result:** {preview}")
            elif isinstance(result_content, list):
                for sub in result_content:
                    if isinstance(sub, dict) and sub.get("type") == "text":
                        preview = sub.get("text", "")[:200].replace("\n", " ")
                        parts.append(f"> **Tool result:** {preview}")

    return "\n".join(parts)


def extract_assistant_text(content):
    """Extract readable text from assistant message content."""
    if not isinstance(content, list):
        return ""

    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        inp = block.get("input", {})
        if not isinstance(inp, dict):
            inp = {}

        if btype == "text":
            text = (block.get("text", "") or "").strip()
            if text:
                parts.append(text)

        elif btype == "tool_use":
            name = block.get("name", "unknown")

            if name == "Bash":
                cmd = inp.get("command", "")
                desc = inp.get("description", "")
                label = f" *({desc})*" if desc else ""
                parts.append(f"```bash{label}\n{cmd}\n```")

            elif name == "Read":
                parts.append(f"*[Read: `{inp.get('file_path', '')}`]*")

            elif name == "Write":
                parts.append(f"*[Write: `{inp.get('file_path', '')}`]*")

            elif name == "Edit":
                parts.append(f"*[Edit: `{inp.get('file_path', '')}`]*")

            elif name == "Glob":
                parts.append(f"*[Glob: `{inp.get('pattern', '')}`]*")

            elif name == "Grep":
                parts.append(f"*[Grep: `{inp.get('pattern', '')}`]*")

            elif name == "WebSearch":
                parts.append(f'*[Web search: "{inp.get("query", "")}"]*')

            elif name == "WebFetch":
                parts.append(f"*[Fetch: {inp.get('url', '')}]*")

            elif name == "TodoWrite":
                todos = inp.get("todos", []) or []
                items = [
                    f"  - [{t.get('status', '?')}] {t.get('content', '')}"
                    for t in todos
                    if isinstance(t, dict)
                ]
                parts.append("*[Todo update:]*\n" + "\n".join(items))

            elif name in ("Task", "Agent"):
                parts.append(f"*[Spawned agent: {inp.get('description', '')}]*")

            elif name == "Skill":
                parts.append(f"*[Skill: {inp.get('skill', '')}]*")

            else:
                parts.append(f"*[Tool: {name}]*")

        elif btype == "thinking":
            # Skip thinking blocks - they're usually empty/redacted
            pass

    return "\n\n".join(parts)


def _has_human_text(content):
    """Check if a user message content contains actual human text blocks
    (as opposed to only tool_result blocks, which are system responses)."""
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text")
        for b in content
    )


def iter_messages(input_path):
    """Yield parsed JSON records from a .jsonl transcript, skipping bad lines."""
    with open(input_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def message_content(msg):
    """Return the content payload of a user/assistant record."""
    message = msg.get("message", "")
    if isinstance(message, dict):
        return message.get("content", "")
    return message


def first_human_text(input_path):
    """Return the first genuine human prose in a transcript, or None.

    Skips meta records, compact summaries, tool results, and injected
    wrapper blocks (system reminders, IDE state, slash-command scaffolding).
    """
    for msg in iter_messages(input_path):
        if msg.get("type") != "user":
            continue
        if msg.get("isMeta") or msg.get("isCompactSummary"):
            continue
        if msg.get("userType") not in (None, "external"):
            continue

        content = message_content(msg)
        if not _has_human_text(content):
            continue

        blocks = [content] if isinstance(content, str) else content
        texts = [_block_text(b) for b in blocks]
        cleaned = strip_noise("\n".join(t for t in texts if t))
        if cleaned:
            return cleaned
    return None


# Claude Code records an aborted turn as a user message. It is an event, not
# a prompt, so it should not take a number in the index.
INTERRUPTION_RE = re.compile(r"^\[Request interrupted by user[^\]]*\]$")


def clean_single_line(content):
    """All the human text of one turn, collapsed to a single line."""
    blocks = [content] if isinstance(content, str) else (content or [])
    text = strip_noise("\n".join(t for t in (_block_text(b) for b in blocks) if t))
    return re.sub(r"\s+", " ", text).strip()


def is_prompt(content):
    """Whether a user turn is something you actually asked.

    Excludes aborted-turn markers and turns whose only content is scaffolding
    -- a bare slash command such as /compact, or an IDE "opened file" notice.
    Those still appear in the body, but they do not take a prompt number.
    """
    text = clean_single_line(content)
    return bool(text) and not INTERRUPTION_RE.match(text)


def prompt_preview(content, limit=90):
    """A single-line summary of one human turn, safe to use as link text."""
    text = clean_single_line(content)
    if not text:
        return None
    # Square brackets and backslashes would break the surrounding link syntax
    text = text.replace("\\", "").replace("[", "(").replace("]", ")")
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
        text = cut.rstrip(" ,.;:-") + "..."
    return text


def build_index(entries):
    """Numbered links to each human turn.

    `## Human (n)` slugifies to `#human-n` under GitHub's heading anchors,
    which is what the numbering is for.
    """
    if len(entries) < 2:
        return []
    lines = ["\n## Prompts\n"]
    lines += [f"{n}. [{preview}](#human-{n})" for n, preview in entries]
    lines.append("\n---")
    return lines


def convert(input_path, output_file=None, index=True, verbose=True):
    header = [
        "# Claude Code Conversation\n",
        f"*Source: `{input_path}`*\n",
        "---",
    ]
    out = []
    entries = []

    turn_num = 0

    for msg in iter_messages(input_path):
        msg_type = msg.get("type", "")

        # Skip non-message lines (snapshots, queue ops, attachments, etc.)
        if msg_type not in ("user", "assistant"):
            continue

        content = message_content(msg)

        if msg_type == "user":
            if msg.get("isMeta"):
                continue

            if _has_human_text(content):
                text = extract_user_text(content)

                if text.strip() and not is_prompt(content):
                    # An event or a command, not a prompt: shown, not numbered
                    marker = clean_single_line(content)
                    out.append(f"\n*{marker}*\n" if marker else f"\n{text.strip()}\n")
                    continue

                # Actual human turn — contains text blocks (your input)
                if text.strip():
                    turn_num += 1
                    preview = prompt_preview(content)
                    if preview:
                        entries.append((turn_num, preview))
                    out.append(f"\n## Human ({turn_num})\n")
                    out.append(text)
                    out.append("")
            else:
                # Tool results only — system response to a tool call
                text = extract_user_text(content if isinstance(content, list) else [])
                if text.strip():
                    out.append(text)
                    out.append("")

        elif msg_type == "assistant":
            text = extract_assistant_text(content if isinstance(content, list) else [])
            if text.strip():
                out.append("\n## Assistant\n")
                out.append(text)
                out.append("")

    if not out:
        # Nothing convertible: an empty or damaged transcript
        return 0

    index_block = build_index(entries) if index else []
    result = "\n".join(header + index_block + [""] + out)

    if output_file:
        path = Path(output_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result, encoding="utf-8")
        if verbose:
            print(f"Written to {output_file}", file=sys.stderr)
    else:
        # Avoid UnicodeEncodeError on consoles with a narrow default encoding
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(result)
    return turn_num


def _check_output_target(input_path, output_file):
    """Refuse to write over a transcript.

    `script *.jsonl` expands to several paths, and argv[2] would then be a real
    transcript that we would silently overwrite with markdown. Guard against it.
    """
    out = Path(output_file)
    if out.suffix.lower() == ".jsonl":
        raise SystemExit(
            f"Refusing to write markdown over a .jsonl transcript: {out}\n"
            "  (a shell glob such as *.jsonl expands to several paths — "
            "quote the input or pass one file at a time)"
        )
    if out.resolve() == Path(input_path).resolve():
        raise SystemExit(f"Refusing to overwrite the input file: {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Usage: python jsonl_to_markdown.py <input.jsonl> [output.md]", file=sys.stderr)
        sys.exit(1)
    if len(args) > 2:
        print(
            f"Error: expected at most 2 arguments, got {len(args)}.\n"
            "  A glob like *.jsonl probably expanded to several files. "
            "Use export_all_convos.py to convert a whole folder.",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = args[0]
    output_path = args[1] if len(args) > 1 else None
    if output_path:
        _check_output_target(input_path, output_path)

    if convert(input_path, output_path) == 0:
        print(f"No readable messages in {input_path} — nothing written.",
              file=sys.stderr)
        sys.exit(1)
