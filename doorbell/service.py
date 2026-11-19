"""`doorbell service`: run doorbelld (and optionally a dispatcher) as a per-user service.

Linux uses systemd --user units, macOS launchd agents, Windows Task Scheduler tasks that start
at logon. macOS and Windows are not covered by CI yet.
"""
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .agentkit import doorbell_argv

SYSTEMD_DIR = Path.home() / ".config/systemd/user"
LAUNCHD_DIR = Path.home() / "Library/LaunchAgents"


def units(dispatch, folder, command):
    exe = doorbell_argv()
    out = {"doorbelld": {"desc": "Agent Doorbell watcher (polls Slack only while agents are registered)",
                         "argv": exe + ["daemon"], "cwd": None, "after": None}}
    if dispatch:
        if not command:
            raise SystemExit("error: --dispatch needs the agent command after --, e.g. "
                             "-- claude -p \"/agent-doorbell:handle\"")
        if not folder:
            raise SystemExit("error: --dispatch needs --dir <agent folder>")
        folder = str(Path(folder).expanduser().resolve())
        exe_path = shutil.which(command[0])
        if exe_path:
            command = [exe_path] + command[1:]
        out[f"doorbell-dispatch-{dispatch}"] = {
            "desc": f"Agent Doorbell dispatcher for agent {dispatch}",
            "argv": exe + ["dispatch", "--name", dispatch, "--dir", folder, "--"] + command,
            "cwd": folder, "after": "doorbelld.service"}
    return out


