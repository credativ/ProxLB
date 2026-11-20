#!/usr/bin/env bash
# End-to-end test for "ProxLB on another machine" (as root, throwaway box or
# container): install-agent.sh with a real bundle, then proxlb-agent talking
# HTTPS + API token to a mock Proxmox API whose certificate is signed by the
# bundled cluster CA. Checks: config written, CA trusted, GUI command tag
# picked up from the anchor VM, status published into the anchor VM Notes.
#
#   contrib/proxmox/packaging/test-external.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
WORK=$(mktemp -d)
MOCK_PID=""
AGENT_PID=""
cleanup() {
    [ -n "$AGENT_PID" ] && kill "$AGENT_PID" 2>/dev/null || true
    [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT
[ -n "${KEEP_WORK:-}" ] && trap - EXIT
pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; [ -f "$WORK/agent.log" ] && tail -20 "$WORK/agent.log" >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

echo "== prerequisites"
if command -v apt-get >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv openssl ca-certificates git curl >/dev/null
fi
id plb >/dev/null 2>&1 || useradd --system --user-group --home-dir /var/lib/proxlb --shell /usr/sbin/nologin plb

# systemd is not running in a test container: a stub records the calls
mkdir -p "$WORK/bin"
cat > "$WORK/bin/systemctl" <<EOF
#!/bin/sh
echo "systemctl \$*" >> "$WORK/systemctl.log"
case "\$1" in is-active) [ "\$2" = "--quiet" ] && exit 0; exit 3 ;; is-enabled|cat) exit 1 ;; esac
exit 0
EOF
chmod +x "$WORK/bin/systemctl"
export PATH="$WORK/bin:$PATH"

echo "== cluster CA + mock Proxmox API certificate"
cd "$WORK"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=Proxmox Virtual Environment/OU=test/O=PVE Cluster Manager CA" \
    -keyout ca.key -out pve-root-ca.pem >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -subj "/CN=pve1" -keyout node.key -out node.csr >/dev/null 2>&1
printf 'subjectAltName=IP:127.0.0.1,DNS:localhost\n' > san.ext
openssl x509 -req -in node.csr -CA pve-root-ca.pem -CAkey ca.key -CAcreateserial -days 2 \
    -extfile san.ext -out node.pem >/dev/null 2>&1

cat > mock_pve.py <<'PY'
import json, ssl, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
TOKEN = "PVEAPIToken=proxlb@pve!external9000=s3cr3t-uuid"
state = {"tags": "proxlb;plb_ignore_vm;proxlb-cmd-dryrun", "description": "", "puts": []}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, data):
        body = json.dumps({"data": data}).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def authed(self):
        import os
        accepted = open("token").read().strip() if os.path.exists("token") else TOKEN
        if self.headers.get("Authorization") != accepted:
            self.reply(401, None); return False
        return True
    def do_GET(self):
        if not self.authed(): return
        p = urllib.parse.urlparse(self.path).path
        if p == "/api2/json/version":
            self.reply(200, {"version": "8.2.0"})
        elif p == "/api2/json/cluster/resources":
            self.reply(200, [
                {"type": "node", "node": "pve1", "status": "online"},
                {"type": "node", "node": "pve2", "status": "online"},
                {"type": "qemu", "vmid": 9000, "node": "pve2", "status": "stopped",
                 "name": "proxlb-anchor", "tags": state["tags"], "hastate": "stopped"},
            ])
        elif p == "/api2/json/access/permissions":
            path = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("path", ["/"])[0]
            if path == "/":
                self.reply(200, {"/": {"Sys.Audit": 1, "VM.Audit": 1, "VM.Migrate": 1, "Datastore.Audit": 1}})
            else:
                import os
                self.reply(200, {path: {} if os.path.exists("noperm") else {"VM.Config.Options": 1}})
        elif p == "/api2/json/_settags":
            state["tags"] = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["tags"][0]
            self.reply(200, None)
        elif p == "/api2/json/_state":
            self.reply(200, state)
        else:
            self.reply(404, None)
    def do_PUT(self):
        if not self.authed(): return
        p = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        if p != "/api2/json/nodes/pve2/qemu/9000/config":
            self.reply(404, None); return
        state["puts"].append(sorted(form))
        if "tags" in form: state["tags"] = form["tags"]
        if "description" in form: state["description"] = form["description"]
        self.reply(200, None)
srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain("node.pem", "node.key")
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
PY
PORT=18006
python3 mock_pve.py "$PORT" & MOCK_PID=$!
sleep 1

echo "== bundle (as proxlb-external.sh writes it)"
cat > connect.env <<EOF
PROXLB_VMID=9000
PROXLB_GUEST_TYPE=qemu
PROXLB_API_HOSTS=127.0.0.1:$PORT
PROXLB_API_USER=proxlb@pve
PROXLB_TOKEN_ID=external9000
PROXLB_TOKEN_SECRET=s3cr3t-uuid
PROXLB_CLUSTER=testcluster
EOF
tar -cf bundle.tar connect.env pve-root-ca.pem

