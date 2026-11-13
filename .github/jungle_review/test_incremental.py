import copy
import tempfile
import unittest
from pathlib import Path

from incremental import (choose_checkpoint, incremental_context, make_checkpoint, parse_checkpoints,
                         runtime_identity)
from review import validated_findings
from test_publish import (BASE_TIP, BOT, HEAD, MB, NEWER, REPO, RUN, SIGNER, FakeGitHub,
                          genuine_run, publisher)

RELEASE = "f" * 64


def marker(**changes):
    args = dict(repository=REPO, number=1, head=HEAD, base=MB, base_ref="main", release=RELEASE,
                run_id=RUN, start=MB, signer=SIGNER)
    args.update(changes)
    return make_checkpoint(**args)


def choose(records, **changes):
    args = dict(head=NEWER, base=MB, base_ref="main", release=RELEASE,
                genuine=lambda run, head: True, complete=lambda head, base: True,
                ancestor=lambda old, new: True)
    args.update(changes)
    return choose_checkpoint(records, **args)


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.records = parse_checkpoints(marker(), REPO, 1, SIGNER)

    def test_authenticated_complete_ancestor_is_selected(self):
        self.assertEqual(choose(self.records)["head"], HEAD)

    def test_scope_tampering_and_cross_pr_replay_refused(self):
        for body in (marker().replace(HEAD, NEWER), marker().replace("ref=bWFpbg", "ref=ZGV2")):
            self.assertEqual(parse_checkpoints(body, REPO, 1, SIGNER), [])
        self.assertEqual(parse_checkpoints(marker(), REPO, 2, SIGNER), [])
        self.assertEqual(parse_checkpoints(marker(), "other/repo", 1, SIGNER), [])

    def test_every_eligibility_condition_is_required(self):
        for changes in ({"base": "e"*40}, {"base_ref":"release"}, {"release":"0"*64},
                        {"genuine":lambda *_:False}, {"complete":lambda *_:False},
                        {"ancestor":lambda *_:False}, {"head":HEAD}):
            with self.subTest(changes=changes): self.assertIsNone(choose(self.records, **changes))

    def test_newest_eligible_checkpoint_wins(self):
        newer = parse_checkpoints(marker(head="e"*40, run_id=RUN+1), REPO, 1, SIGNER)
        self.assertEqual(choose([*self.records,*newer])["run"], RUN+1)

    def test_runtime_change_invalidates_old_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/"review.py").write_text("version=1")
            first=runtime_identity(root);(root/"test_review.py").write_text("a test")
            self.assertEqual(first,runtime_identity(root))
            (root/"review.py").write_text("version=2")
            self.assertNotEqual(first,runtime_identity(root))

    def test_model_change_invalidates_old_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertNotEqual(runtime_identity(root, model="gpt-6.1-sol"),
                                runtime_identity(root, model="claude-opus-5-5"))

    def test_git_failure_does_not_create_a_guessed_scope(self):
        def failed(*_): raise RuntimeError("unavailable")
        with self.assertRaises(RuntimeError): choose(self.records, ancestor=failed)


def context(head, base, path="src/a.py", patch="@@ -1 +1 @@\n-old\n+new\n"):
    return {"repository":REPO,"number":1,"head_sha":head,"base_sha":base,
            "files":[{"path":path,"status":"modified","patch":patch,"changed_lines":[1]}],
            "coverage":{"complete":True},"context_files":[]}


