#!/usr/bin/env python3
"""
psi_inspect.py - Dump raw PSI RRD data from all Proxmox nodes.

Shows the exact values ProxLB reads from the Proxmox API for PSI balancing,
so you can compare them against what the Proxmox web UI graphs display.

Usage:
    python3 psi_inspect.py -c /etc/proxlb/proxlb.yaml
    python3 psi_inspect.py -c /etc/proxlb/proxlb.yaml --timeframe day
    python3 psi_inspect.py -c /etc/proxlb/proxlb.yaml --raw    # dump all RRD fields

Requires: proxmoxer, pyyaml
"""

import argparse
import sys

try:
    import yaml
except ImportError:
    print("Error: pyyaml is not installed. Run: pip install pyyaml")
    sys.exit(1)

try:
    import proxmoxer
except ImportError:
    print("Error: proxmoxer is not installed. Run: pip install proxmoxer")
    sys.exit(1)

try:
    import urllib3
    urllib3.disable_warnings(category=urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass


PSI_METRICS = ["memory", "cpu", "disk"]
PSI_TYPES   = ["some", "full"]


def connect(cfg: dict):
    api_cfg = cfg["proxmox_api"]
    host    = api_cfg["hosts"][0]
    port    = 8006
    if ":" in host and not host.startswith("["):
        host, port = host.rsplit(":", 1)
        port = int(port)

    kwargs = dict(
        host       = host,
        port       = port,
        verify_ssl = api_cfg.get("ssl_verification", True),
        timeout    = api_cfg.get("timeout", 30),
    )
    if "token_secret" in api_cfg:
        kwargs["user"]         = api_cfg["user"]
        kwargs["token_name"]   = api_cfg["token_id"]
        kwargs["token_value"]  = api_cfg["token_secret"]
    else:
        kwargs["user"]     = api_cfg["user"]
        kwargs["password"] = api_cfg["pass"]

    return proxmoxer.ProxmoxAPI(**kwargs)


def get_rrd(api, node: str, timeframe: str, cf: str) -> list:
    return api.nodes(node).rrddata.get(timeframe=timeframe, cf=cf)


def summarize_psi(rows: list, metric: str, ptype: str, spikes: bool) -> dict:
    key = f"pressure{metric}{ptype}"
    values = [r.get(key) for r in rows if r.get(key) is not None]
    if not values:
        return {"key": key, "count": 0, "min": None, "max": None, "avg": None, "last6_max": None}
    last6_max = max(values[-6:]) if len(values) >= 1 else None
    return {
        "key":      key,
        "count":    len(values),
        "min":      min(values),
        "max":      max(values),
        "avg":      sum(values) / len(values),
        "last6_max": last6_max,   # what ProxLB uses for spikes
    }


def fmt(v) -> str:
    """Values from Proxmox RRD are already in percent (0-100 scale)."""
    if v is None:
        return "n/a      "
    return f"{v:7.4f}%"


def main():
    parser = argparse.ArgumentParser(description="Dump raw PSI RRD data from Proxmox nodes")
    parser.add_argument("-c", "--config",    required=True, help="Path to proxlb.yaml")
    parser.add_argument("--timeframe",       default="hour", choices=["hour", "day", "week", "month", "year"],
                        help="RRD timeframe (default: hour)")
    parser.add_argument("--raw",             action="store_true",
                        help="Also dump the full list of RRD field names present in the data")
    parser.add_argument("--node",            help="Inspect a single node only")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    print(f"Connecting to {cfg['proxmox_api']['hosts'][0]} ...")
    api = connect(cfg)

    nodes = [n["node"] for n in api.nodes.get() if n["status"] == "online"]
    if args.node:
        if args.node not in nodes:
            print(f"Node '{args.node}' not found or not online. Available: {', '.join(nodes)}")
            sys.exit(1)
        nodes = [args.node]

    for node in sorted(nodes):
        print(f"\n{'=' * 70}")
        print(f"  Node: {node}  (timeframe={args.timeframe})")
        print(f"{'=' * 70}")

        avg_rows = get_rrd(api, node, args.timeframe, "AVERAGE")
        max_rows = get_rrd(api, node, args.timeframe, "MAX")

        print(f"  RRD entries: {len(avg_rows)} (AVERAGE), {len(max_rows)} (MAX)")

        # Collect all PSI field names actually present in the RRD data
        all_avg_keys = set()
        for row in avg_rows:
            all_avg_keys.update(row.keys())
        present_psi_keys = sorted(k for k in all_avg_keys if "pressure" in k)
        missing_psi_keys = sorted(
            f"pressure{m}{t}" for m in PSI_METRICS for t in PSI_TYPES
            if f"pressure{m}{t}" not in all_avg_keys
        )
        if missing_psi_keys:
            print(f"\n  WARNING: these PSI keys are MISSING from RRD data (will read as 0.0 in ProxLB):")
            print(f"    {', '.join(missing_psi_keys)}")

        print()
        print(f"  {'Metric':<8} {'Type':<5}  {'avg(all%)':<10} {'min%':<10} {'max%':<10}  "
              f"{'MAX-cf peak':<12}  {'proxlb_spikes':<14}  Key")
        print(f"  {'-'*8} {'-'*5}  {'-'*10} {'-'*10} {'-'*10}  {'-'*12}  {'-'*14}  {'-'*20}")

        for metric in PSI_METRICS:
            for ptype in PSI_TYPES:
                avg_s = summarize_psi(avg_rows, metric, ptype, spikes=False)
                max_s = summarize_psi(max_rows, metric, ptype, spikes=True)

                proxlb_avg    = avg_s["avg"]
                proxlb_spikes = max_s["last6_max"]  # what ProxLB stores as _spikes_percent

                print(f"  {metric:<8} {ptype:<5}  "
                      f"{fmt(proxlb_avg):<10} "
                      f"{fmt(avg_s['min']):<10} "
                      f"{fmt(avg_s['max']):<10}  "
                      f"{fmt(max_s['max']):<12}  "
                      f"{fmt(proxlb_spikes):<14}  "
                      f"{avg_s['key']}")

        if args.raw:
            # Show all field names that appear in the RRD data
            other_keys = sorted(k for k in all_avg_keys if "pressure" not in k)
            print()
            print(f"  PSI fields present in RRD: {', '.join(present_psi_keys) if present_psi_keys else 'none'}")
            print(f"  Other fields: {', '.join(other_keys)}")

    print()


if __name__ == "__main__":
    main()