def systemd_unit(u):
    lines = ["[Unit]", f"Description={u['desc']}", "After=network-online.target"]
    if u["after"]:
        lines += [f"After={u['after']}", f"Wants={u['after']}"]
    lines += ["", "[Service]", "ExecStart=" + shlex.join(u["argv"])]
    if u["cwd"]:
        lines.append(f"WorkingDirectory={u['cwd']}")
    # The agent CLI (claude, codex, ...) is usually on the login shell's PATH, not systemd's.
    lines += [f"Environment=PATH={os.environ.get('PATH', '/usr/bin:/bin')}",
              "Restart=on-failure", "RestartSec=5", "", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def launchd_plist(label, u):
    args = "".join(f"<string>{a}</string>" for a in u["argv"])
    cwd = f"<key>WorkingDirectory</key><string>{u['cwd']}</string>" if u["cwd"] else ""
    log = Path.home() / f"Library/Logs/{label}.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{label}</string>
<key>ProgramArguments</key><array>{args}</array>{cwd}
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{os.environ.get('PATH', '')}</string></dict>
<key>LimitLoadToSessionType</key><array><string>Aqua</string><string>Background</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def windows_task(name, u):
    """A Task Scheduler task that starts at logon and restarts on failure. Runs hidden via
    pythonw when available so no console window stays open."""
    argv = list(u["argv"])
    if argv and argv[0].lower().endswith("python.exe"):
        pyw = argv[0][:-10] + "pythonw.exe"
        if os.path.exists(pyw):
            argv[0] = pyw
    exe, args = argv[0], subprocess.list2cmdline(argv[1:])
    esc = lambda t: t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    cwd = f"<WorkingDirectory>{esc(u['cwd'])}</WorkingDirectory>" if u["cwd"] else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>{esc(u['desc'])}</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>{esc(exe)}</Command><Arguments>{esc(args)}</Arguments>{cwd}</Exec></Actions>
</Task>
"""


def run(cmd, quiet=False):
    if not quiet:
        print("  $ " + shlex.join(cmd))
    return subprocess.run(cmd, check=False, capture_output=quiet).returncode


def launchd_domains():
    """The desktop session when someone is logged in at the Mac, else the user's background
    session (SSH, a headless Mac). The plist allows both session types."""
    uid = os.getuid()
    return [f"gui/{uid}", f"user/{uid}"]


def launchd_unload(label):
    for dom in launchd_domains():
        run(["launchctl", "bootout", f"{dom}/{label}"], quiet=True)


def launchd_load(label, path):
    import time
    launchd_unload(label)
    for attempt in range(20):   # bootout finishes asynchronously: retry while the old one stops
        for dom in launchd_domains():
            if run(["launchctl", "bootstrap", dom, str(path)], quiet=True) == 0:
                print(f"  loaded {label} in launchd domain {dom}")
                return True
        time.sleep(0.5)
    print(f"error: launchd didn't load {path}. Run `doorbell daemon` in a terminal instead, or log in "
          f"at the Mac once and run `doorbell service install` again.", file=sys.stderr)
    return False


def wait_for_daemon(seconds=15):
    import time
    from . import cli
    for _ in range(int(seconds * 2)):
        try:
            cli.call("status")
            return True
        except (cli.DaemonDown, SystemExit, OSError):
            time.sleep(0.5)
    return False


def service(action, dispatch, folder, command):
    system = platform.system()
    if system == "Windows":
        return service_windows(action, dispatch, folder, command)
    mac = system == "Darwin"
    if not mac and not shutil.which("systemctl"):
        raise SystemExit("error: no systemd found. Run `doorbell daemon` under your own supervisor instead.")
    us = units(dispatch, folder, command) if action != "uninstall" else \
        {"doorbelld": None, **({f"doorbell-dispatch-{dispatch}": None} if dispatch else {})}
    for name, u in us.items():
        if mac:
            label = f"dev.agent-doorbell.{name}"
            path = LAUNCHD_DIR / f"{label}.plist"
            text = launchd_plist(label, u) if u else None
        else:
            path = SYSTEMD_DIR / f"{name}.service"
            text = systemd_unit(u) if u else None
        if action == "print":
            print(f"# {path}\n{text}")
            continue
        if action == "uninstall":
            if dispatch and name == "doorbelld":
                continue   # removing a dispatcher keeps the shared daemon
            if mac:
                launchd_unload(f"dev.agent-doorbell.{name}")
            else:
                run(["systemctl", "--user", "disable", "--now", f"{name}.service"])
            path.unlink(missing_ok=True)
            print(f"removed {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"wrote {path}")
        if mac:
            if not launchd_load(f"dev.agent-doorbell.{name}", path):
                raise SystemExit(1)
        else:
            run(["systemctl", "--user", "daemon-reload"])
            run(["systemctl", "--user", "enable", "--now", f"{name}.service"])
            run(["systemctl", "--user", "restart", f"{name}.service"])   # pick up config changes
    if action == "install":
        if wait_for_daemon():
            print("doorbelld is running.")
        else:
            raise SystemExit("error: the service was installed but doorbelld didn't start within 15 s; check "
                             + ("~/Library/Logs/dev.agent-doorbell.doorbelld.log" if mac else
                                "`journalctl --user -u doorbelld`"))
        if not mac:
            print("Tip: `loginctl enable-linger $USER` keeps user services running when you are logged out.")


def service_windows(action, dispatch, folder, command):
    us = units(dispatch, folder, command) if action != "uninstall" else \
        {"doorbelld": None, **({f"doorbell-dispatch-{dispatch}": None} if dispatch else {})}
    task_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "doorbell" / "tasks"
    for name, u in us.items():
        task = f"AgentDoorbell\\{name}"
        if action == "print":
            print(f"# Task Scheduler: {task}\n{windows_task(name, u)}")
            continue
        if action == "uninstall":
            if dispatch and name == "doorbelld":
                continue
            run(["schtasks", "/End", "/TN", task])
            run(["schtasks", "/Delete", "/TN", task, "/F"])
            continue
        task_dir.mkdir(parents=True, exist_ok=True)
        xml = task_dir / f"{name}.xml"
        xml.write_text(windows_task(name, u), encoding="utf-16")
        run(["schtasks", "/End", "/TN", task])
        run(["schtasks", "/Create", "/TN", task, "/XML", str(xml), "/F"])
        run(["schtasks", "/Run", "/TN", task])
    if action == "install":
        if not wait_for_daemon():
            raise SystemExit("error: the task was created but doorbelld didn't start within 15 s; check "
                             "Task Scheduler (AgentDoorbell folder) or run `doorbell daemon` in a terminal")
        print("doorbelld is running. The tasks start at every logon. Logs: `doorbell log` and `doorbell status`.")
