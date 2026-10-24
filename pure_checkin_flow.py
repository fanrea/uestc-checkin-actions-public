"""Run code -> Ticket -> one check-in POST using saved session files.

Runtime: Python 3.10+ and cryptography. No WeChat process access or Frida.
The profile supplies the current user's own location and device diagnostics.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pure_request_login import request_code


ROOT = Path(__file__).resolve().parent
ORIGIN = "https://jzsz.uestc.edu.cn"
TICKET_PATH = "/platform-app/api/auth/checkin-ticket"
CHECKIN_PATH = "/daily-checkin/api/student-checkin-record/checkin"
APP_ID = "wxaa78bf2905f01cee"
CHINA_TIME = timezone(timedelta(hours=8))
TEXT_FIELDS = (
    "reverseGeocode", "userAgent", "coordinateSystemCode", "networkTypeCode",
    "locationAuthorized", "locationAccuracy", "errorMessage",
)


def timestamp():
    return datetime.now(CHINA_TIME).isoformat(timespec="seconds")


def save_result(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_cookie(path):
    cookie = path.read_text(encoding="utf-8-sig").strip()
    if cookie.lower().startswith("cookie:"):
        cookie = cookie.split(":", 1)[1].strip()
    if "\r" in cookie or "\n" in cookie:
        raise ValueError("Cookie must be a single line.")
    if not re.search(r"(?:^|;\s*)SESSION=[A-Za-z0-9._~%-]{16,512}(?:;|$)", cookie):
        raise ValueError("Cookie file must contain SESSION=<sessionId>.")
    return cookie


def read_profile(path):
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Profile must contain a payload object.")
    payload = dict(payload)
    for name in TEXT_FIELDS:
        if not isinstance(payload.get(name), str):
            raise ValueError(f"Profile {name} must be a string.")
    for name in ("latitude", "longitude", "accuracy", "locationDurationMs"):
        value = payload.get(name)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Profile {name} must be a finite number.")
    if not -90 <= payload["latitude"] <= 90 or not -180 <= payload["longitude"] <= 180:
        raise ValueError("Invalid coordinates.")
    if payload["accuracy"] < 0 or payload["locationDurationMs"] < 0:
        raise ValueError("Accuracy and duration must be nonnegative.")
    if type(payload.get("locationEnabled")) is not bool:
        raise ValueError("Profile locationEnabled must be boolean.")
    if any(c in payload["userAgent"] for c in "\r\n"):
        raise ValueError("User-Agent must be a single line.")
    expected = set(TEXT_FIELDS) | {
        "latitude", "longitude", "accuracy", "locationDurationMs", "locationEnabled",
    }
    if set(payload) != expected:
        raise ValueError("Unexpected or missing profile payload fields.")
    return payload, document.get("description", "")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_json(path, payload, headers, timeout):
    request = Request(
        ORIGIN + path,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        response = build_opener(NoRedirect).open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        status = response.code
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Server response exceeds 1 MiB.")
    try:
        body = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = {"nonJsonBody": raw.decode("utf-8", errors="replace")}
    return {"httpStatus": status, "body": body}


def base_headers(cookie, profile, token_file=None):
    headers = {
        "Content-Type": "application/json", "Accept": "application/json",
        "Cookie": cookie, "User-Agent": profile["userAgent"],
    }
    if token_file:
        token = token_file.read_text(encoding="utf-8-sig").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or "\r" in token or "\n" in token:
            raise ValueError("Token must be a single nonempty line.")
        headers["Authorization"] = "Bearer " + token
    return headers


def ticket_from_response(response):
    body = response["body"]
    if not 200 <= response["httpStatus"] < 300 or not isinstance(body, dict):
        return None
    data = body.get("data")
    ticket = data.get("ticket") if isinstance(data, dict) else None
    if body.get("ok") is not True or not isinstance(ticket, str):
        return None
    return ticket if re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket) else None


def checkin_accepted(response):
    body = response["body"]
    if not 200 <= response["httpStatus"] < 300 or not isinstance(body, dict):
        return False
    return (type(body.get("code")) is int and body["code"] == 0) or body.get("status") is True


def run_flow(args, result):
    state = json.loads(args.state.read_text(encoding="utf-8-sig"))
    if state.get("appId") != APP_ID:
        raise ValueError("Session AppID does not match this mini-program.")
    profile, description = read_profile(args.profile)
    cookie = read_cookie(args.cookie_file)
    headers = base_headers(cookie, profile, args.token_file)
    result.update({"profileDescription": description, "profile": profile})
    result["stage"] = "getting_code"
    result["requestCounts"]["code"] += 1
    save_result(args.output, result)
    print("1/3 Requesting a fresh code using the saved session...", flush=True)
    code, _ = request_code(state, timeout=args.timeout)
    result.update({"stage": "code_received", "code": code, "codeReceivedAt": timestamp()})
    save_result(args.output, result)
    ticket_headers = dict(headers, platform="mp-weixin")
    ticket_headers["Referer"] = f"https://servicewechat.com/{APP_ID}/56/page-frame.html"
    result["stage"] = "getting_ticket"
    result["requestCounts"]["ticket"] += 1
    save_result(args.output, result)
    print("2/3 Exchanging the fresh code for a Ticket...", flush=True)
    response = post_json(TICKET_PATH, {"code": code}, ticket_headers, args.timeout)
    result["ticketResponse"] = response
    ticket = ticket_from_response(response)
    if ticket is None:
        result["stage"] = "ticket_rejected"
        return
    result.update({"stage": "ticket_received", "ticket": ticket})
    save_result(args.output, result)
    submit_checkin(args, result, profile, headers, ticket)


def submit_checkin(args, result, profile, headers, ticket):
    payload = dict(profile, clientTime=datetime.now(CHINA_TIME).strftime("%Y-%m-%d %H:%M:%S"))
    headers = dict(headers, platform="h5")
    headers.update({
        "X-Checkin-Ticket": ticket, "Origin": ORIGIN,
        "Referer": ORIGIN + "/daily-checkin-h5/index.html",
    })
    result.update({"stage": "checkin_sending", "checkinPayload": payload})
    result["requestCounts"]["checkin"] += 1
    save_result(args.output, result)
    print("3/3 Sending one check-in request; automatic retries are disabled...", flush=True)
    response = post_json(CHECKIN_PATH, payload, headers, args.timeout)
    result["checkinResponse"] = response
    result["ok"] = checkin_accepted(response)
    result["stage"] = "checkin_accepted" if result["ok"] else "checkin_rejected"


def print_summary(args, result):
    response = result.get("checkinResponse") or result.get("ticketResponse") or {}
    body = response.get("body")
    body = body if isinstance(body, dict) else {}
    print(json.dumps({
        "ok": result["ok"], "stage": result["stage"],
        "requestCounts": result["requestCounts"], "usesWechatProcess": False,
        "httpStatus": response.get("httpStatus"), "serverCode": body.get("code"),
        "serverMessage": body.get("msg") or body.get("message"),
        "errorType": result.get("errorType"), "error": result.get("error"),
        "savedTo": str(args.output.resolve()),
    }, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "pure-login-state.json")
    parser.add_argument("--cookie-file", type=Path, default=ROOT / "checkin-cookie.txt")
    parser.add_argument("--profile", type=Path, default=ROOT / "checkin-request-profile.json")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "pure-checkin-flow-result.json")
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    result = {
        "ok": False, "stage": "starting", "startedAt": timestamp(),
        "usesWechatProcess": False, "automaticRetries": False,
        "requestCounts": {"code": 0, "ticket": 0, "checkin": 0},
    }
    try:
        run_flow(args, result)
    except (Exception, KeyboardInterrupt) as error:
        stage = result["stage"]
        result["stage"] = "checkin_result_unknown" if stage == "checkin_sending" else "error"
        result.update({
            "errorAtStage": stage, "errorType": type(error).__name__,
            "error": str(error) or type(error).__name__,
        })
    result["finishedAt"] = timestamp()
    save_result(args.output, result)
    print_summary(args, result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
