"""Configured synthetic location data for offline tests only."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def make_profile():
    profile = json.loads(
        (ROOT / "checkin-request-profile.example.json").read_text(encoding="utf-8"),
    )
    profile["configured"] = True
    profile["payload"].update({
        "latitude": 1.25,
        "longitude": 2.5,
        "reverseGeocode": "Synthetic fixture address",
        "userAgent": "FixtureUserAgent/1.0",
    })
    return profile


def write_profile(directory):
    path = Path(directory) / "fixture-profile.json"
    path.write_text(json.dumps(make_profile()), encoding="utf-8")
    return path
