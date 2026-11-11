"""`make verify` runs its own stack on free ports and frees every one of them afterwards.

No real processes or sockets: the process table, the listeners and the signals are fakes.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from db.mongo.provision import host_of
from scripts import verify
from scripts.verify import (
    PORT_NAMES,
    Result,
    Services,
    fixed_ports,
    pick_free_ports,
    reserve_ports,
    stack_env,
    with_port,
)

PORTS = {name: 40000 + i for i, name in enumerate(PORT_NAMES)}


class FakeProc:
    """A started process; `stubborn` ignores SIGTERM, so only SIGKILL stops it."""

    def __init__(self, pid: int, world: World, *, stubborn: bool = False) -> None:
        self.pid = pid
        self.world = world
        self.stubborn = stubborn

    def poll(self) -> int | None:
        return None if self.pid in self.world.alive else 0

    def wait(self, timeout: float | None = None) -> int:
        if self.pid in self.world.alive:
            raise subprocess.TimeoutExpired("fake", timeout or 0)
        return 0


class World:
    """The fake process table and listening sockets."""

    def __init__(self) -> None:
        self.alive: set[int] = set()
        self.children: dict[int, list[int]] = {}
        self.listeners: dict[int, set[int]] = {}  # port -> pids
        self.stubborn: set[int] = set()
        self.signals: list[tuple[str, int, int]] = []
        self.next_pid = 1000

    def spawn(self, command: list[str], **kwargs: object) -> FakeProc:
        assert kwargs["start_new_session"] is True
        self.next_pid += 1
        self.alive.add(self.next_pid)
        return FakeProc(self.next_pid, self, stubborn=self.next_pid in self.stubborn)

    def kill(self, kind: str, pid: int, sig: int) -> None:
        self.signals.append((kind, pid, sig))
        targets = self.tree([pid]) if kind == "group" else {pid}
        for target in targets:
            if sig == signal.SIGKILL or target not in self.stubborn:
                self.alive.discard(target)
                for port in self.listeners:
                    self.listeners[port].discard(target)

    def tree(self, pids: Iterable[int]) -> set[int]:
        found: set[int] = set()
        todo = list(pids)
        while todo:
            pid = todo.pop()
            if pid not in found:
                found.add(pid)
                todo.extend(self.children.get(pid, []))
        return found

    def services(self, tmp_path: Path) -> Services:
        return Services(
            ports=dict(PORTS),
            data=tmp_path / "stack",
            env={"TEMPORAL_ADDRESS": "localhost:1"},
            spawn=self.spawn,
            listening=lambda port: set(self.listeners.get(port, set())),
            tree=self.tree,
            is_alive=lambda pid: pid in self.alive,
            kill_group=lambda pid, sig: self.kill("group", pid, sig),
            kill_pid=lambda pid, sig: self.kill("pid", pid, sig),
            grace=0.01,
        )


@pytest.fixture(autouse=True)
def _logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify, "LOGS", tmp_path / "verify")
    monkeypatch.setenv("VERIFY_PORTS_FILE", str(tmp_path / "ports.json"))


def start_stack(world: World, services: Services) -> list[FakeProc]:
    """mongod, temporal and honcho, honcho with an API and a web child that listen."""
    mongod = services.start(["mongod"], "mongod.out")
    temporal = services.start(["temporal", "server"], "temporal.log")
    honcho = services.start(["honcho"], "stack.log")
    api, web = 5001, 5002  # honcho starts these in sessions of their own
    world.alive |= {api, web}
    world.children[honcho.pid] = [api, web]
    world.listeners = {
        PORTS["mongo"]: {mongod.pid},
        PORTS["temporal"]: {temporal.pid},
        PORTS["api"]: {api},
        PORTS["ui"]: {web},
    }
    return [mongod, temporal, honcho]  # type: ignore[list-item]


def test_free_ports_are_distinct_and_cover_every_service() -> None:
    ports = pick_free_ports()

    assert set(ports) == set(PORT_NAMES)
    assert len(set(ports.values())) == len(PORT_NAMES)
    assert all(1024 < p < 65536 for p in ports.values())


def test_stack_env_points_every_process_at_the_picked_ports(tmp_path: Path) -> None:
    dotenv = {"MONGO_URL_APP": "mongodb://benefit_app:p%40w@localhost:27017/?replicaSet=rs0"}

    env = stack_env(PORTS, dotenv, tmp_path / "mongo")

    assert (
        env["MONGO_URL_APP"]
        == f"mongodb://benefit_app:p%40w@localhost:{PORTS['mongo']}/?replicaSet=rs0"
    )
    assert env["TEMPORAL_ADDRESS"] == f"localhost:{PORTS['temporal']}"
    assert env["API_PORT"] == str(PORTS["api"])
    assert env["E2E_BASE_URL"] == env["WEB_ORIGIN"] == f"http://localhost:{PORTS['ui']}"
    assert env["MONGO_DIR"] == str(tmp_path / "mongo")


def test_with_port_keeps_credentials_and_query() -> None:
    url = "mongodb://owner:pw@db.example.com:27017/?replicaSet=rs0&authSource=admin"

    assert (
        with_port(url, 41234)
        == "mongodb://owner:pw@localhost:41234/?replicaSet=rs0&authSource=admin"
    )


def test_provision_uses_the_url_port() -> None:
    assert host_of("mongodb://owner:pw@localhost:41234/?replicaSet=rs0") == "localhost:41234"
    assert host_of("mongodb://owner:pw@localhost/?replicaSet=rs0") == "localhost:27017"


def test_teardown_after_success_stops_everything_and_frees_every_port(tmp_path: Path) -> None:
    world = World()
    services = world.services(tmp_path)
    start_stack(world, services)

    (result,) = services.stop()

    assert result.status == "PASS"
    assert world.alive == set()
    assert {pid for kind, pid, sig in world.signals if kind == "group"} == {1001, 1002, 1003}
    record = json.loads((tmp_path / "ports.json").read_text())
    assert set(record["after"].values()) == {"free"}
    assert services.stop() == []  # idempotent: atexit and finally both call it


def test_teardown_kills_a_child_honcho_left_behind(tmp_path: Path) -> None:
    world = World()
    services = world.services(tmp_path)
    start_stack(world, services)
    world.stubborn.add(5001)  # the API ignores SIGTERM

    (result,) = services.stop()

    assert result.status == "PASS"
    assert ("pid", 5001, signal.SIGKILL) in world.signals
    assert 5001 not in world.alive


def test_teardown_escalates_to_sigkill_for_a_process_that_ignores_sigterm(tmp_path: Path) -> None:
    world = World()
    world.stubborn.add(1002)  # temporal
    services = world.services(tmp_path)
    start_stack(world, services)

    (result,) = services.stop()

    assert result.status == "PASS"
    assert ("group", 1002, signal.SIGKILL) in world.signals


def test_run_all_stops_the_stack_after_a_failing_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World()
    services = world.services(tmp_path)
    monkeypatch.setattr(verify, "stage", lambda name, *a: Result(name, "FAIL", 0, "boom"))
    monkeypatch.setattr(verify, "fe_unit", lambda: Result("fe: unit", "FAIL", 0))
    monkeypatch.setattr(Services, "ensure_mongo", lambda self: start_stack(world, self) and None)
    monkeypatch.setattr(Services, "ensure_temporal", lambda self: None)
    monkeypatch.setattr(verify, "e2e_stages", lambda s: [Result("fe: e2e", "FAIL", 0)])

    results = verify.run_all(services)

    assert results[-1].name == "teardown: ports free"
    assert results[-1].status == "PASS"
    assert world.alive == set()


def test_run_all_stops_the_stack_after_an_exception_mid_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = World()
    services = world.services(tmp_path)

    def crash(self: Services) -> None:
        raise RuntimeError("Temporal did not become ready")

    monkeypatch.setattr(verify, "stage", lambda name, *a: Result(name, "PASS", 0))
    monkeypatch.setattr(Services, "ensure_mongo", lambda self: start_stack(world, self) and None)
    monkeypatch.setattr(Services, "ensure_temporal", crash)

    results = verify.run_all(services, stack_only=True)

    assert ("stack", "FAIL") in [(r.name, r.status) for r in results]
    assert results[-1].status == "PASS"
    assert world.alive == set()


def test_run_all_stops_the_stack_on_ctrl_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = World()
    services = world.services(tmp_path)

    def interrupted(*_args: object) -> Result:
        raise KeyboardInterrupt

    monkeypatch.setattr(verify, "stage", lambda name, *a: Result(name, "PASS", 0))
    monkeypatch.setattr(Services, "ensure_mongo", lambda self: start_stack(world, self) and None)
    monkeypatch.setattr(Services, "ensure_temporal", lambda self: None)
    monkeypatch.setattr(verify, "e2e_stages", interrupted)

    with pytest.raises(KeyboardInterrupt):
        verify.run_all(services, stack_only=True)

    assert world.alive == set()


def test_a_port_still_held_by_one_of_ours_is_killed_and_fails(tmp_path: Path) -> None:
    world = World()
    services = world.services(tmp_path)
    start_stack(world, services)
    orphan = 7777  # ours (tracked), but not stopped by its group signal
    services.ours.add(orphan)
    world.alive.add(orphan)
    world.stubborn.add(orphan)
    # it looks gone to the leftover sweep, but still listens
    services.is_alive = lambda pid: pid in world.alive and pid != orphan
    world.listeners[PORTS["temporal_ui"]] = {orphan}

    (result,) = services.stop()

    assert result.status == "FAIL"
    assert f"port {PORTS['temporal_ui']} (temporal_ui) still in use after teardown" in result.note
    assert ("pid", orphan, signal.SIGKILL) in world.signals


def test_a_port_held_by_someone_else_fails_and_is_never_touched(tmp_path: Path) -> None:
    world = World()
    services = world.services(tmp_path)
    start_stack(world, services)
    stranger = 9999
    world.alive.add(stranger)
    world.listeners[PORTS["ui"]] = {stranger}

    (result,) = services.stop()

    assert result.status == "FAIL"
    assert "still in use after teardown" in result.note
    assert all(pid != stranger for _, pid, _ in world.signals)
    assert stranger in world.alive
    record = json.loads((tmp_path / "ports.json").read_text())
    assert "not ours" in record["after"][str(PORTS["ui"])]


# ── Concurrent runs and pinned ports ─────────────────────────────────────────────────────────────
def sequence(*batches: dict[str, int]) -> Callable[[Iterable[str]], dict[str, int]]:
    """A fake port picker that hands out the given batches in turn, for the names asked."""
    queue = list(batches)

    def pick(names: Iterable[str]) -> dict[str, int]:
        batch = queue.pop(0)
        return {n: batch[n] for n in names if n in batch}

    return pick


def test_a_second_run_never_gets_a_port_the_first_reserved(tmp_path: Path) -> None:
    registry = tmp_path / "reservations.json"
    first = reserve_ports(registry, names=["api", "ui"], pick=sequence({"api": 41000, "ui": 41001}))
    # the OS hands the second run the first run's API port again (it isn't bound yet)
    second = reserve_ports(
        registry,
        names=["api", "ui"],
        pick=sequence({"api": 41000, "ui": 42001}, {"api": 42000}),
        owner=os.getppid(),  # another live process
    )

    assert first == {"api": 41000, "ui": 41001}
    assert second == {"api": 42000, "ui": 42001}
    assert sorted(json.loads(registry.read_text()).values()) == [[41000, 41001], [42000, 42001]]


def test_stop_releases_the_reservation(tmp_path: Path) -> None:
    registry = tmp_path / "reservations.json"
    reserve_ports(registry, names=["api"], pick=sequence({"api": 41000}))
    world = World()
    services = world.services(tmp_path)
    services.registry = registry

    services.stop()

    assert json.loads(registry.read_text()) == {}


def test_a_dead_runs_reservation_is_dropped(tmp_path: Path) -> None:
    registry = tmp_path / "reservations.json"
    registry.write_text(json.dumps({"999999": [41000]}))  # a run that crashed without releasing

    ports = reserve_ports(registry, names=["api"], pick=sequence({"api": 41000}))

    assert ports == {"api": 41000}
    assert "999999" not in json.loads(registry.read_text())


def test_pinned_ports_are_kept_and_the_rest_picked(tmp_path: Path) -> None:
    ports = reserve_ports(
        tmp_path / "r.json",
        fixed_ports("api=18000, ui=15173"),
        names=["api", "ui", "mongo"],
        pick=sequence({"mongo": 41002}),
        in_use=lambda port: False,
    )

    assert ports == {"api": 18000, "ui": 15173, "mongo": 41002}


def test_a_pinned_port_in_use_stops_the_run(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="api 18000"):
        reserve_ports(
            tmp_path / "r.json", {"api": 18000}, names=["api"], in_use=lambda port: port == 18000
        )


@pytest.mark.parametrize(
    ("spec", "message"),
    [("web=1", "unknown name 'web'"), ("api=80", "between 1025"), ("api=18000,ui=18000", "share")],
)
def test_bad_verify_ports_are_rejected(spec: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        fixed_ports(spec)


def test_no_pinned_ports_by_default() -> None:
    assert fixed_ports("") == {}
