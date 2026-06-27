import types
import unittest

from cryptography.fernet import Fernet

from handex import vault
from handex.tools import runner


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.original_vault_settings = vault.settings
        self.original_decrypt_item_secret = runner.decrypt_item_secret

    def tearDown(self):
        vault.settings = self.original_vault_settings
        runner.decrypt_item_secret = self.original_decrypt_item_secret

    def test_encrypt_decrypt_roundtrip(self):
        vault.settings = types.SimpleNamespace(vault_key=Fernet.generate_key().decode())

        encrypted = vault.encrypt_secret("super-secret")
        self.assertNotIn("super-secret", encrypted)
        self.assertEqual(vault.decrypt_secret(encrypted), "super-secret")

    def test_vault_run_redacts_direct_secret_output(self):
        runner.decrypt_item_secret = lambda item_id: ({"username": "user"}, "super-secret")

        result = runner.registry.run(
            {
                "tool": "vault_run",
                "args": {
                    "credential_id": "handex:1",
                    "env": "TEST_SECRET",
                    "command": "printf \"$TEST_SECRET\"",
                },
                "cwd": ".",
                "mode": "safe",
            },
            "/tmp",
            "safe",
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "[REDACTED]")
        self.assertNotIn("super-secret", result.stdout)


if __name__ == "__main__":
    unittest.main()
