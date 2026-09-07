# Test status

Local automated validation: macOS 26.6, Python 3.14.6, September 7, 2026.

Synthetic fixtures exercise history chains, fork ancestry, independent IDs, message preservation, duplicate protection, symlink refusal, running-app guards and rollback. Source hashes are checked for unchanged originals. Tests clean up their temporary files.

## Manual release check still required

1. Use two disposable signed-in Desktop profiles and a temporary project folder.
2. Create a short source chat, fork it, archive an intermediate fork, and create one destination chat.
3. Finish work and quit Desktop yourself. Preview the source project, then copy it.
4. Open the destination and check all selected titles and messages.
5. With permission to send a test prompt, continue a copied chat. Confirm the original transcript does not change.
6. Check that the destination asks for its own permissions when needed.
7. Remove disposable test profiles and copied fixture histories after recording the result. Do not remove any real account or project data.

The predecessor copied three real chats with sidebar verification. The generalized release has not yet completed this fresh UI/resume checklist. Desktop uses a private storage format, so fixture coverage is not an assurance of compatibility across versions.