class IncrementalContextTests(unittest.TestCase):
    def test_delta_is_primary_and_broader_changes_are_context(self):
        full=context(NEWER,MB);full["files"].append(context(NEWER,MB,path="src/b.py")["files"][0])
        delta=context(NEWER,HEAD);before=copy.deepcopy(delta)
        result=incremental_context(full,delta,{"head":HEAD,"base":MB,"run":RUN})
        self.assertEqual(result["files"],delta["files"])
        self.assertEqual(result["base_sha"],HEAD)
        self.assertEqual(result["merge_base_sha"],MB)
        self.assertIn("src/b.py",result["cumulative_context"]["changed_paths"])
        self.assertEqual(delta,before)

    def test_focused_verifier_retains_delta_scope_and_cumulative_context(self):
        import focused
        full = context(NEWER, MB)
        delta = incremental_context(full, context(NEWER, HEAD),
                                    {"head": HEAD, "base": MB, "run": RUN})
        package, _, _ = focused.focused_package(delta, [], lambda *_: {})
        self.assertEqual(package["incremental"]["from_sha"], HEAD)
        self.assertEqual(package["cumulative_context"], delta["cumulative_context"])
        self.assertEqual(package["merge_base_sha"], MB)

    def test_identity_mismatch_is_refused(self):
        with self.assertRaises(ValueError):
            incremental_context(context(NEWER,MB),context(HEAD,MB),{"head":HEAD,"base":MB,"run":RUN})

    def test_different_repository_or_pull_request_cannot_supply_the_delta(self):
        for changes in ({"repository": "other/repository"}, {"number": 2}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                incremental_context(context(NEWER, MB), {**context(NEWER, HEAD), **changes},
                                    {"head": HEAD, "base": MB, "run": RUN})

    def test_reverted_file_cannot_get_an_invalid_inline_comment(self):
        full=context(NEWER,MB,path="src/b.py")
        result=incremental_context(full,context(NEWER,HEAD),{"head":HEAD,"base":MB,"run":RUN})
        finding={"path":"src/a.py","line":1,"priority":"P2","title":"Fix return", "body":"Bad result", "confidence":.99,
                 "counterargument":"No guard", "evidence":["src/a.py:1 changed return"]}
        valid,gaps=validated_findings({"findings":[finding]},result)
        self.assertEqual(valid,[]);self.assertTrue(gaps)


class SequentialGitHub(FakeGitHub):
    def get_merge_base(self, repository, base, head):
        return base if base in (HEAD,NEWER) else self.merge_base


class PublisherCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.github=SequentialGitHub()
        self.scopes=[]
        def collect(repository, number, head_sha=None, base_sha=None):
            self.scopes.append((head_sha,base_sha))
            return context(head_sha, MB if base_sha==BASE_TIP else base_sha)
        self.collect=collect
        self.seen=[]

    def run_review(self, outcome="complete", head=HEAD, run_id=RUN):
        self.github.heads=[head];self.github.reads=0
        self.github.runs[run_id]=genuine_run(head=head)
        def review(data,*_):
            self.seen.append(data)
            return {"status":outcome,"findings":[],"coverage_gaps":[]}
        return publisher(self.github,review,collect=self.collect,event_head=head,run_id=run_id).run()

    def test_second_push_reviews_only_since_complete_checkpoint(self):
        self.run_review();self.run_review(head=NEWER,run_id=RUN+1)
        self.assertEqual(self.seen[-1]["base_sha"],HEAD)
        self.assertEqual(self.seen[-1]["incremental"]["mode"],"incremental")
        self.assertEqual(self.scopes[-1],(NEWER,HEAD))
        self.assertIn("Changes since the last completed review", self.github.comments[0]["body"])

    def test_partial_first_review_cannot_be_checkpoint(self):
        self.run_review("partial");self.run_review(head=NEWER,run_id=RUN+1)
        self.assertEqual(self.seen[-1]["base_sha"],MB)

    def test_partial_middle_push_keeps_unreviewed_commits_in_next_diff(self):
        self.run_review();self.run_review("partial",head=NEWER,run_id=RUN+1)
        third="f"*40;self.run_review(head=third,run_id=RUN+2)
        self.assertEqual(self.seen[-1]["base_sha"],HEAD)
        self.assertEqual(self.scopes[-1],(third,HEAD))

    def test_unavailable_middle_push_keeps_checkpoint(self):
        self.run_review();self.run_review("unavailable",head=NEWER,run_id=RUN+1)
        third="f"*40;self.run_review(head=third,run_id=RUN+2)
        self.assertEqual(self.seen[-1]["base_sha"],HEAD)

    def test_rerun_does_not_spend_or_repeat_review(self):
        self.run_review();self.run_review(run_id=RUN+1)
        self.assertEqual(len(self.seen),1)

    def test_force_push_falls_back_to_whole_pr(self):
        self.run_review()
        self.github.get_merge_base=lambda *_:MB
        self.run_review(head=NEWER,run_id=RUN+1)
        self.assertEqual(self.seen[-1]["base_sha"],MB)

    def test_base_retarget_even_with_same_merge_base_falls_back(self):
        self.run_review();self.github.base_refs=["release"]
        self.run_review(head=NEWER,run_id=RUN+1)
        self.assertEqual(self.seen[-1]["base_sha"],MB)

    def test_edited_or_forged_checkpoint_cannot_skip_changes(self):
        self.run_review();self.github.comments[0].update(editor="someone",editor_type="User")
        self.run_review(head=NEWER,run_id=RUN+1)
        self.assertEqual(self.seen[-1]["base_sha"],MB)

    def test_no_completion_receipt_means_full_review(self):
        self.run_review();self.github.status_store[HEAD]=[]
        self.run_review(head=NEWER,run_id=RUN+1)
        self.assertEqual(self.seen[-1]["base_sha"],MB)


if __name__=="__main__":unittest.main()
