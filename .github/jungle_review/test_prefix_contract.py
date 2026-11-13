import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

path=Path(__file__).with_name("provider.py")
spec=importlib.util.spec_from_file_location("prefix_provider",path)
p=importlib.util.module_from_spec(spec);sys.modules[spec.name]=p;spec.loader.exec_module(p)

class PrefixTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{"OPENAI_API_KEY":"test"});self.env.start();self.addCleanup(self.env.stop)
    def transport(self,writes=0,reads=0,short=None,long=0,error=None):
        calls=[]
        def send(url,headers,body,timeout=180):
            calls.append(body)
            if url.endswith("input_tokens"):return {"input_tokens":10000}
            if error:raise error
            details={"cache_write_tokens":writes,"cached_tokens":reads}
            if short is not None:details["cache_write_tokens_details"]={"ttl_30m_tokens":short,"ttl_12h_tokens":long}
            return {"status":"completed","service_tier":"flex","usage":{"input_tokens":10000,"output_tokens":1000,"input_tokens_details":details},
                    "output":[{"type":"message","content":[{"type":"output_text","text":'{"findings":[]}'}]}]}
        return send,calls
    def test_no_prefix_means_no_writes(self):
        send,calls=self.transport();budget=p.Budget();model=p.Model("gpt-6.1-sol",send,cache_prefix=True)
        model.invoke("system",{},budget,"verification")
        self.assertEqual(budget.spent,15000);self.assertEqual(calls[-1]["prompt_cache_options"],{"mode":"explicit","ttl":"30m"})
    def test_verified_short_write_cost_is_exact(self):
        send,calls=self.transport(writes=10000,short=10000);budget=p.Budget()
        p.Model("gpt-6.1-sol",send,cache_prefix=True).invoke("system",{},budget,"contracts",cache_context={"code":"x"})
        self.assertEqual(budget.spent,17500)
        self.assertEqual(calls[-1]["input"][0]["content"][0]["prompt_cache_breakpoint"],{"mode":"explicit","ttl":"30m"})
    def test_verified_warm_prefix_is_billed_at_read_rate(self):
        send,_=self.transport(reads=10000,short=0);budget=p.Budget()
        p.Model("gpt-6.1-sol",send,cache_prefix=True).invoke("system",{},budget,"discovery",cache_context={"code":"x"})
        self.assertEqual(budget.spent,5500)
    def test_unknown_lifetime_uses_worst_rate_then_stops(self):
        send,_=self.transport(writes=10000);budget=p.Budget()
        with self.assertRaises(p.ReviewUnavailable):
            p.Model("gpt-6.1-sol",send,cache_prefix=True).invoke("system",{},budget,"discovery",cache_context={"code":"x"})
        self.assertEqual(budget.spent,30000)
    def test_long_write_is_rejected_even_when_prefix_requested(self):
        send,_=self.transport(writes=10000,short=0,long=10000);budget=p.Budget()
        with self.assertRaises(p.ReviewUnavailable):p.Model("gpt-6.1-sol",send,cache_prefix=True).invoke("system",{},budget,"discovery",cache_context={})
    def test_different_reviews_are_cold_but_phases_share_prefix(self):
        one=p.Model("gpt-6.1-sol",cache_prefix=True);two=p.Model("gpt-6.1-sol",cache_prefix=True)
        a=one.prepare("system",{},"contracts",{"code":"x"});b=one.prepare("system",{},"discovery",{"code":"x"})
        c=two.prepare("system",{},"contracts",{"code":"x"})
        self.assertEqual(a["input"][0],b["input"][0]);self.assertNotEqual(a["input"][0],c["input"][0])
    def test_definitive_uncharged_resource_rejection_refunds_reservation(self):
        send,_=self.transport(error=p.ResourceUnavailable("no resource"));budget=p.Budget()
        with self.assertRaises(p.ResourceUnavailable):p.Model("gpt-6.1-sol",send).invoke("system",{},budget,"discovery")
        self.assertEqual(budget.spent,0);self.assertTrue(budget.report()["billing_complete"])
    def test_unknown_rejection_preserves_reservation(self):
        send,_=self.transport(error=p.ReviewUnavailable("HTTP 429"));budget=p.Budget()
        with self.assertRaises(p.ReviewUnavailable):p.Model("gpt-6.1-sol",send).invoke("system",{},budget,"discovery")
        self.assertGreater(budget.spent,0);self.assertFalse(budget.report()["billing_complete"])

if __name__=="__main__":unittest.main()
