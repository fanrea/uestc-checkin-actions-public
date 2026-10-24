"""Load private profile settings without committing a user's location."""
import json
from pathlib import Path

from pure_checkin_flow import read_profile
from session_config import write_private


MAX_PROFILE_BYTES = 16 * 1024


def validate_profile(path):
    read_profile(path)
    profile = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if profile.get("configured") is not True:
        raise ValueError("Fill your own profile and set configured to true.")
    for value in profile["payload"].values():
        if isinstance(value, str) and "REPLACE_WITH_" in value:
            raise ValueError("Replace all profile placeholders before use.")
    return profile


def select_profile(local_path, directory, environment):
    contents = environment.get("CHECKIN_PROFILE_JSON", "").strip()
    if not contents:
        path = Path(local_path)
    else:
        if len(contents.encode("utf-8")) > MAX_PROFILE_BYTES:
            raise ValueError("Profile exceeds 16 KiB.")
        json.loads(contents)
        path = directory / "checkin-request-profile.json"
        write_private(path, contents)
    validate_profile(path)
    return path
