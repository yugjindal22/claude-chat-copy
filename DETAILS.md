# Details

The guided picker discovers account directories inside `~/Library/Application Support/Claude*/claude-code-sessions`. It asks for both endpoints because a profile can contain directories from more than one account. Account UUIDs are identifiers, not proof of the current login: check the account in the app first.

The tool copies visible local sessions for one existing project folder. It includes each selected session's current transcript, prior CLI transcript chain and files in those session directories, including subagent histories. Archived intermediary forks are not added as extra chats. A copied fork is linked to its closest selected ancestor, or becomes a standalone conversation if no ancestor was selected.

Fresh Desktop and CLI IDs prevent appends to the original history. Message bodies and tool output remain unchanged. Structural session references are remapped. Metadata is built from a small allowlist; permission bypasses, browser grants, remote MCP configuration and old desktop prompt snapshots are not imported.

It validates transcripts, checks source hashes before and after copying, rejects symlinks and destination collisions, and writes metadata last. A handled failure removes only files created by that attempt. An OS crash or power loss can leave partial output; the tool is not a transactional database. A private receipt records created paths and hashes. Repeating the same snapshot into the same account is refused.

The originals are the unchanged source snapshot. Keep a separate backup before migration. Receipts and histories can contain sensitive paths/content and must never be committed or attached to public issues. The tool itself makes no network requests; resuming a copied chat in Claude can send its context to Anthropic under the destination account's settings.

## Command-line options

```sh
python3 claude_chat_copy.py --list
python3 claude_chat_copy.py --source '/source/account/directory' --target '/target/account/directory' --project '/your/project'
```

Use the exact account paths printed by `--list`. That second command previews only. Add `--apply` to copy, or repeat `--session local_UUID` to copy specific chats. A custom **shared** Code config root can be supplied with `--config-dir`.

## Limits

macOS, local Code-tab sessions, one computer, a regular existing project folder and a shared Claude Code history directory. Scratch workspaces, remote sessions, Cowork tasks, cloud chats, project duplication, cross-machine migration, distinct source/destination config roots and file-rewind checkpoints outside the session directory are not supported. Attachments or external tool outputs referenced outside copied directories are not duplicated.

All Desktop instances must be closed before applying. Terminal Claude Code can stay open only if it is not writing the selected histories. The process guard and hash checks reduce races; they cannot lock out unrelated programs. Never run parallel agents against the same project files unless you intend shared changes.

## Testing

```sh
python3 -m unittest discover -s tests -v
```

Tests use synthetic accounts and chats in temporary folders. They cover independent IDs, preserved message content, prior histories, archived fork ancestry, source integrity, permission omission, repeat-copy protection, malformed files, symlinks, running-app checks, subagents and rollback after a simulated disk failure.

The predecessor script was used to copy three real Code chats and their sidebar presence was verified. This rewritten version's fresh UI/resume test is still a manual release check. Fixture tests do not establish compatibility with every Desktop build. See [TESTING.md](TESTING.md).
