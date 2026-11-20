#!/usr/bin/env bash
# =============================================================================
#  proxlb-external.sh - connect ProxLB running on ANOTHER machine (a VM
#                       outside the cluster, a physical server, a Docker host)
#                       to the ProxLB tab in the Proxmox VE web interface.
#
#  Run as root on one Proxmox VE node:
#
#    bash proxlb-external.sh                  # next free VMID, anchor "proxlb-anchor"
#    bash proxlb-external.sh --vmid 9000
#    bash proxlb-external.sh --ct 150         # ... and connect LXC 150 automatically
#    bash proxlb-external.sh --vmid 138 --ct 150   # existing anchor 138 -> connect LXC 150
#    bash proxlb-external.sh --ssh root@mgmt01     # connect a machine over SSH
#    bash proxlb-external.sh --create-ct --ip 10.0.0.60/24 --gw 10.0.0.1
#                                             # create a new LXC host for ProxLB too
#
#  What it does:
#    1. creates a tiny "anchor" VM: no disks, no network, never started. It
#       uses no storage or RAM; its Notes and tags are the mailbox between
#       the web interface and proxlb-agent. Tags: proxlb, plb_ignore_vm.
#       Protected against deletion; HA-managed (state stopped) so it moves to
#       a surviving node when its node fails.
#    2. creates proxlb@pve with the role ProxLB on / and ProxLBAgent
#       (VM.Config.Options) on the anchor only, plus an API token,
#    3. installs the web-interface plugin on all nodes,
#    4. writes /root/proxlb-external-<VMID>.tar (0600): API hosts, token and
#       the cluster CA - copy it to the ProxLB machine and run there:
#         install-agent.sh --bundle proxlb-external-<VMID>.tar
#       With --ct / --ssh this step is done for you: the ProxLB source and the
#       bundle are pushed to the target, install-agent.sh runs there, and the
#       bundle is deleted on both sides afterwards.
# =============================================================================
set -euo pipefail

VMID=""
NAME="proxlb-anchor"
PVE_USER="proxlb@pve"
GUI=1
GUI_ALL_NODES=1
HA="auto"
CMD="${PROXLB_EXTERNAL_CMD:-bash $0}"

usage() {
    cat <<EOF
Usage: $CMD [options]

  --vmid ID              VMID for the anchor VM (default: next free ID)
  --name NAME            anchor VM name (default: $NAME)
  --no-ha                do not make the anchor VM HA-managed
  --no-gui               do not install the web-interface plugin
  --gui-this-node-only   install the plugin only on this node
  --rotate-token         only create a new API token + bundle for an existing anchor VM

Connect the ProxLB machine automatically (instead of copying the bundle):
  --ct CTID              ProxLB runs in this LXC container of the cluster (any node)
  --ssh USER@HOST        ProxLB runs on this machine, reachable over SSH as root
  --create-ct            create a new Debian LXC on this node as the ProxLB host
                         (tags plb_ignore_vm, proxlb-host; HA in a cluster unless --no-ha)

Options for --create-ct:
  --ctid ID              container ID (default: next free ID)
  --hostname NAME        hostname (default: $CT_HOSTNAME)
  --storage STORAGE      rootfs storage (default: local-lvm, else first rootdir storage)
  --template-storage S   storage for the Debian template (default: $TEMPLATE_STORAGE)
  --bridge BRIDGE        network bridge (default: $BRIDGE)
  --vlan TAG             VLAN tag
  --ip CIDR|dhcp         IPv4 address, e.g. 10.0.0.60/24 (default: dhcp)
  --gw IP                IPv4 gateway (with a static --ip)
  --dns IP               DNS server
  --cores N / --memory MB / --disk GB   (default: $CORES / $MEMORY / $DISK)

An existing anchor VM (--vmid, tag proxlb) is reused, with its bundle if it is
still in /root; otherwise a new token is created.
EOF
}

ROTATE=0
TARGET_CT=""
TARGET_SSH=""
CREATE_CT=0
NEW_CTID=""
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
while [ $# -gt 0 ]; do
    case "$1" in
        --vmid) VMID="$2"; shift ;;
        --name) NAME="$2"; shift ;;
        --no-ha) HA=no ;;
        --no-gui) GUI=0 ;;
        --gui-this-node-only) GUI_ALL_NODES=0 ;;
        --rotate-token) ROTATE=1 ;;
        --ct) TARGET_CT="$2"; shift ;;
        --ssh) TARGET_SSH="$2"; shift ;;
        --create-ct) CREATE_CT=1 ;;
        --ctid) NEW_CTID="$2"; shift ;;
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
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 1 ;;
    esac
    shift
