"""Invoke one native mini-program login request on WeChat's own task sequence."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import threading

import frida

ROOT = Path(__file__).resolve().parent


def detect_host_pid():
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'WeChatAppEx.exe'\" | "
        "Where-Object { $_.CommandLine -like '*--utility-sub-type=flue.mojom.ILinkServiceHost*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, text=True, check=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    pids = [int(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    if len(pids) != 1:
        raise RuntimeError(
            f'Expected one WeChat mini-program login host, found {pids}. '
            'Open the mini-program first; use --pid PID when multiple hosts are running.'
        )
    return pids[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', type=int)
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    idle = threading.Event()
    log_path = ROOT / ('active-login-probe.jsonl' if args.probe else 'active-login-events.jsonl')
    result_path = ROOT / 'active-login-code.json'
    session = frida.attach(args.pid if args.pid is not None else detect_host_pid())
    script = session.create_script((ROOT / 'active_login_code.js').read_text(encoding='utf-8'))

    def on_message(message, data):
        payload = message.get('payload', message)
        with log_path.open('a', encoding='utf-8') as log:
            log.write(json.dumps(payload, ensure_ascii=False) + '\n')
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        if payload.get('event') == 'active.login-code':
            result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        if payload.get('event') == 'active.idle':
            idle.set()

    script.on('message', on_message)
    try:
        script.load()
        print(json.dumps(script.exports_sync.trigger(args.probe)), flush=True)
        if not idle.wait(45):
            print(json.dumps({'status': 'pending', 'time': datetime.now().isoformat()}), flush=True)
            # Keep callback code and data alive until WeChat releases their ownership.
            idle.wait()
    finally:
        session.detach()


if __name__ == '__main__':
    main()