echo "== existing ProxLB config with password auth (gets replaced, backup kept)"
mkdir -p /etc/proxlb
printf 'proxmox_api:\n  hosts: [old-host]\n  user: root@pam\n  pass: oldpw\nbalancing:\n  method: cpu\n  balanciness: 7\nservice:\n  schedule:\n    interval: 6\n    format: hours\n' > /etc/proxlb/proxlb.yaml

echo "== install-agent.sh"
bash "$ROOT/contrib/proxmox/agent/install-agent.sh" --bundle "$WORK/bundle.tar" > install.log 2>&1 \
    || { cat install.log; fail "install-agent.sh"; }
PY=$(sed -n 's/^python: //p' /etc/proxlb/agent.yaml)
grep -q '^vmid: 9000$' /etc/proxlb/agent.yaml && grep -q '^guest_type: qemu$' /etc/proxlb/agent.yaml || fail "agent.yaml"
"$PY" - <<'PYEOF' || fail "proxlb.yaml not merged correctly"
import yaml
c = yaml.safe_load(open("/etc/proxlb/proxlb.yaml"))
a = c["proxmox_api"]
assert a["hosts"] == ["127.0.0.1:18006"], a
assert a["user"] == "proxlb@pve" and a["token_id"] == "external9000" and a["ssl_verification"] is True
assert "pass" not in a
assert c["balancing"]["method"] == "cpu" and c["balancing"]["balanciness"] == 7   # user settings kept
PYEOF
ls /etc/proxlb/proxlb.yaml.bak-* >/dev/null || fail "no backup of the old config"
grep -q "ExecStart=$PY /opt/proxlb/agent/proxlb-agent.py -c /etc/proxlb/agent.yaml" /etc/systemd/system/proxlb-agent.service || fail "unit ExecStart"
CA_BUNDLE=$(sed -n 's/^Environment="REQUESTS_CA_BUNDLE=\(.*\)"/\1/p' /etc/systemd/system/proxlb-agent.service)
grep -q "$(sed -n 2p pve-root-ca.pem)" "$CA_BUNDLE" || fail "cluster CA not in $CA_BUNDLE"
grep -q "enable proxlb-agent.service" systemctl.log && grep -q "restart proxlb-agent.service" systemctl.log \
    || fail "agent service not enabled + restarted"
pass "install-agent.sh: agent.yaml, proxlb.yaml (backup, settings kept), CA, unit"

echo "== TLS: old Proxmox CA without keyUsage (PVE bug #6701)"
probe() { # $1 = CA file, extra env before; prints ok|error text
    "$PY" - "$1" "$PORT" <<'PYEOF' 2>&1 | tail -1
import sys, requests
try:
    requests.get(f"https://127.0.0.1:{sys.argv[2]}/api2/json/version", verify=sys.argv[1], timeout=5)
    print("ok")
except Exception as err:
    print("error:", err)
PYEOF
}
RELAX=/opt/proxlb/agent/tls-relax
strict=$(PROXLB_TLS_STRICT=1 PYTHONPATH=$RELAX probe "$CA_BUNDLE")
relaxed=$(PYTHONPATH=$RELAX probe "$CA_BUNDLE")
noca=$(PYTHONPATH=$RELAX probe /etc/ssl/certs/does-not-exist.pem)
other=$(PYTHONPATH=$RELAX probe "$("$PY" -c 'import certifi; print(certifi.where())')")
if "$PY" -c 'import ssl, sys; sys.exit(0 if getattr(ssl, "VERIFY_X509_STRICT", 0) and sys.version_info >= (3, 13) else 1)'; then
    grep -q "key usage" <<<"$strict" || fail "strict mode should reject the CA without keyUsage: $strict"
fi
[ "$relaxed" = "ok" ] || fail "relaxed mode should accept the cluster CA: $relaxed"
[ "$noca" != "ok" ] && [ "$other" != "ok" ] || fail "verification is off?! ($noca / $other)"
pass "strict rejects the old CA, relaxed accepts it, a wrong CA is still rejected"

echo "== agent -> mock Proxmox API over verified TLS"
# ProxLB itself would need a full cluster: stand in for it with canned output
cat > fake_python <<'EOF'
#!/bin/sh
echo "2026 - ProxLB - INFO - fake ProxLB run"
cat <<'JSON'
{
    "nodes": {"pve1": {"memory": {"used_percent": 70}, "cpu": {"used_percent": 20}, "disk": {"used_percent": 10}, "maintenance": false},
              "pve2": {"memory": {"used_percent": 30}, "cpu": {"used_percent": 10}, "disk": {"used_percent": 10}, "maintenance": false}},
    "guests": {"web": {"id": 101, "name": "web", "type": "vm", "node_current": "pve1", "node_target": "pve2", "ignore": false}}
}
JSON
EOF
chmod +x fake_python
sed "s#^python: .*#python: $WORK/fake_python#" /etc/proxlb/agent.yaml > agent-test.yaml
REQUESTS_CA_BUNDLE="$CA_BUNDLE" "$PY" /opt/proxlb/agent/proxlb-agent.py -c agent-test.yaml > agent.log 2>&1 & AGENT_PID=$!

