#!/usr/bin/env python3
"""Copy local Claude Desktop Code conversations between profiles on one Mac."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
ID = re.compile(UUID + r"\Z")
SAFE_FIELDS = {"title", "createdAt", "lastActivityAt", "lastFocusedAt", "completedTurns",
               "cwd", "originCwd", "model", "effort", "previousTitles"}
REFERENCE_FIELDS = {"sessionId", "parentSessionId", "forkedFromSessionId", "cliSessionId"}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def no_links(path):
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError("Symlinks are not supported: " + str(path))


def accounts(home):
    """Discover only local session indexes, never cookies or credential stores."""
    support = home / "Library/Application Support"
    found = []
    for profile in sorted(support.glob("Claude*")):
        root = profile / "claude-code-sessions"
        if not root.is_dir() or profile.is_symlink():
            continue
        for directory in sorted(root.glob("*/*")):
            if directory.is_dir() and all(ID.fullmatch(p) for p in directory.parts[-2:]):
                no_links(directory)
                if list(directory.glob("local_*.json")):
                    found.append(directory)
    return found


def read_sessions(directory):
    no_links(directory)
    if directory.parent.parent.name != "claude-code-sessions":
        raise ValueError("Choose an account directory inside claude-code-sessions.")
    if not all(ID.fullmatch(p) for p in directory.parts[-2:]):
        raise ValueError("Unrecognized account directory layout.")
    result = {}
    for path in sorted(directory.glob("local_*.json")):
        no_links(path)
        data = read_json(path)
        sid = data.get("sessionId", "")
        if not re.fullmatch("local_" + UUID, sid) or path.stem != sid:
            raise ValueError("Unrecognized Desktop session record: " + path.name)
        if data.get("envScopeId") not in (None, "builtin_local"):
            continue
        if isinstance(data.get("cwd"), str) and ID.fullmatch(str(data.get("cliSessionId", ""))):
            result[sid] = (path, data)
    return result


def ensure_idle(lines=None):
    if lines is None:
        lines = subprocess.run(["/bin/ps", "-axo", "command="], check=True,
                               capture_output=True, text=True).stdout.splitlines()
    for line in lines:
        # Includes Desktop-owned Code engines. Terminal CLI may remain open but
        # must not be writing any source/destination history during the copy.
        if re.search(r"/Contents/MacOS/Claude(?:\s|$)", line) or "--claude-desktop" in line:
            raise ValueError("Claude Desktop is running. Finish your tasks and quit it yourself, then retry. Nothing was copied.")


def transcript(home, config, cwd, sid):
    if not ID.fullmatch(sid):
        raise ValueError("Invalid CLI session ID.")
    encoded = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    candidate = config / "projects" / encoded / (sid + ".jsonl")
    if not candidate.is_file():
        # Accommodate changed path encoding, but never choose an ambiguous file.
        matches = list((config / "projects").glob("*/" + sid + ".jsonl"))
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous transcript " + sid + ". Check --config-dir.")
        candidate = matches[0]
    no_links(candidate)
    return candidate


def records(path):
    with path.open() as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError:
                raise ValueError("Invalid JSONL: " + path.name + " line " + str(number)) from None
            if not isinstance(value, dict):
                raise ValueError("Unsupported transcript record in " + path.name)
            yield value


def plan_copy(home, source, target, project, ids=None, config=None):
    config = config or home / ".claude"
    no_links(config)
    no_links(source)
    no_links(target)
    source, target = source.resolve(), target.resolve()
    if source == target:
        raise ValueError("Source and destination must be different account directories.")
    source_sessions = read_sessions(source)
    if not read_sessions(target):
        raise ValueError("Sign into the destination and create one local Code chat first.")
    selected = [(p, d) for p, d in source_sessions.values()
                if not d.get("isArchived", False) and d["cwd"] == str(project)
                and (not ids or d["sessionId"] in ids)]
    if not selected:
        raise ValueError("No matching unarchived local Code chats.")
    if ids and set(ids) != {d["sessionId"] for _, d in selected}:
        raise ValueError("Some selected IDs do not belong to this project's visible local chats.")
    project_path = Path(project).expanduser().resolve()
    if not project_path.is_dir():
        raise ValueError("The working folder no longer exists: " + str(project_path))
    if "/scratch-workspaces/" in str(project_path):
        raise ValueError("Scratch workspaces are not supported. Choose a regular local project folder.")
    files, histories = {}, {}
    for metadata_path, data in selected:
        files[metadata_path] = digest(metadata_path)
        chain = [data["cliSessionId"], *data.get("priorCliSessionIds", [])]
        if len(set(chain)) != len(chain):
            raise ValueError("Duplicate IDs in the history chain.")
        for sid in chain:
            path = transcript(home, config, data["cwd"], sid)
            for _ in records(path):  # Validate without retaining large transcripts.
                pass
            files[path] = digest(path)
            histories[sid] = path
            associated = path.with_suffix("")
            if associated.exists():
                no_links(associated)
                for item in sorted(associated.rglob("*")):
                    no_links(item)
                    if item.is_file():
                        files[item] = digest(item)
    snapshot = hashlib.sha256(json.dumps({str(k): v for k, v in files.items()}, sort_keys=True).encode()).hexdigest()
    receipt_dir = target / ".chat-copy-receipts"
    if receipt_dir.exists():
        no_links(receipt_dir)
        for receipt in receipt_dir.glob("*.json"):
            previous = read_json(receipt)
            if previous.get("snapshot") == snapshot:
                raise ValueError("This exact snapshot was already copied. No duplicate chats created.")
    return {"source": source, "target": target, "selected": selected, "histories": histories,
            "files": files, "snapshot": snapshot, "all_sessions": source_sessions}


def remap_record(value, mapping):
    """Rewrite structural IDs; preserve users' messages, code, UUIDs and tool output."""
    if isinstance(value, dict):
        return {key: remap_id(item, mapping) if key in REFERENCE_FIELDS and isinstance(item, str)
                else remap_record(item, mapping) if key not in {"message", "content", "data"} else item
                for key, item in value.items()}
    if isinstance(value, list):
        return [remap_record(item, mapping) for item in value]
    return value


