"""Get a mini-program login code by replaying a locally prepared session request.

Runtime dependencies: Python 3.10+, cryptography. No Frida, process attachment,
native DLL calls, or WeChat RPCs are used by this program. The state file must
first be prepared from a successful login of the user's own WeChat session.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import re
import time
import zlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent
HOST = 'szextshort.weixin.qq.com'


def hkdf_expand(secret, info, size):
    output = b''
    block = b''
    counter = 1
    while len(output) < size:
        block = hmac.new(secret, block + info + bytes([counter]), hashlib.sha256).digest()
        output += block
        counter += 1
    return output[:size]


def records(body):
    output = []
    offset = 0
    while offset < len(body):
        if offset + 5 > len(body):
            raise ValueError('Truncated MMTLS record header')
        header = body[offset:offset + 5]
        end = offset + 5 + int.from_bytes(header[3:5], 'big')
        if end > len(body) or header[1:3] != b'\xf1\x04':
            raise ValueError('Unexpected or truncated MMTLS record')
        output.append((header, body[offset + 5:end]))
        offset = end
    return output


def decrypt_record(key_iv, sequence, header, data):
    key, iv = key_iv[:16], key_iv[16:]
    counter = sequence.to_bytes(8, 'big')
    nonce = bytes(x ^ y for x, y in zip(iv, b'\0' * 4 + counter))
    return AESGCM(key).decrypt(nonce, data, counter + header)


def parse_request(raw):
    header, separator, body = raw.partition(b'\r\n\r\n')
    if not separator:
        raise ValueError('Missing captured HTTP headers')
    lines = header.decode('ascii').split('\r\n')
    method, path, version = lines[0].split(' ')
    headers = dict(line.split(': ', 1) for line in lines[1:])
    if method != 'POST' or version != 'HTTP/1.1' or headers.get('Host') != HOST:
        raise ValueError('Unexpected captured login destination')
    if not re.fullmatch(r'/mmtls/[a-fA-F0-9]{8}', path):
        raise ValueError('Unexpected captured MMTLS path')
    if int(headers.get('Content-Length', '-1')) != len(body):
        raise ValueError('Captured HTTP request is incomplete')
    return path, headers, body


def session_inputs(state, request_body):
    request_records = records(request_body)
    if len(request_records) != 4:
        raise ValueError('Expected four short-link request records')
    hello = request_records[0][1]
    secret = base64.b64decode(state['mmtlsSecret'], validate=True)
    if len(secret) != 32:
        raise ValueError('Invalid captured MMTLS secret')
    early = hkdf_expand(secret, b'early data key expansion' + hashlib.sha256(hello).digest(), 28)
    header, data = request_records[1]
    extensions = decrypt_record(early, 1, header, data)
    return secret, hello, extensions


def unwrap_business(state, response_body, inputs):
    secret, client_hello, extensions = inputs
    response_records = records(response_body)
    if len(response_records) != 4 or response_records[0][0][0] != 22:
        raise ValueError('Unexpected MMTLS response; the captured session may require refreshing')
    server_hello = response_records[0][1]
    transcript = hashlib.sha256(client_hello + extensions + server_hello).digest()
    key_iv = hkdf_expand(secret, b'handshake key expansion' + transcript, 28)
    plaintext = [
        (header[0], decrypt_record(key_iv, index, header, data))
        for index, (header, data) in enumerate(response_records[1:], 1)
    ]
    messages = [value for kind, value in plaintext if kind == 23]
    if len(messages) != 1:
        raise ValueError('Expected one authenticated business response')
    packet = messages[0]
    if len(packet) < 3 or packet[0] != 0xbf:
        raise ValueError('Unexpected WeChat business packet')
    header_length = packet[1] >> 2
    encrypted = packet[header_length:]
    if len(encrypted) < 28:
        raise ValueError('Truncated business GCM packet')
    key = base64.b64decode(state['businessResponseKey'], validate=True)
    aad = base64.b64decode(state['businessResponseAad'], validate=True)
    compressed = AESGCM(key).decrypt(
        encrypted[-28:-16], encrypted[:-28] + encrypted[-16:], aad)
    inflater = zlib.decompressobj()
    decoded = inflater.decompress(compressed, 1024 * 1024 + 1)
    if len(decoded) > 1024 * 1024 or not inflater.eof:
        raise ValueError('Incomplete or oversized compressed login response')
    return decoded


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError('Malformed Protobuf integer')


def protobuf_fields(data):
    result = []
    offset = 0
    while offset < len(data):
        tag, offset = varint(data, offset)
        field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError('Invalid Protobuf field')
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire == 2:
            size, offset = varint(data, offset)
            value = data[offset:offset + size]
            offset += size
            if len(value) != size:
                raise ValueError('Truncated Protobuf field')
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value = data[offset:offset + size]
            offset += size
            if len(value) != size:
                raise ValueError('Truncated fixed-width Protobuf field')
        else:
            raise ValueError('Unsupported Protobuf wire type')
        result.append((field, wire, value))
    return result


def extract_code(decoded):
    fields = protobuf_fields(decoded)
    base = next((value for field, wire, value in fields if field == 1 and wire == 2), None)
    if base is None:
        raise ValueError('Missing business BaseResponse')
    returns = [value for field, wire, value in protobuf_fields(base) if field == 1 and wire == 0]
    if returns != [0]:
        raise ValueError(f'Business BaseResponse rejected the request: {returns}')
    # The nested transport frame is proprietary. Restrict extraction to the
    # observed JsLoginResponse field 3, 32-byte code, and require uniqueness.
    candidates = set(re.findall(rb'\x1a\x20(0[A-Za-z0-9_-]{31})', decoded))
    if len(candidates) != 1:
        raise ValueError(f'Expected one login code field, found {len(candidates)}')
    return candidates.pop().decode('ascii')


def request_code(state, timeout=20):
    raw = base64.b64decode(state['httpRequest'], validate=True)
    path, headers, body = parse_request(raw)
    inputs = session_inputs(state, body)
    connection = http.client.HTTPConnection(HOST, 80, timeout=timeout)
    try:
        connection.putrequest('POST', path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        received = response.read(262145)
        if response.status != 200 or len(received) > 262144:
            raise ValueError(f'Unexpected login HTTP response: {response.status}, {len(received)} bytes')
    finally:
        connection.close()
    return extract_code(unwrap_business(state, received, inputs)), received


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / 'pure-login-state.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'pure-login-code.json')
    parser.add_argument('--verify-capture', action='store_true', help='Decode saved response without networking')
    args = parser.parse_args()
    started = time.monotonic()
    result = {
        'ok': False, 'mode': 'captured-session pure HTTP',
        'usesWechatProcess': False, 'requestCount': 0,
        'startedAt': datetime.now(timezone.utc).isoformat(),
    }
    try:
        state = json.loads(args.state.read_text(encoding='utf-8'))
        if args.verify_capture:
            raw = base64.b64decode(state['httpRequest'], validate=True)
            response = base64.b64decode(state['capturedHttpResponse'], validate=True).partition(b'\r\n\r\n')[2]
            inputs = session_inputs(state, parse_request(raw)[2])
            code = extract_code(unwrap_business(state, response, inputs))
        else:
            result['requestCount'] = 1
            code, response = request_code(state)
        result.update({
            'ok': True, 'code': code, 'appId': state['appId'],
            'matchesCapturedCode': hmac.compare_digest(
                hashlib.sha256(code.encode()).hexdigest(), state['capturedCodeHash']),
        })
    except Exception as error:
        result['error'] = str(error)
        result['errorType'] = type(error).__name__
    result['elapsedSeconds'] = round(time.monotonic() - started, 3)
    result['finishedAt'] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