for _ in $(seq 1 40); do
    st=$(curl -s --cacert pve-root-ca.pem -H "Authorization: PVEAPIToken=proxlb@pve!external9000=s3cr3t-uuid" \
        "https://127.0.0.1:$PORT/api2/json/_state")
    grep -q 'proxlb-status' <<<"$st" && grep -q '\\"trigger\\":\\"gui\\"' <<<"$st" && break
    sleep 1
done
"$PY" - "$st" <<'PYEOF' || fail "status not published as expected"
import json, sys
s = json.loads(sys.argv[1])["data"]
assert "proxlb-cmd-dryrun" not in s["tags"], s["tags"]            # command picked up + removed
d = s["description"]
assert "ProxLB runs on" in d, d[:200]
raw = d.split("<!--proxlb-status:")[1].rsplit("-->", 1)[0]
st = json.loads(raw)
assert st["trigger"] == "gui" and st["dry_run"] is True, st
assert st["guest_type"] == "qemu" and st["ctid"] == 9000 and st["host"], st
assert st["moves"] == [[101, "web", "vm", "pve1", "pve2"]], st["moves"]
print("published status:", {k: st[k] for k in ("host", "trigger", "moves_total", "ok")})
PYEOF
pass "agent connected over verified TLS with the token, took the GUI command and published the status"

echo "== token replaced on the cluster -> 401, then new token in proxlb.yaml -> agent recovers without restart"
NEW="PVEAPIToken=proxlb@pve!external9000=new-secret"
echo "$NEW" > "$WORK/token"                                   # the cluster only accepts the new token now
curl -s --cacert pve-root-ca.pem -H "Authorization: $NEW" -o /dev/null \
    "https://127.0.0.1:$PORT/api2/json/_settags?tags=proxlb;plb_ignore_vm;proxlb-cmd-dryrun"
for _ in $(seq 1 30); do grep -q "API login rejected" agent.log && break; sleep 1; done
grep -q "API login rejected by 127.0.0.1:$PORT (401): the token proxlb@pve!external9000" agent.log \
    || fail "no clear 401 message"
"$PY" - <<'PYEOF'
import yaml
c = yaml.safe_load(open("/etc/proxlb/proxlb.yaml"))
c["proxmox_api"]["token_secret"] = "new-secret"
yaml.safe_dump(c, open("/etc/proxlb/proxlb.yaml", "w"), sort_keys=False)
PYEOF
for _ in $(seq 1 40); do
    st=$(curl -s --cacert pve-root-ca.pem -H "Authorization: $NEW" "https://127.0.0.1:$PORT/api2/json/_state")
    grep -q "proxlb-cmd-dryrun" <<<"$st" || break
    sleep 1
done
grep -q "proxlb-cmd-dryrun" <<<"$st" && fail "agent did not recover with the new token"
grep -q "proxmox_api in /etc/proxlb/proxlb.yaml changed - reconnecting" agent.log || fail "no reconnect message"
pass "401 reported once with a hint; new token in proxlb.yaml picked up without restart, GUI command handled"

echo "== proxlb-ctl check (self-check)"
chk=$(REQUESTS_CA_BUNDLE="$CA_BUNDLE" "$PY" /opt/proxlb/agent/proxlb-agent.py --check -c /etc/proxlb/agent.yaml 2>&1) || { echo "$chk"; fail "check should pass"; }
grep -q "all checks passed" <<<"$chk" || fail "check summary: $chk"
grep -q "TLS verified" <<<"$chk" && grep -q "qemu 9000 found on node pve2" <<<"$chk" || fail "check details: $chk"
touch "$WORK/noperm"
chk=$(REQUESTS_CA_BUNDLE="$CA_BUNDLE" "$PY" /opt/proxlb/agent/proxlb-agent.py --check -c /etc/proxlb/agent.yaml 2>&1) && fail "check should fail without VM.Config.Options"
grep -q "VM.Config.Options on /vms/9000 .*MISSING" <<<"$chk" || fail "missing-permission line: $chk"
grep -q "pveum acl modify /vms/9000 --users proxlb@pve --roles ProxLBAgent" <<<"$chk" || fail "hint: $chk"
rm -f "$WORK/noperm"
pass "proxlb-ctl check: all OK with permissions, names the missing VM.Config.Options with the fix"

echo "== uninstall"
bash "$ROOT/contrib/proxmox/agent/install-agent.sh" --uninstall >/dev/null
[ ! -e /etc/systemd/system/proxlb-agent.service ] && [ ! -e /opt/proxlb/agent ] || fail "uninstall"
pass "uninstall"

echo "ALL TESTS PASSED"
