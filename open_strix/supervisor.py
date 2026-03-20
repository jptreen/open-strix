"""
Climber supervisor — manages the lifecycle of climbing subprocesses.

Core infrastructure for the mountaineering skill. Handles:
- Manifest-based registration (which climbs are active)
- Process spawning with heartbeat pipe (cross-platform parent-death detection)
- In-memory frozen eval files (Law 4 — scope separation by architecture)
- Manifest-driven restart on supervisor recovery
- Status reporting for monitoring blocks

Usage from an open-strix agent:
    from open_strix.supervisor import Supervisor

    sup = Supervisor(state_dir="/path/to/runtime/state")
    sup.register("my-climb", "/path/to/climb/dir")
    sup.start_all()  # On boot — restarts registered climbs
    status = sup.status()  # For monitoring block
    sup.unregister("my-climb")  # Stop and remove
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


# The climber script lives in the mountaineering builtin skill
CLIMBER_SCRIPT = (
    Path(__file__).parent / "builtin_skills" / "mountaineering" / "climber.py"
)


class Supervisor:
    """Manages the lifecycle of climber subprocesses.

    The supervisor holds frozen copies of evaluation files in memory (Law 4).
    The climber process never has eval files on disk in its workspace — it
    calls back to the supervisor to run evaluations.
    """

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.state_dir / "climbers.json"
        self._processes: dict[str, subprocess.Popen] = {}
        self._heartbeat_fds: dict[str, int] = {}  # write-end fds
        self._frozen_evals: dict[str, dict[str, str]] = {}  # climb_id -> {path: content}

    def _load_manifest(self) -> dict:
        """Load the manifest of registered climbs."""
        if not self.manifest_path.exists():
            return {}
        with open(self.manifest_path) as f:
            return json.load(f)

    def _save_manifest(self, manifest: dict):
        """Save the manifest."""
        with open(self.manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

    def _freeze_eval_files(self, climb_id: str, climb_dir: Path):
        """Load eval files into memory at registration time (Law 4).

        The climber never touches these. The supervisor runs eval by
        writing frozen copies to a temp location, executing, and cleaning up.
        """
        eval_dir = climb_dir / "eval"
        if not eval_dir.is_dir():
            return

        frozen = {}
        for fpath in sorted(eval_dir.rglob("*")):
            if fpath.is_file():
                rel = str(fpath.relative_to(climb_dir))
                with open(fpath) as f:
                    frozen[rel] = f.read()

        self._frozen_evals[climb_id] = frozen

    def register(
        self,
        climb_id: str,
        climb_dir: str | Path,
        *,
        model: str | None = None,
        env: dict | None = None,
        skills: list[str] | None = None,
    ):
        """Register a new climb and start it.

        Args:
            climb_id: Unique identifier for this climb
            climb_dir: Path to the climb directory (must contain program.md,
                config.json, eval/, workspace/)
            model: LangGraph model string (e.g., "anthropic:claude-sonnet-4-6").
                Overrides CLIMBER_MODEL env var.
            env: Optional extra environment variables for the climber process
            skills: Skill directory paths inherited from the parent agent.
                The climber gets whatever tools the operator has configured —
                if they have a coding agent, the climber gets it too.
        """
        climb_dir = Path(climb_dir).resolve()

        # Validate climb directory
        for required in ["program.md", "config.json"]:
            if not (climb_dir / required).exists():
                raise FileNotFoundError(f"{climb_dir / required} not found")

        if not (climb_dir / "eval").is_dir():
            raise FileNotFoundError(f"{climb_dir / 'eval'} not found")

        # Freeze eval files into memory (Law 4)
        self._freeze_eval_files(climb_id, climb_dir)

        manifest = self._load_manifest()
        manifest[climb_id] = {
            "climb_dir": str(climb_dir),
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "env": env or {},
            "skills": skills or [],
        }
        self._save_manifest(manifest)

        self._spawn(climb_id, climb_dir, model=model, env=env or {}, skills=skills or [])

    def unregister(self, climb_id: str):
        """Stop a climb and remove it from the manifest."""
        # Close heartbeat pipe — triggers child's heartbeat monitor
        fd = self._heartbeat_fds.pop(climb_id, None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        # Belt-and-suspenders: also SIGTERM
        proc = self._processes.pop(climb_id, None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        # Clean up frozen evals
        self._frozen_evals.pop(climb_id, None)

        # Remove from manifest
        manifest = self._load_manifest()
        manifest.pop(climb_id, None)
        self._save_manifest(manifest)

    def start_all(self):
        """Start all registered climbs. Call this on supervisor boot."""
        manifest = self._load_manifest()
        for climb_id, entry in manifest.items():
            climb_dir = Path(entry["climb_dir"])
            if not climb_dir.exists():
                print(
                    f"WARNING: climb dir for {climb_id} not found: {climb_dir}",
                    file=sys.stderr,
                )
                continue
            # Re-freeze eval files on restart
            self._freeze_eval_files(climb_id, climb_dir)
            self._spawn(
                climb_id,
                climb_dir,
                model=entry.get("model"),
                env=entry.get("env", {}),
                skills=entry.get("skills", []),
            )

    def stop_all(self):
        """Stop all running climbers."""
        # Close all heartbeat pipes first — triggers child exit via EOF
        for climb_id in list(self._heartbeat_fds.keys()):
            fd = self._heartbeat_fds.pop(climb_id)
            try:
                os.close(fd)
            except OSError:
                pass

        # Terminate any that didn't exit from heartbeat
        for climb_id in list(self._processes.keys()):
            proc = self._processes.pop(climb_id)
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

        self._frozen_evals.clear()

    def status(self) -> dict[str, dict]:
        """Get status of all registered climbs.

        Returns a dict suitable for a monitoring memory block.
        """
        manifest = self._load_manifest()
        statuses = {}

        for climb_id, entry in manifest.items():
            climb_dir = Path(entry["climb_dir"])
            status: dict = {
                "climb_dir": str(climb_dir),
                "registered_at": entry.get("registered_at", "unknown"),
                "model": entry.get("model", "default"),
            }

            # Check process status
            proc = self._processes.get(climb_id)
            if proc and proc.poll() is None:
                status["process"] = "running"
                status["pid"] = proc.pid
            elif proc:
                status["process"] = f"exited ({proc.returncode})"
            else:
                status["process"] = "not started"

            # Read recent results for trend info using ring buffer
            log_path = climb_dir / "logs" / "results.jsonl"
            if log_path.exists():
                ring = deque(maxlen=10)
                total = 0
                with open(log_path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            total += 1
                            try:
                                ring.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass

                status["total_iterations"] = total
                recent = list(ring)

                if recent:
                    last = recent[-1]
                    status["last_score"] = last.get("score")
                    status["last_decision"] = last.get("decision")
                    status["last_timestamp"] = last.get("timestamp")

                    # Trend: slope over last 10 results
                    scores = [
                        r.get("score", 0)
                        for r in recent
                        if r.get("score") is not None
                    ]
                    if len(scores) >= 3:
                        n = len(scores)
                        x_mean = (n - 1) / 2
                        y_mean = sum(scores) / n
                        num = sum(
                            (i - x_mean) * (s - y_mean)
                            for i, s in enumerate(scores)
                        )
                        den = sum((i - x_mean) ** 2 for i in range(n))
                        slope = num / den if den > 0 else 0
                        status["trend_slope"] = round(slope, 4)

                        # Plateau detection
                        keeps = sum(
                            1 for r in recent if r.get("decision") == "keep"
                        )
                        plateaus = sum(
                            1 for r in recent if r.get("decision") == "plateau"
                        )
                        if plateaus >= 3 or keeps == 0:
                            status["status"] = "plateau"
                        elif slope > 0.001:
                            status["status"] = "improving"
                        elif slope < -0.001:
                            status["status"] = "degrading"
                        else:
                            status["status"] = "flat"
                    else:
                        status["status"] = "insufficient_data"
            else:
                status["total_iterations"] = 0
                status["status"] = "no_data"

            statuses[climb_id] = status

        return statuses

    def format_monitoring_block(self) -> str:
        """Format status as a concise monitoring block string."""
        statuses = self.status()
        if not statuses:
            return "No active climbs."

        lines = []
        for climb_id, s in statuses.items():
            parts = [climb_id]
            parts.append(s.get("status", "unknown"))
            if s.get("total_iterations"):
                parts.append(f"iter {s['total_iterations']}")
            if s.get("last_score") is not None:
                parts.append(f"score {s['last_score']}")
            if s.get("trend_slope") is not None:
                parts.append(f"slope {s['trend_slope']}")
            parts.append(s.get("process", "unknown"))
            lines.append(": ".join([parts[0], ", ".join(parts[1:])]))

        return "\n".join(lines)

    def _spawn(
        self,
        climb_id: str,
        climb_dir: Path,
        *,
        model: str | None = None,
        env: dict,
        skills: list[str] | None = None,
    ):
        """Spawn a climber subprocess with heartbeat pipe.

        The heartbeat pipe is the cross-platform parent-death mechanism:
        supervisor holds write end, child holds read end. When supervisor
        dies, write end closes → child's blocking read returns EOF → child
        exits. Works on Linux, macOS, and Windows.
        """
        child_env = os.environ.copy()
        child_env.update(env)

        python = sys.executable
        log_dir = climb_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / "climber_stdout.log"

        # Create heartbeat pipe
        read_fd, write_fd = os.pipe()

        cmd = [python, str(CLIMBER_SCRIPT), str(climb_dir), "--heartbeat-fd", str(read_fd)]
        if model:
            cmd.extend(["--model", model])
        if skills:
            cmd.extend(["--skills"] + skills)

        with open(stdout_log, "a") as log_file:
            if sys.platform == "win32":
                os.set_inheritable(read_fd, True)
                proc = subprocess.Popen(
                    cmd,
                    env=child_env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    close_fds=False,
                )
            else:
                proc = subprocess.Popen(
                    cmd,
                    env=child_env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    pass_fds=(read_fd,),
                )

        # Close read end in parent — only child needs it
        os.close(read_fd)

        self._processes[climb_id] = proc
        self._heartbeat_fds[climb_id] = write_fd
        print(f"Spawned climber {climb_id} (pid={proc.pid})")


def preflight_check(climb_dir: str | Path) -> list[str]:
    """Run the mechanical pre-flight checklist on a climb directory.

    Returns a list of issues found. Empty list = ready to climb.
    This covers the mechanical checks only — judgment checks (S4 maturity,
    S5 clarity, budget) require human input.
    """
    climb_dir = Path(climb_dir).resolve()
    issues = []

    # Structure checks
    if not (climb_dir / "program.md").exists():
        issues.append("Missing program.md (S5 — frozen goal and constraints)")
    if not (climb_dir / "config.json").exists():
        issues.append("Missing config.json (climb configuration)")
    if not (climb_dir / "eval").is_dir():
        issues.append("Missing eval/ directory (evaluation scripts)")

    # Config validation
    if (climb_dir / "config.json").exists():
        try:
            with open(climb_dir / "config.json") as f:
                config = json.load(f)

            if "eval_command" not in config:
                issues.append("config.json missing eval_command")
            if "scope" not in config:
                issues.append(
                    "config.json missing scope (what can the climber modify?)"
                )

            # Check scope directories exist
            for scope_path in config.get("scope", []):
                if not (climb_dir / scope_path).exists():
                    issues.append(f"Scope path not found: {scope_path}")

        except json.JSONDecodeError:
            issues.append("config.json is not valid JSON")

    # Eval script check
    eval_dir = climb_dir / "eval"
    if eval_dir.is_dir() and not any(eval_dir.iterdir()):
        issues.append("eval/ directory is empty")

    # Workspace check
    workspace = climb_dir / "workspace"
    if not workspace.exists():
        issues.append("Missing workspace/ directory (mutable surface)")
    elif not any(workspace.rglob("*")):
        issues.append("workspace/ is empty — nothing to optimize")

    return issues


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Climber supervisor")
    sub = parser.add_subparsers(dest="command")

    check_p = sub.add_parser(
        "preflight", help="Run pre-flight checks on a climb directory"
    )
    check_p.add_argument("climb_dir", help="Path to climb directory")

    status_p = sub.add_parser(
        "status", help="Show status of all registered climbs"
    )
    status_p.add_argument(
        "--state-dir", default=".", help="Supervisor state directory"
    )

    args = parser.parse_args()

    if args.command == "preflight":
        issues = preflight_check(args.climb_dir)
        if issues:
            print("PRE-FLIGHT FAILED:")
            for issue in issues:
                print(f"  [ ] {issue}")
            sys.exit(1)
        else:
            print("PRE-FLIGHT PASSED: Ready to climb.")
            sys.exit(0)

    elif args.command == "status":
        sup = Supervisor(args.state_dir)
        print(sup.format_monitoring_block())

    else:
        parser.print_help()
