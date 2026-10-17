"""scripts/semantic_conflict_demo.py - End-to-End Semantic Merge Conflict Demonstration.

Reproduces a real cross-file semantic collision on psf/requests (pinned commit 611c616):
1. Clones psf/requests into a temporary directory at pinned commit 611c616.
2. Branch agent1: Refactors get_environ_proxies(url, no_proxy=...) -> (url, bypass_list=...)
   and safely updates ALL existing call sites (utils.py:933 and sessions.py:848).
3. Branch agent2: Introduces a new call get_environ_proxies(prep.url, no_proxy=...) in Session.request.
4. Smoke tests both branches independently -> both pass cleanly (exit code 0).
5. Executes `git merge agent1` into agent2 -> Git reports CLEAN MERGE (exit code 0, 0 text conflicts).
6. Runs smoke test on merged tree -> Runtime crash: TypeError (exit code 1).
7. Demonstrates Conflict Radar catching the in-flight collision ahead of time on FalkorDB:
   - agent-1 SIGNATURE on utils.py::get_environ_proxies -> SAFE
   - agent-2 BODY on sessions.py::Session.request -> BLOCK (2 hops via merge_environment_settings)
   - agent-2 BODY on adapters.py::_urllib3_request_context (unrelated) -> SAFE
   - agent-1 release -> agent-2 automatically promoted to ACTIVE.
8. Automatically cleans up temp repository and temp FalkorDB graph.
"""

import os
import sys
import uuid
import shutil
import tempfile
import subprocess
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from indexer import index_codebase
from graph_db import GraphDB
from radar_core import ConflictRadar
import falkordb

PINNED_COMMIT = "611c616"
REQUESTS_GIT_URL = "https://github.com/psf/requests.git"


def run_cmd(args, cwd=None, check=True):
    """Run a shell command and return CompletedProcess."""
    res = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False
    )
    if check and res.returncode != 0:
        raise RuntimeError(
            f"Command failed ({res.returncode}): {' '.join(str(x) for x in args)}\n"
            f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        )
    return res


SMOKE_TEST_SCRIPT = """
import sys
import requests
from requests.utils import resolve_proxies
from requests.models import Request

# 1. Test Session.request (exercises request & merge_environment_settings)
s = requests.Session()
s.send = lambda req, **kw: requests.Response()
resp = s.request('GET', 'http://example.com/test')
assert resp is not None

# 2. Test resolve_proxies
req = Request('GET', 'http://example.com/test').prepare()
proxies = resolve_proxies(req, {})
assert isinstance(proxies, dict)

print("SMOKE TEST OK: Session.request and resolve_proxies executed successfully.")
"""


