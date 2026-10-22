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
from cc_eval.dashboard.runs import RunManager, RunRequestError
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
    assert "run_finished" in kinds
    assert kinds[-1] == "experiment_finished", events

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
