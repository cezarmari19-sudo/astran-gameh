import unittest

from astran_sandbox.ratelimit import SlidingWindowLimiter


class RateLimitTests(unittest.TestCase):
    def test_blocks_after_limit_and_recovers(self):
        t = [0.0]
        lim = SlidingWindowLimiter(3, 10, clock=lambda: t[0])
        self.assertEqual([lim.hit("a") for _ in range(3)], [None, None, None])
        wait = lim.hit("a")
        self.assertIsNotNone(wait)
        self.assertGreaterEqual(wait, 1)
        self.assertIsNone(lim.hit("b"))      # alta cheie nu e afectata
        t[0] = 11.0
        self.assertIsNone(lim.hit("a"))      # fereastra a expirat

    def test_bounded_memory(self):
        lim = SlidingWindowLimiter(1, 1000, max_keys=50)
        for i in range(500):
            lim.hit(f"k{i}")
        self.assertLessEqual(len(lim._events), 50)


if __name__ == "__main__":
    unittest.main()