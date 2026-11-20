"""Tests for contrib/proxmox/agent/proxlb-agent.py (LXC agent + GUI status)."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any, Optional
from unittest.mock import MagicMock

AGENT_PATH = Path(__file__).resolve().parent.parent / "contrib" / "proxmox" / "agent" / "proxlb-agent.py"


def load_agent() -> ModuleType:
    spec = importlib.util.spec_from_file_location("proxlb_agent", AGENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["proxlb_agent"] = module
    spec.loader.exec_module(module)
    return module


agent = load_agent()


def node(mem: float, cpu: float, maintenance: bool = False) -> dict[str, Any]:
    return {
        "memory": {"used_percent": mem},
        "cpu": {"used_percent": cpu},
        "disk": {"used_percent": 10.0},
        "maintenance": maintenance,
    }


def guest(gid: int, cur: str, tgt: str, ignore: bool = False) -> dict[str, Any]:
    return {"id": gid, "name": f"g{gid}", "type": "vm", "node_current": cur,
            "node_target": tgt, "ignore": ignore}


PROXLB_DATA = {
    "nodes": {"pve1": node(80.123, 20.0), "pve2": node(30.0, 10.0, True)},
    "guests": {
        "g101": guest(101, "pve1", "pve2"),
        "g102": guest(102, "pve1", "pve1"),
        "g103": guest(103, "pve2", "pve2", ignore=True),
    },
}

HEALTHY: list[dict[str, Any]] = [
    {"type": "node", "node": "pve1", "status": "online"},
    {"type": "node", "node": "pve2", "status": "online"},
    {"type": "qemu", "vmid": 101, "node": "pve1", "hastate": "started"},
]

CFG = {
    "balancing": {"method": "cpu", "mode": "used", "balanciness": 7, "enable": True},
    "service": {"daemon": True, "enable_ha": True,
                "schedule": {"interval": 30, "format": "minutes"},
                "delay": {"enable": True, "time": 2, "format": "hours"}},
}


class TestAgentHelpers(unittest.TestCase):
    def test_split_tags(self) -> None:
        self.assertEqual(agent.split_tags("a;b,c d"), ["a", "b", "c", "d"])
        self.assertEqual(agent.split_tags(None), [])
        self.assertEqual(agent.split_tags(""), [])

    def test_schedule_seconds(self) -> None:
        self.assertEqual(agent.schedule_seconds(CFG), 1800)
        self.assertEqual(agent.schedule_seconds({"service": {"schedule": {"interval": 2, "format": "hours"}}}), 7200)
        self.assertEqual(agent.schedule_seconds({}), 12 * 3600)
        # never more often than every 5 minutes
        self.assertEqual(agent.schedule_seconds({"service": {"schedule": {"interval": 1, "format": "minutes"}}}), 300)

    def test_oneshot_config(self) -> None:
        out = agent.oneshot_config(CFG)
        self.assertFalse(out["service"]["daemon"])
        self.assertFalse(out["service"]["enable_ha"])
        self.assertFalse(out["service"]["delay"]["enable"])
        self.assertTrue(CFG["service"]["daemon"])  # original untouched

    def test_extract_json(self) -> None:
        stdout = ("2026-01-01 - ProxLB - INFO - hello\n"
                  + json.dumps(PROXLB_DATA, indent=4)
                  + "\nDaemon mode not active - stopping.\n")
        data, lines = agent.extract_json(stdout)
        self.assertEqual(data, PROXLB_DATA)
        self.assertEqual(lines[0], "2026-01-01 - ProxLB - INFO - hello")
        self.assertIn("Daemon mode not active - stopping.", lines)

    def test_extract_json_missing(self) -> None:
        data, lines = agent.extract_json("x - ProxLB - CRITICAL - boom\n")
        self.assertIsNone(data)
        self.assertEqual(len(lines), 1)

    def test_build_status(self) -> None:
        st = agent.build_status(PROXLB_DATA, [], dry_run=True, trigger="gui",
                                started=100, finished=130, rc=0, cfg=CFG)
        self.assertTrue(st["ok"])
        self.assertEqual(st["moves"], [[101, "g101", "vm", "pve1", "pve2"]])
        self.assertEqual(st["moves_total"], 1)
        self.assertEqual(st["guests"], 3)
        self.assertEqual(st["ignored"], 1)
        self.assertEqual(st["nodes"]["pve1"], [80.1, 20.0, 10.0, 0])
        self.assertEqual(st["nodes"]["pve2"][3], 1)
        self.assertEqual(st["method"], "cpu")
        self.assertEqual(st["balanciness"], 7)
        self.assertEqual(st["duration"], 30)

    def test_build_status_failure(self) -> None:
        lines = ["2026 - ProxLB - CRITICAL - ProxLB: A Proxmox API call failed"]
        st = agent.build_status(None, lines, dry_run=False, trigger="schedule",
                                started=1, finished=2, rc=0, cfg=CFG)
        self.assertFalse(st["ok"])
        self.assertEqual(st["error"], "ProxLB: A Proxmox API call failed")

    def test_render_description_roundtrip(self) -> None:
        st = agent.build_status(PROXLB_DATA, [], dry_run=True, trigger="gui",
                                started=100, finished=130, rc=0, cfg=CFG)
        st["error"] = "evil --> name"
        text = agent.render_description(st)
        self.assertIn("**ProxLB**", text)
        self.assertTrue(text.endswith("-->"))
        start = text.index(agent.STATUS_MARKER) + len(agent.STATUS_MARKER)
        end = text.index("-->", start)
        self.assertEqual(end, len(text) - 3)  # no early close of the comment
        self.assertEqual(json.loads(text[start:end])["error"], "evil --> name")

    def test_render_description_limit(self) -> None:
        st = agent.build_status(PROXLB_DATA, [], dry_run=True, trigger="gui",
                                started=100, finished=130, rc=0, cfg=CFG)
        st["moves"] = [[i, "x" * 40, "vm", "pve-node-long-name", "pve-node-other"] for i in range(500)]
        st["moves_total"] = 500
        text = agent.render_description(st)
        self.assertLessEqual(len(text.encode("utf-8")), agent.DESCRIPTION_LIMIT)
        start = text.index(agent.STATUS_MARKER) + len(agent.STATUS_MARKER)
        parsed = json.loads(text[start:-3])
        self.assertGreater(len(parsed["moves"]), 0)
        self.assertEqual(parsed["moves_total"], 500)


class TestClusterDegraded(unittest.TestCase):
    def test_healthy(self) -> None:
        self.assertIsNone(agent.cluster_degraded(HEALTHY, []))

    def test_node_offline(self) -> None:
        res = HEALTHY + [{"type": "node", "node": "pve3", "status": "offline"}]
        self.assertEqual(agent.cluster_degraded(res, []), "node(s) offline: pve3")
        self.assertIsNone(agent.cluster_degraded(res, ["pve3"]))  # ignored nodes do not count

    def test_ha_recovery(self) -> None:
        res = HEALTHY + [{"type": "lxc", "vmid": 7, "node": "pve2", "hastate": "recovery"},
                         {"type": "qemu", "vmid": 8, "node": "pve2", "hastate": "fence"}]
        self.assertEqual(agent.cluster_degraded(res, []), "HA is recovering 2 guest(s)")


class TestAgentLoop(unittest.TestCase):
    def make_agent(self, tmp: str, tags: list[str], extra: Optional[dict[str, Any]] = None) -> Any:
        cfg_path = Path(tmp) / "proxlb.yaml"
        cfg_path.write_text(json.dumps(CFG))  # JSON is valid YAML
        fake_proxlb = Path(tmp) / "fake_python"
        fake_proxlb.write_text(
            "#!/bin/sh\n"
            "echo '2026 - ProxLB - INFO - fake run'\n"
            f"cat {Path(tmp) / 'out.json'}\n"
            "echo 'Daemon mode not active - stopping.'\n")
        fake_proxlb.chmod(0o755)
        (Path(tmp) / "out.json").write_text(json.dumps(PROXLB_DATA, indent=4))
        a = agent.Agent({"ctid": 120, "proxlb_config": str(cfg_path),
                         "python": str(fake_proxlb), "state_dir": tmp, **(extra or {})})
        a.pve = MagicMock()
        a.pve.cluster_state.return_value = ("pve1", tags, list(HEALTHY))
        return a

    def test_startup_run_is_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb", "proxlb-active"])
            a.tick()
            self.assertTrue(a.status["dry_run"])
            self.assertEqual(a.status["trigger"], "startup")
            self.assertTrue(a.status["auto"])
            self.assertEqual(a.status["moves_total"], 1)
            self.assertTrue((Path(tmp) / "status.json").exists())
            a.pve.set_description.assert_called()
            text = a.pve.set_description.call_args[0][2]
            self.assertIn(agent.STATUS_MARKER, text)

    def test_gui_command_removes_tag_and_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb", "proxlb-cmd-run"])
            a.startup_run_done = True
            a.tick()
            a.pve.set_tags.assert_called_once_with("pve1", 120, ["proxlb"], "lxc")
            self.assertFalse(a.status["dry_run"])
            self.assertEqual(a.status["trigger"], "gui")

    def test_schedule_respects_active_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb"])
            a.startup_run_done = True
            a.next_run = 0
            a.tick()
            self.assertTrue(a.status["dry_run"])  # not active -> preview only
            self.assertEqual(a.status["trigger"], "schedule")
            self.assertGreater(a.next_run, 0)

    def test_local_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb"])
            a.startup_run_done = True
            (Path(tmp) / "trigger-dryrun").touch()
            a.tick()
            self.assertEqual(a.status["trigger"], "cli")
            self.assertFalse((Path(tmp) / "trigger-dryrun").exists())

    def test_degraded_cluster_downgrades_to_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb", "proxlb-active", "proxlb-cmd-run"])
            a.pve.cluster_state.return_value = (
                "pve1", ["proxlb", "proxlb-active", "proxlb-cmd-run"],
                HEALTHY + [{"type": "node", "node": "pve3", "status": "offline"}])
            a.startup_run_done = True
            a.tick()
            self.assertTrue(a.status["dry_run"])  # real run refused while degraded
            self.assertEqual(a.status["paused"], "node(s) offline: pve3")
            text = a.pve.set_description.call_args[0][2]
            self.assertIn("degraded - balancing paused", text)
            # cluster recovers -> scheduled run balances for real again
            a.pve.cluster_state.return_value = ("pve1", ["proxlb", "proxlb-active"], list(HEALTHY))
            a.next_run = 0
            a.tick()
            self.assertFalse(a.status["dry_run"])
            self.assertIsNone(a.status["paused"])

    def test_external_host_with_anchor_vm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb", "proxlb-cmd-dryrun"],
                                {"ctid": None, "vmid": 9000, "guest_type": "qemu"})
            a.startup_run_done = True
            a.tick()
            a.pve.cluster_state.assert_called_with(9000, "qemu")
            a.pve.set_tags.assert_called_once_with("pve1", 9000, ["proxlb"], "qemu")
            args = a.pve.set_description.call_args[0]
            self.assertEqual(args[1], 9000)
            self.assertEqual(args[3], "qemu")
            self.assertEqual(a.status["guest_type"], "qemu")
            self.assertTrue(a.status["host"])
            self.assertIn("ProxLB runs on", args[2])

    def test_tls_relax_reaches_proxlb_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb", "proxlb-cmd-dryrun"])
            # fake "python" that prints PYTHONPATH instead of running ProxLB
            fake = Path(tmp) / "env_python"
            fake.write_text("#!/bin/sh\necho \"x - ProxLB - INFO - PYTHONPATH=$PYTHONPATH\"\n")
            fake.chmod(0o755)
            a.python = str(fake)
            a.startup_run_done = True
            a.tick()
            log_text = (Path(tmp) / "last-run.log").read_text()
            self.assertIn(str(agent.TLS_RELAX_DIR), log_text)
            import ssl
            import urllib3.connection
            ctx = urllib3.connection.create_urllib3_context()  # type: ignore[attr-defined]
            self.assertFalse(ctx.verify_flags & getattr(ssl, "VERIFY_X509_STRICT", 0))
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)  # still verifying

    def test_new_token_in_config_is_picked_up_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb"])
            mock_pve = a.pve
            a.startup_run_done = True
            a.tick()
            self.assertIs(a.pve, mock_pve)  # unchanged config: same connection
            cfg = dict(CFG)
            cfg["proxmox_api"] = {"hosts": ["10.0.0.1"], "user": "proxlb@pve",
                                  "token_id": "external1000", "token_secret": "new"}
            Path(tmp, "proxlb.yaml").write_text(json.dumps(cfg))
            a.reload_api_if_changed()
            self.assertIsNot(a.pve, mock_pve)
            self.assertEqual(a.pve.api_cfg["token_secret"], "new")

    def test_401_stops_at_first_host_with_a_hint(self) -> None:
        calls: list[str] = []

        class Denied(Exception):
            status_code = 401

        def fake_api(host: str, **_: Any) -> Any:
            calls.append(host)
            api = MagicMock()
            api.version.get.side_effect = Denied("401 Unauthorized: Authentication failed!")
            return api

        pve = agent.Pve({"proxmox_api": {"hosts": ["10.0.0.1", "10.0.0.2", "10.0.0.3"], "user": "proxlb@pve",
                                         "token_id": "external1000", "token_secret": "old"}})
        orig = agent.proxmoxer.ProxmoxAPI
        agent.proxmoxer.ProxmoxAPI = fake_api
        try:
            with self.assertRaises(RuntimeError) as ctx:
                pve.connect()
        finally:
            agent.proxmoxer.ProxmoxAPI = orig
        self.assertEqual(calls, ["10.0.0.1"])
        self.assertIn("proxlb@pve!external1000", str(ctx.exception))
        self.assertIn("rotate-token", str(ctx.exception))

    def test_bad_agent_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                self.make_agent(tmp, [], {"ctid": None})
            with self.assertRaises(ValueError):
                self.make_agent(tmp, [], {"guest_type": "vm"})

    def test_idle_tick_does_not_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a = self.make_agent(tmp, ["proxlb"])
            a.startup_run_done = True
            a.tick()
            self.assertNotIn("trigger", a.status)


if __name__ == "__main__":
    unittest.main()
