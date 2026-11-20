#!/usr/bin/env bash
# =============================================================================
#  install-agent.sh - install proxlb-agent on the machine where ProxLB runs,
#                     outside the Proxmox cluster, and connect it to the
#                     ProxLB tab in the Proxmox web interface.
#
#  First run proxlb-external.sh on a Proxmox VE node; it writes a bundle
#  /root/proxlb-external-<VMID>.tar. Copy that here and run as root:
#
#    bash install-agent.sh --bundle /root/proxlb-external-9000.tar
#
#  Options:
#    --bundle FILE        connection bundle from proxlb-external.sh (required)
#    --python PATH        Python that has ProxLB installed (default: detect,
#                         else a venv in /opt/proxlb/venv is created)
#    --config PATH        ProxLB config (default /etc/proxlb/proxlb.yaml)
#    --keep-api-config    keep proxmox_api in an existing config as it is
#                         (its user then needs VM.Config.Options on the anchor)
#    --uninstall          remove the agent again (re-enables proxlb.service)
#
#  Works on Debian/Ubuntu and RHEL-like systems with systemd and python3.
# =============================================================================
set -euo pipefail

BUNDLE=""
PY=""
CFG=/etc/proxlb/proxlb.yaml
KEEP_API=0
UNINSTALL=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
AGENT_DIR=/opt/proxlb/agent
STATE=/var/lib/proxlb

while [ $# -gt 0 ]; do
    case "$1" in
        --bundle) BUNDLE="$2"; shift ;;
        --python) PY="$2"; shift ;;
        --config) CFG="$2"; shift ;;
        --keep-api-config) KEEP_API=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

# a fresh container often lacks the caller's locale (e.g. en_US.UTF-8 passed in
# by pct exec): C.UTF-8 always exists and keeps perl/apt quiet
export LC_ALL=C.UTF-8 LANG=C.UTF-8 LANGUAGE=

info() { echo "[proxlb-agent] $*"; }
warn() { echo "[proxlb-agent] WARNING: $*"; }
die()  { echo "[proxlb-agent] ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root"
command -v systemctl >/dev/null || die "systemd is required"

if [ "$UNINSTALL" -eq 1 ]; then
    systemctl disable --now proxlb-agent.service 2>/dev/null || true
    rm -f /etc/systemd/system/proxlb-agent.service /usr/local/bin/proxlb-ctl
    rm -rf "$AGENT_DIR"
    systemctl daemon-reload
    if systemctl cat proxlb.service >/dev/null 2>&1; then
        systemctl enable --now proxlb.service && info "re-enabled proxlb.service"
    fi
    info "agent removed (config and $STATE kept)"
    exit 0
fi

[ -n "$BUNDLE" ] || die "--bundle is required (create it with proxlb-external.sh on a Proxmox node)"
[ -f "$BUNDLE" ] || die "bundle $BUNDLE not found"
[ -f "$HERE/proxlb-agent.py" ] || die "proxlb-agent.py not found next to this script"

# ----------------------------------------------------------------- bundle ---
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
tar -C "$TMP" -xf "$BUNDLE" connect.env pve-root-ca.pem || die "not a proxlb-external bundle"
declare -A B
while IFS='=' read -r key value; do
    case "$key" in
        PROXLB_VMID|PROXLB_GUEST_TYPE|PROXLB_API_HOSTS|PROXLB_API_USER|PROXLB_TOKEN_ID|PROXLB_TOKEN_SECRET|PROXLB_CLUSTER)
            B[$key]="$value" ;;
    esac
done < "$TMP/connect.env"
for k in PROXLB_VMID PROXLB_API_HOSTS PROXLB_API_USER PROXLB_TOKEN_ID PROXLB_TOKEN_SECRET; do
    [ -n "${B[$k]:-}" ] || die "bundle is missing $k"
done
[[ "${B[PROXLB_VMID]}" =~ ^[0-9]+$ ]] || die "bad VMID in bundle"
info "connecting to cluster '${B[PROXLB_CLUSTER]:-?}' (anchor VM ${B[PROXLB_VMID]}, hosts ${B[PROXLB_API_HOSTS]})"

