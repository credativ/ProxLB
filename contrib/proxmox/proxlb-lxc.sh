#!/usr/bin/env bash
# =============================================================================
#  proxlb-lxc.sh - deploy ProxLB in an LXC container on Proxmox VE,
#                  including the ProxLB tab in the Proxmox web interface.
#
#  Run as root on one Proxmox VE node:
#
#    bash proxlb-lxc.sh                       # defaults: DHCP, vmbr0, local-lvm
#    bash proxlb-lxc.sh --ip 10.0.0.50/24 --gw 10.0.0.1 --storage local-zfs
#    bash proxlb-lxc.sh --active              # also enable automatic balancing
#    bash proxlb-lxc.sh --upgrade 120         # update ProxLB + agent in CT 120
#
#  What it does:
#    1. creates a Proxmox user proxlb@pve with a least-privilege role and an
#       API token (one token per container),
#    2. creates an unprivileged Debian container (tagged "proxlb"),
#    3. installs ProxLB + proxlb-agent in a Python venv in the container,
#    4. writes /etc/proxlb/proxlb.yaml with all cluster nodes as API hosts,
#    5. installs the web-interface plugin (Datacenter -> ProxLB),
#    6. makes the container highly available (ha-manager): if its node dies,
#       Proxmox restarts it on another node. Needs shared storage, or local
#       ZFS (then storage replication to the other nodes is set up).
#
#    bash proxlb-lxc.sh --enable-ha 120       # make an existing CT HA
#
#  Safe by default: scheduled runs are dry-runs until you switch on
#  "Automatic balancing" in the GUI (or pass --active).
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------- defaults ---
CTID=""
CT_HOSTNAME="proxlb"
STORAGE=""
TEMPLATE_STORAGE="local"
BRIDGE="vmbr0"
VLAN=""
IP="dhcp"
GW=""
DNS=""
CORES=1
MEMORY=1024
DISK=6
INTERVAL=1
INTERVAL_FORMAT="hours"
METHOD="memory"
BALANCINESS=5
ACTIVE=0
GUI=1
GUI_ALL_NODES=1
REPO="https://github.com/credativ/ProxLB.git"
BRANCH="main"
SOURCE="auto"          # auto | local | git
UPGRADE=""
HA="auto"             # auto | yes | no
ENABLE_HA=""
REPL_SCHEDULE="*/5"
PVE_USER="proxlb@pve"

# how to call this script again (the proxlb-pve package sets PROXLB_LXC_CMD)
CMD="${PROXLB_LXC_CMD:-bash $0}"

