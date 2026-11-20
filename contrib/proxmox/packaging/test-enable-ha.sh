#!/usr/bin/env bash
# Regression test for proxlb-lxc.sh --enable-ha with stubbed Proxmox commands:
# shared (rbd), local ZFS (replication) and plain local storage.
# Was broken: "read" of a value without a trailing newline made set -e exit
# silently ("HA for LXC 149 failed").
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
LOG="$W/log"
mkdir -p "$W/bin"
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; cat "$LOG" >&2; exit 1; }
if ! perl -MJSON -e1 2>/dev/null; then
    apt-get update -qq && apt-get install -y -qq perl libjson-perl >/dev/null
fi

cat > "$W/bin/pvesh" <<STUB
#!/bin/bash
echo "pvesh \$*" >> "$LOG"
case "\$1 \$2" in
  "get /cluster/status") echo '[{"type":"cluster","name":"c"},{"type":"node","name":"node03","ip":"10.0.0.3","online":1},{"type":"node","name":"node04","ip":"10.0.0.4","online":1,"local":1},{"type":"node","name":"node05","ip":"10.0.0.5","online":1}]' ;;
  "get /cluster/resources") echo '[{"type":"lxc","vmid":149,"node":"node04","status":"running"}]' ;;
  "get /nodes/node04/lxc/149/config") echo '{"rootfs":"data:vm-149-disk-0,size=6G"}' ;;
  "get /storage/data") cat "$W/storage.json" ;;
  "get /cluster/replication") echo '[]' ;;
  "create /cluster/replication"|"create /nodes/node04/replication/"*) ;;
  *) echo "unexpected: pvesh \$*" >&2; exit 1 ;;
esac
STUB
for c in ha-manager pct pveum; do
    printf '#!/bin/bash\necho "%s $*" >> "%s"\n' "$c" "$LOG" > "$W/bin/$c"
done
chmod +x "$W"/bin/*
export PATH="$W/bin:$PATH"

run() { # $1 = storage config JSON
    : > "$LOG"
    echo "$1" > "$W/storage.json"
    bash "$ROOT/contrib/proxmox/proxlb-lxc.sh" --enable-ha 149 > "$W/out" 2>&1
}

rc=0; run '{"storage":"data","type":"rbd"}' || rc=$?
[ "$rc" -eq 0 ] || fail "rbd: exit $rc $(cat "$W/out")"
grep -q "^ha-manager add ct:149 --state started" "$LOG" || fail "rbd: no ha-manager add"
pass "shared rbd storage -> HA"

rc=0; run '{"storage":"data","type":"zfspool"}' || rc=$?
[ "$rc" -eq 0 ] || fail "zfs: exit $rc $(cat "$W/out")"
dups=$(grep -o -- "--id 149-[0-9]*" "$LOG" | sort | uniq -d || true)
[ -z "$dups" ] || fail "zfs: replication job id used twice: $dups"
[ "$(grep -c '^pvesh create /cluster/replication' "$LOG")" -eq 2 ] || fail "zfs: expected 2 replication jobs"
grep -q "^ha-manager add ct:149" "$LOG" || fail "zfs: no ha-manager add"
pass "local ZFS -> replication to the 2 other nodes (unique job ids) + HA"

rc=0; run '{"storage":"data","type":"dir"}' || rc=$?
[ "$rc" -eq 0 ] || fail "dir: exit $rc"
if grep -q "^ha-manager add" "$LOG"; then fail "dir: HA must not be enabled on local storage"; fi
grep -q "move-volume" "$W/out" || fail "dir: no hint"
pass "plain local storage -> refused with a move-volume hint"
echo "ALL TESTS PASSED"
