#!/usr/bin/env bash
# Install / remove the ProxLB plugin for the Proxmox VE web interface.
#
#   install-gui.sh [--all-nodes] [--js FILE]   install (or update)
#   install-gui.sh --uninstall [--all-nodes]   remove
#   install-gui.sh --reapply                   re-patch index.html.tpl
#
# The plugin adds "Datacenter -> ProxLB". It must be installed on every node
# whose web interface you use; --all-nodes does that over the cluster's root SSH.
# A pve-manager update overwrites index.html.tpl, so it has to be re-applied:
#   - script install: an apt hook does that,
#   - proxlb-pve .deb (/usr/sbin/proxlb-gui --packaged): a dpkg file trigger.
set -euo pipefail

PVE_JS_DIR=/usr/share/pve-manager/js
PVE_TPL=/usr/share/pve-manager/index.html.tpl
JS_NAME=proxlb-gui.js
MARK='<!-- proxlb-gui -->'

# script install
SCRIPT_SHARE=/usr/local/share/proxlb-gui
SCRIPT_SELF=/usr/local/sbin/proxlb-gui
APT_HOOK=/etc/apt/apt.conf.d/99proxlb-gui
# proxlb-pve package
PKG_SHARE=/usr/share/proxlb-pve/gui
PKG_SELF=/usr/sbin/proxlb-gui

ACTION=install
ALL_NODES=0
PACKAGED=0
JS_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$JS_NAME"
[ "$(readlink -f "$0")" = "$PKG_SELF" ] && PACKAGED=1
ARGS=("$@")

while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) ACTION=uninstall ;;
        --reapply)   ACTION=reapply ;;
        --all-nodes) ALL_NODES=1 ;;
        --packaged)  PACKAGED=1 ;;
        --js)        JS_SRC="$2"; shift ;;
        -h|--help)   sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

msg() { echo "[proxlb-gui] $*"; }

[ "$(id -u)" -eq 0 ] || { echo "run as root on a Proxmox VE node" >&2; exit 1; }
[ -f "$PVE_TPL" ] || { echo "$PVE_TPL not found - is this a Proxmox VE node?" >&2; exit 1; }

# The proxlb-pve package owns the plugin on this node: hand over to it.
if [ "$PACKAGED" -eq 0 ] && [ -x "$PKG_SELF" ] && [ "$ACTION" != "uninstall" ]; then
    msg "proxlb-pve package is installed on this node - using it"
    filtered=()
    skip=0
    for a in "${ARGS[@]}"; do
        if [ "$skip" -eq 1 ]; then skip=0; continue; fi
        [ "$a" = "--js" ] && { skip=1; continue; }
        filtered+=("$a")
    done
    exec "$PKG_SELF" --packaged "${filtered[@]}"
fi

if [ "$PACKAGED" -eq 1 ]; then
    SHARE_DIR=$PKG_SHARE
    SELF=$PKG_SELF
else
    SHARE_DIR=$SCRIPT_SHARE
    SELF=$SCRIPT_SELF
fi

patch_tpl() {
    local ver
    ver=$(sha256sum "$SHARE_DIR/$JS_NAME" | cut -c1-10)
    install -d "$PVE_JS_DIR"
    install -m 0644 "$SHARE_DIR/$JS_NAME" "$PVE_JS_DIR/$JS_NAME"
    # drop an old line, then add ours right after pvemanagerlib.js
    sed -i "\#${MARK}#d" "$PVE_TPL"
    if ! grep -q 'pvemanagerlib.js' "$PVE_TPL"; then
        msg "WARNING: pvemanagerlib.js not referenced in $PVE_TPL - not patched"
        return 1
    fi
    sed -i "\#pvemanagerlib.js#a\\    <script type=\"text/javascript\" src=\"/pve2/js/${JS_NAME}?ver=${ver}\"></script>${MARK}" "$PVE_TPL"
}

restart_proxy() {
    # pveproxy indexes /pve2/js at start, so a restart is needed for a new file
    if [ -d /run/systemd/system ] && systemctl cat pveproxy.service >/dev/null 2>&1; then
        systemctl restart pveproxy.service || msg "WARNING: could not restart pveproxy"
    else
        msg "pveproxy not running here - restart it yourself: systemctl restart pveproxy"
    fi
}

unpatch_tpl() {
    sed -i "\#${MARK}#d" "$PVE_TPL"
    rm -f "$PVE_JS_DIR/$JS_NAME"
}