usage() {
    cat <<EOF
Usage: $CMD [options]

Container
  --ctid ID              container ID (default: next free ID)
  --hostname NAME        hostname (default: $CT_HOSTNAME)
  --storage STORAGE      rootfs storage (default: local-lvm, else first rootdir storage)
  --template-storage S   storage for the Debian template (default: $TEMPLATE_STORAGE)
  --bridge BRIDGE        network bridge (default: $BRIDGE)
  --vlan TAG             VLAN tag
  --ip CIDR|dhcp         IPv4 address, e.g. 10.0.0.50/24 (default: dhcp)
  --gw IP                IPv4 gateway (with static --ip)
  --dns IP               DNS server
  --cores N              CPU cores (default: $CORES)
  --memory MB            memory (default: $MEMORY)
  --disk GB              disk size (default: $DISK)

ProxLB
  --interval N           run every N ... (default: $INTERVAL)
  --interval-format F    hours | minutes (default: $INTERVAL_FORMAT)
  --method M             memory | cpu | disk (default: $METHOD)
  --balanciness N        allowed spread in percent (default: $BALANCINESS)
  --active               enable automatic balancing (default: dry-run only)

Source / GUI
  --repo URL             git repository (default: $REPO)
  --branch BRANCH        git branch (default: $BRANCH)
  --source auto|local|git  auto = use this checkout if the script runs from one
  --no-gui               do not install the web-interface plugin
  --gui-this-node-only   install the plugin only on this node
  --upgrade CTID         upgrade ProxLB, agent and GUI of an existing container

High availability
  --ha / --no-ha         make the CT HA (default: auto = when the cluster and storage allow it)
  --enable-ha CTID       make an existing ProxLB CT HA
  --repl-schedule S      replication schedule for local ZFS (default: $REPL_SCHEDULE)
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --ctid) CTID="$2"; shift ;;
        --hostname) CT_HOSTNAME="$2"; shift ;;
        --storage) STORAGE="$2"; shift ;;
        --template-storage) TEMPLATE_STORAGE="$2"; shift ;;
        --bridge) BRIDGE="$2"; shift ;;
        --vlan) VLAN="$2"; shift ;;
        --ip) IP="$2"; shift ;;
        --gw) GW="$2"; shift ;;
        --dns) DNS="$2"; shift ;;
        --cores) CORES="$2"; shift ;;
        --memory) MEMORY="$2"; shift ;;
        --disk) DISK="$2"; shift ;;
        --interval) INTERVAL="$2"; shift ;;
        --interval-format) INTERVAL_FORMAT="$2"; shift ;;
        --method) METHOD="$2"; shift ;;
        --balanciness) BALANCINESS="$2"; shift ;;
        --active) ACTIVE=1 ;;
        --repo) REPO="$2"; shift ;;
        --branch) BRANCH="$2"; shift ;;
        --source) SOURCE="$2"; shift ;;
        --no-gui) GUI=0 ;;
        --gui-this-node-only) GUI_ALL_NODES=0 ;;
        --upgrade) UPGRADE="$2"; shift ;;
        --ha) HA=yes ;;
        --no-ha) HA=no ;;
        --enable-ha) ENABLE_HA="$2"; HA=yes; shift ;;
        --repl-schedule) REPL_SCHEDULE="$2"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

# ----------------------------------------------------------------- helpers ---
C_G=$'\e[32m'; C_Y=$'\e[33m'; C_R=$'\e[31m'; C_0=$'\e[0m'
info() { echo "${C_G}[ProxLB]${C_0} $*"; }
warn() { echo "${C_Y}[ProxLB] WARNING:${C_0} $*"; }
die()  { echo "${C_R}[ProxLB] ERROR:${C_0} $*" >&2; exit 1; }
# C.UTF-8 exists in every container (the caller's locale may not); never read stdin
ct()   { pct exec "$CTID" -- env LC_ALL=C.UTF-8 LANG=C.UTF-8 LANGUAGE= "$@" </dev/null; }
ctsh() { pct exec "$CTID" -- env LC_ALL=C.UTF-8 LANG=C.UTF-8 LANGUAGE= bash -ec "$1" </dev/null; }

[ "$(id -u)" -eq 0 ] || die "run as root on a Proxmox VE node"
command -v pct >/dev/null && command -v pveum >/dev/null || die "this is not a Proxmox VE node"

case "$METHOD" in memory|cpu|disk) ;; *) die "--method must be memory, cpu or disk" ;; esac
case "$INTERVAL_FORMAT" in hours|minutes) ;; *) die "--interval-format must be hours or minutes" ;; esac
[[ "$INTERVAL" =~ ^[0-9]+$ && "$BALANCINESS" =~ ^[0-9]+$ ]] || die "--interval and --balanciness must be numbers"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." 2>/dev/null && pwd || true)"
if [ "$SOURCE" = "auto" ]; then
    if [ -f "$REPO_ROOT/setup.py" ] && [ -d "$REPO_ROOT/proxlb" ] && [ -f "$SCRIPT_DIR/agent/proxlb-agent.py" ]; then
        SOURCE=local
    else
        SOURCE=git
    fi
fi

json_get() { # json_get <perl expression on $d>  (reads JSON from stdin)
    perl -MJSON -e 'my $d = decode_json(join("", <STDIN>)); '"$1"
}

