"""Run every check CI runs, in both repos, and print one table (`make verify`).

It mirrors the jobs in .github/workflows/ci.yml: static, unit, workflow, contract, integration and
e2e for benefit-be, and static, unit, contract, build and e2e for benefit-fe. It keeps going after a
failure so one run shows everything that is wrong, writes each stage's output to
.data/verify/<stage>.log, and exits non-zero unless every stage passed.

The stages that need services run against an isolated stack of their own: a MongoDB, a Temporal
dev server, the API, the worker and the web preview, each on a free port picked for this run, with
their own data under .data/verify/stack/. So `make verify` runs beside `make dev` or another
worktree's run and never resets their data. At the end, whatever happened (pass, fail, an error,
Ctrl+C), it stops every process it started and checks that every port it used is free again; a
port still in use is a FAIL. The ports and that check's result are written to
.data/verify/ports.json (or $VERIFY_PORTS_FILE).

Runs started at the same time, from any checkout on this machine, never pick the same port: each
records its ports in ~/.cache/benefit-verify/ports.json (or $VERIFY_RESERVATIONS_FILE) under a
lock, skips the ports another live run holds, and removes its entry when it stops. To pin some
ports (to open the UI while it runs, say), set VERIFY_PORTS, such as
`VERIFY_PORTS=api=18000,ui=15173 make verify`; the rest are still picked free, and a pinned port
already in use stops the run.

`make verify-stack` (--stack-only) runs only the stages that need the stack: client in sync,
integration, stack and api tests, and e2e.

Statuses: PASS, FAIL, BLOCKED (a known external step, such as pushing the branch, is missing) and
INCONCLUSIVE (the check could not run, for example because the network is down).
"""

from __future__ import annotations

import atexit
import base64
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess  # noqa: S404 - runs the project's own check commands
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

BE = Path(__file__).resolve().parent.parent
FE = Path(os.environ.get("BENEFIT_FE_DIR", BE.parent / "benefit-fe")).resolve()
LOGS = BE / ".data" / "verify"
STACK_ENV = {"TRACING_ENABLED": "false"}
OFFLINE_MARKERS = ("NameResolutionError", "Failed to resolve", "ConnectionError", "ENOTFOUND")
# Every port the isolated stack listens on. Temporal's HTTP and metrics ports are picked too, so the
# dev server never opens a random one this run doesn't know about.
PORT_NAMES = (
    "mongo",
    "temporal",
    "temporal_ui",
    "temporal_http",
    "temporal_metrics",
    "api",
    "worker_health",
    "ui",
)


@dataclass
class Result:
    name: str
    status: str
    seconds: float
    note: str = ""