remove_script_install() {
    # a previous script install is superseded by the package
    if [ -e "$APT_HOOK" ] || [ -e "$SCRIPT_SELF" ] || [ -e "$SCRIPT_SHARE" ]; then
        msg "removing the previous script install (apt hook, /usr/local copies)"
        rm -f "$APT_HOOK" "$SCRIPT_SELF" "$SCRIPT_SHARE/$JS_NAME"
        rmdir "$SCRIPT_SHARE" 2>/dev/null || true
    fi
}

other_nodes() {
    # IPs of all other online cluster nodes (empty on a single node)
    command -v pvesh >/dev/null || return 0
    pvesh get /cluster/status --output-format json 2>/dev/null | perl -MJSON -e '
        my $d = eval { decode_json(join("", <STDIN>)) } || [];
        for (@$d) { print "$_->{ip}\n" if $_->{type} eq "node" && !$_->{local} && $_->{online} && $_->{ip} }'
}

ssh_copy() { # $1 = host, $2 = local file, $3 = remote path
    # ssh + cat instead of scp: scp of OpenSSH 9/10 (PVE 8/9) speaks SFTP and
    # fails with "scp: Connection closed" where the SFTP subsystem is unusable
    ssh -o BatchMode=yes "root@$1" "cat > '$3'" < "$2"
}

remote_has_package() {
    ssh -o BatchMode=yes "root@$1" "test -x $PKG_SELF" 2>/dev/null
}

case "$ACTION" in
    install)
        if [ "$PACKAGED" -eq 1 ]; then
            [ -f "$SHARE_DIR/$JS_NAME" ] || { echo "$SHARE_DIR/$JS_NAME missing - reinstall proxlb-pve" >&2; exit 1; }
            remove_script_install
        else
            [ -f "$JS_SRC" ] || { echo "plugin file $JS_SRC not found" >&2; exit 1; }
            mkdir -p "$SHARE_DIR"
            if [ "$(readlink -f "$JS_SRC")" != "$(readlink -f "$SHARE_DIR/$JS_NAME")" ]; then
                install -m 0644 "$JS_SRC" "$SHARE_DIR/$JS_NAME"
            fi
            if [ "$(readlink -f "$0")" != "$SELF" ]; then
                install -D -m 0755 "$0" "$SELF"
            fi
            cat > "$APT_HOOK" <<EOF
// Re-apply the ProxLB GUI plugin after pve-manager updates
DPkg::Post-Invoke { "if [ -x $SELF ] && ! grep -q 'proxlb-gui' $PVE_TPL 2>/dev/null; then $SELF --reapply || true; fi"; };
EOF
        fi
        patch_tpl
        restart_proxy
        msg "installed on $(hostname) - reload the web interface (Ctrl+F5) and open Datacenter -> ProxLB"
        if [ "$ALL_NODES" -eq 1 ]; then
            for ip in $(other_nodes); do
                if remote_has_package "$ip"; then
                    msg "$ip has the proxlb-pve package - re-applying there"
                    ssh -o BatchMode=yes "root@$ip" "$PKG_SELF --packaged --reapply" || msg "WARNING: failed on $ip"
                    continue
                fi
                msg "installing on $ip"
                ssh_copy "$ip" "$SHARE_DIR/$JS_NAME" "/tmp/$JS_NAME" &&
                ssh_copy "$ip" "$SELF" /tmp/proxlb-gui-install.sh &&
                ssh -o BatchMode=yes "root@$ip" "bash /tmp/proxlb-gui-install.sh --js /tmp/$JS_NAME; rc=\$?; rm -f /tmp/$JS_NAME /tmp/proxlb-gui-install.sh; exit \$rc" ||
                msg "WARNING: failed on $ip"
            done
        fi
        ;;
    reapply)
        [ -f "$SHARE_DIR/$JS_NAME" ] || exit 0
        patch_tpl && restart_proxy && msg "re-applied after a pve-manager update"
        ;;
    uninstall)
        unpatch_tpl
        if [ "$PACKAGED" -eq 0 ]; then
            rm -f "$APT_HOOK" "$SHARE_DIR/$JS_NAME"
            rmdir "$SHARE_DIR" 2>/dev/null || true
        fi
        restart_proxy
        msg "removed from $(hostname)"
        if [ "$ALL_NODES" -eq 1 ]; then
            for ip in $(other_nodes); do
                if remote_has_package "$ip"; then
                    msg "$ip has the proxlb-pve package - remove it there with: apt remove proxlb-pve"
                    continue
                fi
                msg "removing on $ip"
                ssh -o BatchMode=yes "root@$ip" "[ -x $SCRIPT_SELF ] && $SCRIPT_SELF --uninstall" || msg "WARNING: failed on $ip"
            done
        fi
        [ "$PACKAGED" -eq 0 ] && rm -f "$SELF"
        ;;
esac
exit 0