done

C_G=$'\e[32m'; C_Y=$'\e[33m'; C_R=$'\e[31m'; C_0=$'\e[0m'
info() { echo "${C_G}[ProxLB]${C_0} $*"; }
warn() { echo "${C_Y}[ProxLB] WARNING:${C_0} $*"; }
die()  { echo "${C_R}[ProxLB] ERROR:${C_0} $*" >&2; exit 1; }
json_get() { perl -MJSON -e 'my $d = decode_json(join("", <STDIN>)); '"$1"; }

[ "$(id -u)" -eq 0 ] || die "run as root on a Proxmox VE node"
command -v qm >/dev/null && command -v pveum >/dev/null || die "this is not a Proxmox VE node"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

guest_info() { # "node type status tags" of VMID $1 (any type), empty if it does not exist
    pvesh get /cluster/resources --type vm --output-format json | PLB_ID="$1" json_get '
        for (@$d) { next unless $_->{vmid} == $ENV{PLB_ID};
            print join(" ", $_->{node}, $_->{type}, $_->{status} // "-", $_->{tags} || "-") }'
}
guest_node() { guest_info "$1" | awk '{print $1}'; }
local_node() {
    pvesh get /cluster/status --output-format json | json_get '
        for (@$d) { print $_->{name} if $_->{type} eq "node" && $_->{local} }'
}
node_ip() {
    pvesh get /cluster/status --output-format json | PLB_N="$1" json_get '
        for (@$d) { print $_->{ip} if $_->{type} eq "node" && $_->{name} eq $ENV{PLB_N} }'
}

[ -n "$TARGET_CT" ] && [ -n "$TARGET_SSH" ] && die "use either --ct or --ssh, not both"
if [ "$CREATE_CT" -eq 1 ]; then
    [ -z "$TARGET_CT" ] && [ -z "$TARGET_SSH" ] || die "--create-ct cannot be combined with --ct or --ssh"
    [ "$ROTATE" -eq 0 ] || die "--create-ct cannot be combined with --rotate-token"
fi

# ------------------------------------------- create a new LXC host (opt.) ---
create_ct() {
    [ -n "$NEW_CTID" ] || NEW_CTID=$(pvesh get /cluster/nextid)
    [ -z "$(guest_node "$NEW_CTID")" ] || die "ID $NEW_CTID is already in use"
    [ -n "$VMID" ] && [ "$VMID" = "$NEW_CTID" ] && die "--ctid and --vmid must differ"
    if [ -z "$STORAGE" ]; then
        local storages
        storages=$(pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $3=="active" {print $1}')
        if echo "$storages" | grep -qx local-lvm; then STORAGE=local-lvm
        elif echo "$storages" | grep -qx local-zfs; then STORAGE=local-zfs
        else STORAGE=$(echo "$storages" | head -1); fi
        [ -n "$STORAGE" ] || die "no storage for containers found, use --storage"
    fi
    # The template list has amd64 and arm64 images under the same name - only
    # take the host's own architecture (arm64 on x86: "Exec format error").
    local HOST_ARCH
    HOST_ARCH=$(dpkg --print-architecture 2>/dev/null || echo amd64)
    info "looking for a Debian template ($HOST_ARCH)"
    pveam update >/dev/null 2>&1 || warn "pveam update failed, using the cached template list"
    local template="" series
    for series in debian-13-standard debian-12-standard; do
        template=$(pveam available --section system 2>/dev/null | awk '{print $2}' | grep "^${series}_.*_${HOST_ARCH}\.tar" | sort -V | tail -1 || true)
        [ -n "$template" ] && break
        template=$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '{print $1}' | sed 's#.*/##' | grep "^${series}_.*_${HOST_ARCH}\.tar" | sort -V | tail -1 || true)
        [ -n "$template" ] && break
    done
    [ -n "$template" ] || die "no Debian 12/13 template for $HOST_ARCH found"
    if ! pveam list "$TEMPLATE_STORAGE" | grep -q "$template"; then
        info "downloading $template"
        pveam download "$TEMPLATE_STORAGE" "$template"
    fi
    local net="name=eth0,bridge=$BRIDGE,ip=$IP"
    [ -n "$GW" ] && net="$net,gw=$GW"
    [ -n "$VLAN" ] && net="$net,tag=$VLAN"
    local args=(
        --hostname "$CT_HOSTNAME" --ostype debian --unprivileged 1 --features nesting=1
        --cores "$CORES" --memory "$MEMORY" --swap 512
        --rootfs "$STORAGE:$DISK" --net0 "$net" --onboot 1
        # not "proxlb": that tag marks the anchor VM the web interface reads
        --tags "plb_ignore_vm;proxlb-host" --timezone host
        --description "ProxLB host - runs ProxLB and proxlb-agent. The status is in the anchor VM (tag proxlb)."
    )
    [ -n "$DNS" ] && args+=(--nameserver "$DNS")
    info "creating LXC $NEW_CTID ($CT_HOSTNAME) on $STORAGE from $template"
    pct create "$NEW_CTID" "$TEMPLATE_STORAGE:vztmpl/$template" "${args[@]}"
    pct start "$NEW_CTID" || die "LXC $NEW_CTID was created but does not start. See why with:
    pct start $NEW_CTID --debug 2>&1 | grep -E 'ERROR|error'
  remove it with: pct destroy $NEW_CTID   (nothing else was changed yet)"
    # name to resolve as network check (set PROXLB_NET_CHECK_HOST for a local mirror)
    local check="${PROXLB_NET_CHECK_HOST:-deb.debian.org}" _i
    info "waiting for network in LXC $NEW_CTID"
    for _i in $(seq 1 60); do
        pct exec "$NEW_CTID" -- getent hosts "$check" >/dev/null 2>&1 && break
        sleep 2
    done
    pct exec "$NEW_CTID" -- getent hosts "$check" >/dev/null 2>&1 \
        || die "LXC $NEW_CTID has no network/DNS - check --ip/--gw/--bridge (the container stays for debugging)"
    TARGET_CT="$NEW_CTID"
}
[ "$CREATE_CT" -eq 1 ] && create_ct

# ------------------------------------------------- check the target first ---
CT_NODE=""
CT_IP=""
if [ -n "$TARGET_CT" ]; then
    [[ "$TARGET_CT" =~ ^[0-9]+$ ]] || die "--ct needs a container ID"
    read -r CT_NODE ct_type ct_status _ <<<"$(guest_info "$TARGET_CT")"
    [ -n "${CT_NODE:-}" ] || die "container $TARGET_CT does not exist"
    [ "$ct_type" = "lxc" ] || die "$TARGET_CT is not an LXC container (it is: $ct_type)"
    [ "$ct_status" = "running" ] || die "container $TARGET_CT is $ct_status - start it first: pct start $TARGET_CT"
    if [ "$CT_NODE" != "$(local_node)" ]; then
        CT_IP=$(node_ip "$CT_NODE")
        [ -n "$CT_IP" ] || die "no IP for node $CT_NODE"
        ssh -o BatchMode=yes "root@$CT_IP" true || die "cannot SSH to node $CT_NODE ($CT_IP) as root"
    fi
    info "target: LXC $TARGET_CT on node $CT_NODE"
fi
if [ -n "$TARGET_SSH" ]; then
    ssh -o ConnectTimeout=10 "$TARGET_SSH" true || die "cannot SSH to $TARGET_SSH"
    info "target: $TARGET_SSH over SSH"
fi

# ------------------------------------------------------------- anchor VM ---
REUSE=0
if [ "$ROTATE" -eq 1 ]; then
    [ -n "$VMID" ] || die "--rotate-token needs --vmid"
    [ -n "$(guest_node "$VMID")" ] || die "VM $VMID does not exist"
elif [ -n "$VMID" ] && [ -n "$(guest_node "$VMID")" ]; then
    read -r _ a_type _ a_tags <<<"$(guest_info "$VMID")"
    if [ "$a_type" = "qemu" ] && [[ ";$a_tags;" == *";proxlb;"* ]]; then
        REUSE=1
        info "using the existing anchor VM $VMID"
    else
        die "ID $VMID is already in use (and is not a ProxLB anchor VM)"
    fi
else
    [ -n "$VMID" ] || VMID=$(pvesh get /cluster/nextid)
    info "creating anchor VM $VMID ($NAME): no disks, no network, never started"
    qm create "$VMID" --name "$NAME" --memory 64 --cores 1 --ostype other \
        --onboot 0 --tags "proxlb;plb_ignore_vm" --protection 1 \
        --description "ProxLB anchor - status of ProxLB running on another machine. Do not start or delete."
fi
BUNDLE="/root/proxlb-external-$VMID.tar"
NEW_TOKEN=1
if [ "$REUSE" -eq 1 ] && [ -s "$BUNDLE" ]; then
    NEW_TOKEN=0
    info "using the existing bundle $BUNDLE"
elif [ "$REUSE" -eq 1 ]; then
    warn "no bundle $BUNDLE any more - creating a new token; an agent that used the old token stops working"
fi

# -------------------------------------------------------------- API access ---
info "checking Proxmox user $PVE_USER and roles"
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
pveum acl modify "/vms/$VMID" --users "$PVE_USER" --roles ProxLBAgent
TOKEN_NAME="external$VMID"
TOKEN_SECRET=""
if [ "$NEW_TOKEN" -eq 1 ]; then
    info "creating API token $PVE_USER!$TOKEN_NAME"
    pveum user token remove "$PVE_USER" "$TOKEN_NAME" >/dev/null 2>&1 || true
    TOKEN_SECRET=$(pveum user token add "$PVE_USER" "$TOKEN_NAME" --privsep 0 \
        --comment "external ProxLB, anchor VM $VMID" --output-format json | json_get 'print $d->{value}')
    [ -n "$TOKEN_SECRET" ] || die "could not create API token"
fi

API_HOSTS=$(pvesh get /cluster/status --output-format json | json_get '
    print join(",", map { $_->{ip} } grep { $_->{type} eq "node" && $_->{ip} } @$d)')
[ -n "$API_HOSTS" ] || API_HOSTS=$(hostname --ip-address | awk '{print $1}')
CLUSTER=$(pvesh get /cluster/status --output-format json | json_get '
    for (@$d) { print $_->{name} if $_->{type} eq "cluster" }')

# -------------------------------------------------------------------- HA ---
NODES=$(pvesh get /cluster/status --output-format json | json_get 'print scalar grep { $_->{type} eq "node" } @$d')
if [ "$ROTATE" -eq 0 ] && [ "$HA" != "no" ] && [ "${NODES:-1}" -ge 2 ]; then
    if ha-manager config 2>/dev/null | grep -q "^vm: *$VMID\$"; then
        ha-manager set "vm:$VMID" --state stopped
    else
        ha-manager add "vm:$VMID" --state stopped --comment "ProxLB anchor"
    fi
    info "anchor VM is HA-managed (state stopped): it moves to another node if its node fails"
    [ "$NODES" -eq 2 ] && warn "2-node cluster: HA needs a QDevice for quorum (pvecm qdevice setup <ip>)"
fi

# ------------------------------------------------------------ GUI plugin ---
if [ "$GUI" -eq 1 ] && [ "$REUSE" -eq 0 ] && [ "$ROTATE" -eq 0 ]; then
    info "installing the ProxLB tab into the Proxmox web interface"
    GUI_ARGS=()
    [ "$GUI_ALL_NODES" -eq 1 ] && GUI_ARGS+=(--all-nodes)
    if [ -x /usr/sbin/proxlb-gui ]; then
        /usr/sbin/proxlb-gui --packaged "${GUI_ARGS[@]}" || warn "GUI plugin installation failed"
    elif [ -f "$SCRIPT_DIR/gui/install-gui.sh" ]; then
        bash "$SCRIPT_DIR/gui/install-gui.sh" "${GUI_ARGS[@]}" || warn "GUI plugin installation failed"
    else
        warn "GUI plugin not found - install the proxlb-pve package or run gui/install-gui.sh"
    fi
fi

# ---------------------------------------------------------------- bundle ---
if [ "$NEW_TOKEN" -eq 1 ]; then
    TMP=$(mktemp -d)
    umask 077
    cat > "$TMP/connect.env" <<EOF
# ProxLB external connection - created by proxlb-external.sh on $(hostname) $(date -u +%FT%TZ)
# Contains an API token secret: keep it private, delete it after use.
PROXLB_VMID=$VMID
PROXLB_GUEST_TYPE=qemu
PROXLB_API_HOSTS=$API_HOSTS
PROXLB_API_USER=$PVE_USER
PROXLB_TOKEN_ID=$TOKEN_NAME
PROXLB_TOKEN_SECRET=$TOKEN_SECRET
PROXLB_CLUSTER=$CLUSTER
EOF
    cp /etc/pve/pve-root-ca.pem "$TMP/pve-root-ca.pem"
    tar -C "$TMP" -cf "$BUNDLE" connect.env pve-root-ca.pem
    chmod 0600 "$BUNDLE"
    rm -rf "$TMP"
    umask 022
fi

# -------------------------------------------- connect the target (optional) ---
# The same one-line script runs in the container or on the SSH host: unpack the
# ProxLB source, run install-agent.sh with the bundle, delete both again.
REMOTE_SCRIPT='export LC_ALL=C.UTF-8 LANG=C.UTF-8 LANGUAGE= APT_LISTCHANGES_FRONTEND=none DEBIAN_FRONTEND=noninteractive; set -e; trap "rm -rf /root/proxlb-src /root/proxlb-src.tgz /root/proxlb-bundle.tar" EXIT; rm -rf /root/proxlb-src; mkdir -p /root/proxlb-src; tar -xzf /root/proxlb-src.tgz -C /root/proxlb-src; bash /root/proxlb-src/contrib/proxmox/agent/install-agent.sh --bundle /root/proxlb-bundle.tar'

make_source_tgz() { # $1 = output file
    local root
    root="$(cd "$SCRIPT_DIR/../.." && pwd)"
    [ -f "$root/contrib/proxmox/agent/install-agent.sh" ] && [ -f "$root/setup.py" ] \
        || die "ProxLB source not found next to this script ($root)"
    tar -C "$root" --exclude=.git --exclude=.github --exclude=docs --exclude=tests --exclude=helm \
        --exclude=.changelogs --exclude=__pycache__ --exclude='*.pyc' -czf "$1" .
}

ct_push() { # $1 local file, $2 path in the container, $3 mode
    if [ -z "$CT_IP" ]; then
        pct push "$TARGET_CT" "$1" "$2" --perms "$3"
    else
        local tmp="/tmp/proxlb-push.$$"
        # ssh + cat instead of scp (scp via SFTP fails on some PVE 9 nodes)
        ssh -o BatchMode=yes "root@$CT_IP" "cat > $tmp" < "$1"
        ssh -o BatchMode=yes "root@$CT_IP" "pct push $TARGET_CT $tmp $2 --perms $3; rc=\$?; rm -f $tmp; exit \$rc"
    fi
}

ct_exec() { # $1 = one-line script
    if [ -z "$CT_IP" ]; then
        pct exec "$TARGET_CT" -- bash -c "$1" </dev/null
    else
        ssh -o BatchMode=yes "root@$CT_IP" "pct exec $TARGET_CT -- bash -c $(printf '%q' "$1")" </dev/null
    fi
}

add_ignore_tag() { # ProxLB must not migrate the container it runs in
    local tags
    tags=$(pvesh get "/nodes/$CT_NODE/lxc/$TARGET_CT/config" --output-format json | json_get 'print $d->{tags} // ""')
    if [[ ";${tags//[, ]/;};" != *";plb_ignore_vm;"* ]]; then
        pvesh set "/nodes/$CT_NODE/lxc/$TARGET_CT/config" --tags "${tags:+$tags;}plb_ignore_vm"
        info "tagged LXC $TARGET_CT with plb_ignore_vm (ProxLB never migrates itself)"
    fi
}

CONNECTED=""
if [ -n "$TARGET_CT" ] || [ -n "$TARGET_SSH" ]; then
    [ -s "$BUNDLE" ] || die "bundle $BUNDLE missing"
    SRC_TGZ=$(mktemp /tmp/proxlb-src.XXXXXX.tgz)
    make_source_tgz "$SRC_TGZ"
    rc=0
    if [ -n "$TARGET_CT" ]; then
        info "installing proxlb-agent in LXC $TARGET_CT (this can take a few minutes the first time)"
        ct_push "$SRC_TGZ" /root/proxlb-src.tgz 0600
        ct_push "$BUNDLE" /root/proxlb-bundle.tar 0600
        ct_exec "$REMOTE_SCRIPT" || rc=$?
        add_ignore_tag || warn "could not tag LXC $TARGET_CT with plb_ignore_vm - add it yourself"
        CONNECTED="LXC $TARGET_CT"
    else
        info "installing proxlb-agent on $TARGET_SSH (this can take a few minutes the first time)"
        ssh "$TARGET_SSH" "umask 077; cat > /root/proxlb-src.tgz" < "$SRC_TGZ"
        ssh "$TARGET_SSH" "umask 077; cat > /root/proxlb-bundle.tar" < "$BUNDLE"
        ssh "$TARGET_SSH" "chmod 600 /root/proxlb-bundle.tar; bash -c $(printf '%q' "$REMOTE_SCRIPT")" || rc=$?
        CONNECTED="$TARGET_SSH"
    fi
    rm -f "$SRC_TGZ"
    [ "$rc" -eq 0 ] || die "install-agent.sh failed on $CONNECTED (exit $rc) - the bundle stays in $BUNDLE"
    rm -f "$BUNDLE"
    info "removed $BUNDLE - it is not needed any more"
fi

# a new LXC host also gets Proxmox HA (shared storage or ZFS replication)
if [ "$CREATE_CT" -eq 1 ] && [ "$HA" != "no" ] && [ "${NODES:-1}" -ge 2 ]; then
    LXC_SCRIPT=""
    if [ -x /usr/sbin/proxlb-lxc ]; then LXC_SCRIPT=/usr/sbin/proxlb-lxc
    elif [ -f "$SCRIPT_DIR/proxlb-lxc.sh" ]; then LXC_SCRIPT="$SCRIPT_DIR/proxlb-lxc.sh"; fi
    if [ -n "$LXC_SCRIPT" ]; then
        info "making LXC $TARGET_CT highly available"
        bash "$LXC_SCRIPT" --enable-ha "$TARGET_CT" || warn "HA for LXC $TARGET_CT failed - later: proxlb-lxc --enable-ha $TARGET_CT"
    fi
fi

if [ -n "$CONNECTED" ]; then
    if [ -n "$TARGET_CT" ]; then
        LOGCMD="pct exec $TARGET_CT -- journalctl -u proxlb-agent -f"
        AGAIN="--ct $TARGET_CT"
    else
        LOGCMD="ssh $TARGET_SSH journalctl -u proxlb-agent -f"
        AGAIN="--ssh $TARGET_SSH"
    fi
    cat <<EOF

${C_G}Done: ProxLB on $CONNECTED is connected to anchor VM $VMID.${C_0}
$( [ "$CREATE_CT" -eq 1 ] && printf '\n  New ProxLB host   LXC %s (%s) on %s, storage %s, ip %s\n' "$TARGET_CT" "$CT_HOSTNAME" "$CT_NODE" "$STORAGE" "$IP" )

  Reload the web interface (Ctrl+F5): Datacenter -> ProxLB shows
  "ProxLB runs on <hostname of $CONNECTED>" within ~30 seconds.

  Agent log:   $LOGCMD
  New token:   $CMD --rotate-token --vmid $VMID $AGAIN
EOF
else
    cat <<EOF

${C_G}Anchor VM $VMID is ready.${C_0} Connect the machine where ProxLB runs:

  automatically, if ProxLB runs in an LXC of this cluster or is reachable over SSH:
       $CMD --vmid $VMID --ct <CTID>
       $CMD --vmid $VMID --ssh root@<proxlb-machine>

  or by hand:
    1. copy the bundle (it holds the API token - keep it private):
         scp $BUNDLE root@<proxlb-machine>:/root/
    2. on the ProxLB machine, as root:
         git clone https://github.com/credativ/ProxLB.git
         bash ProxLB/contrib/proxmox/agent/install-agent.sh --bundle /root/proxlb-external-$VMID.tar
         rm /root/proxlb-external-$VMID.tar

  Then reload the web interface (Ctrl+F5): Datacenter -> ProxLB shows
  "ProxLB runs on <proxlb-machine>" within ~30 seconds.

  The ProxLB machine must reach the nodes on TCP 8006: $API_HOSTS
  New token later:  $CMD --rotate-token --vmid $VMID
EOF
fi
