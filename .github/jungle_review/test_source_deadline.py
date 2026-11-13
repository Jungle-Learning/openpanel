from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import patch

import context


class SourceDeadlineTests(unittest.TestCase):
    def test_tree_walk_uses_remaining_whole_operation_budget(self):
        now=[0.0];timeouts=[]
        def run(*_,**kwargs):
            timeouts.append(kwargs["timeout"])
            now[0]+=[40,15,0][len(timeouts)-1]
            return type("Result",(),{"stdout":"{}"})()
        with patch.object(context.time,"monotonic",lambda:now[0]),patch.object(context.subprocess,"run",run):
            with context.source_deadline(seconds=60):
                for _ in range(3): context._gh_api_get("repos/o/r/git/trees/"+"a"*40)
        self.assertEqual(timeouts,[60,20,5])

    def test_expiration_never_starts_another_subprocess(self):
        with patch.object(context.time,"monotonic",return_value=100),patch.object(context.subprocess,"run") as run:
            with context.source_deadline(deadline=99):
                with self.assertRaises(context.SourceDeadlineReached):context._gh_api_get("repos/o/r")
            run.assert_not_called()

    def test_nested_deadlines_restore_after_exception(self):
        with patch.object(context.time,"monotonic",return_value=100):
            with context.source_deadline(seconds=50):
                self.assertEqual(context.source_timeout(),50)
                try:
                    with context.source_deadline(seconds=10):
                        self.assertEqual(context.source_timeout(),10);raise ValueError()
                except ValueError:pass
                self.assertEqual(context.source_timeout(),50)
            self.assertEqual(context.source_timeout(),120)

    def test_worker_deadlines_are_isolated(self):
        barrier=threading.Barrier(2)
        def bounded(seconds):
            with context.source_deadline(seconds=seconds):
                barrier.wait();return context.source_timeout()
        with ThreadPoolExecutor(max_workers=2) as pool:
            short=pool.submit(bounded,5);long=pool.submit(bounded,60)
            self.assertLess(short.result(),6);self.assertGreater(long.result(),55)

    def test_waiter_timeout_does_not_remove_or_poison_owner(self):
        cache=context._ImmutableResponseCache(10,10000);entered=threading.Event();release=threading.Event()
        def fetch(_):entered.set();release.wait(2);return {"answer":42}
        with ThreadPoolExecutor(max_workers=1) as pool:
            owner=pool.submit(cache.get,"key",fetch);self.assertTrue(entered.wait(1))
            with context.source_deadline(seconds=.01):
                with self.assertRaises(context.SourceDeadlineReached):cache.get("key",fetch)
            self.assertIn("key",cache._in_flight)
            release.set();self.assertEqual(owner.result(),{"answer":42})
        self.assertEqual(cache.get("key",lambda _:self.fail("must be cached")),{"answer":42})

    def test_all_public_source_operations_are_bounded(self):
        for name in ("collect_context","read_context_file","search_context_file","discover_file_paths"):
            self.assertTrue(hasattr(getattr(context,name),"__wrapped__"),name)

    def test_expired_outer_deadline_cannot_be_extended_by_new_operation(self):
        with patch.object(context.time,"monotonic",return_value=100):
            with context.source_deadline(deadline=99):
                with context.source_deadline(seconds=60):
                    with self.assertRaises(context.SourceDeadlineReached):context.source_timeout()


if __name__=="__main__":unittest.main()
