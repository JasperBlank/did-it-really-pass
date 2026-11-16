"""Command line: did-it-pass <command>.

  scan            check your past Claude Code sessions (asks before spending)
  context ID      show one flagged episode with the conversation before it
  install-hook    check every turn live, as a Claude Code Stop hook
  uninstall-hook  remove that hook
  log             show the live monitor's recent verdicts
  hook            (run by Claude Code, not by you)
"""
import json
import shutil
import sys
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
MARKERS = ("did_it_pass", "agent-monitor/live.py")  # commands this tool has installed, now or in the past


def _hook_command():
    return f'"{Path(sys.executable).as_posix()}" -m did_it_pass hook'


def _without_ours(settings):
    stops = []
    for group in settings.get("hooks", {}).get("Stop", []):
        kept = [h for h in group.get("hooks", []) if not any(m in h.get("command", "") for m in MARKERS)]
        if kept:
            stops.append(dict(group, hooks=kept))
    return stops


def _write(settings, stops):
    hooks = dict(settings.get("hooks", {}))
    if stops:
        hooks["Stop"] = stops
    else:
        hooks.pop("Stop", None)
    settings = dict(settings)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    if SETTINGS.exists():
        shutil.copy(SETTINGS, SETTINGS.with_suffix(".json.bak"))
    SETTINGS.parent.mkdir(exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def install_hook():
    settings = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    command = _hook_command()
    stops = _without_ours(settings) + [{"hooks": [{
        "type": "command", "command": command, "timeout": 180, "statusMessage": "did-it-pass is checking this turn"}]}]
    _write(settings, stops)
    print(f"Installed in {SETTINGS} (previous version saved as settings.json.bak):\n  {command}")
    if any(part in sys.executable.replace("\\", "/") for part in ("/uv/cache/", "archive-v0", "/uvx/")):
        print("Warning: this Python belongs to a temporary uvx environment and may disappear.\n"
              "For the live hook, install the tool permanently first:  uv tool install <source>  or  pipx install <source>")
    print("Every finished turn with 3 or more tool calls is now checked. Verdicts: did-it-pass log")


def uninstall_hook():
    if not SETTINGS.exists():
        print("No settings file, nothing to remove.")
        return
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    _write(settings, _without_ours(settings))
    print(f"Removed the did-it-pass hook from {SETTINGS}.")


def show_log():
    from .paths import DATA
    path = DATA / "live.jsonl"
    if not path.exists():
        print(f"No live verdicts yet ({path}).")
        return
    for line in path.read_text(encoding="utf-8").splitlines()[-20:]:
        e = json.loads(line)
        final = e.get("opus") or e.get("haiku") or {}
        verdict = "ERROR " + e["error"] if "error" in e else final.get("failure", "?")
        print(f"{e.get('at', '')}  {verdict:<15} {e.get('seconds', 0):>5}s  {(e.get('request') or '')[:70]}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    command, sys.argv = sys.argv[1], [f"did-it-pass {sys.argv[1]}"] + sys.argv[2:]
    if command == "scan":
        from .scan import main as run
    elif command == "context":
        from .context import main as run
    elif command == "hook":
        from .live import main as run
    elif command == "install-hook":
        run = install_hook
    elif command == "uninstall-hook":
        run = uninstall_hook
    elif command == "log":
        run = show_log
    else:
        sys.exit(f"Unknown command {command!r}.\n{__doc__}")
    run()


if __name__ == "__main__":
    main()
