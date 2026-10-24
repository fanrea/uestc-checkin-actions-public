"""Export three GitHub Secrets values to ignored local text files."""
import argparse
import base64
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pure_checkin_flow import read_cookie
from profile_config import MAX_PROFILE_BYTES, validate_profile
from session_config import validate_state, write_private


def export(state_path, cookie_path, output, profile_path):
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    verified = validate_state(state)
    cookie = read_cookie(cookie_path)
    encoded = base64.b64encode(json.dumps(state, separators=(",", ":")).encode()).decode()
    if len(encoded) > 48 * 1024:
        raise ValueError("State secret exceeds 48 KiB.")
    profile = validate_profile(profile_path)
    profile_contents = json.dumps(profile, ensure_ascii=False, separators=(",", ":"))
    if len(profile_contents.encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ValueError("Profile exceeds 16 KiB.")
    state_output = output / "WECHAT_LOGIN_STATE_B64.txt"
    cookie_output = output / "CHECKIN_COOKIE.txt"
    profile_output = output / "CHECKIN_PROFILE_JSON.txt"
    write_private(state_output, encoded + "\n")
    write_private(cookie_output, cookie + "\n")
    write_private(profile_output, profile_contents + "\n")
    return {
        "ok": True, "stateSecretFile": str(state_output.resolve()),
        "cookieSecretFile": str(cookie_output.resolve()),
        "profileSecretFile": str(profile_output.resolve()),
        "offlineCaptureVerified": verified,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "tools/windows/pure-login-state.json")
    parser.add_argument("--cookie-file", type=Path, default=ROOT / "tools/windows/checkin-cookie.txt")
    parser.add_argument("--profile", type=Path, default=ROOT / "checkin-request-profile.json")
    args = parser.parse_args()
    try:
        summary = export(
            args.state, args.cookie_file, ROOT / ".local/github-secrets", args.profile,
        )
    except Exception as error:
        print(json.dumps({"ok": False, "errorType": type(error).__name__}))
        return 1
    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
