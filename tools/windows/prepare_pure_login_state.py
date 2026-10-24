"""Prepare pure-HTTP session state from the explicitly captured login trace."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pure_request_login import extract_code, parse_request, session_inputs, unwrap_business

ROOT = Path(__file__).resolve().parent
TRACE = ROOT / 'login-request-trace'


def encoded(data):
    return base64.b64encode(data).decode('ascii')


def main():
    events = json.loads((TRACE / 'events.json').read_text(encoding='utf-8'))
    expands = [event['detail'] for event in events if event.get('event') == 'trace.mmtls-expand']
    secrets = {event['secretHex'] for event in expands}
    if len(secrets) != 1:
        raise ValueError('Expected one matching early/handshake session secret')
    inner = [event['detail'] for event in events if event.get('event') == 'trace.mmtls-inner-gcm']
    if len(inner) != 1:
        raise ValueError('Expected one business response key capture')
    login = next(event['detail'] for event in events if event.get('event') == 'active.login-code')
    request_event = next(event for event in events if event.get('event') == 'trace.main-send')
    response_event = next(event for event in events if event.get('event') == 'trace.main-recv')
    request = (TRACE / request_event['binaryFile']).read_bytes()
    response = (TRACE / response_event['binaryFile']).read_bytes()
    state = {
        'version': 1, 'appId': login['appId'],
        'preparedAt': datetime.now(timezone.utc).isoformat(),
        'mode': 'replay of an authenticated session; requires refresh on expiry',
        'httpRequest': encoded(request), 'capturedHttpResponse': encoded(response),
        'mmtlsSecret': encoded(bytes.fromhex(expands[0]['secretHex'])),
        'businessResponseKey': encoded(bytes.fromhex(inner[0]['keyHex'])),
        'businessResponseAad': encoded(bytes.fromhex(inner[0]['aadHex'])),
        'capturedCodeHash': hashlib.sha256(login['code'].encode()).hexdigest(),
    }
    inputs = session_inputs(state, parse_request(request)[2])
    actual = extract_code(unwrap_business(state, response.partition(b'\r\n\r\n')[2], inputs))
    if actual != login['code']:
        raise ValueError('Offline decoded code does not match the native callback')
    output = ROOT / 'pure-login-state.json'
    output.write_text(json.dumps(state, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'offlineCodeMatchesNative': True, 'stateFile': str(output)}))


if __name__ == '__main__':
    main()
