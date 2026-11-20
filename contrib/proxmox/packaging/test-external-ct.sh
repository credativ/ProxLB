#!/usr/bin/env bash
# Test proxlb-external.sh --ct (automatic connect of an LXC) without a real
# Proxmox VE: pvesh/pveum/qm/ha-manager/pct are stubs that log their calls,
# and the stub "pct exec" runs inside this machine (as root, throwaway box or
# container). install-agent.sh runs for real.
#
#   contrib/proxmox/packaging/test-external-ct.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
LOG="$WORK/calls.log"
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; [ -f "$LOG" ] && tail -30 "$LOG" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "== prerequisites"
if command -v apt-get >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq perl libjson-perl python3 python3-venv ca-certificates openssl >/dev/null
fi

# ---------------------------------------------------------------- stubs ----
mkdir -p "$WORK/bin" /etc/pve
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=test CA" \
    -keyout "$WORK/ca.key" -out /etc/pve/pve-root-ca.pem >/dev/null 2>&1
CT_STATUS_FILE="$WORK/ct_status"; echo running > "$CT_STATUS_FILE"
CT_TAGS_FILE="$WORK/ct_tags"; echo web > "$CT_TAGS_FILE"

cat > "$WORK/bin/pvesh" <<EOF
#!/bin/bash
echo "pvesh \$*" >> "$LOG"
case "\$1 \$2" in
  "get /cluster/resources") cat <<J
[{"type":"qemu","vmid":138,"node":"pve1","status":"stopped","tags":"proxlb;plb_ignore_vm","name":"proxlb-anchor"},
 {"type":"lxc","vmid":150,"node":"pve1","status":"\$(cat $CT_STATUS_FILE)","tags":"\$(cat $CT_TAGS_FILE)","name":"proxlb"},
 {"type":"qemu","vmid":200,"node":"pve1","status":"running","tags":"","name":"db"}\$(cat $WORK/extra_resources 2>/dev/null)]
J
  ;;
  "get /cluster/status") echo '[{"type":"cluster","name":"lab"},{"type":"node","name":"pve1","local":1,"ip":"127.0.0.1","online":1}]' ;;
  "get /cluster/nextid") echo 301 ;;
  "get /nodes/pve1/lxc/301/config") echo '{"tags":"plb_ignore_vm;proxlb-host"}' ;;
  "get /nodes/pve1/lxc/150/config") echo "{\"tags\":\"\$(cat $CT_TAGS_FILE)\"}" ;;
  "set /nodes/pve1/lxc/150/config") [ "\$3" = --tags ] && echo "\$4" > "$CT_TAGS_FILE" ;;
  *) echo "unexpected pvesh \$*" >&2; exit 1 ;;
esac
EOF
cat > "$WORK/bin/pveum" <<EOF
#!/bin/bash
echo "pveum \$*" >> "$LOG"
case "\$1 \$2" in
  "role list") echo '[{"roleid":"ProxLB"},{"roleid":"ProxLBAgent"}]' ;;
  "user list") echo '[{"userid":"proxlb@pve"}]' ;;
  "user token") [ "\$3" = add ] && echo '{"value":"tok-secret-138"}' ;;
esac
exit 0
EOF
for c in qm ha-manager; do
    printf '#!/bin/bash\necho "%s $*" >> "%s"\n' "$c" "$LOG" > "$WORK/bin/$c"
done
cat > "$WORK/bin/pveam" <<'EOF'
#!/bin/bash
case "$1" in
  # both architectures under the same name; the arm64 one sorts last and is newer
  available) printf 'system          debian-12-standard_12.12-1_amd64.tar.zst\nsystem          debian-13-standard_13.1-2_amd64.tar.zst\nsystem          debian-13-standard_13.6-1_arm64.tar.zst\nsystem          debian-13-standard_13.6-1_amd64.tar.zst\n' ;;
  list) echo "local:vztmpl/debian-13-standard_13.6-1_amd64.tar.zst 123MB" ;;
