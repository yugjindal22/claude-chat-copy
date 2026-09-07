import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

spec = importlib.util.spec_from_file_location("copytool", Path(__file__).resolve().parents[1] / "claude_chat_copy.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claude-chat-copy-test-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.project = self.home / "Project With Spaces"
        self.project.mkdir()
        self.config = self.home / ".claude"
        self.source = self.account("Claude")
        self.target = self.account("Claude-Two")
        self.history = self.config / "projects" / "fixture-encoding"
        self.history.mkdir(parents=True)
        self.base = self.session(self.source, "First chat")
        self.prior = str(uuid.uuid4())
        self.write_transcript(self.prior)
        self.base["priorCliSessionIds"] = [self.prior]
        self.save(self.source, self.base)
        self.archived = self.session(self.source, "Hidden fork", self.base["sessionId"], True)
        self.fork = self.session(self.source, "Visible fork", self.archived["sessionId"])
        self.other = self.session(self.target, "Existing destination chat")
        self.originals = {p: tool.digest(p) for p in self.home.rglob("*") if p.is_file()}

    def account(self, name):
        path = self.home / "Library/Application Support" / name / "claude-code-sessions" / str(uuid.uuid4()) / str(uuid.uuid4())
        path.mkdir(parents=True)
        return path

    def save(self, directory, data):
        (directory / (data["sessionId"] + ".json")).write_text(json.dumps(data))

    def write_transcript(self, sid):
        (self.history / (sid + ".jsonl")).write_text(json.dumps({"type": "user", "sessionId": sid,
              "message": {"content": "Keep literal ID " + sid + " and café exactly"}}) + "\n")

    def session(self, directory, title, parent=None, archived=False):
        sid = str(uuid.uuid4())
        data = {"sessionId": "local_" + str(uuid.uuid4()), "title": title, "cliSessionId": sid,
                "cwd": str(self.project), "originCwd": str(self.project), "isArchived": archived,
                "envScopeId": "builtin_local", "createdAt": 1,
                "permissionMode": "bypassPermissions", "remoteMcpServersConfig": {"secret": "SYNTHETIC-DO-NOT-COPY"}}
        if parent:
            data["forkedFromSessionId"] = parent
        self.save(directory, data)
        self.write_transcript(sid)
        return data

    def plan(self, ids=None):
        return tool.plan_copy(self.home, self.source, self.target, self.project, ids)

    def test_discovery(self):
        self.assertEqual(set(tool.accounts(self.home)), {self.source, self.target})
        self.assertIn("Claude | 2 chats", tool.account_label(self.source))

    def test_preview_has_no_writes(self):
        plan = self.plan()
        self.assertEqual(len(plan["selected"]), 2)
        self.assertEqual(len(plan["histories"]), 3)
        self.assertEqual(self.originals, {p: tool.digest(p) for p in self.home.rglob("*") if p.is_file()})

    def test_complete_copy_preserves_messages_and_originals(self):
        plan = self.plan()
        receipt_path = tool.apply_copy(plan, idle_check=lambda: None)
        receipt = tool.read_json(receipt_path)
        self.assertEqual(len(receipt["sessions"]), 2)
        for path, expected in self.originals.items():
            self.assertEqual(tool.digest(path), expected)
        copied = tool.read_sessions(self.target)
        by_title = {d["title"]: d for _, d in copied.values()}
        self.assertEqual(set(by_title), {"First chat", "Visible fork", "Existing destination chat"})
        first = by_title["First chat"]
        self.assertNotEqual(first["cliSessionId"], self.base["cliSessionId"])
        self.assertEqual(by_title["Visible fork"]["forkedFromSessionId"], first["sessionId"])
        self.assertNotIn("forkedFromSessionId", first)
        self.assertNotIn("permissionMode", first)
        self.assertNotIn("remoteMcpServersConfig", first)
        record = list(tool.records(self.history / (first["cliSessionId"] + ".jsonl")))[0]
        self.assertEqual(record["sessionId"], first["cliSessionId"])
        self.assertIn(self.base["cliSessionId"], record["message"]["content"])
        self.assertEqual(first["cwd"], str(self.project))
        self.assertEqual(len(first["priorCliSessionIds"]), 1)

    def test_repeat_snapshot_refused(self):
        plan = self.plan()
        tool.apply_copy(plan, idle_check=lambda: None)
        with self.assertRaisesRegex(ValueError, "already copied"):
            self.plan()
        with self.assertRaisesRegex(ValueError, "already copied"):
            tool.apply_copy(plan, idle_check=lambda: None)

    def test_selected_fork_without_parent(self):
        receipt = tool.read_json(tool.apply_copy(self.plan([self.fork["sessionId"]]), idle_check=lambda: None))
        copied = tool.read_json(self.target / (receipt["sessions"][self.fork["sessionId"]] + ".json"))
        self.assertNotIn("forkedFromSessionId", copied)

    def test_missing_transcript_refused(self):
        (self.history / (self.base["cliSessionId"] + ".jsonl")).unlink()
        with self.assertRaisesRegex(ValueError, "Missing"):
            self.plan()

    def test_corrupt_jsonl_refused(self):
        (self.history / (self.base["cliSessionId"] + ".jsonl")).write_text("{broken")
        with self.assertRaisesRegex(ValueError, "Invalid JSONL"):
            self.plan()

    def test_source_mutation_refused(self):
        plan = self.plan()
        self.save(self.source, dict(self.base, title="changed"))
        with self.assertRaisesRegex(ValueError, "Source changed"):
            tool.apply_copy(plan, idle_check=lambda: None)
        self.assertEqual(len(tool.read_sessions(self.target)), 1)

    def test_running_desktop_refused(self):
        for line in ["/Applications/Claude.app/Contents/MacOS/Claude", "/A/Claude Three.app/Contents/MacOS/Claude --foo"]:
            with self.assertRaisesRegex(ValueError, "running"):
                tool.ensure_idle([line])
        tool.ensure_idle(["/opt/homebrew/bin/claude", "/Applications/Codex.app/Contents/MacOS/Codex"])

    def test_running_guard_writes_nothing(self):
        with self.assertRaises(ValueError):
            tool.apply_copy(self.plan(), idle_check=lambda: tool.ensure_idle(["/Applications/Claude.app/Contents/MacOS/Claude"]))
        self.assertFalse((self.target / ".chat-copy-receipts").exists())

    def test_second_running_guard(self):
        calls = []
        def check():
            calls.append(1)
            if len(calls) == 2:
                raise ValueError("running")
        with self.assertRaisesRegex(ValueError, "running"):
            tool.apply_copy(self.plan(), idle_check=check)
        self.assertEqual(len(tool.read_sessions(self.target)), 1)

    def test_subagents(self):
        associated = self.history / self.base["cliSessionId"] / "subagents"
        associated.mkdir(parents=True)
        (associated / "agent-test.jsonl").write_text(json.dumps({"sessionId": self.base["cliSessionId"], "type": "assistant"}) + "\n")
        receipt = tool.read_json(tool.apply_copy(self.plan(), idle_check=lambda: None))
        copy = tool.read_json(self.target / (receipt["sessions"][self.base["sessionId"]] + ".json"))
        record = list(tool.records(self.history / copy["cliSessionId"] / "subagents/agent-test.jsonl"))[0]
        self.assertEqual(record["sessionId"], copy["cliSessionId"])

    def test_symlink_refused(self):
        path = self.history / self.base["cliSessionId"]
        path.mkdir()
        (path / "linked").symlink_to(self.home)
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            self.plan()

    def test_invalid_session_id_refused(self):
        self.base["priorCliSessionIds"] = ["../../escape"]
        self.save(self.source, self.base)
        with self.assertRaisesRegex(ValueError, "Invalid CLI"):
            self.plan()

    def test_same_account_refused(self):
        with self.assertRaisesRegex(ValueError, "different"):
            tool.plan_copy(self.home, self.source, self.source, self.project)

    def test_unknown_selection_refused(self):
        with self.assertRaises(ValueError):
            self.plan(["local_" + str(uuid.uuid4())])

    def test_rollback_on_write_failure(self):
        original_open = Path.open
        writes = []
        def failing_open(path, mode="r", *args, **kwargs):
            if mode == "xb":
                writes.append(path)
                if len(writes) == 2:
                    raise OSError("synthetic disk full")
            return original_open(path, mode, *args, **kwargs)
        plan = self.plan()
        with patch.object(Path, "open", failing_open), self.assertRaisesRegex(OSError, "disk full"):
            tool.apply_copy(plan, idle_check=lambda: None)
        self.assertFalse(writes[0].exists())
        self.assertEqual(len(tool.read_sessions(self.target)), 1)

    def test_fork_cycle_refused(self):
        self.archived["forkedFromSessionId"] = self.archived["sessionId"]
        self.save(self.source, self.archived)
        with self.assertRaisesRegex(ValueError, "Cycle"):
            tool.apply_copy(self.plan(), idle_check=lambda: None)
        self.assertEqual(len(tool.read_sessions(self.target)), 1)

    def test_non_uuid_metadata_refused(self):
        self.base["sessionId"] = "local_invalid"
        self.save(self.source, self.base)
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            self.plan()

    def test_external_reference_not_read(self):
        path = self.history / (self.base["cliSessionId"] + ".jsonl")
        record = {"sessionId": self.base["cliSessionId"], "message": {"content": "/private/secret-not-to-read"}}
        path.write_text(json.dumps(record) + "\n")
        receipt = tool.read_json(tool.apply_copy(self.plan(), idle_check=lambda: None))
        self.assertEqual(len(receipt["sessions"]), 2)


if __name__ == "__main__":
    unittest.main()
