"""Teste de regresie (statice + logica pura) pentru corecturile din FIXES.md.

Rulare din backend:  python -m unittest astran_sandbox.test_fixes_static -v
Nu necesita MongoDB, FastAPI sau retea: citesc sursele si verifica invariantii cheie.
"""
import ast
import math
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = (ROOT / "server.py").read_text(encoding="utf-8")
SHOP = (ROOT / "astran_sandbox" / "shop_routes.py").read_text(encoding="utf-8")
CLOTHES = (ROOT / "astran_sandbox" / "clothes_routes.py").read_text(encoding="utf-8")
GROUPS = (ROOT / "astran_sandbox" / "group_routes.py").read_text(encoding="utf-8")
ROUTES = (ROOT / "astran_sandbox" / "routes.py").read_text(encoding="utf-8")


def _func_src(name: str) -> str:
    tree = ast.parse(SERVER)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(SERVER, node)
    raise AssertionError(name)


class FeeTests(unittest.TestCase):
    def setUp(self):
        ns = {"math": math, "Fraction": Fraction}
        exec(_func_src("_calc_fee"), ns)
        self.calc = ns["_calc_fee"]
        self.tiers = [{"max_amount": 100, "fee_percent": 5}, {"max_amount": None, "fee_percent": 3}]

    def test_small_transfers_are_not_free(self):
        for amount in range(2, 20):
            self.assertGreaterEqual(self.calc(amount, self.tiers)[0], 1, amount)

    def test_fee_rounds_up_and_net_adds_up(self):
        for amount in (1, 7, 20, 99, 100, 101, 12345):
            fee, net = self.calc(amount, self.tiers)
            self.assertEqual(fee + net, amount)
        self.assertEqual(self.calc(101, self.tiers), (4, 97))


class StaticFixes(unittest.TestCase):
    def test_demo_games_not_deleted_unconditionally_at_startup(self):
        startup = _func_src("startup")
        self.assertIn("delete_many", startup)
        idx = startup.index("db.games.delete_many")
        self.assertIn("else:", startup[:idx][-600:])  # sterge DOAR in ramura "demo neactiv"

    def test_transfer_goes_through_journal(self):
        body = _func_src("transfer_astrans")
        self.assertIn("money.begin", body)
        self.assertIn("money.run", body)
        self.assertNotIn('"$inc": {"astrans_balance"', body)  # niciun $inc direct pe sold

    def test_buy_astrans_credits_through_journal(self):
        body = _func_src("buy_astrans")
        self.assertIn("money.begin", body)
        self.assertNotIn('"$inc": {"astrans_balance"', body)

    def test_stripe_intent_must_belong_to_user(self):
        self.assertIn('meta.get("astran_user_id") != user.get("user_id")', SERVER)
        self.assertIn("/wallet/stripe/intent", SERVER)

    def test_age_category_cannot_be_raised(self):
        body = _func_src("update_me")
        self.assertIn("age_locked", body)
        self.assertIn("status_code=403", body)
        self.assertIn('"age_locked": True', _func_src("register"))

    def test_avatar_and_logo_can_be_cleared(self):
        self.assertIn("exclude_unset=True", _func_src("update_me"))
        self.assertIn("exclude_unset=True", GROUPS)

    def test_script_goes_to_game_scripts(self):
        self.assertIn("_with_main_script(initial_files", _func_src("create_game"))
        self.assertIn("_prepare_main_script", _func_src("update_game"))

    def test_player_character_endpoint_and_source_field(self):
        self.assertIn("/games/{game_id}/player-character", SERVER)
        self.assertIn("player_character_source", _func_src("create_game"))

    def test_play_does_not_inflate_total_plays_on_reentry(self):
        body = _func_src("play_game")
        self.assertIn("upserted_id", body)
        first_branch = body[body.index("if current_inst"):body.index("already = None")]
        self.assertNotIn("total_plays", first_branch.replace("NU incrementam total_plays", ""))

    def test_owns_checked_before_visible(self):
        for src in (SHOP, CLOTHES):
            d = src[src.index("async def get_item_detail"):src.index("@router.delete")] if "async def get_item_detail" in src else ""
            self.assertLess(d.index("owns("), d.index("visible("))

    def test_buy_item_only_swallows_duplicate_key(self):
        for src in (SHOP, CLOTHES):
            self.assertIn("except DuplicateKeyError", src)
            self.assertNotIn('except Exception:\n            return {"ok": True, "already_owned": True}', src)
            self.assertIn("money.begin", src)

    def test_no_n_plus_one_in_listings(self):
        for src in (SHOP, CLOTHES):
            self.assertIn("owned_set(", src)
            self.assertNotIn("is_owned = await owns(current[\"user_id\"], d)", src)

    def test_group_create_is_journaled_and_token_before_debit(self):
        body = GROUPS[GROUPS.index("async def create_group"):GROUPS.index('@router.get("/mine")')]
        self.assertLess(body.index("_generate_token"), body.index("money.debit"))
        self.assertIn("money.rollback", body)

    def test_group_chat_is_filtered_and_respects_blocks(self):
        self.assertIn("filter_text", GROUPS)
        self.assertIn("_blocked_with", GROUPS)
        self.assertIn("sender_adult", GROUPS)

    def test_view_only_editor_cannot_change_target(self):
        self.assertIn('_clean_target(f.get("target"))) for f in old_files', ROUTES)

    def test_login_register_reports_are_rate_limited(self):
        self.assertIn("_login_ip_limiter.hit", _func_src("login"))
        self.assertIn("_register_limiter.hit", _func_src("register"))
        self.assertIn("_report_limiter.hit", _func_src("create_report"))
        self.assertIn("REPORT_DETAILS_MAX", SERVER)
        self.assertIn("_report_target_exists", _func_src("create_report"))

    def test_blocking_work_is_offloaded(self):
        self.assertNotIn("= hash_pw(", SERVER.replace("def hash_pw", ""))
        self.assertIn("asyncio.to_thread(psec.send_recovery_code_email", SERVER)
        self.assertIn("asyncio.to_thread(psec.hash_project_password", SERVER)

    def test_google_accounts_can_start_recovery(self):
        self.assertIn("_reauth_account_password", _func_src("recover_start"))
        self.assertIn("_reauth_account_password", _func_src("reset_password_start"))

    def test_limiter_imported_before_module_level_use(self):
        """Regresie: SlidingWindowLimiter era folosit la import inainte sa fie importat (NameError la pornire)."""
        self.assertLess(SERVER.index("from astran_sandbox.ratelimit import SlidingWindowLimiter"),
                        SERVER.index("_register_limiter = SlidingWindowLimiter("))

    def test_ws_token_is_redacted_from_logs(self):
        self.assertIn("_RedactTokenFilter", SERVER)
        self.assertIn("uvicorn.access", SERVER)


if __name__ == "__main__":
    unittest.main()