def remap_id(value, mapping):
    prefix = "local_" if value.startswith("local_") else ""
    raw = value[len(prefix):]
    if ID.fullmatch(raw):
        return prefix + mapping.setdefault(raw, str(uuid.uuid4()))
    return value


def closest_parent(data, plan, desktop_map):
    parent = data.get("forkedFromSessionId")
    visited = set()
    while parent:
        if parent in visited:
            raise ValueError("Cycle in fork ancestry.")
        visited.add(parent)
        if parent in desktop_map:
            return desktop_map[parent]
        ancestor = plan["all_sessions"].get(parent)
        parent = ancestor[1].get("forkedFromSessionId") if ancestor else None
    return None


def apply_copy(plan, idle_check=ensure_idle):
    idle_check()
    target = plan["target"]
    mapping = {sid: str(uuid.uuid4()) for sid in plan["histories"]}
    desktop_map = {data["sessionId"]: "local_" + str(uuid.uuid4()) for _, data in plan["selected"]}
    created = []
    receipt_dir = target / ".chat-copy-receipts"
    no_links(receipt_dir)
    receipt_dir.mkdir(mode=0o700, exist_ok=True)
    lock_path = receipt_dir / ".lock"
    no_links(lock_path)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for receipt in receipt_dir.glob("*.json"):
            if read_json(receipt).get("snapshot") == plan["snapshot"]:
                raise ValueError("This snapshot was already copied by another process.")
        with tempfile.TemporaryDirectory(prefix="stage-", dir=receipt_dir) as temporary:
            stage = Path(temporary)
            staged = []
            for sid, original in plan["histories"].items():
                destination = original.with_name(mapping[sid] + ".jsonl")
                staged_file = stage / (mapping[sid] + ".jsonl")
                with staged_file.open("w") as stream:
                    for record in records(original):
                        stream.write(json.dumps(remap_record(record, mapping), ensure_ascii=False) + "\n")
                staged.append((staged_file, destination))
                associated = original.with_suffix("")
                if associated.exists():
                    for item in sorted(associated.rglob("*")):
                        no_links(item)
                        if not item.is_file():
                            continue
                        relative = item.relative_to(associated)
                        output = stage / mapping[sid] / relative
                        output.parent.mkdir(parents=True, exist_ok=True)
                        if item.suffix == ".jsonl":
                            with output.open("w") as stream:
                                for record in records(item):
                                    stream.write(json.dumps(remap_record(record, mapping), ensure_ascii=False) + "\n")
                        elif item.suffix == ".json":
                            output.write_text(json.dumps(remap_record(read_json(item), mapping), ensure_ascii=False))
                        else:
                            shutil.copyfile(item, output)
                        staged.append((output, destination.with_suffix("") / relative))
            for _, data in plan["selected"]:
                # Do not import grants, MCP secrets, browser tab IDs, spawn state,
                # permission bypasses or old desktop prompt snapshots.
                copied = {key: data[key] for key in SAFE_FIELDS if key in data}
                copied.update(sessionId=desktop_map[data["sessionId"]],
                              cliSessionId=mapping[data["cliSessionId"]],
                              priorCliSessionIds=[mapping[sid] for sid in data.get("priorCliSessionIds", [])],
                              envScopeId="builtin_local", isArchived=False, titleSource="user")
                parent = closest_parent(data, plan, desktop_map)
                if parent:
                    copied["forkedFromSessionId"] = parent
                output = stage / (copied["sessionId"] + ".json")
                output.write_text(json.dumps(copied, indent=2, ensure_ascii=False) + "\n")
                staged.append((output, target / output.name))
            for original, expected in plan["files"].items():
                if digest(original) != expected:
                    raise ValueError("Source changed while copying. Retry after all writers stop.")
            idle_check()
            for _, destination in staged:
                no_links(destination)
                if destination.exists():
                    raise ValueError("Destination collision. Nothing was replaced.")
            made_dirs = []
            receipt_path = None
            receipt_created = False
            try:
                # Metadata is committed last, after all transcript files exist.
                for staged_file, destination in staged:
                    missing = []
                    parent = destination.parent
                    while not parent.exists():
                        missing.append(parent)
                        parent = parent.parent
                    for parent in reversed(missing):
                        parent.mkdir(mode=0o700)
                        made_dirs.append(parent)
                    with destination.open("xb") as stream:
                        created.append(destination)
                        os.chmod(destination, 0o600)
                        stream.write(staged_file.read_bytes())
                for original, expected in plan["files"].items():
                    if digest(original) != expected:
                        raise ValueError("Source changed during commit.")
                receipt = {"snapshot": plan["snapshot"], "sessions": desktop_map,
                           "files": {str(path): digest(path) for path in created}}
                receipt_path = receipt_dir / (str(uuid.uuid4()) + ".json")
                with receipt_path.open("x") as stream:
                    receipt_created = True
                    os.chmod(receipt_path, 0o600)
                    json.dump(receipt, stream, indent=2)
                return receipt_path
            except BaseException:
                if receipt_created:
                    receipt_path.unlink(missing_ok=True)
                for path in reversed(created):
                    path.unlink(missing_ok=True)
                for directory in reversed(made_dirs):
                    directory.rmdir()
                raise


