"""python -m unittest astran_sandbox.test_ws_fanout"""
import asyncio
import time
import unittest

from astran_sandbox import ws_fanout


class _WS:
    def __init__(self, delay=0.0, boom=False, hang=False):
        self.delay, self.boom, self.hang = delay, boom, hang
        self.got, self.closed = [], False

    async def send_text(self, t):
        if self.hang:
            await asyncio.sleep(3600)          # nu citeste niciodata: send_text nu se termina
        if self.boom:
            raise RuntimeError("closed")
        await asyncio.sleep(self.delay)
        self.got.append(t)

    async def close(self, code=1000):
        self.closed = True


class FanOutTests(unittest.TestCase):
    def test_slow_client_does_not_block_others_and_is_disconnected(self):
        async def go():
            good = [_WS() for _ in range(50)]
            stuck, dead = _WS(hang=True), _WS(boom=True)
            t0 = time.monotonic()
            await ws_fanout.fan_out([*good[:25], stuck, dead, *good[25:]], "hi", timeout=0.2)
            took = time.monotonic() - t0
            self.assertTrue(all(w.got == ["hi"] for w in good))
            self.assertLess(took, 1.0)            # secvential cu clientul blocat ar fi durat > 1h
            self.assertTrue(stuck.closed)         # cel blocat e deconectat
            self.assertFalse(dead.closed)         # cel deja inchis nu mai e inchis a doua oara
        asyncio.run(go())

    def test_send_safely_results(self):
        async def go():
            self.assertTrue(await ws_fanout.send_safely(_WS(), "x"))
            self.assertFalse(await ws_fanout.send_safely(_WS(boom=True), "x"))
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()