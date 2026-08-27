# Conversation export scripts

Save Claude Code conversations as readable markdown, then choose — deliberately,
one at a time — which of them are allowed into a public repo.

Everything here is stdlib Python 3.8+. No dependencies, no config file, nothing
to install. Copy the `scripts/` folder into any project and it works.

---

## The workflow

```bash
# 1. Export, scan, and build the publish list — one command
python3 scripts/export_all_convos.py            # or: --redact, see below

# 2. Open llm_convos/.gitignore and uncomment what you want public
```

That is the whole routine. Step 1 writes every conversation, scans each one, and
lists them all commented out with their risk tag beside them. Nothing is ever
published for you — uncommenting is deliberate, and yours.

Once, when you first set the repo up:

```bash
python3 scripts/publish_convos.py --install-hook   # refuse commits with keys in
```

And whenever you prefer a command to an editor:

```bash
python3 scripts/publish_convos.py --allow 2026-07-15_Just_check
python3 scripts/publish_convos.py --status
```

### Re-running it later

Re-run step 1 whenever you want the archive up to date. Your choices survive —
a conversation you published stays published when it gains new turns.

With one exception. If a conversation's **risk has gone up** since you approved
it, it is un-published and you are told:

```
⚠ UNPUBLISHED 1 conversation(s) — new findings since you approved them

    2026-08-20_1606_Write_a_research_doc_for_me.md
      [clean] → [BLOCK]
      BLOCK line 489: Anthropic API key

  They have been re-commented in the list. Re-publish once happy:
      python3 publish_convos.py --allow 2026-08-20_1606_Write_a_research_d
```

The rule is that the file is no longer the thing you approved, so the decision
should be made again rather than inherited:

| Change | What happens |
|---|---|
| `[clean]` → `[review]` | un-published — personal information appeared |
| `[clean]` → `[BLOCK]` | un-published — a credential appeared |
| `[review]` → `[BLOCK]` | un-published — a credential appeared |
| `[review]` → `[review]` | stays published — same kind of content as before |
| `[BLOCK]` → `[clean]` | stays as you left it — risk went down |

Without this, a conversation you published months ago could quietly gain a key
on re-export and stay published, tagged `[BLOCK]`, with only the commit hook
standing in the way.

### Doing less than the default

| Flag | Effect |
|---|---|
| `--no-scan` | Build the list without re-scanning. Recorded tags are kept, not discarded, and nothing is un-published |
| `--no-list` | Write only the deny-all stub, leaving the list to `publish_convos.py` |

Scanning your whole archive costs about a second, so there is rarely a reason to
skip it.

### Step 1: with or without `--redact`?

The flag changes the **markdown content only**. It has no bearing on what is
safe to commit — the deny-all `.gitignore` is written either way, and
`scan_convos.py` finds the same credentials and personal information whichever
you choose. The only difference is whether your machine's username appears in
the output.

Without it, every file's `*Source:*` header and every absolute path keeps the
real path, plus the owner column of any `ls -l` in captured output:

```
*Source: `/Users/dan/.claude/projects/-Users-dan-Code-thing/4599c368-....jsonl`*
-rw-r--r--@ 1 dan  staff  23722 Jul 16 13:04 report.md
```

With it, those become `~/...`, `-Users-user-...` and `1 user  staff`. On a
6-conversation export that was 1,830 path references, 140 hyphenated project
names and 74 owner fields rewritten. `scan_convos.py` drops from 7 `NOTE`
findings to none.

**If your account name is already public — the same as your GitHub handle, say —
then skipping it costs you very little.** Reach for it when:

- You are exporting from a machine whose account name you would rather not
  publish: a work laptop, a client box, a shared account
- You want the output to be machine-neutral, so re-exporting from somewhere else
  does not produce a diff in every line
- You simply prefer `~/Code/thing/main.py` to read cleanly for other people

**What it does not do.** It removes the username, not the directory structure.
Folder and project names survive intact, so an export can still name projects
sitting alongside this one — in a real 48-conversation archive, 10 files
referenced three or more distinct project folders, and `~/Code/claude/career`
stayed readable after redaction. If that is the concern, redaction is not the
answer: read the conversation before publishing it.

