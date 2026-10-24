"""GitHub Actions entry: secrets in, public status out; no process attachment."""
import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pure_checkin_flow as flow
from profile_config import select_profile
from session_config import decode_state_secret, validate_state, write_private


ROOT = Path(__file__).resolve().parent
STAGES = {
    "validated": "离线校验通过；未发送网络请求。",
    "checkin_accepted": "服务端确认提交成功。",
    "checkin_rejected": "打卡被服务端拒绝。",
    "ticket_rejected": "兑换 Ticket 被服务端拒绝。",
    "checkin_result_unknown": "提交期间连接中断，先查看当日记录再决定是否重试。",
    "error": "执行中止；请检查 Secrets、会话有效性及网络。",
}


def make_public_summary(result):
    response = result.get("checkinResponse") or result.get("ticketResponse") or {}
    body = response.get("body")
    body = body if isinstance(body, dict) else {}
    code = body.get("code")
    code = code if type(code) is int else None
    reason = STAGES.get(result["stage"], STAGES["error"])
    if result["stage"] == "checkin_rejected" and code == 4006:
        reason = "当前不在打卡时间段内。"
    return {
        "ok": result["ok"], "stage": result["stage"], "reason": reason,
        "startedAt": result["startedAt"], "finishedAt": result.get("finishedAt"),
        "requestCounts": result["requestCounts"], "usesWechatProcess": False,
        "httpStatus": response.get("httpStatus"), "serverCode": code,
        "errorType": result.get("errorType"),
        "offlineCaptureVerified": result.get("offlineCaptureVerified"),
    }


def write_public_outputs(summary):
    output = ROOT / "output" / "checkin-summary.json"
    flow.save_result(output, summary)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write("## Check-in result\n\n```json\n")
            stream.write(json.dumps(summary, ensure_ascii=False, indent=2))
            stream.write("\n```\n")
    print(json.dumps(summary, ensure_ascii=True))


def prepare_files(directory, environment):
    state = decode_state_secret(environment.get("WECHAT_LOGIN_STATE_B64", ""))
    verified = validate_state(state)
    cookie = environment.get("CHECKIN_COOKIE", "").strip()
    if not cookie:
        raise ValueError("Missing CHECKIN_COOKIE secret.")
    paths = {
        "state": directory / "pure-login-state.json",
        "cookie_file": directory / "checkin-cookie.txt",
        "token_file": None,
        "output": directory / "private-flow-result.json",
    }
    write_private(paths["state"], json.dumps(state, ensure_ascii=False))
    write_private(paths["cookie_file"], cookie + "\n")
    flow.read_cookie(paths["cookie_file"])
    token = environment.get("CHECKIN_BEARER_TOKEN", "").strip()
    if token:
        paths["token_file"] = directory / "bearer-token.txt"
        write_private(paths["token_file"], token + "\n")
    return paths, verified


def execute(args, result, environment):
    temporary_root = Path(environment.get("RUNNER_TEMP") or tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="uestc-checkin-", dir=temporary_root) as temporary:
        directory = Path(temporary).resolve()
        if directory.parent != temporary_root or not directory.name.startswith("uestc-checkin-"):
            raise ValueError("Unexpected temporary directory.")
        paths, verified = prepare_files(directory, environment)
        result["offlineCaptureVerified"] = verified
        selected = select_profile(args.profile, directory, environment)
        call_args = SimpleNamespace(**paths, profile=selected, timeout=args.timeout)
        profile, _ = flow.read_profile(selected)
        flow.base_headers(flow.read_cookie(paths["cookie_file"]), profile, paths["token_file"])
        if args.validate_only:
            result.update({"ok": True, "stage": "validated"})
            return
        with redirect_stdout(StringIO()):
            flow.run_flow(call_args, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=ROOT / "checkin-request-profile.json")
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("--timeout must be greater than 0 and at most 60")
    args.validate_only = args.validate_only or os.environ.get("CHECKIN_VALIDATE_ONLY") == "1"
    result = {
        "ok": False, "stage": "starting", "startedAt": flow.timestamp(),
        "requestCounts": {"code": 0, "ticket": 0, "checkin": 0},
    }
    try:
        execute(args, result, os.environ)
    except (Exception, KeyboardInterrupt) as error:
        result["stage"] = (
            "checkin_result_unknown" if result["stage"] == "checkin_sending" else "error"
        )
        result["errorType"] = type(error).__name__
    result["finishedAt"] = flow.timestamp()
    summary = make_public_summary(result)
    write_public_outputs(summary)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