def main():
    print("=" * 70)
    print("CONFLICT RADAR: REPRODUCIBLE SEMANTIC MERGE CONFLICT DEMO")
    print(f"Target: psf/requests @ pinned commit {PINNED_COMMIT}")
    print("=" * 70)

    temp_dir = Path(tempfile.mkdtemp(prefix="radar_demo_"))
    temp_graph_name = f"demo_semantic_{uuid.uuid4().hex[:6]}"
    falkor_client = falkordb.FalkorDB(host="localhost", port=6379)

    try:
        # Step 1: Clone requests repo
        repo_dir = temp_dir / "requests_clone"
        print(f"\n[1/7] Cloning psf/requests into {repo_dir}...")
        try:
            run_cmd(["git", "clone", "--depth", "50", REQUESTS_GIT_URL, str(repo_dir)])
            run_cmd(["git", "checkout", PINNED_COMMIT], cwd=repo_dir)
        except Exception as e:
            # Fallback to local throwaway clone if available
            local_fallback = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "throwaway_requests"
            if local_fallback.exists():
                print(f"  Note: Direct git clone failed or timed out. Falling back to local cache: {local_fallback}")
                if repo_dir.exists():
                    shutil.rmtree(repo_dir)
                run_cmd(["git", "clone", str(local_fallback), str(repo_dir)])
                run_cmd(["git", "checkout", PINNED_COMMIT], cwd=repo_dir)
            else:
                raise RuntimeError(f"Failed to clone psf/requests from {REQUESTS_GIT_URL}: {e}")

        # Configure local git author in temp repo
        run_cmd(["git", "config", "user.name", "ConflictRadarDemo"], cwd=repo_dir)
        run_cmd(["git", "config", "user.email", "demo@conflictradar.local"], cwd=repo_dir)

        # Setup isolated venv in temp_dir for running smoke tests
        venv_dir = temp_dir / "venv"
        print(f"[2/7] Preparing isolated runtime for requests in {venv_dir}...")
        import venv
        venv.create(str(venv_dir), with_pip=True)
        py_bin = venv_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
        pip_res = run_cmd([str(py_bin), "-m", "pip", "install", "urllib3", "certifi", "idna", "charset-normalizer"], cwd=repo_dir)
        run_cmd([str(py_bin), "-m", "pip", "install", "-e", "."], cwd=repo_dir)

        def run_smoke():
            return subprocess.run(
                [str(py_bin), "-c", SMOKE_TEST_SCRIPT],
                cwd=repo_dir,
                capture_output=True,
                text=True
            )

        # Step 2: Create branch agent1 (Safe refactor of get_environ_proxies + callers)
        print("\n[3/7] Branch 'agent1': Refactoring get_environ_proxies parameter no_proxy -> bypass_list...")
        run_cmd(["git", "checkout", "-b", "agent1", PINNED_COMMIT], cwd=repo_dir)

        utils_file = repo_dir / "src" / "requests" / "utils.py"
        sessions_file = repo_dir / "src" / "requests" / "sessions.py"

        # Update utils.py
        u_text = utils_file.read_text(encoding="utf-8")
        assert "def get_environ_proxies(url: str, no_proxy: str | None = None)" in u_text
        u_text = u_text.replace(
            "def get_environ_proxies(url: str, no_proxy: str | None = None) -> dict[str, str]:\n    \"\"\"\n    Return a dict of environment proxies.\n\n    :rtype: dict\n    \"\"\"\n    if should_bypass_proxies(url, no_proxy=no_proxy):",
            "def get_environ_proxies(url: str, bypass_list: str | None = None) -> dict[str, str]:\n    \"\"\"\n    Return a dict of environment proxies.\n\n    :rtype: dict\n    \"\"\"\n    if should_bypass_proxies(url, no_proxy=bypass_list):"
        )
        assert "environ_proxies = get_environ_proxies(url, no_proxy=no_proxy)" in u_text
        u_text = u_text.replace(
            "environ_proxies = get_environ_proxies(url, no_proxy=no_proxy)",
            "environ_proxies = get_environ_proxies(url, bypass_list=no_proxy)"
        )
        utils_file.write_text(u_text, encoding="utf-8")

        # Update sessions.py
        s_text = sessions_file.read_text(encoding="utf-8")
        assert "env_proxies = get_environ_proxies(url, no_proxy=no_proxy)" in s_text
        s_text = s_text.replace(
            "env_proxies = get_environ_proxies(url, no_proxy=no_proxy)",
            "env_proxies = get_environ_proxies(url, bypass_list=no_proxy)"
        )
        sessions_file.write_text(s_text, encoding="utf-8")

        run_cmd(["git", "commit", "-am", "refactor(utils): rename no_proxy to bypass_list in get_environ_proxies"], cwd=repo_dir)

        # Smoke test agent1 alone
        test1 = run_smoke()
        print(f"  agent1 standalone smoke test exit code: {test1.returncode}")
        print(f"  agent1 output: {test1.stdout.strip()}")
        assert test1.returncode == 0, f"agent1 smoke test failed: {test1.stderr}"

        # Step 3: Create branch agent2 (Add feature using base get_environ_proxies signature)
        print("\n[4/7] Branch 'agent2': Adding environment proxy pre-check in Session.request...")
        run_cmd(["git", "checkout", "-b", "agent2", PINNED_COMMIT], cwd=repo_dir)

        s_text2 = sessions_file.read_text(encoding="utf-8")
        target_marker = "proxies = proxies or {}\n\n        settings = self.merge_environment_settings("
        replacement = (
            "proxies = proxies or {}\n\n"
            "        # [Agent 2 Feature] Pre-check environment proxies for request URL\n"
            "        _env_p = get_environ_proxies(prep.url, no_proxy=proxies.get(\"no_proxy\"))\n\n"
            "        settings = self.merge_environment_settings("
        )
        assert target_marker in s_text2, "Failed to find injection target in sessions.py"
        s_text2 = s_text2.replace(target_marker, replacement, 1)
        sessions_file.write_text(s_text2, encoding="utf-8")

        run_cmd(["git", "commit", "-am", "feat(sessions): pre-check environment proxies in Session.request"], cwd=repo_dir)

        # Smoke test agent2 alone
        test2 = run_smoke()
        print(f"  agent2 standalone smoke test exit code: {test2.returncode}")
        print(f"  agent2 output: {test2.stdout.strip()}")
        assert test2.returncode == 0, f"agent2 smoke test failed: {test2.stderr}"

        # Step 4: Merge agent1 into agent2
        print("\n[5/7] Merging agent1 into agent2 via standard Git merge...")
        merge_res = run_cmd(["git", "merge", "agent1", "--no-edit"], cwd=repo_dir, check=False)
        print(f"  git merge returncode: {merge_res.returncode}")
        print(f"  git merge output:\n{merge_res.stdout.strip()}")
        assert merge_res.returncode == 0, "Git merge was not clean!"

        # Step 5: Smoke test merged tree -> Expect TypeError
        print("\n[6/7] Running smoke test on merged codebase (Git Clean, Runtime Broken)...")
        test_merged = run_smoke()
        print(f"  Merged codebase exit code: {test_merged.returncode}")
        print(f"  Merged codebase STDERR:\n{test_merged.stderr.strip()}")
        assert test_merged.returncode != 0, "Expected runtime crash on merged codebase, but test passed!"
        assert "unexpected keyword argument 'no_proxy'" in test_merged.stderr, "Expected TypeError on no_proxy keyword"
        print("  -> Confirmed: Git merged with 0 text conflicts, but runtime failed with TypeError!")

        # Step 6: Conflict Radar Live Prevention on FalkorDB
        print("\n[7/7] Demonstrating Conflict Radar prevention on FalkorDB...")
        # Check out clean un-edited base commit before indexing
        run_cmd(["git", "checkout", PINNED_COMMIT], cwd=repo_dir)
        g = falkor_client.select_graph(temp_graph_name)
        stats = index_codebase(str(repo_dir / "src" / "requests"), graph=g)
        persisted_calls = g.query("MATCH ()-[r:CALLS]->() RETURN count(r)").result_set[0][0]
        print(f"  Indexed clean requests @ {PINNED_COMMIT} into throwaway graph '{temp_graph_name}':")
        print(f"    Symbols: {stats['symbols_count']}")
        print(f"    AST call sites discovered: {stats['calls_count']} | CALLS edges persisted in graph: {persisted_calls}")

        db = GraphDB(host="localhost", port=6379, graph_name=temp_graph_name)
        db.connect()
        radar = ConflictRadar(db=db)

        # Claim 1: agent-1 SIGNATURE on utils.py::get_environ_proxies
        v1 = radar.claim("agent-1", "utils.py::get_environ_proxies", "SIGNATURE")
        print(f"\n  [Claim 1] agent-1 SIGNATURE on utils.py::get_environ_proxies:")
        print(f"    Verdict:              {v1.verdict}")
        print(f"    Claim ID:             {v1.claim_id}")
        print(f"    Conflicting Claim ID: {v1.conflicting_claim_id}")
        print(f"    Raw: {v1}")
        assert v1.verdict == "SAFE"

        # Claim 2: agent-2 BODY on sessions.py::Session.request
        v2 = radar.claim("agent-2", "sessions.py::Session.request", "BODY")
        print(f"\n  [Claim 2] agent-2 BODY on sessions.py::Session.request:")
        print(f"    Verdict:              {v2.verdict}")
        print(f"    Claim ID:             {v2.claim_id}")
        print(f"    Conflicting Claim ID: {v2.conflicting_claim_id}")
        print(f"    Hops:                 {v2.hops}")
        print(f"    Path:                 {v2.path}")
        print(f"    Raw: {v2}")
        assert v2.verdict == "BLOCK"
        assert v2.conflicting_claim_id == v1.claim_id

        # Claim 3: agent-2 BODY on unrelated symbol
        unrelated_sym = "adapters.py::_urllib3_request_context"
        v3 = radar.claim("agent-2", unrelated_sym, "BODY")
        print(f"\n  [Claim 3] agent-2 BODY on {unrelated_sym} (unrelated):")
        print(f"    Verdict:              {v3.verdict}")
        print(f"    Claim ID:             {v3.claim_id}")
        print(f"    Hops:                 {v3.hops}")
        print(f"    Raw: {v3}")
        assert v3.verdict == "SAFE"

        # Release agent-1 and check promotion
        print(f"\n  Releasing agent-1 claim ({v1.claim_id})...")
        rel = radar.release(v1.claim_id)
        print(f"    Release response: {rel}")
        chk_v2 = radar.check(v2.claim_id)
        print(f"    Check agent-2 status after release: status={chk_v2['status']}")
        assert chk_v2["status"] == "active"

        print("\n" + "=" * 70)
        print("DEMO COMPLETE: All stages verified successfully.")
        print("=" * 70)

    finally:
        # Cleanup temp graph and directory
        try:
            falkor_client.select_graph(temp_graph_name).delete()
            print(f"[Cleanup] Deleted temporary FalkorDB graph '{temp_graph_name}'.")
        except Exception:
            pass

        if temp_dir.exists():
            def remove_readonly(func, path, excinfo):
                import stat
                try:
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                except Exception:
                    pass
            try:
                shutil.rmtree(temp_dir, onerror=remove_readonly)
                print(f"[Cleanup] Removed temporary directory '{temp_dir}'.")
            except Exception as e:
                print(f"[Cleanup] Note: Could not completely remove {temp_dir}: {e}")


if __name__ == "__main__":
    main()