It also leaves emails, phone numbers and credentials completely alone. Those
need `redact_convos.py --emails --personal --secrets`, described below.

**Forgetting it is not a problem.** Running `redact_convos.py` over an already
exported folder produces byte-identical output to having passed `--redact` at
export time, so you can decide after the fact without re-exporting.

### Does anything create the folders?

Yes, both:

| Thing | Created by | When |
|---|---|---|
| `llm_convos/` | `export_all_convos.py` | On every run, including parent folders |
| `llm_convos/.gitignore` | `export_all_convos.py` | A deny-all stub the moment the folder exists, replaced by the full annotated list at the end of the same run |
| The same list, re-synced | `publish_convos.py` | Whenever you run it directly |

The stub is written before any conversation is, so there is never a moment when
a `git add -A` would sweep them into the repo. The annotated list replaces it at
the end of the run.

Transcripts with no readable messages — damaged or empty — are skipped rather
than exported as an empty file.

---

## The scripts

### `jsonl_to_markdown.py` — one transcript to markdown

The converter everything else is built on. Reads one `.jsonl` from
`~/.claude/projects/` and writes readable markdown.

- Human turns become `## Human (n)`, assistant turns `## Assistant`
- Transcripts containing no readable messages are skipped
- A numbered `## Prompts` index at the top links to each one, using the
  `#human-n` anchors GitHub generates from those headings. It is omitted for
  conversations with fewer than two prompts
- Interruptions and bare slash commands (`/compact`, `/model`) are shown where
  they happened but take no prompt number — the index is an index of things you
  actually asked
- Tool calls render compactly: Bash as a fenced block with its description,
  file tools as `*[Read: path]*`, and so on
- Tool results are truncated to 200 characters — enough to follow the thread,
  not enough to drown it
- Injected scaffolding is stripped: system reminders, IDE state, slash-command
  wrappers, background-task notifications
- Thinking blocks are skipped

```bash
python3 scripts/jsonl_to_markdown.py <input.jsonl>              # to stdout
python3 scripts/jsonl_to_markdown.py <input.jsonl> out/convo.md # to a file
```

It refuses to write markdown over a `.jsonl`, or to accept more than two
arguments. Both guards exist because `jsonl_to_markdown.py *.jsonl` expands to
several paths and the second one would otherwise be silently overwritten with
the output. Missing parent folders for the output are created.

### `export_all_convos.py` — a whole project at once

Finds every conversation belonging to a project and converts them all.

```bash
python3 scripts/export_all_convos.py                 # this project, from cwd
python3 scripts/export_all_convos.py /path/to/proj   # a specific project
python3 scripts/export_all_convos.py --list          # what is available
python3 scripts/export_all_convos.py --redact        # scrub username as it writes
python3 scripts/export_all_convos.py --no-scan       # list, but skip the scan
python3 scripts/export_all_convos.py --no-list       # deny-all stub only
python3 scripts/export_all_convos.py --set-retention 3650   # stop transcripts expiring
python3 scripts/export_all_convos.py --output-dir DIR
python3 scripts/export_all_convos.py --convo-dir ~/.claude/projects/<name>
```

Finding the right transcript folder is the fiddly part. Claude Code encodes the
project path into a folder name (`/Users/x/Code/my_proj` becomes
`-Users-x-Code-my-proj`), so the script tries three things in order:

1. That encoding, applied to the project path
2. The `cwd` recorded *inside* the transcripts — authoritative, and immune to
   any change in how the encoding works
3. A loose name match ignoring separators and case

It also walks up parent directories, so running it from a subfolder of a project
finds that project and reports which root it used.

Output files are named `<date>_<time>_<first few words>.md`, taken from your
first real message — injected wrapper text is excluded, so files are named after
what you actually asked. Collisions get a numeric suffix.

`CLAUDE_CONFIG_DIR` is honoured if you have moved `~/.claude`.

#### Keeping transcripts long enough to export them

