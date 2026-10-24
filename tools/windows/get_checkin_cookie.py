"""Export the SESSION Cookie from the user's open UESTC check-in page.

Requires Windows, a signed-in WeChat mini-program, and frida 16.2.1.
Reads the selected mini-program host only; makes no network requests.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
SESSION_PATTERN = re.compile(r"[A-Za-z0-9._~%-]{16,512}", re.ASCII)
SESSION_AGENT = r"""
'use strict';

rpc.exports.read = function () {
  const sessions = new Set();
  const marker = 'https://jzsz.uestc.edu.cn/';
  const pattern = Array.from(marker, c => c.charCodeAt(0).toString(16)).join(' ');
  const ranges = Process.enumerateRanges({protection: 'rw-', coalesce: true})
    .filter(r => !r.file && r.size <= 134217728);
  for (const range of ranges) {
    try {
      for (const match of Memory.scanSync(range.base, range.size, pattern)) {
        const available = range.base.add(range.size).sub(match.address).toUInt32();
        const bytes = new Uint8Array(match.address.readByteArray(Math.min(1024, available)));
        let text = '';
        for (const byte of bytes) text += String.fromCharCode(byte);
        const url = text.match(/^https:\/\/jzsz\.uestc\.edu\.cn\/daily-checkin-h5(?:-test)?\/index\.html\?([^\x00\s"<>]+)/);
        if (!url) continue;
        const query = url[1].split('#')[0];
        const session = query.match(/(?:^|&)sessionId=([A-Za-z0-9._~%-]{16,512})(?:&|$)/);
        const ticket = query.match(/(?:^|&)checkinTicket=[A-Za-z0-9_-]{43}(?:&|$)/);
        if (session && ticket) sessions.add(decodeURIComponent(session[1]));
      }
    } catch (_) {}
  }
  return Array.from(sessions);
};
"""


def positive_pid(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("PID must be positive.")
    return number


def discover_host(login_pid=None):
    if os.name != "nt":
        raise RuntimeError("Run this exporter on Windows.")
    command = (
        "@(Get-CimInstance Win32_Process -Filter \"Name = 'WeChatAppEx.exe'\" | "
        "Where-Object { $_.CommandLine -like '*--utility-sub-type=flue.mojom.ILinkServiceHost*' } | "
        "Select-Object ProcessId,ParentProcessId) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, check=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    hosts = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(hosts, dict):
        hosts = [hosts]
    if login_pid is not None:
        hosts = [host for host in hosts if host["ProcessId"] == login_pid]
    if not hosts:
        raise RuntimeError("Open the signed-in mini-program and its check-in page first.")
    if len(hosts) != 1:
        choices = [
            {"pid": host["ProcessId"], "hostPid": host["ParentProcessId"]}
            for host in hosts
        ]
        raise RuntimeError("Select one host with --pid PID: " + json.dumps(choices))
    return int(hosts[0]["ParentProcessId"])


def read_sessions(host_pid):
    try:
        import frida
    except ImportError:
        raise RuntimeError(
            "Install dependency: py -3.11 -m pip install frida==16.2.1"
        ) from None
    session = frida.attach(host_pid)
    try:
        script = session.create_script(SESSION_AGENT)
        script.load()
        return script.exports_sync.read()
    finally:
        session.detach()


def make_cookie(sessions):
    if not isinstance(sessions, list):
        raise ValueError("Unexpected session extraction result.")
    if any(not isinstance(value, str) for value in sessions):
        raise ValueError("Unexpected session value.")
    values = set(sessions)
    if not values:
        raise RuntimeError(
            "No check-in session found. Open the mini-program's check-in page and retry."
        )
    if len(values) != 1:
        raise RuntimeError(
            f"Found {len(values)} different cached sessions; selection is ambiguous. "
            "Close other mini-programs, reopen this check-in page, and retry."
        )
    value = next(iter(values))
    if SESSION_PATTERN.fullmatch(value) is None:
        raise ValueError("The extracted session has an unexpected format.")
    return "SESSION=" + value


def save_cookie(path, cookie):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(cookie + "\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--pid", type=positive_pid, help="ILinkServiceHost PID")
    selection.add_argument("--host-pid", type=positive_pid, help="Mini-program parent host PID")
    parser.add_argument("--output", type=Path, default=ROOT / "checkin-cookie.txt")
    args = parser.parse_args()
    try:
        host_pid = args.host_pid if args.host_pid is not None else discover_host(args.pid)
        cookie = make_cookie(read_sessions(host_pid))
        output = args.output.resolve()
        save_cookie(output, cookie)
        print(json.dumps({
            "ok": True, "savedTo": str(output), "hostPid": host_pid,
            "source": "open-checkin-url", "cookieName": "SESSION",
            "cookieLength": len(cookie), "networkRequests": 0,
            "validityChecked": False,
        }, ensure_ascii=True))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Interrupted."}), file=sys.stderr)
        return 130
    except Exception as error:
        print(json.dumps({
            "ok": False, "error": str(error) or type(error).__name__,
        }, ensure_ascii=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
