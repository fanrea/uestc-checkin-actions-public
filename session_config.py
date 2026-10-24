"""Validate saved session files offline without revealing their contents."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path

from pure_checkin_flow import APP_ID
from pure_request_login import extract_code, parse_request, session_inputs, unwrap_business


def validate_state(state):
    if not isinstance(state, dict) or state.get("appId") != APP_ID:
        raise ValueError("State AppID does not match this mini-program.")
    request = base64.b64decode(state["httpRequest"], validate=True)
    if len(request) > 262144:
        raise ValueError("Saved request exceeds the size limit.")
    inputs = session_inputs(state, parse_request(request)[2])
    key = base64.b64decode(state["businessResponseKey"], validate=True)
    base64.b64decode(state["businessResponseAad"], validate=True)
    if len(key) not in (16, 24, 32):
        raise ValueError("Invalid business response key length.")
    if "capturedHttpResponse" not in state:
        return False
    response = base64.b64decode(state["capturedHttpResponse"], validate=True)
    header, separator, body = response.partition(b"\r\n\r\n")
    if not separator or len(response) > 262144 or not header.startswith(b"HTTP/1.1 200 "):
        raise ValueError("Invalid saved HTTP response.")
    code = extract_code(unwrap_business(state, body, inputs))
    expected = state.get("capturedCodeHash")
    if expected and not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), expected):
        raise ValueError("Saved response code does not match the capture.")
    return True


def decode_state_secret(value):
    if not value:
        raise ValueError("Missing WECHAT_LOGIN_STATE_B64 secret.")
    if len(value) > 48 * 1024:
        raise ValueError("State secret exceeds 48 KiB.")
    decoded = base64.b64decode(value.strip(), validate=True)
    return json.loads(decoded.decode("utf-8-sig"))


def write_private(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        os.chmod(path, 0o600)
        stream.write(text)
