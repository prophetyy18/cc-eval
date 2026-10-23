"""The console: authoring, the security model, and the live run stream.

The console can be exposed to the internet, so the security properties are
tested as carefully as the happy path:

* it refuses to start on a public interface without a token;
* reading and writing are separate roles;
* a case id or a framework path from the network cannot escape its directory;
* the case it writes is an ordinary cc-eval case that the loader sees.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from cc_eval.core.config import EvalPaths
from cc_eval.dashboard.authoring import AuthoringError, write_case
from cc_eval.dashboard.runs import RunHandle, RunManager, RunRequestError
from cc_eval.dashboard.server import ConsoleConfig, ConsoleServer
from cc_eval.dashboard.types import all_types
from cc_eval.dashboard.types import get as get_test_type

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _post(url: str, payload: dict, cookie: str | None = None) -> tuple[int, dict, str]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
            set_cookie = response.headers.get("Set-Cookie", "")
            return response.status, json.loads(body or "{}"), set_cookie
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body or "{}")
        except json.JSONDecodeError:
            parsed = {"error": body}
        return exc.code, parsed, exc.headers.get("Set-Cookie", "")


def _get(url: str, cookie: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(url, method="GET")
    if cookie:
        request.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body or "{}")
        except json.JSONDecodeError:
            parsed = {"error": body}
        return exc.code, parsed


@pytest.fixture()
def console(tmp_path: Path, monkeypatch):
    """A console on an ephemeral port, with a viewer and an admin token.

    The authoring code writes into the *repository*, so the tests point it at
    a temporary copy: a test must never add a case to the shipped benchmark.
    """
    import cc_eval.dashboard.authoring as authoring

    repo = tmp_path / "repo"
    (repo / "benchmarks" / "intent_skill" / "cases").mkdir(parents=True)
    (repo / "benchmarks" / "intent_skill" / "manifest.yaml").write_text(
        "name: intent-skill\nversion: v1\ncases: []\n", encoding="utf-8"
    )
    monkeypatch.setattr(authoring, "repo_root", lambda: repo)

    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    server = ConsoleServer(
        paths,
        ConsoleConfig(
            host="127.0.0.1",
            port=0,
            viewer_token="viewer-token",
            admin_token="admin-token",
            allow_write=True,
            allowed_framework_roots=[tmp_path],
        ),
    )
    import socketserver

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _handler(server))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, server
    httpd.shutdown()
    httpd.server_close()


def _handler(server: ConsoleServer):
    from cc_eval.dashboard.server import _make_handler

    return _make_handler(server)


# --------------------------------------------------------------------------- #
# test types
# --------------------------------------------------------------------------- #


def test_the_intent_test_type_is_registered() -> None:
    types = all_types()
    assert [t.name for t in types] == ["intent-skill"]
    intent = get_test_type("intent-skill")
    assert [f.key for f in intent.form] == ["request", "title", "tags"]
    assert intent.outputs[0].collect == "docs/intents/*/INTENT.md"
    assert intent.task_type == "intent_capture"


# --------------------------------------------------------------------------- #
# authoring
# --------------------------------------------------------------------------- #


def test_authoring_writes_a_real_case(tmp_path: Path, monkeypatch) -> None:
    """The console writes an ordinary case the loader can read."""
    import cc_eval.dashboard.authoring as authoring
    from cc_eval.datasets.loader import load_benchmark

    monkeypatch.setattr(authoring, "repo_root", lambda: tmp_path)
    intent = get_test_type("intent-skill")
    (tmp_path / "benchmarks" / "intent_skill" / "cases").mkdir(parents=True)
    (tmp_path / "benchmarks" / "intent_skill" / "manifest.yaml").write_text(
        "name: intent-skill\nversion: v1\ncases: []\n", encoding="utf-8"
    )

    case = write_case(
        intent,
        {"request": "客服说订单找不到\n", "title": "订单找不到", "tags": ["a", "b"]},
    )
    assert case["case_id"] == "INT-S001"

    case_dir = tmp_path / "benchmarks" / "intent_skill" / "cases" / "INT-S001"
    assert (case_dir / "input" / "request.md").read_text(encoding="utf-8") == "客服说订单找不到\n"

    # The loader sees the new case.
    bench = load_benchmark(tmp_path / "benchmarks" / "intent_skill")
    assert bench.case_ids == ["INT-S001"]
    loaded = bench.cases[0]
    assert loaded.bundle.task_type == "intent_capture"
    assert loaded.bundle.allowed_tools
    assert loaded.bundle.output_paths == ["docs/intents"]
    assert loaded.bundle.input_files["request.md"] == "客服说订单找不到\n"


def test_authoring_round_trips_oracle_and_registers_case(tmp_path: Path, monkeypatch) -> None:
    import cc_eval.dashboard.authoring as authoring
    from cc_eval.core.models import RunResult
    from cc_eval.datasets.loader import load_benchmark
    from cc_eval.evaluators import get_evaluator

    monkeypatch.setattr(authoring, "repo_root", lambda: tmp_path)
    benchmark = tmp_path / "benchmarks" / "intent_skill"
    (benchmark / "cases").mkdir(parents=True)
    (benchmark / "manifest.yaml").write_text(
        "name: intent-skill\nversion: v1\ncases: []\n", encoding="utf-8"
    )
    intent = get_test_type("intent-skill")
    original = "expected_output:\n  expected_intent_count: 1\nnotes: first\n"
    case = write_case(intent, {"request": "PHA 数量不变", "title": "PHA"}, oracle_yaml=original)
    assert case["registered"] is True
    assert case["has_oracle"] is True
    assert case["oracle_yaml"] == original
    assert load_benchmark(benchmark).cases[0].oracle.expected_output["expected_intent_count"] == 1

    task_file = benchmark / "cases" / case["case_id"] / "task.yaml"
    task_text = task_file.read_text(encoding="utf-8")
    task_file.write_text(task_text.replace("There is no human", "There is a human"), encoding="utf-8")
    updated = original.replace("count: 1", "count: 2")
    edited = write_case(
        intent, {"request": "PHA 数量必须不变", "title": "PHA"},
        case_id=case["case_id"], oracle_yaml=updated,
    )
    assert edited["oracle_yaml"] == updated
    assert load_benchmark(benchmark).cases[0].oracle.expected_output["expected_intent_count"] == 2
    assert "There is a human" in task_file.read_text(encoding="utf-8")

    # The normal evaluator, not a separate console-only scorer, reads the
    # edited oracle from the benchmark when this case is run.
    result = RunResult(
        run_id="R1", experiment_id="E1", case_id=case["case_id"], variant="baseline",
        output_files={"docs/intents/INT-001/INTENT.md": "id: INT-001\n"},
    )
    evaluator = get_evaluator("rubric:intent-doc-v1", Path(__file__).resolve().parents[1] / "rubrics")
    scored = evaluator.evaluate(load_benchmark(benchmark).cases[0], result)
    assert scored.details["DECOMPOSITION"]["score"] == 0.0

    with pytest.raises(AuthoringError):
        write_case(
            intent, {"request": "不应写入"}, case_id=case["case_id"],
            oracle_yaml="expected_output: [broken",
        )
    assert load_benchmark(benchmark).cases[0].bundle.input_files["request.md"] == "PHA 数量必须不变"


def test_authoring_rejects_a_traversing_case_id(tmp_path: Path, monkeypatch) -> None:
    import cc_eval.dashboard.authoring as authoring

    monkeypatch.setattr(authoring, "repo_root", lambda: tmp_path)
    intent = get_test_type("intent-skill")
    with pytest.raises(AuthoringError):
        write_case(intent, {"request": "x"}, case_id="../../etc/passwd")
    with pytest.raises(AuthoringError):
        write_case(intent, {"request": "x"}, case_id="a/b")


def test_authoring_requires_the_required_fields(tmp_path: Path, monkeypatch) -> None:
    import cc_eval.dashboard.authoring as authoring

    monkeypatch.setattr(authoring, "repo_root", lambda: tmp_path)
    intent = get_test_type("intent-skill")
    (tmp_path / "benchmarks" / "intent_skill" / "cases").mkdir(parents=True)
    (tmp_path / "benchmarks" / "intent_skill" / "manifest.yaml").write_text(
        "name: intent-skill\nversion: v1\ncases: []\n", encoding="utf-8"
    )
    with pytest.raises(AuthoringError):
        write_case(intent, {"request": "   "})


# --------------------------------------------------------------------------- #
# the security model
# --------------------------------------------------------------------------- #


def test_it_refuses_to_bind_a_public_interface_without_a_token(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    with pytest.raises(SystemExit) as exc:
        ConsoleServer(paths, ConsoleConfig(host="0.0.0.0", port=30000))
    assert "token" in str(exc.value)


def test_it_refuses_writing_without_an_admin_token(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    with pytest.raises(SystemExit):
        ConsoleServer(
            paths,
            ConsoleConfig(host="127.0.0.1", port=30000, viewer_token="v", allow_write=True),
        )


def test_admin_token_stays_read_only_without_allow_write(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    server = ConsoleServer(
        paths, ConsoleConfig(host="127.0.0.1", port=0, admin_token="admin-token")
    )
    import socketserver

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _handler(server))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        _, login, cookie = _post(f"{base}/api/login", {"token": "admin-token"})
        assert login["can_write"] is False
        status, _, _ = _post(
            f"{base}/api/test-types/intent-skill/cases",
            {"values": {"request": "x"}}, cookie.split(";")[0],
        )
        assert status == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_reading_needs_a_token_and_writing_needs_the_admin_one(console) -> None:
    base, _ = console

    # No cookie: the API is closed.
    status, _ = _get(f"{base}/api/test-types")
    assert status == 401

    # The viewer token may read but not write.
    _, _, cookie = _post(f"{base}/api/login", {"token": "viewer-token"})
    assert "cc_eval_session=" in cookie
    session_cookie = cookie.split(";")[0]

    status, _ = _get(f"{base}/api/test-types", session_cookie)
    assert status == 200

    status, _, _ = _post(
        f"{base}/api/test-types/intent-skill/cases",
        {"values": {"request": "x"}},
        session_cookie,
    )
    assert status == 403


def test_a_wrong_token_is_rejected(console) -> None:
    base, _ = console
    status, payload, _ = _post(f"{base}/api/login", {"token": "nope"})
    assert status == 401
    assert payload["error"]


def test_the_admin_token_may_write(console) -> None:
    base, _ = console
    _, _, cookie = _post(f"{base}/api/login", {"token": "admin-token"})
    session_cookie = cookie.split(";")[0]
    status, payload, _ = _post(
        f"{base}/api/test-types/intent-skill/cases",
        {"values": {"request": "hello"}},
        session_cookie,
    )
    assert status == 200, payload
    assert payload["case"]["case_id"].startswith("INT-S")


def test_admin_can_edit_hidden_oracle_via_api(console) -> None:
    base, _ = console
    _, _, cookie = _post(f"{base}/api/login", {"token": "admin-token"})
    session_cookie = cookie.split(";")[0]
    oracle = "expected_output:\n  expected_intent_count: 1\n"
    status, payload, _ = _post(
        f"{base}/api/test-types/intent-skill/cases",
        {"values": {"request": "一个意图"}, "oracle_yaml": oracle},
        session_cookie,
    )
    assert status == 200, payload
    assert payload["case"]["oracle_yaml"] == oracle
    assert payload["case"]["registered"] is True
    status, listed = _get(f"{base}/api/test-types/intent-skill/cases", session_cookie)
    assert status == 200
    assert listed["cases"][0]["oracle_yaml"] == oracle


def test_admin_can_create_candidate_branch_via_api(console, monkeypatch) -> None:
    import subprocess

    base, server = console
    repo = server.paths.root.parent / "cc-workflow"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    skill = repo / ".claude/skills/intent/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("original\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "base")

    _, _, cookie = _post(f"{base}/api/login", {"token": "admin-token"})
    session_cookie = cookie.split(";")[0]
    status, listed = _get(f"{base}/api/candidates", session_cookie)
    assert status == 200
    assert listed["suggested_repository"] == str(repo)
    status, payload, _ = _post(
        f"{base}/api/candidates",
        {"repository": str(repo), "name": "clarify first"}, session_cookie,
    )
    assert status == 200, payload
    candidate = payload["candidate"]
    assert candidate["branch"].startswith("codex/intent-clarify-first-")
    status, read_back = _get(f"{base}/api/candidates/{candidate['id']}", session_cookie)
    assert status == 200
    assert read_back["candidate"]["baseline_commit"] == candidate["baseline_commit"]

    def capture_edit(candidate_id, instruction, allowed_paths, limits, permission_mode):
        assert candidate_id == candidate["id"]
        assert instruction == "Clarify intent"
        assert allowed_paths == [".claude/skills/intent/"]
        assert permission_mode == "bypassPermissions"
        return {**candidate, "permission_mode": permission_mode}

    monkeypatch.setattr(server.candidates, "edit", capture_edit)
    status, edited, _ = _post(
        f"{base}/api/candidates/{candidate['id']}/edit",
        {"instruction": "Clarify intent", "allowed_paths": [".claude/skills/intent/"],
         "permission_mode": "bypassPermissions"}, session_cookie,
    )
    assert status == 200, edited
    assert edited["candidate"]["permission_mode"] == "bypassPermissions"

    def capture_discard(candidate_id):
        assert candidate_id == candidate["id"]
        return {**candidate, "status": "ready", "changed_files": []}

    monkeypatch.setattr(server.candidates, "discard_current", capture_discard)
    status, discarded, _ = _post(
        f"{base}/api/candidates/{candidate['id']}/discard-current", {}, session_cookie,
    )
    assert status == 200, discarded
    assert discarded["candidate"]["status"] == "ready"


def test_candidate_publish_preview_links_latest_experiment(console, monkeypatch) -> None:
    base, server = console
    candidate_id = "a" * 12
    head = "b" * 40
    server.registry.register_experiment(
        "EXP-PUBLISH", candidate_id=candidate_id, candidate_commit=head,
        status="completed",
    )
    monkeypatch.setattr(server.candidates, "publish_preview", lambda candidate: {
        "candidate_id": candidate, "candidate_commit": head, "ready": True,
    })
    _, _, viewer_cookie = _post(f"{base}/api/login", {"token": "viewer-token"})
    status, _ = _get(
        f"{base}/api/candidates/{candidate_id}/publish-preview", viewer_cookie.split(";")[0],
    )
    assert status == 403
    _, _, cookie = _post(f"{base}/api/login", {"token": "admin-token"})
    status, payload = _get(
        f"{base}/api/candidates/{candidate_id}/publish-preview", cookie.split(";")[0],
    )
    assert status == 200
    assert payload["preview"]["latest_experiment"] == {
        "experiment_id": "EXP-PUBLISH", "status": "completed",
        "candidate_commit": head, "matches_current_commit": True,
    }


def test_merge_to_main_api_requires_admin_and_passes_checked_commits(console, monkeypatch) -> None:
    base, server = console
    candidate_id = "a" * 12
    main_sha = "b" * 40
    candidate_sha = "c" * 40
    calls = []

    def merge(candidate, main, head):
        calls.append((candidate, main, head))
        return {"merged_commit": "d" * 40, "remote_pushed": True, "local_main_updated": True}

    monkeypatch.setattr(server.candidates, "merge_to_main", merge)
    path = f"{base}/api/candidates/{candidate_id}/merge-to-main"
    payload = {"expected_main_commit": main_sha, "expected_candidate_commit": candidate_sha}
    status, _, _ = _post(path, payload)
    assert status == 401
    _, _, viewer_cookie = _post(f"{base}/api/login", {"token": "viewer-token"})
    status, _, _ = _post(path, payload, viewer_cookie.split(";")[0])
    assert status == 403
    assert calls == []
    _, _, admin_cookie = _post(f"{base}/api/login", {"token": "admin-token"})
    status, response, _ = _post(path, payload, admin_cookie.split(";")[0])
    assert status == 200
    assert response["merge"]["remote_pushed"] is True
    assert calls == [(candidate_id, main_sha, candidate_sha)]
    monkeypatch.setattr(server.candidates, "sync_local_main", lambda candidate: {
        "main_commit": main_sha, "local_main_updated": candidate == candidate_id,
    })
    status, response, _ = _post(
        f"{base}/api/candidates/{candidate_id}/sync-main", {}, admin_cookie.split(";")[0],
    )
    assert status == 200 and response["sync"]["local_main_updated"] is True


def test_live_run_snapshot_and_event_stream_use_the_run_id(console) -> None:
    base, server = console
    handle = RunHandle(id="CONSOLE-001-EXP-LIVE", test_type="intent-skill", status="finished")
    handle.results.append({"run_id": "EXP-LIVE-baseline-C1-r1", "outputs": {"INTENT.md": "live result"}})
    handle.publish({"type": "run_planned", "run_id": "EXP-LIVE-baseline-C1-r1"})
    handle.publish({"type": "experiment_finished", "experiment_id": "EXP-LIVE", "runs": 1})
    with server.manager._lock:
        server.manager._handles[handle.id] = handle

    _, _, cookie = _post(f"{base}/api/login", {"token": "admin-token"})
    session_cookie = cookie.split(";")[0]
    status, snapshot = _get(f"{base}/api/runs/{handle.id}", session_cookie)
    assert status == 200
    assert snapshot["results"][0]["outputs"]["INTENT.md"] == "live result"
    request = urllib.request.Request(f"{base}/api/runs/{handle.id}/events")
    request.add_header("Cookie", session_cookie)
    with urllib.request.urlopen(request, timeout=10) as response:
        lines = []
        while len(lines) < 10:
            line = response.readline().decode("utf-8")
            lines.append(line)
            if '"type": "experiment_finished"' in line:
                break
        streamed = "".join(lines)
    assert '"type": "run_planned"' in streamed
    assert '"type": "experiment_finished"' in streamed


def test_the_session_cookie_is_signed(console) -> None:
    base, server = console
    forged = "eyJyb2xlIjoiYWRtaW4iLCJleHAiOjk5OTk5OTk5OTl9.forged"
    status, _ = _get(f"{base}/api/test-types", forged)
    assert status == 401


def test_a_report_path_cannot_escape_the_reports_directory(console) -> None:
    base, _ = console
    _, _, cookie = _post(f"{base}/api/login", {"token": "viewer-token"})
    session_cookie = cookie.split(";")[0]
    status, _ = _get(f"{base}/reports/..%2f..%2fconfig.yaml/report.html", session_cookie)
    assert status in {400, 404}


# --------------------------------------------------------------------------- #
# the run request
# --------------------------------------------------------------------------- #


def test_a_framework_path_outside_the_allowed_roots_is_refused(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    manager = RunManager(paths, allowed_framework_roots=[tmp_path / "allowed"])
    with pytest.raises(RunRequestError) as exc:
        manager.start(
            {
                "test_type": "intent-skill",
                "baseline": {"type": "path", "path": "/etc"},
                "candidate": {"type": "path", "path": str(tmp_path / "allowed" / "x")},
            }
        )
    assert "allowed roots" in str(exc.value)


def test_an_unknown_test_type_is_refused(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    manager = RunManager(paths)
    with pytest.raises(RunRequestError):
        manager.start({"test_type": "nope"})


def test_an_unknown_case_is_refused(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    manager = RunManager(paths)
    with pytest.raises(RunRequestError) as exc:
        manager.start({"test_type": "intent-skill", "case_ids": ["INT-NOPE"]})
    assert "unknown case" in str(exc.value)


def test_console_run_requires_an_explicit_case_selection(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    manager = RunManager(paths)
    with pytest.raises(RunRequestError, match="至少选择一个用例"):
        manager.start({"test_type": "intent-skill", "case_ids": []})


def test_intent_console_experiment_declares_skill_change_and_rubric(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    manager = RunManager(paths, allowed_framework_roots=[tmp_path])
    spec, _, case_ids, _, _ = manager._prepare({
        "test_type": "intent-skill",
        "case_ids": ["INT-S002"],
        "framework_change": "Ask before assuming",
        "baseline": {"type": "path", "path": str(tmp_path / "base")},
        "candidate": {"type": "path", "path": str(tmp_path / "candidate")},
    })
    assert case_ids == ["INT-S002"]
    assert spec.controls.evaluator == "rubric:intent-doc-v1"
    assert spec.decision_rules.primary_metric == "rubric_score"
    assert spec.expected_changes == [".claude/skills/intent/*"]
    assert spec.controls.max_turns == 100
    assert spec.framework_change == "Ask before assuming"


def test_bound_candidate_declares_all_changed_files_as_expected(tmp_path: Path) -> None:
    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    manager = RunManager(paths, allowed_framework_roots=[tmp_path])
    changed = [
        ".claude/skills/intent/SKILL.md",
        ".claude/skills/intent/references/clarification.md",
        ".claude/references/verification.md",
    ]
    spec, _, _, _, _ = manager._prepare({
        "test_type": "intent-skill",
        "case_ids": ["INT-S002"],
        "candidate_id": "candidate-1",
        "max_turns": 120,
        "candidate_snapshot": {"comparison_files": changed},
        "baseline": {"type": "path", "path": str(tmp_path / "base")},
        "candidate": {"type": "path", "path": str(tmp_path / "candidate")},
    })
    assert spec.expected_changes == changed
    assert spec.controls.max_turns == 120


# --------------------------------------------------------------------------- #
# the whole loop: author -> run -> stream
# --------------------------------------------------------------------------- #


def test_a_case_can_be_authored_run_and_streamed(tmp_path: Path, monkeypatch) -> None:
    """The console's core loop, end to end, with the fake runner.

    The test type is registered on the fly: that is how a new kind of test is
    added - a registration, not a code change.
    """
    import cc_eval.dashboard.authoring as authoring
    from cc_eval.dashboard.types import Field, OutputSpec, TestType, register

    repo = tmp_path / "repo"
    benchmark = repo / "benchmarks" / "toy"
    (benchmark / "cases").mkdir(parents=True)
    (benchmark / "manifest.yaml").write_text("name: toy\nversion: v1\ncases: []\n", encoding="utf-8")
    monkeypatch.setattr(authoring, "repo_root", lambda: repo)

    # A framework the fake runner can execute.
    framework = tmp_path / "framework"
    (framework / "skills").mkdir(parents=True)
    (framework / "skills" / "toy.py").write_text(
        "def run(payload):\n    return {'findings': [{'id': 'X-1', 'description': 'toy'}]}\n",
        encoding="utf-8",
    )
    (framework / "skills.manifest.json").write_text(
        json.dumps(
            {
                "name": "toy",
                "skills": [
                    {"name": "toy", "module": "skills/toy.py", "task_types": ["toy_task"]}
                ],
            }
        ),
        encoding="utf-8",
    )

    register(
        TestType(
            name="toy",
            title="Toy",
            benchmark=Path("benchmarks/toy"),
            form=[Field("request", "输入", "textarea", required=True)],
            outputs=[
                OutputSpec(
                    key="doc",
                    label="out.json",
                    collect="out/*.json",
                    view="text",
                    count_pattern=r'"id"',
                    count_label="条目",
                )
            ],
            prompt_template="Do the thing.\n",
            input_files={"request": "request.md"},
            output_paths=["out"],
            case_prefix="TOY",
            task_type="toy_task",
        )
    )

    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    manager = RunManager(paths, allowed_framework_roots=[tmp_path])

    # 1. author the case
    case = write_case(
        get_test_type("toy"), {"request": "hello"}, case_id=None
    )
    assert case["case_id"] == "TOY001"

    # 2. run it
    handle = manager.start(
        {
            "test_type": "toy",
            "case_ids": ["TOY001"],
            "runner": "fake",
            "repetitions": 1,
            "baseline": {"type": "path", "path": str(framework)},
            "candidate": {"type": "path", "path": str(framework)},
        }
    )

    # 3. wait for the stream to finish
    subscriber = manager.subscribe(handle.id)
    assert subscriber is not None
    events = []
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            event = subscriber.get(timeout=5)
        except Exception:  # pragma: no cover - the queue is closed on timeout
            break
        events.append(event)
        if event["type"] in {"experiment_finished", "experiment_failed"}:
            break

    kinds = [event["type"] for event in events]
    assert "run_planned" in kinds
    assert "run_started" in kinds
    assert "run_finished" in kinds
    assert kinds[-1] == "experiment_finished", events
    assert events[-1]["report_url"].endswith("/report.html")
    assert (paths.reports / events[-1]["report_url"].split("/")[-2] / "report.html").exists()
    experiment_id = events[-1]["experiment_id"]
    saved_spec = paths.experiments / f"{experiment_id}.yaml"
    saved_snapshot = paths.experiments / f"{experiment_id}.dataset.json"
    assert saved_spec.is_file()
    snapshot = json.loads(saved_snapshot.read_text(encoding="utf-8"))
    assert snapshot["case_ids"] == ["TOY001"]
    assert snapshot["cases"][0]["bundle"]["input_files"]["request.md"] == "hello"
    (benchmark / "cases" / "TOY001" / "input" / "request.md").write_text("changed", encoding="utf-8")
    assert json.loads(saved_snapshot.read_text(encoding="utf-8"))["cases"][0]["bundle"]["input_files"]["request.md"] == "hello"

    # A viewer reads the structured report in the console; the original HTML
    # remains available as an export.
    server = ConsoleServer(paths, ConsoleConfig(viewer_token="viewer-token"))
    import socketserver

    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _handler(server))
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        status, _ = _get(f"{base}/api/experiments/{experiment_id}")
        assert status == 401
        _, _, cookie = _post(f"{base}/api/login", {"token": "viewer-token"})
        status, payload = _get(f"{base}/api/experiments/{experiment_id}", cookie.split(";")[0])
        assert status == 200
        assert payload["report"]["metadata"]["experiment_id"] == experiment_id
        assert payload["snapshot"]["cases"][0]["bundle"]["input_files"]["request.md"] == "hello"
        assert payload["experiment"]["dataset_hash"] == snapshot["content_hash"]
    finally:
        httpd.shutdown()
        httpd.server_close()

    finished = [event for event in events if event["type"] == "run_finished"]
    assert len(finished) == 2  # baseline + candidate
    for event in finished:
        assert event["status"] == "success"
        assert "out/result.json" in event["outputs"]
        assert event["counts"]["doc"] >= 1


def test_a_restart_keeps_the_session(tmp_path: Path) -> None:
    """Restarting the console must not log everybody out."""
    from cc_eval.dashboard.server import ConsoleServer as Server

    paths = EvalPaths(tmp_path / "ws")
    paths.ensure()
    config = ConsoleConfig(
        host="127.0.0.1", port=0, viewer_token="v-token", admin_token="a-token"
    )
    first = Server(paths, config)
    second = Server(paths, config)
    assert first.secret == second.secret

    cookie = first.issue("viewer")
    session = second.read_session(cookie)
    assert session is not None
    assert session.role == "viewer"

    # A different token is a different secret: the old sessions die with it.
    other = Server(paths, ConsoleConfig(host="127.0.0.1", port=0, viewer_token="other"))
    assert other.read_session(cookie) is None
