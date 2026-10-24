"""Trace one explicitly triggered login and persist its request construction."""
import base64
import json
from pathlib import Path
import subprocess
import threading
import time

import frida

from active_login_code import detect_host_pid

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'login-request-trace'


def main():
    OUTPUT.mkdir(exist_ok=True)
    events = []
    idle = threading.Event()
    sequence = 0

    def message(message, data):
        nonlocal sequence
        payload = message.get('payload', message)
        if data:
            sequence += 1
            name = f'{sequence:02d}-{payload.get("event", "raw")}.bin'
            (OUTPUT / name).write_bytes(data)
            payload['binaryFile'] = name
            payload['base64'] = base64.b64encode(data).decode()
        events.append(payload)
        event = payload.get('event')
        if event == 'active.idle':
            idle.set()
        print(json.dumps({
            'event': event or message.get('type'),
            'detail': (
                {'capturedLocally': True, 'label': payload.get('detail', {}).get('label')}
                if event and event.startswith('trace.mmtls-')
                else payload.get('detail') if event != 'active.login-code' else {'codeReceived': True}
            ),
            'binaryFile': payload.get('binaryFile'),
        }, ensure_ascii=True), flush=True)

    pid = detect_host_pid()
    command = (
        f'$p=Get-CimInstance Win32_Process -Filter "ProcessId = {pid}";'
        '$h=Get-CimInstance Win32_Process -Filter ("ProcessId = "+$p.ParentProcessId);'
        '$h.ParentProcessId'
    )
    parent = int(subprocess.check_output(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        creationflags=subprocess.CREATE_NO_WINDOW, text=True).strip())
    network_session = frida.attach(parent)
    session = frida.attach(pid)
    session.on('detached', lambda *args: idle.set())
    try:
        tracer = session.create_script((ROOT / 'trace_login_request.js').read_text(encoding='utf-8'))
        tracer.on('message', message)
        tracer.load()
        wire = network_session.create_script((ROOT / 'trace_login_wire.js').read_text(encoding='utf-8'))
        wire.on('message', message)
        wire.load()
        wire.exports_sync.enable(True)
        login = session.create_script((ROOT / 'active_login_code.js').read_text(encoding='utf-8'))
        login.on('message', message)
        login.load()
        login.exports_sync.trigger(False)
        if not idle.wait(45):
            print('Pending native callbacks; waiting for release.', flush=True)
            idle.wait()
        time.sleep(0.1)
        tracer.exports_sync.finish()
        wire.exports_sync.enable(False)
    finally:
        (OUTPUT / 'events.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
        session.detach()
        network_session.detach()
    print('Saved:', OUTPUT)


if __name__ == '__main__':
    main()