Claude Code deletes session transcripts older than `cleanupPeriodDays`, and the
documented default is **30 days**. It happens at startup, with `unlink()` rather
than a trash folder, and nothing warns you. An archive tool is not much use if
its source disappears a month after the conversation, so every export ends by
checking the setting.

The first time it creates a conversation folder, it always tells you where you
stand — that being the moment you would want to know:

```
  Transcript retention: cleanupPeriodDays = 3650 days (~10 years),
  from /Users/you/.claude/settings.json.
```

After that it stays quiet, unless the period is short enough to be losing
conversations, in which case it says so on every run:

```
  Note: cleanupPeriodDays is unset, so the default applies: 30 days.
  Claude Code deletes older transcripts at startup. To keep them:
      python3 scripts/export_all_convos.py --set-retention 3650
```

That flag writes `cleanupPeriodDays` into `~/.claude/settings.json` and exits.
No Claude Code command is needed — it is a plain JSON file. The edit is
deliberately narrow: it backs the file up first, changes or inserts that single
line while leaving the rest byte for byte, checks the result still parses, and
refuses to write if it would not.

Two things to know:

- **It is a global setting.** It governs transcripts for every project, not just
  this one, and takes effect the next time Claude Code starts — which is also
  the next moment cleanup would have run.
- **Do not use `0`.** Third-party posts describe it as "never delete", but
  Anthropic does not document that, and if it means "retain zero days" it would
  delete everything. A large number is the safe way to say the same thing, so
  the flag refuses anything below 1.

A year or more counts as healthy; below that, or unset, gets the warning.

### `redact_convos.py` — remove machine identity

Exports carry your username in more places than you would guess: the `*Source:*`
header, every absolute path in every tool call, the hyphenated project folder
names, and the owner column of any `ls -l` output.

```bash
python3 scripts/redact_convos.py               # rewrite ./llm_convos in place
python3 scripts/redact_convos.py --dry-run     # preview
python3 scripts/redact_convos.py --output-dir DIR
```

By default it rewrites paths and file ownership only:

```
/Users/dan/Code/thing/main.py                    ->  ~/Code/thing/main.py
.../projects/-Users-dan-Code-thing/x.jsonl       ->  .../projects/-Users-user-Code-thing/x.jsonl
-rw-r--r--@ 1 dan  staff  23722 report.md        ->  -rw-r--r--@ 1 user  staff  23722 report.md
```

Three opt-in flags go further: `--emails`, `--personal` (phones, postcodes,
cards, pay figures) and `--secrets`.

> Redacting a credential does not un-leak it. Anything real that reaches this
> stage is already compromised and needs rotating.

It deliberately leaves **deliberate** identity alone — your own website, GitHub
handle, package IDs, author lines. Only incidental machine identity is removed.
Running it twice changes nothing the second time.

### `scan_convos.py` — what is actually in these files

Grades findings, because "this is a credential" and "this is too personal to
publish" are different kinds of problem.

| Severity | Meaning | Examples |
|---|---|---|
| `BLOCK` | Credentials. Never publish. | Anthropic/OpenAI/AWS/GitHub/Slack/Google/Stripe keys, private keys, JWTs, bearer headers, DB URLs with passwords, `SECRET=` assignments |
| `REVIEW` | Personal information. **Your call.** | Emails, UK phone/postcode/NI/sort code, payment cards, pay figures, ID documents |
| `NOTE` | Low-signal leakage | Usernames in paths, public IPs |

```bash
python3 scripts/scan_convos.py                   # scan ./llm_convos
python3 scripts/scan_convos.py --severity BLOCK  # only the serious findings
python3 scripts/scan_convos.py --json            # machine-readable
```

Exit code is 1 if anything reaches `--fail-on` (default `BLOCK`), so it drops
straight into CI.

Rules were tuned against a real 48-conversation archive, which is the only way
to tell a useful rule from a noisy one. Cards are Luhn-checked and must carry a
real issuer prefix, so ISBNs and floats do not trip them. Dotted quads inside
tokens containing letters are treated as version numbers, not IP addresses.
Pay figures need a salary keyword nearby, because a corpus full of legitimate
economic figures would otherwise flag on every `£`.

