"""Run the actual workflow checkout scripts against a local repo with broken gitlinks."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


class CheckoutTests(unittest.TestCase):
    def test_both_workflows_fetch_the_exact_commit_without_persisting_credentials(self):
        workflow_directory = Path(__file__).resolve().parents[1] / "workflows"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            env = {**os.environ, "GIT_CONFIG_GLOBAL": str(root / "empty-gitconfig"),
                   "GIT_CONFIG_NOSYSTEM": "1"}

            def git(*args):
                return subprocess.check_output(["git", *args], cwd=source, env=env,
                                               stderr=subprocess.DEVNULL, text=True).strip()

            git("init", "-q")
            git("config", "user.name", "Checkout fixture")
            git("config", "user.email", "checkout@example.invalid")
            publisher = source / ".github/jungle_review/publish.py"
            publisher.parent.mkdir(parents=True)
            publisher.write_text("# exact reviewed source\n")
            git("add", ".")
            git("commit", "-qm", "source")
            git("update-index", "--add", "--cacheinfo", "160000," + git("rev-parse", "HEAD") + ",broken-link")
            git("commit", "-qm", "unregistered gitlink")
            expected = git("rev-parse", "HEAD")
            publisher.write_text("# newer source must not be selected\n")
            git("commit", "-qam", "later source")

            for filename in ("jungle-review.yml", "jungle-review-tests.yml"):
                with self.subTest(workflow=filename):
                    text = (workflow_directory / filename).read_text()
                    script = textwrap.dedent(text.split("        run: |\n", 1)[1].split("\n      - ", 1)[0])
                    checkout = root / filename
                    checkout.mkdir()
                    process_env = {**env, "GH_TOKEN": "fixture-only-read-token",
                                   "GITHUB_REPOSITORY": "checkout-fixture/source", "GITHUB_SHA": expected,
                                   "GIT_CONFIG_COUNT": "1",
                                   "GIT_CONFIG_KEY_0": "url." + source.as_uri() + ".insteadOf",
                                   "GIT_CONFIG_VALUE_0": "https://github.com/checkout-fixture/source.git"}
                    result = subprocess.run(["bash", "-e", "-c", script], cwd=checkout, env=process_env,
                                            capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((checkout / ".github/jungle_review/publish.py").read_text(),
                                     "# exact reviewed source\n")
                    config = (checkout / ".git/config").read_text()
                    self.assertNotIn("fixture-only-read-token", config)
                    self.assertNotIn("credential", config)
                    self.assertNotIn("extraheader", config)


if __name__ == "__main__":
    unittest.main()
