#!/usr/bin/env bash
# Integration test for the proxlb-pve .deb on a plain Debian system (as root,
# e.g. in a debian:bookworm / debian:trixie container). A stand-in pve-manager
# package provides index.html.tpl, so install, the dpkg file trigger on
# pve-manager upgrades, migration from a script install, and removal can be
# checked without a real Proxmox VE.
#
#   contrib/proxmox/packaging/test-deb.sh dist/proxlb-pve_*_all.deb
set -euo pipefail

DEB="$(readlink -f "$1")"
TPL=/usr/share/pve-manager/index.html.tpl
JS=/usr/share/pve-manager/js/proxlb-gui.js
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
export DEBIAN_FRONTEND=noninteractive

pass() { echo "PASS: $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

fake_pve_manager() { # build + install a stand-in pve-manager of version $1
    local dir="$WORK/pve-manager-$1"
    mkdir -p "$dir/DEBIAN" "$dir/usr/share/pve-manager/js"
    cat > "$dir/DEBIAN/control" <<EOF
Package: pve-manager
Version: $1
Architecture: all
Maintainer: test <test@example.org>
Description: stand-in for the Proxmox VE web interface (test only)
EOF
    cat > "$dir/usr/share/pve-manager/index.html.tpl" <<'EOF'
<!DOCTYPE html>
<html>
  <head>
    <script type="text/javascript" src="/pve2/js/proxmoxlib.js?ver=[% wtversion %]"></script>
    <script type="text/javascript" src="/pve2/js/pvemanagerlib.js?ver=[% version %]"></script>
    <script type="text/javascript" src="/pve2/ext6/locale/locale-[% lang %].js?ver=7.0.0"></script>
  </head>
  <body></body>
</html>
EOF
    echo "// stand-in" > "$dir/usr/share/pve-manager/js/pvemanagerlib.js"
    dpkg-deb --root-owner-group --build "$dir" "$WORK/pve-manager_$1_all.deb" >/dev/null
    dpkg -i "$WORK/pve-manager_$1_all.deb" >/dev/null
}

lines() { grep -c 'proxlb-gui' "$TPL" || true; }

echo "== prerequisites"
apt-get update -qq
apt-get install -y -qq perl libjson-perl python3 python3-venv >/dev/null
fake_pve_manager 8.0.0-test

echo "== package metadata"
dpkg-deb -f "$DEB" Package | grep -qx proxlb-pve || fail "package name"
dpkg-deb -f "$DEB" Depends | grep -q pve-manager || fail "depends on pve-manager"
pass "metadata ($(dpkg-deb -f "$DEB" Version))"

echo "== a previous script install is migrated to the package"
mkdir -p /usr/local/share/proxlb-gui /usr/local/sbin
echo "// old" > /usr/local/share/proxlb-gui/proxlb-gui.js
echo "#!/bin/sh" > /usr/local/sbin/proxlb-gui && chmod +x /usr/local/sbin/proxlb-gui
echo "// old hook" > /etc/apt/apt.conf.d/99proxlb-gui

echo "== install"
apt-get install -y -qq "$DEB" >/dev/null
[ "$(lines)" = "1" ] || fail "index.html.tpl not patched exactly once ($(lines))"
grep -A1 'pvemanagerlib.js' "$TPL" | grep -q 'src="/pve2/js/proxlb-gui.js?ver=' || fail "plugin line not right after pvemanagerlib.js"
cmp -s "$JS" /usr/share/proxlb-pve/gui/proxlb-gui.js || fail "plugin JS not installed"
[ ! -e /etc/apt/apt.conf.d/99proxlb-gui ] && [ ! -e /usr/local/sbin/proxlb-gui ] && [ ! -e /usr/local/share/proxlb-gui ] \
    || fail "old script install not removed"
pass "install patches the web interface and replaces the script install"

echo "== commands"
out=$(proxlb-lxc --help)
grep -q 'Usage: proxlb-lxc' <<<"$out" || fail "proxlb-lxc --help"
proxlb-gui --help >/dev/null || fail "proxlb-gui --help"
out=$(proxlb-external --help)
grep -q 'Usage: proxlb-external' <<<"$out" || fail "proxlb-external --help"
[ -f /usr/share/proxlb-pve/src/contrib/proxmox/agent/tls-relax/sitecustomize.py ] || fail "tls-relax not packaged"
[ -x /usr/share/proxlb-pve/src/contrib/proxmox/agent/install-agent.sh ] || fail "install-agent.sh not packaged"
proxlb-gui --reapply >/dev/null
[ "$(lines)" = "1" ] || fail "reapply is not idempotent"
pass "proxlb-lxc, proxlb-external and proxlb-gui work"

echo "== the script installer hands over to the package"
out=$(bash /usr/share/proxlb-pve/src/contrib/proxmox/gui/install-gui.sh)
grep -q 'package is installed' <<<"$out" || fail "no hand-over: $out"
[ ! -e /etc/apt/apt.conf.d/99proxlb-gui ] || fail "script installer created an apt hook"
[ "$(lines)" = "1" ] || fail "duplicate plugin line after script installer"
pass "install-gui.sh uses the package"

echo "== pve-manager upgrade -> dpkg trigger re-applies the plugin"
fake_pve_manager 8.0.1-test
[ "$(lines)" = "1" ] || fail "trigger did not re-apply the plugin after pve-manager upgrade"
pass "dpkg file trigger works"

echo "== bundled ProxLB source installs (as the container installer does)"
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" install -q --disable-pip-version-check /usr/share/proxlb-pve/src
ver=$("$WORK/venv/bin/python" -m proxlb --version)
grep -qi proxlb <<<"$ver" || fail "proxlb from bundled source: $ver"
"$WORK/venv/bin/python" /usr/share/proxlb-pve/src/contrib/proxmox/agent/proxlb-agent.py --help >/dev/null || fail "agent"
pass "bundled source: $(head -1 <<<"$ver")"

echo "== upgrade of proxlb-pve itself keeps the plugin"
dpkg -i "$DEB" >/dev/null
[ "$(lines)" = "1" ] || fail "plugin lost on package reinstall"
pass "reinstall keeps the plugin"

echo "== remove"
apt-get remove -y -qq proxlb-pve >/dev/null
[ "$(lines)" = "0" ] || fail "plugin line left after remove"
[ ! -e "$JS" ] || fail "plugin JS left after remove"
grep -q 'pvemanagerlib.js' "$TPL" || fail "index.html.tpl damaged"
dpkg -s proxlb-pve >/dev/null 2>&1 && fail "package still installed"
pass "remove cleans up"

echo "ALL TESTS PASSED"
