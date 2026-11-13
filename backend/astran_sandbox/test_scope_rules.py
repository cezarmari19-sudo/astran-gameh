"""python -m unittest astran_sandbox.test_scope_rules"""
import unittest
from astran_sandbox.scope_rules import target_escalation as te


class ScopeRulesTests(unittest.TestCase):
    OLD = {"client/a.lua": ("x", "client"), "client/s.lua": ("x", "server")}

    def test_unrestricted_editor_unaffected(self):
        new = {**self.OLD, "n.lua": ("y", "server")}
        self.assertIsNone(te(False, self.OLD, new, ["n.lua"], []))

    def test_restricted_cannot_add_server_file(self):
        new = {**self.OLD, "client/n.lua": ("y", "server")}
        self.assertEqual(te(True, self.OLD, new, ["client/n.lua"], []), "client/n.lua")

    def test_restricted_can_add_client_file(self):
        new = {**self.OLD, "client/n.lua": ("y", "client")}
        self.assertIsNone(te(True, self.OLD, new, ["client/n.lua"], []))

    def test_restricted_cannot_retarget_client_to_server(self):
        new = {**self.OLD, "client/a.lua": ("x", "server")}
        self.assertEqual(te(True, self.OLD, new, [], ["client/a.lua"]), "client/a.lua")

    def test_restricted_can_edit_existing_server_file_source(self):
        new = {**self.OLD, "client/s.lua": ("EDITED", "server")}
        self.assertIsNone(te(True, self.OLD, new, [], ["client/s.lua"]))


if __name__ == "__main__":
    unittest.main()