# export_all_convos

A template for keeping Claude Code conversations as readable markdown, and for
choosing — one at a time, deliberately — which of them are safe to make public.

Copy the [`scripts/`](scripts/) folder into any project. It is plain Python 3.8+
with no dependencies.

## Use it

```bash
python3 scripts/export_all_convos.py          # export, scan, build the publish list
```

Every conversation for the current project is written to `llm_convos/`, scanned
for credentials and personal information, and listed in `llm_convos/.gitignore`
**commented out**. 

If running for the first time, the `llm_convos/` folder and `llm_convos/.gitignore` file will be made.

To make one committable, delete the
`# ` in front of its line:

```gitignore
# --- [clean] 2026-07-15_1450_Just_check_through_the_list_of.md
# !2026-07-15_1450_Just_check_through_the_list_of.md      <- uncomment to publish
```

By default, nothing goes into commit history. You have to uncomment chats you wish to be committed. The scan will try and pick up on really obvious boo-boos and flag those, **but make sure you know what's in them.** That's still your responsibility.

Re-run it whenever you like. Choices you have already made survive, unless a
conversation's risk has *risen* since you approved it — then it is un-published
and you are told why, so the decision gets made again rather than inherited.

## Your chat history is on a timer

Claude Code deletes session transcripts after **30 days by default**. It happens
quietly at startup, and the files are unlinked rather than moved to a trash
folder. Since those transcripts are what this tool exports *from*, an archive is
not much use if the source is deleted a month after the conversation.

To keep them for ten years instead:

```bash
python3 scripts/export_all_convos.py --set-retention 3650
```

That writes `cleanupPeriodDays` into `~/.claude/settings.json` — it is a plain
JSON file, so no Claude Code command is needed. It backs the file up first and
changes only that one line. The setting is **global**, covering every project,
and takes effect the next time Claude Code starts.

Use a big number rather than `0`. Some blog posts describe `0` as "never
delete", but Anthropic does not document that, and if it means "keep zero days"
it would delete everything.

The export tells you where you stand the first time it creates a folder, and
after that only speaks up if transcripts are expiring:

```
  Transcript retention: cleanupPeriodDays = 3650 days (~10 years),
  from /Users/you/.claude/settings.json.
```

## Full documentation

[**scripts/README.md**](scripts/README.md) covers the rest: what each of the
five scripts does, the redaction pass that strips your username from exported
paths, how the scanner grades findings, why the publish gate is default-deny,
and the `--install-hook` backstop that refuses commits containing credentials.