# ------------------------------------------------- base packages (fresh LXC) ---
pkg_install() { # install packages with the system package manager, if there is one
    if command -v apt-get >/dev/null; then
        # never wait for input: no apt-listchanges pager, keep existing config files
        export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a
        apt-get update -qq </dev/null
        apt-get install -y -qq -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
            "$@" </dev/null >/dev/null
    elif command -v dnf >/dev/null; then
        dnf install -y -q "$@" </dev/null
    elif command -v yum >/dev/null; then
        yum install -y -q "$@" </dev/null
    else
        return 1
    fi
}
NEED=()
command -v python3 >/dev/null || NEED+=(python3)
command -v update-ca-certificates >/dev/null || command -v update-ca-trust >/dev/null || NEED+=(ca-certificates)
if [ ${#NEED[@]} -gt 0 ]; then
    info "installing ${NEED[*]}"
    pkg_install "${NEED[@]}" || die "please install ${NEED[*]} first"
fi

# ------------------------------------------------------------- cluster CA ---
if [ -d /usr/local/share/ca-certificates ] && command -v update-ca-certificates >/dev/null; then
    install -m 0644 "$TMP/pve-root-ca.pem" /usr/local/share/ca-certificates/proxlb-pve-root-ca.crt
    update-ca-certificates >/dev/null 2>&1 || die "update-ca-certificates failed"
    CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
elif [ -d /etc/pki/ca-trust/source/anchors ] && command -v update-ca-trust >/dev/null; then
    install -m 0644 "$TMP/pve-root-ca.pem" /etc/pki/ca-trust/source/anchors/proxlb-pve-root-ca.pem
    update-ca-trust extract
    CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
else
    install -D -m 0644 "$TMP/pve-root-ca.pem" /etc/proxlb/pve-root-ca.pem
    CA_BUNDLE=/etc/proxlb/pve-root-ca.pem
    warn "unknown CA store - using /etc/proxlb/pve-root-ca.pem only"
fi
info "trusting the cluster CA ($CA_BUNDLE)"

# ----------------------------------------------------------------- Python ---
has_proxlb() { [ -x "$1" ] && "$1" -c 'import proxlb, proxmoxer, yaml' >/dev/null 2>&1; }
if [ -z "$PY" ]; then
    for cand in /opt/venvs/proxlb/bin/python3 /opt/proxlb/venv/bin/python "$(command -v python3 || true)"; do
        if [ -n "$cand" ] && has_proxlb "$cand"; then PY="$cand"; break; fi
    done
fi
if [ -z "$PY" ]; then
    info "ProxLB not found - installing it into /opt/proxlb/venv"
    if ! python3 -m venv /opt/proxlb/venv >/dev/null 2>&1; then
        rm -rf /opt/proxlb/venv
        info "installing python3-venv"
        pkg_install python3-venv || true
        python3 -m venv /opt/proxlb/venv || die "python3 venv module missing (Debian: apt install python3-venv)"
    fi
    info "installing ProxLB and its Python packages with pip - this takes 2-5 minutes, please wait"
    /opt/proxlb/venv/bin/pip install --quiet --disable-pip-version-check --upgrade pip </dev/null
    if [ -f "$REPO_ROOT/setup.py" ] && [ -d "$REPO_ROOT/proxlb" ]; then
        /opt/proxlb/venv/bin/pip install --quiet --disable-pip-version-check "$REPO_ROOT" </dev/null
    else
        /opt/proxlb/venv/bin/pip install --quiet --disable-pip-version-check "git+https://github.com/credativ/ProxLB.git" </dev/null
    fi
    PY=/opt/proxlb/venv/bin/python
    info "ProxLB installed"
fi
has_proxlb "$PY" || die "$PY cannot import proxlb, proxmoxer and yaml"
info "using $PY ($("$PY" -m proxlb --version 2>/dev/null | head -1))"

# ------------------------------------------------------------ user + dirs ---
id plb >/dev/null 2>&1 || useradd --system --user-group --home-dir "$STATE" --shell /usr/sbin/nologin plb
install -d -o plb -g plb -m 0750 "$STATE" /var/log/proxlb
install -d -m 0750 /etc/proxlb
chgrp plb /etc/proxlb

# ---------------------------------------------------------- ProxLB config ---
if [ ! -f "$CFG" ]; then
    info "writing a new $CFG (scheduled runs are dry-runs until you enable automatic balancing in the GUI)"
    cat > "$CFG" <<EOF
# ProxLB configuration - created by install-agent.sh
# Reference: docs/03_configuration.md. Scheduling is done by proxlb-agent.
proxmox_api:
  hosts: []
proxmox_cluster:
  maintenance_nodes: []
  ignore_nodes: []
  overprovisioning: True
balancing:
  enable: True
  enforce_affinity: False
  parallel: False
  live: True
  with_local_disks: False
  with_conntrack_state: True
  balance_types: ['vm', 'ct']
  max_job_validation: 1800
  balanciness: 5
  method: memory
  mode: used
service:
  daemon: False
  enable_ha: False
  schedule:
    interval: 1
    format: hours
  log_level: INFO
EOF
    KEEP_API=0
fi
if [ "$KEEP_API" -eq 0 ]; then
    cp -p "$CFG" "$CFG.bak-$(date +%Y%m%d%H%M%S)"
    PLB_HOSTS="${B[PROXLB_API_HOSTS]}" PLB_USER="${B[PROXLB_API_USER]}" \
    PLB_TOKEN="${B[PROXLB_TOKEN_ID]}" PLB_SECRET="${B[PROXLB_TOKEN_SECRET]}" \
    "$PY" - "$CFG" <<'PYEOF'
import os, sys, yaml
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    cfg = yaml.safe_load(fh) or {}
api = cfg.setdefault("proxmox_api", {}) or {}
api.pop("pass", None)
api.pop("password", None)
api.update({
    "hosts": [h.strip() for h in os.environ["PLB_HOSTS"].split(",") if h.strip()],
    "user": os.environ["PLB_USER"],
    "token_id": os.environ["PLB_TOKEN"],
    "token_secret": os.environ["PLB_SECRET"],
    "ssl_verification": True,
})
api.setdefault("timeout", 10)
cfg["proxmox_api"] = api
with open(path, "w", encoding="utf-8") as fh:
    fh.write("# ProxLB configuration - proxmox_api set by install-agent.sh (backup next to this file)\n")
    yaml.safe_dump(cfg, fh, sort_keys=False)
PYEOF
    info "proxmox_api in $CFG now uses ${B[PROXLB_API_USER]}!${B[PROXLB_TOKEN_ID]} (backup: $CFG.bak-*)"
else
    warn "keeping proxmox_api in $CFG - its user needs VM.Config.Options on /vms/${B[PROXLB_VMID]}"
fi
chgrp plb "$CFG" && chmod 0640 "$CFG"

cat > /etc/proxlb/agent.yaml <<EOF
# proxlb-agent configuration - created by install-agent.sh
# ProxLB runs on this machine; its status goes to anchor VM ${B[PROXLB_VMID]} in the cluster.
vmid: ${B[PROXLB_VMID]}
guest_type: ${B[PROXLB_GUEST_TYPE]:-qemu}
proxlb_config: $CFG
python: $PY
poll_seconds: 10
run_timeout_seconds: 14400
state_dir: $STATE
pause_when_degraded: true
EOF
chgrp plb /etc/proxlb/agent.yaml && chmod 0640 /etc/proxlb/agent.yaml

# ------------------------------------------------------------------ agent ---
install -d -m 0755 "$AGENT_DIR"
install -m 0755 "$HERE/proxlb-agent.py" "$AGENT_DIR/proxlb-agent.py"
install -D -m 0644 "$HERE/tls-relax/sitecustomize.py" "$AGENT_DIR/tls-relax/sitecustomize.py"
install -m 0755 "$HERE/proxlb-ctl" /usr/local/bin/proxlb-ctl
sed -e "s#^ExecStart=.*#ExecStart=$PY $AGENT_DIR/proxlb-agent.py -c /etc/proxlb/agent.yaml#" \
    -e "s#^Environment=\"REQUESTS_CA_BUNDLE=.*#Environment=\"REQUESTS_CA_BUNDLE=$CA_BUNDLE\"#" \
    "$HERE/proxlb-agent.service" > /etc/systemd/system/proxlb-agent.service

# only one scheduler: the agent replaces the ProxLB daemon
if systemctl is-enabled proxlb.service >/dev/null 2>&1 || systemctl is-active proxlb.service >/dev/null 2>&1; then
    info "disabling proxlb.service - proxlb-agent schedules the runs now"
    systemctl disable --now proxlb.service || true
fi
if command -v docker >/dev/null && docker ps --format '{{.Image}} {{.Names}}' 2>/dev/null | grep -qi proxlb; then
    warn "a ProxLB Docker container is running - stop it, otherwise ProxLB runs twice"
fi

systemctl daemon-reload
systemctl enable proxlb-agent.service
# restart, not just start: an agent that is already running must pick up the new token
systemctl restart proxlb-agent.service
sleep 5
info "self-check (proxlb-ctl check):"
if ! REQUESTS_CA_BUNDLE="$CA_BUNDLE" "$PY" "$AGENT_DIR/proxlb-agent.py" --check -c /etc/proxlb/agent.yaml; then
    warn "the self-check found problems (see the -> hints above) - the web interface will show 'waiting for agent' until they are fixed"
fi
if systemctl is-active --quiet proxlb-agent.service; then
    info "proxlb-agent is running. Follow it with: journalctl -u proxlb-agent -f"
    info "Within ~30 s the Proxmox web interface shows Datacenter -> ProxLB: 'ProxLB runs on $(hostname)'."
else
    die "proxlb-agent did not start - see: journalctl -u proxlb-agent -n 50"
fi