### `publish_convos.py` — the gate

Maintains a default-deny `.gitignore` inside the conversation folder. Everything
is ignored; each conversation gets a commented-out exception line annotated with
its scan result.

```gitignore
*
!.gitignore

# --- [review] 2026-07-16_0746_I_should_probably_update_my_linkedin.md
#     REVIEW line 50: Email address
# !2026-07-16_0746_I_should_probably_update_my_linkedin.md
```

Delete the `# ` in front of a `!` line to publish that conversation; put it back
to withdraw it. Re-syncing preserves what you have chosen and adds anything new
as denied.

```bash
python3 scripts/publish_convos.py                # sync the list
python3 scripts/publish_convos.py --status       # what is public
python3 scripts/publish_convos.py --allow NAME   # publish one
python3 scripts/publish_convos.py --deny NAME    # withdraw one
python3 scripts/publish_convos.py --check        # fail if anything unsafe is public
python3 scripts/publish_convos.py --check --staged  # scan what is about to commit
python3 scripts/publish_convos.py --install-hook # pre-commit backstop
```

`--allow` refuses outright on `BLOCK` findings. Names can be any unambiguous
substring; an ambiguous one lists the candidates rather than guessing.

**Why it is default-deny, not default-allow.** An "uncomment to hide" list would
make each newly exported conversation public before you had read it. The risk
sits on the wrong side of that choice.

**The tracked-file trap.** `.gitignore` has no effect on files git already
tracks. A gate built only on ignore rules will happily report "safe" while a
tracked conversation sits in the repo with a key in it. So `--check` scans the
union of allow-listed *and* git-tracked files, `--deny` runs `git rm --cached`,
and syncing warns about anything tracked but unpublished.

**None of this helps for what is already committed.** Once a secret is in
history, the only real remedies are rewriting history and rotating the key.

#### What `--install-hook` does

It writes a git `pre-commit` hook that runs `--check --staged` before every
commit in this repo. If a conversation you have published contains credentials,
the commit is refused:

```
✗ 2026-08-20_1447_Godot_is_now_on_472_Can.md — 1 BLOCK finding(s) in the staged content
    line 131: Anthropic API key: sk-ant-a...abcd (51 chars)

1 staged conversation(s) contain credentials.
Unstage them and rotate the exposed keys:
    git restore --staged 2026-08-20_1447_Godot_is_now_on_472_Can.md
pre-commit: blocked by conversation scan (git commit --no-verify overrides)
```

The allow-list already stops anything you have not reviewed. The hook exists for
the case the allow-list cannot see: a conversation you published weeks ago,
re-exported today, that now contains a key because you pasted one mid-session.
Nothing about your choices changed, so nothing prompts you to look again.

It scans the **staged** content, not the working tree. Those differ after
`git add` followed by an edit, or after `git add -p`, and it is the staged
version that reaches history. `--check` on its own scans the working tree, which
is what you want when reviewing by hand.

Practical limits, none of them avoidable from inside a hook:

- **`git commit --no-verify` skips it.** That is git's behaviour, and the
  message says so rather than pretending otherwise.
- **Hooks are not committed.** `.git/hooks/` is local to your clone, so anyone
  else cloning the repo has to run `--install-hook` themselves. Publishing the
  repo does not publish the safety net.
- **It will not overwrite an existing `pre-commit` hook.** If you already have
  one it prints the line to add and stops, rather than clobbering your setup.
- If `core.hooksPath` is set — husky, the `pre-commit` framework — it installs
  there instead, because git would never look in `.git/hooks` and a hook written
  there would silently never run.

---

## Notes

- Do not add `llm_convos/` to the repo's top-level `.gitignore`. Git cannot
  re-include a file inside an excluded directory, which would silently break
  every per-conversation exception.
- The markdown is regenerable from the `.jsonl` transcripts at any time, so
  in-place rewriting is safe. The transcripts themselves are the originals and
  nothing here writes to them.
- Claude Code deletes old transcripts (`cleanupPeriodDays`, 30 days by default),
  so exporting is also how you keep them. `--set-retention 3650` changes that;
  see above.
