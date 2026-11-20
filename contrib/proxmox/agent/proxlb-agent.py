#!/usr/bin/env python3
"""
proxlb-agent - runs ProxLB inside a Proxmox LXC and publishes its status
to the Proxmox VE web interface.

How it works
------------
* The agent is the scheduler: it runs ProxLB as a one-shot process
  (``python -m proxlb -j``) every ``service.schedule`` of the ProxLB config.
* After every run it writes a compact status (last run, planned/performed
  migrations, node usage) into the *Notes* (description) of its own
  container (or of an anchor VM, see below) through the Proxmox API. The ProxLB GUI plugin reads it back
  with the logged-in user's normal session - no extra ports, no CORS.
* The GUI sends commands by adding tags to the container:
    proxlb-cmd-dryrun   run a dry-run (preview) now
    proxlb-cmd-run      run a real balancing now
    proxlb-active       scheduled runs balance for real (otherwise dry-run)
  The agent removes the ``proxlb-cmd-*`` tags when it picks them up.
* High availability: the container itself is made HA by Proxmox
  (ha-manager, see proxlb-lxc.sh --ha). The agent talks to any reachable
  cluster node and pauses real balancing while the cluster is degraded
  (a node offline or HA recovering guests), so it never fights HA recovery.
* ProxLB on another machine: the status lives in a small "anchor" VM
  (no disks, never started) with the tag ``proxlb`` - set ``guest_type: qemu``
  and ``vmid`` (see contrib/proxmox/proxlb-external.sh).
* Local triggers also work: ``touch /var/lib/proxlb/trigger-dryrun`` or
  ``/var/lib/proxlb/trigger-run`` (see ``proxlb-ctl``).
"""

import argparse
import importlib.util
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, cast

import proxmoxer
import yaml

Json = dict[str, Any]

AGENT_VERSION = "1.2.0"
STATUS_MARKER = "<!--proxlb-status:"
TAG_CMD_DRYRUN = "proxlb-cmd-dryrun"
TAG_CMD_RUN = "proxlb-cmd-run"
TAG_ACTIVE = "proxlb-active"
TLS_RELAX_DIR = Path(__file__).resolve().parent / "tls-relax"
DESCRIPTION_LIMIT = 7800          # PVE limits the description to 8 KiB
HEARTBEAT_SECONDS = 300
MAX_MOVES = 80

log = logging.getLogger("proxlb-agent")


def enable_tls_relax() -> Optional[str]:
    """Relax only VERIFY_X509_STRICT (old Proxmox CAs lack keyUsage, PVE bug
    #6701) in this process; return the directory to put on PYTHONPATH so the
    ProxLB runs get the same. Chain and hostname stay verified."""
    module = TLS_RELAX_DIR / "sitecustomize.py"
    if not module.is_file():
        log.warning("%s not found - strict X.509 checks stay on", module)
        return None
    spec = importlib.util.spec_from_file_location("proxlb_tls_relax", module)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return str(TLS_RELAX_DIR)


# --------------------------------------------------------------------------
# Helpers (pure functions, unit-tested in tests/test_contrib_agent.py)
# --------------------------------------------------------------------------
def now_ts() -> int:
    return int(time.time())


def split_tags(tags: Optional[str]) -> list[str]:
    if not tags:
        return []
    for sep in (",", " "):
        tags = tags.replace(sep, ";")
    return [t for t in tags.split(";") if t]


def schedule_seconds(cfg: Json) -> int:
    sched = (cfg.get("service") or {}).get("schedule") or {}
    interval = int(sched.get("interval", 12))
    fmt = str(sched.get("format", "hours")).lower()
    seconds = interval * 60 if fmt.startswith("min") else interval * 3600
    return max(seconds, 300)


def oneshot_config(cfg: Json) -> Json:
    """Return a copy of the ProxLB config suitable for a single run."""
    out = cast(Json, json.loads(json.dumps(cfg)))
    service = out.setdefault("service", {})
    service["daemon"] = False
    service["enable_ha"] = False
    service["delay"] = {"enable": False, "time": 1, "format": "hours"}
    return out


