"""
F-03: KeyGenerator — unit tests.
No DB required; pure Python.
"""

import hashlib

from apps.sdk_keys.key_generator import KeyGenerator


class TestGenerate:
    def test_server_key_has_correct_prefix(self):
        full_key, _, _ = KeyGenerator.generate("server")
        assert full_key.startswith("sdk_srv_")

    def test_client_key_has_correct_prefix(self):
        full_key, _, _ = KeyGenerator.generate("client")
        assert full_key.startswith("sdk_cli_")

    def test_prefix_is_first_16_chars(self):
        full_key, prefix, _ = KeyGenerator.generate("server")
        assert prefix == full_key[:16]
        assert len(prefix) == 16

    def test_hashed_key_is_sha256_of_full_key(self):
        full_key, _, hashed = KeyGenerator.generate("server")
        expected = hashlib.sha256(full_key.encode()).hexdigest()
        assert hashed == expected

    def test_hashed_key_is_64_hex_chars(self):
        _, _, hashed = KeyGenerator.generate("server")
        assert len(hashed) == 64
        assert all(c in "0123456789abcdef" for c in hashed)

    def test_two_calls_produce_different_keys(self):
        key1, _, _ = KeyGenerator.generate("server")
        key2, _, _ = KeyGenerator.generate("server")
        assert key1 != key2

    def test_full_key_min_length(self):
        full_key, _, _ = KeyGenerator.generate("server")
        assert len(full_key) >= 48


class TestHashRaw:
    def test_matches_sha256(self):
        raw = "sdk_srv_sometoken"
        expected = hashlib.sha256(raw.encode()).hexdigest()
        assert KeyGenerator.hash_raw(raw) == expected

    def test_is_deterministic(self):
        raw = "sdk_srv_sometoken"
        assert KeyGenerator.hash_raw(raw) == KeyGenerator.hash_raw(raw)

    def test_different_inputs_produce_different_hashes(self):
        assert KeyGenerator.hash_raw("key_a") != KeyGenerator.hash_raw("key_b")
