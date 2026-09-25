import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import bcrypt
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from password_policy import valid_new_password,verify_password


class PasswordPolicyTests(unittest.TestCase):
    def test_character_boundaries(self):
        for size,expected in ((7,False),(8,True),(30,True),(31,False)):
            self.assertEqual(bool(valid_new_password('Aa1!'+'x'*(size-4))),expected)

    def test_required_character_classes(self):
        for value in ('abcdef1!','ABCDEF1!','Abcdefgh!','Abcdefg1?','Abcdefg!１'):
            with self.subTest(value=value):self.assertFalse(valid_new_password(value))
        for symbol in '!@#$%^&*':self.assertTrue(valid_new_password('Abcdefg1'+symbol))

    def test_whitespace_and_control_bypass_rejected(self):
        for value in ('a\naaaaaa','Abcd1!\nx','Abcd1! x','Abcd1!\tx','Abcd1!\x00x','Abcd1!\u200bx'):
            with self.subTest(value=repr(value)):self.assertFalse(valid_new_password(value))

    def test_utf8_byte_limit(self):
        self.assertTrue(valid_new_password('Aa1!'+'中'*22+'xx'))  # exactly 72 bytes, 28 characters
        self.assertFalse(valid_new_password('Aa1!'+'中'*23))

    def test_non_strings_and_surrogates_rejected(self):
        for value in (None,12345678,[],{},'Aa1!xxxx\ud800'):
            self.assertFalse(valid_new_password(value))

    def test_existing_password_not_subject_to_new_strength_rules(self):
        for old in (b'weak',b'old password',b'a'*40):
            stored=bcrypt.hashpw(old,bcrypt.gensalt(rounds=4))
            self.assertTrue(verify_password(old,stored))
            self.assertFalse(verify_password(b'incorrect',stored))

    def test_overlong_login_never_truncated_or_hashed(self):
        with patch('password_policy.bcrypt.checkpw') as check:
            self.assertFalse(verify_password(b'a'*73,b'unused'))
            check.assert_not_called()

    def test_malformed_hash_is_failed_login(self):
        self.assertFalse(verify_password(b'password',b'bad hash'))