def extract_json(stdout: str) -> tuple[Optional[Json], list[str]]:
    """ProxLB prints its log lines and the JSON (-j) to stdout. Split them."""
    lines = stdout.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == "{":
            text = "\n".join(lines[idx:])
            try:
                data, end = json.JSONDecoder().raw_decode(text)
            except ValueError:
                continue
            rest = text[end:].splitlines()
            return data, lines[:idx] + rest
    return None, lines


HA_FAILURE_STATES = ("fence", "recovery", "error")


def cluster_degraded(resources: list[Json], ignore_nodes: list[str]) -> Optional[str]:
    """Return why the cluster is degraded, or None when it is healthy."""
    reasons = []
    offline = sorted(r["node"] for r in resources
                     if r.get("type") == "node" and r.get("status") != "online"
                     and r.get("node") not in ignore_nodes)
    if offline:
        reasons.append(f"node(s) offline: {', '.join(offline)}")
    recovering = [str(r.get("vmid")) for r in resources
                  if r.get("type") in ("qemu", "lxc") and r.get("hastate") in HA_FAILURE_STATES]
    if recovering:
        reasons.append(f"HA is recovering {len(recovering)} guest(s)")
    return "; ".join(reasons) or None


def build_status(data: Optional[Json], log_lines: list[str], *, dry_run: bool,
                 trigger: str, started: int, finished: int, rc: int,
                 cfg: Json) -> Json:
    balancing = cfg.get("balancing") or {}
    status: dict[str, Any] = {
        "v": 1,
        "agent": AGENT_VERSION,
        "state": "idle",
        "dry_run": dry_run,
        "trigger": trigger,
        "started": started,
        "finished": finished,
        "duration": finished - started,
        "method": balancing.get("method", "memory"),
        "mode": balancing.get("mode", "used"),
        "balanciness": balancing.get("balanciness", 10),
        "balancing_enabled": bool(balancing.get("enable", False)),
        "nodes": {},
        "moves": [],
        "moves_total": 0,
        "guests": 0,
        "ignored": 0,
        "ok": False,
        "error": None,
    }
    errors = [ln for ln in log_lines if " - CRITICAL - " in ln or " - ERROR - " in ln]
    if data is None:
        status["error"] = (errors[-1].split(" - ", 3)[-1] if errors
                           else f"ProxLB returned no data (exit code {rc})")
        return status

    for name, node in sorted((data.get("nodes") or {}).items()):
        def pct(metric: str, _node: Json = node) -> float:
            return round(float((_node.get(metric) or {}).get("used_percent", 0.0)), 1)
        status["nodes"][name] = [pct("memory"), pct("cpu"), pct("disk"),
                                 1 if node.get("maintenance") else 0]

    moves = []
    guests = data.get("guests") or {}
    for guest in guests.values():
        if guest.get("ignore"):
            status["ignored"] += 1
        if guest.get("node_current") != guest.get("node_target"):
            moves.append([guest.get("id"), guest.get("name"), guest.get("type"),
                          guest.get("node_current"), guest.get("node_target")])
    moves.sort(key=lambda m: (str(m[3]), int(m[0] or 0)))
    status["guests"] = len(guests)
    status["moves_total"] = len(moves)
    status["moves"] = moves[:MAX_MOVES]
    status["ok"] = rc == 0 and not errors
    if errors:
        status["error"] = errors[-1].split(" - ", 3)[-1]
    return status


