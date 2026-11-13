"""Coada marginita + kill la anulare in runner.run_files (fara binar real: proces fals).
Ruleaza:  python -m unittest astran_sandbox.test_runner_queue"""
import asyncio
import json
import unittest

from astran_sandbox import runner


class _FakeProc:
    pid = 1
    returncode = 0

    async def communicate(self, data=None):
        await asyncio.sleep(0.3)
        return (json.dumps({"type": "ops", "data": {"ops": []}}).encode() + b"\n", b"")

    async def wait(self):
        return 0


class RunnerQueueTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: getattr(runner, k) for k in
                       ("find_binary", "_check_bwrap", "_require_bwrap", "_make_preexec", "_kill",
                        "MAX_QUEUED_RUNS", "_semaphore", "_waiting")}
        self.killed = []

        async def no_bwrap():
            return None

        async def fake_exec(*a, **k):
            return _FakeProc()

        runner.find_binary = lambda: "/bin/true"
        runner._check_bwrap = no_bwrap
        runner._require_bwrap = lambda: False
        runner._make_preexec = lambda *a, **k: None
        runner._kill = lambda p: self.killed.append(p)
        self._orig_exec = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = fake_exec
        runner.MAX_QUEUED_RUNS = 3
        runner._semaphore = None
        runner._waiting = 0

    def tearDown(self):
        asyncio.create_subprocess_exec = self._orig_exec
        for k, v in self._saved.items():
            setattr(runner, k, v)

    def test_queue_overflow_and_cancel(self):
        files = [{"name": "main", "source": "print(1)"}]

        async def scenario():
            tasks = [asyncio.create_task(runner.run_files(files)) for _ in range(runner.MAX_CONCURRENT_RUNS + 3)]
            await asyncio.sleep(0.05)
            with self.assertRaises(runner.SandboxBusy):
                await runner.run_files(files)
            res = await asyncio.gather(*tasks)
            self.assertTrue(all(r["ok"] for r in res))
            self.assertEqual(runner._waiting, 0)
            self.assertEqual(runner._semaphore._value, runner.MAX_CONCURRENT_RUNS)
            t = asyncio.create_task(runner.run_files(files))
            await asyncio.sleep(0.05)
            t.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await t
            self.assertEqual(len(self.killed), 1)
            self.assertEqual(runner._semaphore._value, runner.MAX_CONCURRENT_RUNS)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()