# ------------------------------------------------------ source into the CT ---
deploy_source() {
    info "deploying ProxLB source ($SOURCE) into CT $CTID"
    ctsh "rm -rf /opt/proxlb/src && mkdir -p /opt/proxlb"
    if [ "$SOURCE" = "local" ]; then
        local tgz
        tgz=$(mktemp /tmp/proxlb-src.XXXXXX.tgz)
        tar -C "$REPO_ROOT" --exclude=.git --exclude='*.pyc' --exclude=__pycache__ -czf "$tgz" .
        pct push "$CTID" "$tgz" /tmp/proxlb-src.tgz
        rm -f "$tgz"
        ctsh "mkdir -p /opt/proxlb/src && tar -C /opt/proxlb/src -xzf /tmp/proxlb-src.tgz && rm -f /tmp/proxlb-src.tgz"
    else
        ct git clone --depth 1 --branch "$BRANCH" "$REPO" /opt/proxlb/src
    fi
}

install_software() {
    info "installing ProxLB into a Python venv (this can take a few minutes)"
    ctsh "[ -x /opt/proxlb/venv/bin/python ] || python3 -m venv /opt/proxlb/venv"
    ctsh "/opt/proxlb/venv/bin/pip install --quiet --no-cache-dir --upgrade pip"
    ctsh "/opt/proxlb/venv/bin/pip install --quiet --no-cache-dir --upgrade /opt/proxlb/src"
    ctsh "/opt/proxlb/venv/bin/python -m proxlb --version" | head -1

    info "installing proxlb-agent"
    ctsh "id plb >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/proxlb --shell /usr/sbin/nologin plb"
    ctsh "install -d -o plb -g plb -m 0750 /var/lib/proxlb /var/log/proxlb /var/log/proxlb/solver
          install -d -m 0755 /opt/proxlb/agent
          install -m 0755 /opt/proxlb/src/contrib/proxmox/agent/proxlb-agent.py /opt/proxlb/agent/proxlb-agent.py
          install -D -m 0644 /opt/proxlb/src/contrib/proxmox/agent/tls-relax/sitecustomize.py /opt/proxlb/agent/tls-relax/sitecustomize.py
          install -m 0755 /opt/proxlb/src/contrib/proxmox/agent/proxlb-ctl /usr/local/bin/proxlb-ctl
          install -m 0644 /opt/proxlb/src/contrib/proxmox/agent/proxlb-agent.service /etc/systemd/system/proxlb-agent.service
          systemctl daemon-reload"
}

install_gui() {
    [ "$GUI" -eq 1 ] || return 0
    info "installing the ProxLB tab into the Proxmox web interface"
    local tmp
    tmp=$(mktemp -d /tmp/proxlb-gui.XXXXXX)
    pct pull "$CTID" /opt/proxlb/src/contrib/proxmox/gui/proxlb-gui.js "$tmp/proxlb-gui.js"
    pct pull "$CTID" /opt/proxlb/src/contrib/proxmox/gui/install-gui.sh "$tmp/install-gui.sh"
    local args=(--js "$tmp/proxlb-gui.js")
    [ "$GUI_ALL_NODES" -eq 1 ] && args+=(--all-nodes)
    bash "$tmp/install-gui.sh" "${args[@]}" || warn "GUI plugin installation failed"
    rm -rf "$tmp"
}

