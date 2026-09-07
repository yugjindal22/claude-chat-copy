# Claude Chat Copy

Keep the conversation when you change Claude Desktop profiles.

Copy a project's **local Code-tab chats** into another profile on the same Mac. The copies get new session IDs, so you can continue them independently. The working folder stays the same.

## Use it

**Easiest:** copy [this prompt](PROMPT.md) into a local coding assistant.

Or run the guided picker. Requires Python 3.9+ and macOS.

```sh
git clone https://github.com/yugjindal22/claude-chat-copy.git
cd claude-chat-copy
python3 claude_chat_copy.py
```

Pick the source, destination and project. Review the chat titles, then type `COPY`.

Sign into the destination profile and create one local Code chat first so it has an account directory. **Finish your tasks and quit Claude Desktop before copying.** The tool refuses to copy while Desktop is running and never quits it for you. Run it from Terminal, Codex, or a terminal/VS Code assistant that is not using the chats being copied.

Original chats stay in place. Copied chats have independent histories; project files are shared. Stored permission grants and connector configuration are omitted. No cookies or account credentials are copied.

This is an **experimental local migration tool**. It does not transfer claude.ai cloud chats, Cowork tasks, remote sessions or account ownership. Desktop's private session format can change. Back up your local history first.

Need a second Desktop profile? [Claude Dock](https://github.com/yugjindal22/claude-dock).

[Options, scope and testing](DETAILS.md) · [MIT license](LICENSE) · [Use and contact](USE.md)
