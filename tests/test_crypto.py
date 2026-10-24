import base64
import unittest

from cryptography.exceptions import InvalidTag

from fixture_session import encoded_state, make_state
from pure_request_login import parse_request
from session_config import decode_state_secret, validate_state


class CryptoTests(unittest.TestCase):
    def test_synthetic_capture_roundtrip(self):
        self.assertTrue(validate_state(make_state()))

    def test_secret_encoding(self):
        self.assertTrue(validate_state(decode_state_secret(encoded_state())))

    def test_tampered_ciphertext_rejected(self):
        state = make_state()
        response = bytearray(base64.b64decode(state["capturedHttpResponse"]))
        response[-1] ^= 1
        state["capturedHttpResponse"] = base64.b64encode(response).decode()
        with self.assertRaises(InvalidTag):
            validate_state(state)

    def test_code_hash_mismatch_rejected(self):
        state = make_state()
        state["capturedCodeHash"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_other_destination_rejected(self):
        state = make_state()
        request = base64.b64decode(state["httpRequest"])
        request = request.replace(b"szextshort.weixin.qq.com", b"invalid.example")
        with self.assertRaises(ValueError):
            parse_request(request)

    def test_missing_or_oversized_secret_rejected(self):
        for value in ("", "A" * (48 * 1024 + 1)):
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                decode_state_secret(value)

    def test_different_app_rejected(self):
        state = make_state()
        state["appId"] = "fixture-other-app"
        with self.assertRaises(ValueError):
            validate_state(state)