# ---------------------------------------------------------- high availability ---
# Proxmox HA restarts the container on another node when its node fails.
# The rootfs must be reachable there: shared storage, or local ZFS with
# storage replication (the CT then restarts from the last replica).
SHARED_TYPES=" rbd cephfs nfs cifs glusterfs iscsi iscsidirect zfs "
setup_ha() {
    [ "$HA" = "no" ] && return 0
    local nodes count rootfs stype shared allowed ct_node others="" n i last_job=""
    nodes=$(pvesh get /cluster/status --output-format json | json_get '
        print join(" ", sort map { $_->{name} } grep { $_->{type} eq "node" } @$d)')
    count=$(echo "$nodes" | wc -w)
    if [ "$count" -lt 2 ]; then
        [ "$HA" = "yes" ] && warn "single node - HA needs a cluster, skipping"
        HA_RESULT="not possible (single node)"
        return 0
    fi
    [ "$count" -eq 2 ] && warn "2-node cluster: when one node fails the other has no quorum and HA cannot act. Add a QDevice: pvecm qdevice setup <ip>"

    ct_node=$(pvesh get /cluster/resources --type vm --output-format json | \
        PLB_CTID="$CTID" json_get 'for (@$d) { print $_->{node} if $_->{type} eq "lxc" && $_->{vmid} == $ENV{PLB_CTID} }')
    [ -n "$ct_node" ] || die "container $CTID not found in the cluster"
    rootfs=$(pvesh get "/nodes/$ct_node/lxc/$CTID/config" --output-format json | \
        json_get 'my ($s) = ($d->{rootfs} // "") =~ /^([^:]+):/; print $s // ""')
    read -r stype shared allowed < <(pvesh get "/storage/$rootfs" --output-format json | json_get '
        print join(" ", $d->{type} // "", $d->{shared} ? 1 : 0, $d->{nodes} // "-"), "\n"')
    [[ "$SHARED_TYPES" == *" $stype "* ]] && shared=1

    if [ "$shared" = "1" ]; then
        info "storage $rootfs ($stype) is shared - enabling HA"
    elif [ "$stype" = "zfspool" ]; then
        for n in $nodes; do
            [ "$n" = "$ct_node" ] && continue
            if [ "$allowed" != "-" ] && [[ ",$allowed," != *",$n,"* ]]; then
                warn "storage $rootfs is not available on $n - HA could start the CT there and fail"
                continue
            fi
            others="$others $n"
        done
        if [ -z "$others" ]; then
            warn "no other node has storage $rootfs - HA not possible"
            HA_RESULT="not possible (no replication target)"
            return 0
        fi
        info "storage $rootfs is local ZFS - replicating CT $CTID to:$others (every $REPL_SCHEDULE)"
        for n in $others; do
            # existing job to this node? otherwise use the next free job number
            i=$(pvesh get /cluster/replication --output-format json | PLB_CTID="$CTID" PLB_NODE="$n" json_get '
                my @j = grep { $_->{guest} == $ENV{PLB_CTID} } @$d;
                if (grep { $_->{target} eq $ENV{PLB_NODE} } @j) { print "exists"; exit }
                my %used = map { $_->{jobnum} => 1 } @j; my $i = 0; $i++ while $used{$i}; print $i')
            [ "$i" = "exists" ] && continue
            # never reuse a job number of this run, even if the API is slow to list it
            if [ -n "${last_job:-}" ] && [ "$i" -le "$last_job" ]; then i=$((last_job + 1)); fi
            last_job=$i
            if pvesh create /cluster/replication --id "$CTID-$i" --type local --target "$n" \
                    --schedule "$REPL_SCHEDULE" --comment "ProxLB HA" >/dev/null; then
                pvesh create "/nodes/$ct_node/replication/$CTID-$i/schedule_now" >/dev/null || true
            else
                warn "replication to $n failed"
            fi
        done
    else
        if [ "$HA" = "yes" ]; then
            warn "storage $rootfs ($stype) is local and not ZFS - another node cannot start this CT."
            warn "move it first, e.g.: pct move-volume $CTID rootfs <shared-storage>, then: $CMD --enable-ha $CTID"
        else
            info "storage $rootfs is local - HA skipped (use shared storage or ZFS, then --enable-ha $CTID)"
        fi
        HA_RESULT="not possible (local $stype storage)"
        return 0
    fi

    if ha-manager config 2>/dev/null | grep -q "^ct: *$CTID\$"; then
        ha-manager set "ct:$CTID" --state started
    else
        ha-manager add "ct:$CTID" --state started --max_restart 2 --max_relocate 2 --comment "ProxLB"
    fi
    HA_RESULT="enabled (ha-manager ct:$CTID${others:+, ZFS replication to$others})"
    info "HA $HA_RESULT"
}
HA_RESULT="off"

if [ -n "$ENABLE_HA" ]; then
    CTID="$ENABLE_HA"
    setup_ha
    exit 0
fi

# ----------------------------------------------------------------- upgrade ---
if [ -n "$UPGRADE" ]; then
    CTID="$UPGRADE"
    pct status "$CTID" >/dev/null 2>&1 || die "container $CTID does not exist"
    pct status "$CTID" | grep -q running || pct start "$CTID"
    if ct test -d /opt/proxlb/src/.git && [ "$SOURCE" = "git" ]; then
        info "updating git checkout in CT $CTID"
        ct git -C /opt/proxlb/src pull --ff-only
    else
        deploy_source
    fi
    install_software
    ct systemctl restart proxlb-agent
    install_gui
    info "upgrade of CT $CTID done"
    exit 0
fi

# ------------------------------------------------------------ preparations ---
[ -n "$CTID" ] || CTID=$(pvesh get /cluster/nextid)
if pct status "$CTID" >/dev/null 2>&1 || qm status "$CTID" >/dev/null 2>&1; then
    die "ID $CTID is already in use"
fi

if [ -z "$STORAGE" ]; then
    STORAGES=$(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
    if echo "$STORAGES" | grep -qx local-lvm; then STORAGE=local-lvm
    elif echo "$STORAGES" | grep -qx local-zfs; then STORAGE=local-zfs
    else STORAGE=$(echo "$STORAGES" | head -1); fi
    [ -n "$STORAGE" ] || die "no storage for containers found, use --storage"
fi

info "ProxLB container: CT $CTID, storage $STORAGE, bridge $BRIDGE, ip $IP, source $SOURCE"

# -------------------------------------------------------------- API access ---
info "creating Proxmox user $PVE_USER, roles and API token"
ROLE_PRIVS="Datastore.Audit,Sys.Audit,VM.Audit,VM.Migrate"
if pveum role list --output-format json | json_get 'exit(!(grep { $_->{roleid} eq "ProxLB" } @$d))'; then
    pveum role modify ProxLB --privs "$ROLE_PRIVS"
else
    pveum role add ProxLB --privs "$ROLE_PRIVS"
fi
if ! pveum role list --output-format json | json_get 'exit(!(grep { $_->{roleid} eq "ProxLBAgent" } @$d))'; then
    pveum role add ProxLBAgent --privs "VM.Config.Options"
fi
if ! pveum user list --output-format json | PLB_USER="$PVE_USER" json_get 'exit(!(grep { $_->{userid} eq $ENV{PLB_USER} } @$d))'; then
    pveum user add "$PVE_USER" --comment "ProxLB service user (API token only)"
fi
pveum acl modify / --users "$PVE_USER" --roles ProxLB
TOKEN_NAME="lxc$CTID"
pveum user token remove "$PVE_USER" "$TOKEN_NAME" >/dev/null 2>&1 || true
TOKEN_SECRET=$(pveum user token add "$PVE_USER" "$TOKEN_NAME" --privsep 0 \
    --comment "ProxLB in CT $CTID" --output-format json | json_get 'print $d->{value}')
[ -n "$TOKEN_SECRET" ] || die "could not create API token"

# API hosts: all cluster nodes
API_HOSTS=$(pvesh get /cluster/status --output-format json | json_get '
    my @ips = map { $_->{ip} } grep { $_->{type} eq "node" && $_->{ip} } @$d;
    print join(", ", map { "\x27$_\x27" } @ips)')
if [ -z "$API_HOSTS" ]; then
    API_HOSTS="'$(hostname --ip-address | awk '{print $1}')'"
fi
info "API hosts: $API_HOSTS"

# ---------------------------------------------------------------- template ---
# The template list has amd64 and arm64 images under the same name - only take
# the host's own architecture (an arm64 rootfs on x86 fails with "Exec format error").
HOST_ARCH=$(dpkg --print-architecture 2>/dev/null || echo amd64)
info "looking for a Debian template ($HOST_ARCH)"
pveam update >/dev/null 2>&1 || warn "pveam update failed, using the cached template list"
TEMPLATE=""
for series in debian-13-standard debian-12-standard; do
    TEMPLATE=$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep "^${series}_.*_${HOST_ARCH}\.tar" | sort -V | tail -1 || true)
    [ -n "$TEMPLATE" ] && break
    TEMPLATE=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | sed 's#.*/##' | grep "^${series}_.*_${HOST_ARCH}\.tar" | sort -V | tail -1 || true)
    [ -n "$TEMPLATE" ] && break
done
[ -n "$TEMPLATE" ] || die "no Debian 12/13 template for $HOST_ARCH found"
if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$TEMPLATE"; then
    info "downloading $TEMPLATE"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi

# --------------------------------------------------------------- container ---
NET="name=eth0,bridge=$BRIDGE,ip=$IP"
[ -n "$GW" ] && NET="$NET,gw=$GW"
[ -n "$VLAN" ] && NET="$NET,tag=$VLAN"
TAGS="proxlb;plb_ignore_vm"   # ProxLB never migrates its own container
[ "$ACTIVE" -eq 1 ] && TAGS="$TAGS;proxlb-active"

info "creating container $CTID from $TEMPLATE"
CREATE_ARGS=(
    --hostname "$CT_HOSTNAME" --ostype debian --unprivileged 1 --features nesting=1
    --cores "$CORES" --memory "$MEMORY" --swap 512
    --rootfs "$STORAGE:$DISK" --net0 "$NET" --onboot 1
    --tags "$TAGS" --timezone host
    --description "ProxLB - installing ..."
)
[ -n "$DNS" ] && CREATE_ARGS+=(--nameserver "$DNS")
pct create "$CTID" "$TEMPLATE_STORAGE:vztmpl/$TEMPLATE" "${CREATE_ARGS[@]}"
pveum acl modify "/vms/$CTID" --users "$PVE_USER" --roles ProxLBAgent
pct start "$CTID" || die "container $CTID was created but does not start. See why with:
    pct start $CTID --debug 2>&1 | grep -E 'ERROR|error'
  remove it with: pct destroy $CTID"

info "waiting for network in the container"
for _ in $(seq 1 60); do
    ct getent hosts deb.debian.org >/dev/null 2>&1 && break
    sleep 2
done
ct getent hosts deb.debian.org >/dev/null 2>&1 || die "container $CTID has no network/DNS - check --ip/--gw/--bridge"

info "installing packages in the container"
ctsh "export DEBIAN_FRONTEND=noninteractive APT_LISTCHANGES_FRONTEND=none NEEDRESTART_MODE=a
      apt-get update -qq
      apt-get install -y -qq -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
          python3 python3-venv git ca-certificates nano >/dev/null"

# Trust the cluster CA so TLS verification works against the node certificates
pct push "$CTID" /etc/pve/pve-root-ca.pem /usr/local/share/ca-certificates/pve-root-ca.crt
ct update-ca-certificates >/dev/null 2>&1

deploy_source
install_software

# ------------------------------------------------------------------ config ---
SSL_VERIFY=True
FIRST_HOST=$(echo "$API_HOSTS" | cut -d"'" -f2)
# chain + hostname are verified; only the RFC 5280 strict checks are relaxed
# (Proxmox CAs before pve-cluster 9.1.2 lack keyUsage, PVE bug #6701)
if ! ct env PYTHONPATH=/opt/proxlb/agent/tls-relax /opt/proxlb/venv/bin/python -c "
import requests, sys
host = sys.argv[1]
url = 'https://[%s]:8006/api2/json/version' % host if ':' in host else 'https://%s:8006/api2/json/version' % host
requests.get(url, timeout=5, verify='/etc/ssl/certs/ca-certificates.crt')
" "$FIRST_HOST" >/dev/null 2>&1; then
    warn "TLS verification against $FIRST_HOST failed - setting ssl_verification: False"
    SSL_VERIFY=False
fi

info "writing /etc/proxlb/proxlb.yaml"
ct mkdir -p /etc/proxlb
CFG=$(mktemp)
cat > "$CFG" <<EOF
# ProxLB configuration - generated by proxlb-lxc.sh
# Full reference: /opt/proxlb/src/config/proxlb_example.yaml and docs/03_configuration.md
# Scheduling is done by proxlb-agent (service.schedule below); 'service.daemon'
# is ignored. Turn real balancing on/off in Datacenter -> ProxLB.
proxmox_api:
  hosts: [$API_HOSTS]
  user: $PVE_USER
  token_id: $TOKEN_NAME
  token_secret: $TOKEN_SECRET
  ssl_verification: $SSL_VERIFY
  timeout: 10

proxmox_cluster:
  maintenance_nodes: []
  ignore_nodes: []
  overprovisioning: True

balancing:
  enable: True
  enforce_affinity: False
  enforce_pinning: False
  parallel: False
  parallel_jobs: 1
  live: True
  # Migrating guests with local disks needs Datastore.AllocateSpace for the
  # proxlb@pve user - grant it before switching this on.
  with_local_disks: False
  with_conntrack_state: True
  balance_types: ['vm', 'ct']
  max_job_validation: 1800
  balanciness: $BALANCINESS
  method: $METHOD
  mode: used
  balance_larger_guests_first: False
  node_resource_reserve:
    defaults:
      memory: 4

service:
  daemon: False
  enable_ha: False
  schedule:
    interval: $INTERVAL
    format: $INTERVAL_FORMAT
  log_level: INFO

# solver:
#   enable: False
#   mode: shadow
#   log_dir: /var/log/proxlb/solver
EOF
pct push "$CTID" "$CFG" /etc/proxlb/proxlb.yaml --perms 0640
cat > "$CFG" <<EOF
# proxlb-agent configuration - generated by proxlb-lxc.sh
ctid: $CTID
proxlb_config: /etc/proxlb/proxlb.yaml
python: /opt/proxlb/venv/bin/python
poll_seconds: 10
run_timeout_seconds: 14400
state_dir: /var/lib/proxlb
# Turn real runs into dry-runs while a node is offline or HA recovers guests
pause_when_degraded: true
EOF
pct push "$CTID" "$CFG" /etc/proxlb/agent.yaml --perms 0640
rm -f "$CFG"
ctsh "chgrp plb /etc/proxlb /etc/proxlb/proxlb.yaml /etc/proxlb/agent.yaml && chmod 0750 /etc/proxlb"

ct systemctl enable --now proxlb-agent >/dev/null 2>&1
sleep 3
ct systemctl is-active --quiet proxlb-agent || warn "proxlb-agent is not running - check: pct exec $CTID -- journalctl -u proxlb-agent"

setup_ha
install_gui

CT_IP=$(ct hostname -I 2>/dev/null | awk '{print $1}' || true)
cat <<EOF

${C_G}ProxLB is installed.${C_0}

  Container         CT $CTID ($CT_HOSTNAME${CT_IP:+, $CT_IP})
  API user / token  $PVE_USER!$TOKEN_NAME
  Mode              $( [ "$ACTIVE" -eq 1 ] && echo "automatic balancing ON" || echo "dry-run only (switch on in the GUI)" )
  High availability $HA_RESULT
  Schedule          every $INTERVAL $INTERVAL_FORMAT, method $METHOD, balanciness $BALANCINESS %

  Web interface     reload the page (Ctrl+F5) and open  Datacenter -> ProxLB
  In the container  pct enter $CTID   then   proxlb-ctl {dryrun|run|status|logs|config}
  Upgrade later     $CMD --upgrade $CTID

EOF