esac
exit 0
EOF
cat > "$WORK/bin/pvesm" <<'EOF'
#!/bin/bash
printf 'Name Type Status Total Used Available %%\nlocal dir active 1 1 1 1%%\nlocal-lvm lvmthin active 1 1 1 1%%\n'
EOF
cat > "$WORK/bin/pct" <<EOF
#!/bin/bash
echo "pct \$*" >> "$LOG"
case "\$1" in
  create) echo ',{"type":"lxc","vmid":'"\$2"',"node":"pve1","status":"running","tags":"plb_ignore_vm;proxlb-host","name":"proxlb"}' >> "$WORK/extra_resources" ;;
  push) install -m "\${6:-0644}" "\$3" "\$4" ;;          # pct push CT SRC DST --perms MODE
  exec) shift 3; "\$@" ;;                                # pct exec CT -- CMD...
esac
EOF
cat > "$WORK/bin/systemctl" <<EOF
#!/bin/sh
echo "systemctl \$*" >> "$LOG"
case "\$1" in is-active) [ "\$2" = "--quiet" ] && exit 0; exit 3 ;; is-enabled|cat) exit 1 ;; esac
exit 0
EOF
chmod +x "$WORK"/bin/*
export PATH="$WORK/bin:$PATH"
EXT="$ROOT/contrib/proxmox/proxlb-external.sh"

echo "== error cases"
out=$(bash "$EXT" --vmid 200 --ct 150 --no-gui 2>&1) && fail "reused a non-anchor VM"
grep -q "not a ProxLB anchor" <<<"$out" || fail "wrong error: $out"
out=$(bash "$EXT" --vmid 138 --ct 200 --no-gui 2>&1) && fail "accepted a VM as --ct"
grep -q "not an LXC" <<<"$out" || fail "wrong error: $out"
echo stopped > "$CT_STATUS_FILE"
out=$(bash "$EXT" --vmid 138 --ct 150 --no-gui 2>&1) && fail "accepted a stopped CT"
grep -q "start it first" <<<"$out" || fail "wrong error: $out"
echo running > "$CT_STATUS_FILE"
grep -q "^qm create" "$LOG" 2>/dev/null && fail "something was created during the error cases"
pass "refuses a non-anchor VMID, a VM as --ct and a stopped container - before changing anything"

echo "== proxlb-external --vmid 138 --ct 150 (existing anchor, no bundle left)"
: > "$LOG"
rm -f /root/proxlb-external-138.tar
bash "$EXT" --vmid 138 --ct 150 > "$WORK/run.log" 2>&1 || { cat "$WORK/run.log"; fail "proxlb-external --ct"; }
grep -q "using the existing anchor VM 138" "$WORK/run.log" || fail "anchor not reused"
grep -q "^qm create" "$LOG" && fail "created a new anchor VM"
grep -q "^pveum user token add proxlb@pve external138" "$LOG" || fail "no new token"
grep -q "^pct push 150 .* /root/proxlb-bundle.tar --perms 0600" "$LOG" || fail "bundle not pushed with 0600"
grep -q "^pvesh set /nodes/pve1/lxc/150/config --tags web;plb_ignore_vm" "$LOG" || fail "plb_ignore_vm not added"
[ ! -e /root/proxlb-external-138.tar ] || fail "bundle left on the host"
[ ! -e /root/proxlb-bundle.tar ] && [ ! -e /root/proxlb-src.tgz ] && [ ! -e /root/proxlb-src ] || fail "files left in the container"
grep -q "Done: ProxLB on LXC 150 is connected to anchor VM 138" "$WORK/run.log" || fail "summary"
pass "anchor reused, token created, bundle + source pushed, install-agent.sh ran, bundle removed everywhere"

echo "== result in the container"
grep -q '^vmid: 138$' /etc/proxlb/agent.yaml && grep -q '^guest_type: qemu$' /etc/proxlb/agent.yaml || fail "agent.yaml"
PY=$(sed -n 's/^python: //p' /etc/proxlb/agent.yaml)
"$PY" - <<'PYEOF' || fail "proxlb.yaml"
import yaml
a = yaml.safe_load(open("/etc/proxlb/proxlb.yaml"))["proxmox_api"]
assert a["user"] == "proxlb@pve" and a["token_id"] == "external138" and a["token_secret"] == "tok-secret-138", a
assert a["hosts"] == ["127.0.0.1"] and a["ssl_verification"] is True, a
PYEOF
[ -f /opt/proxlb/agent/proxlb-agent.py ] && [ -f /opt/proxlb/agent/tls-relax/sitecustomize.py ] || fail "agent files"
grep -q "restart proxlb-agent.service" "$LOG" || fail "agent service not restarted"
pass "agent configured for anchor VM 138 with the new token"

echo "== second run reuses everything, no new token"
: > "$LOG"
cp /etc/proxlb/agent.yaml "$WORK/agent.yaml.1"
# keep a bundle around this time: it must be used as is
bash "$EXT" --vmid 138 --rotate-token --no-gui > /dev/null 2>&1 || fail "--rotate-token"
[ -s /root/proxlb-external-138.tar ] || fail "rotate-token wrote no bundle"
: > "$LOG"
bash "$EXT" --vmid 138 --ct 150 > "$WORK/run2.log" 2>&1 || { cat "$WORK/run2.log"; fail "second run"; }
grep -q "using the existing bundle" "$WORK/run2.log" || fail "existing bundle not used"
grep -q "user token add" "$LOG" && fail "created a token although a bundle existed"
grep -q "^pvesh set /nodes/pve1/lxc/150/config" "$LOG" && fail "tag added twice"
pass "existing bundle used, no extra token, tag not duplicated"

echo "== proxlb-external --vmid 138 --create-ct (new LXC host)"
out=$(bash "$EXT" --vmid 138 --create-ct --ct 150 2>&1) && fail "accepted --create-ct with --ct"
grep -q "cannot be combined" <<<"$out" || fail "wrong error: $out"
bash "$EXT" --vmid 138 --rotate-token --no-gui > /dev/null 2>&1 || fail "--rotate-token"
: > "$LOG"
PROXLB_NET_CHECK_HOST=localhost bash "$EXT" --vmid 138 --create-ct --ip 10.0.0.60/24 --gw 10.0.0.1 \
    > "$WORK/run3.log" 2>&1 || { cat "$WORK/run3.log"; fail "--create-ct"; }
create=$(grep "^pct create" "$LOG") || fail "no pct create"
HOST_ARCH=$(dpkg --print-architecture)
grep -q "^pct create 301 local:vztmpl/debian-13-standard_13.6-1_${HOST_ARCH}.tar.zst " <<<"$create" \
    || fail "template/ctid (must match the host architecture $HOST_ARCH): $create"
grep -q -- "--tags plb_ignore_vm;proxlb-host " <<<"$create" || fail "tags: $create"
grep -q -- "--tags proxlb;" <<<"$create" && fail "new host must not carry the anchor tag 'proxlb'"
grep -q -- "--rootfs local-lvm:6 " <<<"$create" || fail "storage: $create"
grep -q -- "--net0 name=eth0,bridge=vmbr0,ip=10.0.0.60/24,gw=10.0.0.1 " <<<"$create" || fail "network: $create"
grep -q -- "--unprivileged 1" <<<"$create" || fail "unprivileged"
grep -q "^pct start 301" "$LOG" || fail "not started"
grep -q "^pct push 301 .* /root/proxlb-bundle.tar --perms 0600" "$LOG" || fail "bundle not pushed into the new CT"
grep -q "^pvesh set /nodes/pve1/lxc/301/config" "$LOG" && fail "tags changed although plb_ignore_vm is set"
grep -q "^qm create" "$LOG" && fail "anchor VM created again"
grep -q "New ProxLB host   LXC 301 (proxlb)" "$WORK/run3.log" || fail "summary"
[ ! -e /root/proxlb-external-138.tar ] || fail "bundle left on the host"
pass "template for the host architecture ($HOST_ARCH, not the newer-sorting other arch); new unprivileged Debian 13 LXC 301 (tags plb_ignore_vm;proxlb-host) created, started and connected to anchor 138"

bash "$ROOT/contrib/proxmox/agent/install-agent.sh" --uninstall >/dev/null
echo "ALL TESTS PASSED"