def fmt_time(ts: Optional[int]) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def render_description(status: Json) -> str:
    """Markdown for the container Notes + machine readable JSON comment."""
    def build(st: Json) -> str:
        mode = "dry-run (preview)" if st.get("dry_run") else "balancing"
        if st.get("state") == "running":
            result = "running ..."
        elif not st.get("finished"):
            result = "no run yet"
        elif st.get("ok"):
            verb = "planned" if st.get("dry_run") else "performed"
            result = f"OK - {st.get('moves_total', 0)} migration(s) {verb}"
        else:
            result = f"FAILED - {st.get('error') or 'see journal'}"
        auto = "on" if st.get("auto") else "off (scheduled runs are dry-run only)"
        md = [
            "**ProxLB** - managed by proxlb-agent. This note is overwritten automatically.",
            "",
            "| | |",
            "|---|---|",
            f"| Last run | {fmt_time(st.get('finished') or st.get('started'))} ({mode}, {st.get('trigger', '-')}) |",
            f"| Result | {result} |",
            f"| Automatic balancing | {auto} |",
            f"| Cluster | {'degraded - balancing paused: ' + st['paused'] if st.get('paused') else 'healthy'} |",
            f"| Next scheduled run | {fmt_time(st.get('next_run'))} |",
            f"| Method | {st.get('method')} / {st.get('mode')} (balanciness {st.get('balanciness')}) |",
            f"| ProxLB runs on | {st.get('host') or '-'} |",
            "",
            "Open **Datacenter → ProxLB** for the full view.",
            "",
        ]
        raw = json.dumps(st, separators=(",", ":"), ensure_ascii=False)
        raw = raw.replace(">", "\\u003e")  # never close the HTML comment early
        return "\n".join(md) + STATUS_MARKER + raw + "-->"

    st = dict(status)
    text = build(st)
    while len(text.encode("utf-8")) > DESCRIPTION_LIMIT and st.get("moves"):
        st["moves"] = st["moves"][: max(len(st["moves"]) // 2, 0)]
        text = build(st)
    if len(text.encode("utf-8")) > DESCRIPTION_LIMIT:
        st["nodes"] = {}
        text = build(st)
    return text


# --------------------------------------------------------------------------
# Proxmox API access
# --------------------------------------------------------------------------
def parse_host(host: Any) -> tuple[str, int]:
    """'10.0.0.1', '10.0.0.1:8006', '[fd00::1]:8006' -> (host, port)."""
    host_s = str(host)
    port = 8006
    if host_s.startswith("["):  # [v6]:port
        h, _, p = host_s[1:].partition("]")
        host_s, port = h, int(p.lstrip(":") or 8006)
    elif host_s.count(":") == 1:
        host_s, p = host_s.split(":")
        port = int(p)
    return host_s, port


class Pve:
    def __init__(self, cfg: Json) -> None:
        self.api_cfg = cfg.get("proxmox_api") or {}
        self.api: Any = None

    def connect(self) -> Any:
        if self.api is not None:
            return self.api
        hosts = self.api_cfg.get("hosts") or []
        last_err: Optional[Exception] = None
        for host in hosts:
            host_s, port = parse_host(host)
            kwargs: dict[str, Any] = {
                "port": port,
                "verify_ssl": bool(self.api_cfg.get("ssl_verification", True)),
                "timeout": int(self.api_cfg.get("timeout", 10)),
            }
            if self.api_cfg.get("token_id"):
                kwargs.update(user=self.api_cfg["user"],
                              token_name=self.api_cfg["token_id"],
                              token_value=self.api_cfg["token_secret"])
            else:
                kwargs.update(user=self.api_cfg["user"],
                              password=self.api_cfg.get("pass") or self.api_cfg.get("password"))
            try:
                api = proxmoxer.ProxmoxAPI(host_s, **kwargs)
                api.version.get()
                self.api = api
                return api
            except Exception as err:  # noqa: BLE001 - try the next host
                if getattr(err, "status_code", None) == 401 or "401" in str(err):
                    # a token is valid on every node or on none: stop here
                    raise RuntimeError(
                        f"API login rejected by {host} (401): the token "
                        f"{self.api_cfg.get('user')}!{self.api_cfg.get('token_id')} is wrong or was "
                        "replaced - run 'proxlb-ctl check'; a new token: proxlb-external "
                        "--rotate-token --vmid <anchor> --ct <ctid>") from err
                last_err = err
                log.warning("Proxmox API %s not usable: %s", host, err)
        raise RuntimeError(f"no Proxmox API host reachable: {last_err}")

    def reset(self) -> None:
        self.api = None

    def cluster_state(self, vmid: int, guest_type: str = "lxc") -> tuple[str, list[str], list[Json]]:
        """Node and tags of our status guest plus all cluster resources."""
        resources = cast(list[Json], self.connect().cluster.resources.get())
        for res in resources:
            if res.get("type") == guest_type and int(res.get("vmid", -1)) == vmid:
                return res["node"], split_tags(res.get("tags")), resources
        raise RuntimeError(f"{guest_type} {vmid} not found in cluster resources")

    def _config(self, node: str, vmid: int, guest_type: str) -> Any:
        return getattr(self.connect().nodes(node), guest_type)(vmid).config

    def set_tags(self, node: str, vmid: int, tags: list[str], guest_type: str = "lxc") -> None:
        cfg = self._config(node, vmid, guest_type)
        if tags:
            cfg.put(tags=";".join(tags))
        else:
            cfg.put(delete="tags")

    def set_description(self, node: str, vmid: int, text: str, guest_type: str = "lxc") -> None:
        self._config(node, vmid, guest_type).put(description=text)


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------
class Agent:
    def __init__(self, agent_cfg: Json) -> None:
        # vmid of the guest that carries the status: the ProxLB container
        # itself (guest_type lxc), or an anchor VM when ProxLB runs elsewhere.
        self.ctid = int(agent_cfg.get("vmid") or agent_cfg.get("ctid") or 0)
        if not self.ctid:
            raise ValueError("agent config needs 'vmid' (or 'ctid')")
        self.guest_type = str(agent_cfg.get("guest_type", "lxc"))
        if self.guest_type not in ("lxc", "qemu"):
            raise ValueError("guest_type must be 'lxc' or 'qemu'")
        self.host = socket.gethostname()
        self.tls_relax_dir: Optional[str] = None
        if bool(agent_cfg.get("tls_relax_x509_strict", True)):
            self.tls_relax_dir = enable_tls_relax()
        self.config_path = Path(agent_cfg.get("proxlb_config", "/etc/proxlb/proxlb.yaml"))
        self.python = agent_cfg.get("python", sys.executable)
        self.poll = int(agent_cfg.get("poll_seconds", 10))
        self.run_timeout = int(agent_cfg.get("run_timeout_seconds", 4 * 3600))
        self.state_dir = Path(agent_cfg.get("state_dir", "/var/lib/proxlb"))
        self.pause_when_degraded = bool(agent_cfg.get("pause_when_degraded", True))
        self.paused: Optional[str] = None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = self.load_cfg()
        self.pve = Pve(self.cfg)
        self.api_cfg: Json = dict(self.cfg.get("proxmox_api") or {})
        self.status: Json = self.load_last_status()
        self.next_run = now_ts() + schedule_seconds(self.cfg)
        self.last_publish = 0
        self.published_text = ""
        self.stop = False
        self.startup_run_done = False

    # -- persistence -------------------------------------------------------
    def load_cfg(self) -> Json:
        with open(self.config_path, encoding="utf-8") as fh:
            return cast(Json, yaml.safe_load(fh) or {})

    def load_last_status(self) -> Json:
        try:
            return cast(Json, json.loads((self.state_dir / "status.json").read_text()))
        except (OSError, ValueError):
            return {"v": 1, "agent": AGENT_VERSION, "state": "idle", "finished": 0}

    def save_status(self) -> None:
        tmp = self.state_dir / "status.json.tmp"
        tmp.write_text(json.dumps(self.status, indent=2))
        tmp.replace(self.state_dir / "status.json")

    # -- publishing ---------------------------------------------------------
    def publish(self, node: str, auto: bool, force: bool = False) -> None:
        self.status["auto"] = auto
        self.status["next_run"] = self.next_run
        self.status["hb"] = now_ts()
        self.status["ctid"] = self.ctid
        self.status["guest_type"] = self.guest_type
        self.status["host"] = self.host
        self.status["paused"] = self.paused
        text = render_description(self.status)
        # Compare without the heartbeat to avoid needless writes.
        body = text.split(STATUS_MARKER)[0]
        changed = body != self.published_text.split(STATUS_MARKER)[0]
        if not (force or changed or now_ts() - self.last_publish >= HEARTBEAT_SECONDS):
            return
        try:
            self.pve.set_description(node, self.ctid, text, self.guest_type)
            self.published_text = text
            self.last_publish = now_ts()
        except Exception as err:  # noqa: BLE001
            log.error("could not publish status to container notes: %s", err)
            self.pve.reset()

    # -- running ProxLB -----------------------------------------------------
    def run_proxlb(self, dry_run: bool, trigger: str, node: str, auto: bool) -> None:
        self.cfg = self.load_cfg()
        if not dry_run and self.paused:
            log.warning("cluster degraded (%s) - running a dry-run instead of balancing", self.paused)
            dry_run = True
        started = now_ts()
        self.status.update(state="running", started=started, dry_run=dry_run, trigger=trigger)
        self.publish(node, auto, force=True)
        log.info("starting ProxLB run (dry_run=%s, trigger=%s)", dry_run, trigger)

        fd, tmp_cfg = tempfile.mkstemp(prefix="proxlb-oneshot-", suffix=".yaml")
        try:
            with os.fdopen(fd, "w") as fh:
                yaml.safe_dump(oneshot_config(self.cfg), fh)
            cmd = [self.python, "-m", "proxlb", "-c", tmp_cfg, "-j"]
            if dry_run:
                cmd.append("-d")
            env = dict(os.environ)
            if self.tls_relax_dir:
                env["PYTHONPATH"] = os.pathsep.join(
                    p for p in (self.tls_relax_dir, env.get("PYTHONPATH")) if p)
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                                      timeout=self.run_timeout, check=False)
                stdout, rc = proc.stdout, proc.returncode
                stderr = proc.stderr
            except subprocess.TimeoutExpired as err:
                stdout = err.stdout.decode() if isinstance(err.stdout, bytes) else (err.stdout or "")
                stderr, rc = "run timed out", -1
        finally:
            os.unlink(tmp_cfg)

        data, log_lines = extract_json(stdout)
        if stderr.strip():
            log_lines += [f"x - ProxLB - ERROR - {ln}" for ln in stderr.strip().splitlines()[-5:]]
        for line in log_lines:
            print(line, flush=True)  # into the journal of proxlb-agent

        (self.state_dir / "last-run.log").write_text("\n".join(log_lines) + "\n")
        if data is not None:
            (self.state_dir / "last-run.json").write_text(json.dumps(data, indent=2))

        self.status = build_status(data, log_lines, dry_run=dry_run, trigger=trigger,
                                   started=started, finished=now_ts(), rc=rc, cfg=self.cfg)
        self.status["paused"] = self.paused
        self.save_status()
        log.info("ProxLB run finished: ok=%s moves=%s", self.status["ok"], self.status["moves_total"])

    # -- main loop ------------------------------------------------------------
    def take_local_trigger(self) -> Optional[str]:
        for name in ("run", "dryrun"):
            path = self.state_dir / f"trigger-{name}"
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
                return name
        return None

    def reload_api_if_changed(self) -> None:
        """Take a new token / host list from proxlb.yaml without a restart."""
        try:
            fresh = self.load_cfg()
        except (OSError, yaml.YAMLError) as err:
            log.error("cannot read %s: %s", self.config_path, err)
            return
        fresh_api = dict(fresh.get("proxmox_api") or {})
        if fresh_api != self.api_cfg:
            log.info("proxmox_api in %s changed - reconnecting with the new settings", self.config_path)
            self.pve = Pve(fresh)
            self.api_cfg = fresh_api
        self.cfg = fresh

    def tick(self) -> None:
        self.reload_api_if_changed()
        try:
            node, tags, resources = self.pve.cluster_state(self.ctid, self.guest_type)
        except Exception as err:  # noqa: BLE001
            log.error("Proxmox API not usable: %s", err)
            self.pve.reset()
            return
        auto = TAG_ACTIVE in tags
        ignore = list((self.cfg.get("proxmox_cluster") or {}).get("ignore_nodes") or [])
        paused = cluster_degraded(resources, ignore) if self.pause_when_degraded else None
        if paused != self.paused:
            if paused:
                log.warning("cluster degraded, real balancing paused: %s", paused)
            else:
                log.info("cluster healthy again, balancing resumed")
        self.paused = paused

        command = None
        if TAG_CMD_RUN in tags:
            command = "run"
        elif TAG_CMD_DRYRUN in tags:
            command = "dryrun"
        if command:
            try:
                self.pve.set_tags(node, self.ctid,
                                  [t for t in tags if t not in (TAG_CMD_RUN, TAG_CMD_DRYRUN)],
                                  self.guest_type)
            except Exception as err:  # noqa: BLE001
                log.error("could not remove command tag, ignoring command: %s", err)
                return
            trigger = "gui"
        else:
            command = self.take_local_trigger()
            trigger = "cli"

        ran = True
        if command:
            self.run_proxlb(command == "dryrun", trigger, node, auto)
        elif not self.startup_run_done:
            # First run after start is always a preview so the GUI has data,
            # without surprising migrations after a reboot.
            self.run_proxlb(True, "startup", node, auto)
        elif now_ts() >= self.next_run:
            self.run_proxlb(not auto, "schedule", node, auto)
            self.next_run = now_ts() + schedule_seconds(self.cfg)
        else:
            ran = False
        self.startup_run_done = True
        self.publish(node, auto, force=ran)

    def loop(self) -> None:
        signal.signal(signal.SIGTERM, self._sigterm)
        signal.signal(signal.SIGINT, self._sigterm)
        log.info("proxlb-agent %s started on %s for %s %s, schedule every %ss",
                 AGENT_VERSION, self.host, self.guest_type, self.ctid, schedule_seconds(self.cfg))
        while not self.stop:
            self.tick()
            for _ in range(self.poll):
                if self.stop:
                    break
                time.sleep(1)

    def _sigterm(self, *_: Any) -> None:
        self.stop = True


# --------------------------------------------------------------------------
# Self-check: proxlb-ctl check  (proxlb-agent.py --check)
# --------------------------------------------------------------------------
REQUIRED_ROOT_PRIVS = ("Sys.Audit", "VM.Audit", "VM.Migrate", "Datastore.Audit")


def system_ca_bundle() -> Optional[str]:
    for path in (os.environ.get("REQUESTS_CA_BUNDLE"), "/etc/ssl/certs/ca-certificates.crt",
                 "/etc/pki/tls/certs/ca-bundle.crt"):
        if path and os.path.isfile(path):
            return path
    return None


def run_check(agent_cfg: Json, out: Any = None) -> int:
    """Test every link between this agent and the web interface; return 0 if all OK."""
    import requests

    stream = out or sys.stdout
    problems = 0

    def say(state: str, text: str, hint: str = "") -> None:
        nonlocal problems
        if state == "FAIL":
            problems += 1
        print(f"[{state:4}] {text}", file=stream)
        if hint:
            print(f"       -> {hint}", file=stream)

    vmid = int(agent_cfg.get("vmid") or agent_cfg.get("ctid") or 0)
    gtype = str(agent_cfg.get("guest_type", "lxc"))
    cfg_path = agent_cfg.get("proxlb_config", "/etc/proxlb/proxlb.yaml")
    say("OK" if vmid else "FAIL", f"agent.yaml: status guest {gtype} {vmid or '?'}",
        "" if vmid else "set vmid: <ID> in /etc/proxlb/agent.yaml")
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = cast(Json, yaml.safe_load(fh) or {})
    except OSError as err:
        say("FAIL", f"cannot read {cfg_path}: {err}")
        return 1
    api_cfg = cfg.get("proxmox_api") or {}
    hosts = api_cfg.get("hosts") or []
    verify = bool(api_cfg.get("ssl_verification", True))
    auth = (f"{api_cfg.get('user')}!{api_cfg.get('token_id')}" if api_cfg.get("token_id")
            else f"{api_cfg.get('user')} (password)")
    say("OK" if hosts else "FAIL", f"{cfg_path}: {len(hosts)} API host(s), auth {auth}, "
        f"ssl_verification {verify}", "" if hosts else "add proxmox_api.hosts")

    if bool(agent_cfg.get("tls_relax_x509_strict", True)):
        enable_tls_relax()
    ca = system_ca_bundle()
    if ca:
        os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)

    reachable = 0
    for host in hosts:
        h, port = parse_host(host)
        url = f"https://[{h}]:{port}/api2/json/version" if ":" in h else f"https://{h}:{port}/api2/json/version"
        try:
            requests.get(url, timeout=5, verify=(ca or True) if verify else False)
            say("OK", f"{host}: reachable, TLS {'verified' if verify else 'NOT verified'}")
            reachable += 1
        except requests.exceptions.SSLError as err:
            try:
                requests.get(url, timeout=5, verify=False)
                say("FAIL", f"{host}: reachable, but TLS verification fails: {err}",
                    "the node certificate is not signed by a trusted CA or not issued for this "
                    "address (custom/ACME certificate?). Trust its CA, use host names in "
                    "proxmox_api.hosts, or set ssl_verification: False")
            except Exception:  # noqa: BLE001
                say("FAIL", f"{host}: TLS error: {err}")
        except requests.exceptions.RequestException as err:
            say("FAIL", f"{host}: not reachable: {err.__class__.__name__}",
                "check routing/firewall to TCP 8006 from this machine")
    if not reachable:
        return problems or 1

    pve = Pve(cfg)
    try:
        api = pve.connect()
        say("OK", "API login with the token works")
    except Exception as err:  # noqa: BLE001
        msg = str(err)
        hint = ("token or secret rejected - create a new one: proxlb-external --rotate-token --vmid "
                f"{vmid}" if "401" in msg or "authentication" in msg.lower() else "")
        say("FAIL", f"API login failed: {msg}", hint)
        return problems

    try:
        node, tags, _ = pve.cluster_state(vmid, gtype)
        say("OK", f"{gtype} {vmid} found on node {node}, tags: {';'.join(tags) or '-'}")
        if "proxlb" not in tags:
            say("FAIL", f"{gtype} {vmid} has no tag 'proxlb' - the web interface will not find it",
                f"add the tag 'proxlb' to {gtype} {vmid}")
    except Exception as err:  # noqa: BLE001
        say("FAIL", f"status guest not found: {err}",
            f"is {gtype} {vmid} right? (anchor VM: guest_type qemu, ProxLB container: lxc)")
        return problems

    def privs(path: str) -> set[str]:
        data = api.access.permissions.get(path=path)
        return {k for k, v in (cast(Json, data).get(path) or {}).items() if v}

    try:
        root = privs("/")
        missing = [p for p in REQUIRED_ROOT_PRIVS if p not in root]
        root_msg = f"missing {', '.join(missing)}" if missing else "ProxLB can read and migrate"
        say("FAIL" if missing else "OK", f"permissions on /: {root_msg}",
            f"pveum acl modify / --users {api_cfg.get('user')} --roles ProxLB" if missing else "")
        guest = privs(f"/vms/{vmid}")
        ok = "VM.Config.Options" in guest
        say("OK" if ok else "FAIL",
            f"permission VM.Config.Options on /vms/{vmid} (write status, read commands): "
            f"{'yes' if ok else 'MISSING'}",
            "" if ok else f"pveum acl modify /vms/{vmid} --users {api_cfg.get('user')} --roles ProxLBAgent")
    except Exception as err:  # noqa: BLE001
        say("WARN", f"could not read permissions: {err}")

    state = Path(agent_cfg.get("state_dir", "/var/lib/proxlb")) / "status.json"
    try:
        st = json.loads(state.read_text())
        say("OK", f"last run: {fmt_time(st.get('finished'))}, ok={st.get('ok')}, "
            f"moves={st.get('moves_total')}, error={st.get('error')}")
    except (OSError, ValueError):
        say("WARN", "no run yet on this machine (status.json missing)",
            "the agent runs a dry-run within ~10 s after it starts: systemctl status proxlb-agent")

    print(f"\n{'all checks passed' if not problems else f'{problems} problem(s) found'}", file=stream)
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n")[0])
    parser.add_argument("-c", "--config", default="/etc/proxlb/agent.yaml")
    parser.add_argument("--check", action="store_true",
                        help="test config, network/TLS, token, status guest and permissions, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with open(args.config, encoding="utf-8") as fh:
        agent_cfg = yaml.safe_load(fh) or {}
    if args.check:
        logging.getLogger().setLevel(logging.ERROR)
        sys.exit(run_check(agent_cfg))
    Agent(agent_cfg).loop()


if __name__ == "__main__":
    main()