def choose(label, items, display=str):
    if not items:
        raise ValueError("No choices for " + label + ". Sign in and create a local Code chat first.")
    print("\n" + label)
    for number, item in enumerate(items, 1):
        print(str(number) + ". " + display(item))
    answer = input("Number: ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(items):
        raise ValueError("Invalid selection. Nothing changed.")
    return items[int(answer) - 1]


def account_label(directory):
    sessions = [d for _, d in read_sessions(directory).values() if not d.get("isArchived")]
    sessions.sort(key=lambda d: d.get("lastActivityAt", d.get("createdAt", 0)), reverse=True)
    examples = ", ".join(d.get("title", "Untitled")[:45] for d in sessions[:2])
    return directory.parents[2].name + " | " + str(len(sessions)) + " chats | " + examples + " | " + directory.name[:8]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Source account directory from --list")
    parser.add_argument("--target", type=Path, help="Destination account directory from --list")
    parser.add_argument("--project", type=Path, help="Existing working folder, kept the same")
    parser.add_argument("--session", action="append", help="Only copy this Desktop session ID; repeat as needed")
    parser.add_argument("--config-dir", type=Path, help="Shared Claude Code config directory; default ~/.claude")
    parser.add_argument("--list", action="store_true", help="List account directories and chat titles without writing")
    parser.add_argument("--apply", action="store_true", help="Copy the previewed selection")
    parser.add_argument("--home", type=Path, default=Path.home(), help=argparse.SUPPRESS)
    args = parser.parse_args()
    home = args.home.expanduser().resolve()
    available = accounts(home)
    if args.list:
        for directory in available:
            print("\n" + str(directory))
            for _, session in read_sessions(directory).values():
                if not session.get("isArchived"):
                    print("  " + session["sessionId"] + "  " + session.get("title", "Untitled") + "  " + session["cwd"])
        return
    interactive = not any([args.source, args.target, args.project])
    if interactive:
        if not sys.stdin.isatty():
            raise ValueError("Use a terminal for the guided picker, or provide --source --target --project.")
        args.source = choose("Copy from", available, account_label)
        args.target = choose("Copy into", [p for p in available if p != args.source],
                             account_label)
        projects = sorted({d["cwd"] for _, d in read_sessions(args.source).values() if not d.get("isArchived")})
        args.project = Path(choose("Project folder (all its visible chats will be copied)", projects))
    if not all([args.source, args.target, args.project]):
        raise ValueError("Provide --source, --target and --project together.")
    for path in [args.source, args.target]:
        no_links(path.expanduser())
    plan = plan_copy(home, args.source.expanduser(), args.target.expanduser(),
                     args.project.expanduser().resolve(), args.session,
                     args.config_dir.expanduser().resolve() if args.config_dir else None)
    print("\nCopy " + str(len(plan["selected"])) + " chats into " + str(plan["target"]))
    for _, data in plan["selected"]:
        print("  " + data.get("title", "Untitled"))
    print("Working folder: " + str(args.project))
    print("New session IDs. Originals stay in place. Project files remain shared.")
    if interactive and not args.apply:
        args.apply = input("Quit Claude Desktop yourself when ready. Type COPY to proceed: ") == "COPY"
    if args.apply:
        receipt = apply_copy(plan)
        print("Copied. Reopen the destination profile. Local receipt: " + str(receipt))
    else:
        print("Preview only. No files written. Add --apply to copy.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, EOFError) as error:
        print("Error: " + str(error), file=sys.stderr)
        sys.exit(1)