def run(command: str, cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    merged = {**os.environ, **(env or {})}
    done = subprocess.run(  # noqa: S603 - commands are fixed strings below
        shlex.split(command), cwd=cwd, env=merged, capture_output=True, text=True, check=False
    )
    return done.returncode, done.stdout + done.stderr


def log_path(name: str) -> Path:
    return LOGS / (name.replace(": ", "__").replace(" ", "_").replace("/", "_") + ".log")


def stage(name: str, command: str, cwd: Path, env: dict[str, str] | None = None) -> Result:
    started = time.monotonic()
    code, output = run(command, cwd, env)
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path(name).write_text(output)
    seconds = time.monotonic() - started
    if code == 0:
        return Result(name, "PASS", seconds)
    if any(marker in output for marker in OFFLINE_MARKERS):
        return Result(name, "INCONCLUSIVE", seconds, "network unavailable")
    return Result(name, "FAIL", seconds, output.strip().splitlines()[-1][:110] if output else "")


def fe_unit() -> Result:
    result = stage("fe: unit", "pnpm test", FE)
    if result.status != "FAIL":
        return result
    placeholder = {"ALLOW_PLACEHOLDER_BE_COMMIT": "1"}
    code, _ = run("pnpm test", FE, placeholder)
    log = log_path(result.name).read_text()
    if code == 0 and "BE_COMMIT is a full commit SHA" in log:
        return Result(
            result.name,
            "BLOCKED",
            result.seconds,
            "benefit-fe/openapi/BE_COMMIT is a placeholder until benefit-be is pushed",
        )
    return result


# ── Ports and processes ──────────────────────────────────────────────────────────────────────────
def pick_free_ports(names: Iterable[str] = PORT_NAMES) -> dict[str, int]:
    """One free localhost port per name, all different: each stays bound until all are picked."""
    sockets: list[socket.socket] = []
    ports: dict[str, int] = {}
    try:
        for name in names:
            sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sk.bind(("127.0.0.1", 0))
            sockets.append(sk)
            ports[name] = sk.getsockname()[1]
    finally:
        for sk in sockets:
            sk.close()
    return ports


def fixed_ports(spec: str, names: Iterable[str] = PORT_NAMES) -> dict[str, int]:
    """The ports $VERIFY_PORTS pins, as `api=18000,ui=15173`; every other port is picked free."""
    known = set(names)
    pinned: dict[str, int] = {}
    for item in filter(None, (part.strip() for part in spec.split(","))):
        name, _, value = item.partition("=")
        name = name.strip()
        if name not in known:
            choices = ", ".join(sorted(known))
            raise ValueError(f"VERIFY_PORTS: unknown name '{name}' (one of {choices})")
        if not value.strip().isdigit() or not 1024 < int(value) < 65536:
            raise ValueError(f"VERIFY_PORTS: '{item}' needs a port between 1025 and 65535")
        pinned[name] = int(value)
    if len(set(pinned.values())) != len(pinned):
        raise ValueError("VERIFY_PORTS: two names share a port")
    return pinned


def port_in_use(port: int) -> bool:
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sk:
                sk.settimeout(0.5)
                if sk.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            pass
    return False


def default_registry() -> Path:
    """One file for every checkout on this machine, so two worktrees' runs see each other."""
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return Path(os.environ.get("VERIFY_RESERVATIONS_FILE", cache / "benefit-verify" / "ports.json"))


@contextmanager
def locked(registry: Path) -> Iterator[dict[str, list[int]]]:
    """The reservations ({pid: ports}) under an exclusive lock, dead runs' entries dropped, written
    back on exit. Concurrent runs pick ports one at a time, so they can't pick the same one."""
    registry.parent.mkdir(parents=True, exist_ok=True)
    with (registry.parent / (registry.name + ".lock")).open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(registry.read_text()) if registry.exists() else {}
        except (OSError, ValueError):
            data = {}
        live = {pid: ports for pid, ports in data.items() if pid.isdigit() and alive(int(pid))}
        yield live
        registry.write_text(json.dumps(live, indent=2) + "\n")


def reserve_ports(
    registry: Path,
    pinned: dict[str, int] | None = None,
    names: Iterable[str] = PORT_NAMES,
    pick: Callable[[Iterable[str]], dict[str, int]] = pick_free_ports,
    in_use: Callable[[int], bool] = port_in_use,
    owner: int | None = None,
) -> dict[str, int]:
    """This run's ports: the pinned ones, plus a free port for every other name, none of them
    reserved by another live run. Recorded in the registry until `release_ports`."""
    pinned = dict(pinned or {})
    names = list(names)
    with locked(registry) as reservations:
        taken = {p for ports in reservations.values() for p in ports}
        clash = [f"{n} {p}" for n, p in pinned.items() if p in taken or in_use(p)]
        if clash:
            raise RuntimeError(f"VERIFY_PORTS: already in use: {', '.join(clash)}")
        ports = dict(pinned)
        for _ in range(20):  # the OS may hand out a port another run reserved but hasn't bound yet
            todo = [n for n in names if n not in ports]
            if not todo:
                break
            for name, port in pick(todo).items():
                if port not in taken and port not in ports.values():
                    ports[name] = port
        missing = [n for n in names if n not in ports]
        if missing:
            raise RuntimeError(f"no free port found for {', '.join(missing)}")
        reservations[str(owner or os.getpid())] = sorted(ports.values())
    return {n: ports[n] for n in names}


def release_ports(registry: Path, owner: int | None = None) -> None:
    with locked(registry) as reservations:
        reservations.pop(str(owner or os.getpid()), None)


def with_port(url: str, port: int) -> str:
    """The MongoDB URL on localhost:<port>, its credentials and query kept."""
    return re.sub(r"^(mongodb://(?:[^@/]*@)?)[^/?]+", rf"\g<1>localhost:{port}", url)


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def stack_env(ports: dict[str, int], dotenv: dict[str, str], mongo_dir: Path) -> dict[str, str]:
    """The variables that point every process and test of this run at the isolated stack."""
    env = {
        **STACK_ENV,
        "MONGO_PORT": str(ports["mongo"]),
        "MONGO_DIR": str(mongo_dir),
        "TEMPORAL_ADDRESS": f"localhost:{ports['temporal']}",
        "API_PORT": str(ports["api"]),
        "WORKER_HEALTH_PORT": str(ports["worker_health"]),
        "UI_PORT": str(ports["ui"]),
        "WEB_ORIGIN": f"http://localhost:{ports['ui']}",
        "API_BASE_URL": f"http://localhost:{ports['api']}",
        "E2E_BASE_URL": f"http://localhost:{ports['ui']}",
    }
    for key in ("MONGO_URL_APP", "MONGO_URL_OWNER"):
        url = os.environ.get(key) or dotenv.get(key)
        if url:
            env[key] = with_port(url, ports["mongo"])
    return env


def listening_pids(port: int) -> set[int]:
    """Pids listening on the TCP port, IPv4 or IPv6."""
    done = subprocess.run(  # noqa: S603
        ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return {int(p) for p in done.stdout.split() if p.isdigit()}


def descendants(pids: Iterable[int]) -> set[int]:
    """The pids and every process below them (honcho starts each process in its own session)."""
    done = subprocess.run(  # noqa: S603
        ["ps", "-A", "-o", "pid=,ppid="],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    children: dict[int, list[int]] = {}
    for line in done.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found: set[int] = set()
    todo = list(pids)
    while todo:
        pid = todo.pop()
        if pid not in found:
            found.add(pid)
            todo.extend(children.get(pid, []))
    return found


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def signal_pid(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def signal_group(pid: int, sig: int) -> None:
    """Signal the process group a tracked process leads (each starts in its own session)."""
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        signal_pid(pid, sig)


class Proc(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def popen(command: list[str], **kwargs: Any) -> Proc:
    return subprocess.Popen(command, **kwargs)  # noqa: S603 - fixed commands below


@dataclass
class Services:
    """The isolated stack: MongoDB, Temporal and the app processes, started only when a stage needs
    them and all stopped by `stop()`, which then checks every port this run used is free again."""

    ports: dict[str, int] = field(default_factory=pick_free_ports)
    # The machine-wide file `ports` are reserved in (see reserve_ports); stop() releases them.
    registry: Path | None = None
    data: Path = field(default_factory=lambda: LOGS / "stack")
    env: dict[str, str] = field(default_factory=dict)
    # Swapped in tests, so no real processes or sockets are needed.
    spawn: Callable[..., Proc] = popen
    listening: Callable[[int], set[int]] = listening_pids
    tree: Callable[[Iterable[int]], set[int]] = descendants
    is_alive: Callable[[int], bool] = alive
    kill_group: Callable[[int, int], None] = signal_group
    kill_pid: Callable[[int, int], None] = signal_pid
    grace: float = 15.0
    processes: list[Proc] = field(default_factory=list)
    ours: set[int] = field(default_factory=set)
    started: set[str] = field(default_factory=set)
    stopped: bool = False

    def __post_init__(self) -> None:
        if not self.env:
            self.env = stack_env(self.ports, read_env_file(BE / ".env"), self.data / "mongo")

    # starting
    def start(self, command: list[str], log_name: str, cwd: Path = BE) -> Proc:
        LOGS.mkdir(parents=True, exist_ok=True)
        with (LOGS / log_name).open("wb") as log:
            process = self.spawn(
                command,
                cwd=cwd,
                env={**os.environ, **self.env},
                stdout=log,
                stderr=log,
                start_new_session=True,  # its own process group, so stop() reaches what it starts
            )
        self.processes.append(process)
        self.ours.add(process.pid)
        return process

    def wait_for_port(self, port: int, seconds: float = 60) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
                sk.settimeout(0.5)
                if sk.connect_ex(("127.0.0.1", port)) == 0:
                    return True
            time.sleep(0.5)
        return False

    def ensure_mongo(self) -> None:
        if "mongo" in self.started:
            return
        self.started.add("mongo")
        mongo = self.data / "mongo"
        shutil.rmtree(mongo, ignore_errors=True)
        (mongo / "db").mkdir(parents=True)
        keyfile = mongo / "keyfile"
        keyfile.write_text(base64.b64encode(os.urandom(512)).decode())
        keyfile.chmod(0o400)
        port = str(self.ports["mongo"])
        self.start(
            ["mongod", "--replSet", "rs0", "--keyFile", str(keyfile), "--bind_ip", "127.0.0.1"]
            + [
                "--port",
                port,
                "--dbpath",
                str(mongo / "db"),
                "--logpath",
                str(mongo / "mongod.log"),
            ]
            + ["--pidfilepath", str(mongo / "mongod.pid")],
            "mongod.out",
        )
        if not self.wait_for_port(self.ports["mongo"]):
            raise RuntimeError(f"mongod did not listen on port {port}")
        # `make mongo-up` finds this mongod by its pid file in MONGO_DIR and only provisions it.
        code, output = run("make mongo-up", BE, self.env)
        if code != 0:
            raise RuntimeError("provisioning the isolated MongoDB failed: " + output[-200:])

    def ensure_temporal(self) -> None:
        if "temporal" in self.started:
            return
        self.started.add("temporal")
        temporal = shutil.which("temporal")
        if temporal is None:
            raise RuntimeError("the temporal CLI is not installed (brew bundle)")
        db = self.data / "temporal.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.unlink(missing_ok=True)
        p = self.ports
        self.start(
            [temporal, "server", "start-dev", "--headless", "--db-filename", str(db)]
            + [
                "--ip",
                "127.0.0.1",
                "--port",
                str(p["temporal"]),
                "--ui-port",
                str(p["temporal_ui"]),
            ]
            + [
                "--http-port",
                str(p["temporal_http"]),
                "--metrics-port",
                str(p["temporal_metrics"]),
            ],
            "temporal.log",
        )
        address = self.env["TEMPORAL_ADDRESS"]
        for _ in range(60):
            time.sleep(1)
            if run(f"temporal operator cluster health --address {address}", BE)[0] == 0:
                return
        raise RuntimeError(f"Temporal did not become ready on {address}")

    def start_stack(self) -> bool:
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is not installed (brew bundle)")
        # Temporal already runs (started above), so the stack starts without the Procfile's own
        # `temporal` process, which would listen on the default port.
        procfile = LOGS / "Procfile.stack"
        lines = (BE / "Procfile.ci").read_text().splitlines()
        procfile.write_text("\n".join(x for x in lines if not x.startswith("temporal:")) + "\n")
        # honcho loads .env over the environment, which would put the stack back on the default
        # ports; it gets this run's values as its env file instead (the apps still read .env for
        # everything else, with these variables taking precedence).
        env_file = LOGS / "stack.env"
        env_file.write_text("".join(f"{k}={v}\n" for k, v in self.env.items()))
        env_file.chmod(0o600)
        self.start(
            [uv, "run", "honcho", "-e", str(env_file), "-f", str(procfile), "start"], "stack.log"
        )
        code, _ = run("scripts/ci/wait-ready.sh 120", BE, self.env)
        return code == 0

    # stopping
    def stop(self) -> list[Result]:
        """Stop every process this run started, then check each of its ports is free again."""
        if self.stopped:
            return []
        self.stopped = True
        began = time.monotonic()
        # honcho starts its children in sessions of their own, so find them by parent pid
        self.ours |= self.tree([p.pid for p in self.processes])
        for process in reversed(self.processes):  # the app first, MongoDB last
            if process.poll() is None:
                self.kill_group(process.pid, signal.SIGTERM)  # mongod shuts down cleanly on SIGTERM
            try:
                process.wait(timeout=self.grace)
            except subprocess.TimeoutExpired:
                self.kill_group(process.pid, signal.SIGKILL)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        leftovers = [pid for pid in sorted(self.ours) if self.is_alive(pid)]
        for pid in leftovers:  # what a tracked process left behind
            self.kill_pid(pid, signal.SIGTERM)
        deadline = time.monotonic() + self.grace
        while leftovers and time.monotonic() < deadline:
            leftovers = [pid for pid in leftovers if self.is_alive(pid)]
            if leftovers:
                time.sleep(0.2)
        for pid in leftovers:
            self.kill_pid(pid, signal.SIGKILL)
        (LOGS / "stack.env").unlink(missing_ok=True)  # it holds the MongoDB credentials
        result = self.check_ports(time.monotonic() - began)
        if self.registry is not None:
            release_ports(self.registry)
        return [result]

    def check_ports(self, seconds: float = 0.0) -> Result:
        """FAIL if any port this run picked still has a listener. A listener of ours is killed;
        anyone else's is reported and never touched."""
        still: list[str] = []
        after: dict[str, str] = {}
        for name, port in self.ports.items():
            holders = self.listening(port)
            if not holders:
                after[str(port)] = "free"
                continue
            mine = holders & self.ours
            for pid in sorted(mine):
                self.kill_pid(pid, signal.SIGKILL)
            others = holders - mine
            after[str(port)] = "in use" + (f", not ours: pids {sorted(others)}" if others else "")
            still.append(f"port {port} ({name}) still in use after teardown")
        self.write_ports_file(after)
        if still:
            return Result("teardown: ports free", "FAIL", seconds, "; ".join(still))
        return Result("teardown: ports free", "PASS", seconds, f"all {len(self.ports)} ports free")

    def write_ports_file(self, after: dict[str, str] | None = None) -> None:
        path = Path(os.environ.get("VERIFY_PORTS_FILE", str(LOGS / "ports.json")))
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ports": self.ports, "pids": sorted(self.ours), "after": after}
        path.write_text(json.dumps(record, indent=2) + "\n")


def e2e_stages(services: Services) -> list[Result]:
    password = read_env_file(BE / ".env").get("DEMO_PASSWORD", "")
    results = [stage("fe: build", "pnpm build", FE)]
    services.ensure_mongo()
    services.ensure_temporal()
    run("make reset", BE, services.env)
    if not services.start_stack():
        results.append(Result("be: stack + api tests", "FAIL", 0, "stack did not become ready"))
        results.append(Result("fe: e2e", "FAIL", 0, "stack did not become ready"))
        return results
    results.append(
        stage("be: stack + api tests", 'uv run pytest -m "stack or api"', BE, services.env)
    )
    results.append(stage("fe: e2e", "pnpm e2e", FE, {**services.env, "E2E_PASSWORD": password}))
    return results


PLAIN: list[tuple[str, str, Path, dict[str, str] | None]] = [
    ("be: lint", "make lint", BE, None),
    ("be: types", "make typecheck", BE, None),
    ("be: pip-audit", "uv run pip-audit", BE, None),
    (
        "be: gitleaks",
        "gitleaks detect --no-banner --config scripts/gitleaks.toml --log-opts=HEAD",
        BE,
        None,
    ),
    ("be: unit", "make test-unit", BE, None),
    ("be: workflow", "make test-workflow", BE, None),
    ("be: openapi in sync", "make openapi-check", BE, None),
    ("be: contract", "make test-contract", BE, None),
    ("fe: lint", "pnpm lint", FE, None),
    ("fe: format", "pnpm format:check", FE, None),
    ("fe: types", "pnpm typecheck", FE, None),
]
CLIENT_IN_SYNC: tuple[str, str, Path, dict[str, str]] = (
    "fe: client in sync",
    "pnpm api:check",
    FE,
    {"BE_LOCAL": "1"},
)


def run_all(services: Services, *, stack_only: bool = False) -> list[Result]:
    """Every stage, then the teardown check, which runs whatever happened before it."""
    results: list[Result] = []
    try:
        if not stack_only:
            results.extend(stage(*plain) for plain in PLAIN)
        results.append(stage(*CLIENT_IN_SYNC))
        if not stack_only:
            results.append(fe_unit())
        services.ensure_mongo()
        services.ensure_temporal()
        services.write_ports_file()
        results.append(stage("be: integration", "make test-integration", BE, services.env))
        results.extend(e2e_stages(services))
    except Exception as exc:  # noqa: BLE001 - reported as a stage; the stack is stopped either way
        results.append(Result("stack", "FAIL", 0, str(exc)[:110]))
    finally:
        results.extend(services.stop())
    return results


def stop_on_signal(services: Services) -> None:
    """Ctrl+C or a kill ends the run through `finally` (and atexit), so the stack is stopped."""

    def handler(signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    atexit.register(services.stop)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    registry = default_registry()
    try:
        ports = reserve_ports(registry, fixed_ports(os.environ.get("VERIFY_PORTS", "")))
    except (ValueError, RuntimeError) as exc:
        print(f"verify: {exc}", file=sys.stderr)
        return 2
    services = Services(ports=ports, registry=registry)
    stop_on_signal(services)
    listed = ", ".join(f"{k} {v}" for k, v in services.ports.items())
    print(f"isolated stack ports: {listed}", flush=True)
    results = run_all(services, stack_only="--stack-only" in args)

    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{r.status:<12} {r.name:<{width}}  {r.seconds:6.1f}s  {r.note}")
    failed = [r for r in results if r.status != "PASS"]
    print(f"\n{len(results) - len(failed)} of {len(results)} stages passed; logs in {LOGS}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
