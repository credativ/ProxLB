#!/usr/bin/env bash
# Build the proxlb-pve .deb (LXC installer + Proxmox web-interface plugin).
#
#   contrib/proxmox/packaging/build-deb.sh [--version X.Y.Z] [--out DIR]
#
# Without --version: vX.Y.Z tags give X.Y.Z, otherwise
# <proxlb version>+git<UTC timestamp>.<short sha>.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
OUT="$ROOT/dist"
VERSION=""

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="$2"; shift ;;
        --out) OUT="$2"; shift ;;
        -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

cd "$ROOT"
if [ -z "$VERSION" ]; then
    base=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' proxlb/utils/version.py)
    ref="${GITHUB_REF:-$(git describe --tags --exact-match 2>/dev/null | sed 's#^#refs/tags/#' || true)}"
    if [[ "$ref" == refs/tags/v* ]]; then
        VERSION="${ref#refs/tags/v}"
    else
        sha=$(git rev-parse --short=7 HEAD 2>/dev/null || echo local)
        VERSION="${base}+git$(date -u +%Y%m%d%H%M).${sha}"
    fi
fi
[[ "$VERSION" =~ ^[0-9][A-Za-z0-9.+~-]*$ ]] || { echo "invalid Debian version: $VERSION" >&2; exit 1; }

PKG=proxlb-pve
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
TREE="$WORK/$PKG"
SHARE="$TREE/usr/share/$PKG"
DOC="$TREE/usr/share/doc/$PKG"
echo "Building $PKG $VERSION"

# --- bundled ProxLB source (only what the container installer needs) ---------
mkdir -p "$SHARE/src"
git ls-files -co --exclude-standard -- \
        proxlb setup.py setup.cfg requirements.txt README.md LICENSE CHANGELOG.md \
        service config contrib/proxmox \
    | grep -v -e '^contrib/proxmox/gui/preview/' -e '^contrib/proxmox/gui/tests/' \
              -e '^contrib/proxmox/packaging/' -e '__pycache__' \
    | while read -r f; do [ -f "$f" ] && echo "$f"; done \
    | tar -cf - -T - | tar -xf - -C "$SHARE/src"

# --- GUI plugin + commands ---------------------------------------------------
install -D -m 0644 contrib/proxmox/gui/proxlb-gui.js "$SHARE/gui/proxlb-gui.js"
install -D -m 0755 contrib/proxmox/gui/install-gui.sh "$TREE/usr/sbin/proxlb-gui"
install -D -m 0755 "$HERE/proxlb-lxc.wrapper" "$TREE/usr/sbin/proxlb-lxc"
install -D -m 0755 "$HERE/proxlb-external.wrapper" "$TREE/usr/sbin/proxlb-external"

# --- docs --------------------------------------------------------------------
install -D -m 0644 contrib/proxmox/README.md "$DOC/README.md"
install -D -m 0644 contrib/proxmox/GUI-PLUGIN.md "$DOC/GUI-PLUGIN.md"
cat > "$DOC/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: ProxLB
Source: https://github.com/credativ/ProxLB

Files: *
Copyright: Florian Paul Azim Hoberg, credativ GmbH and contributors
License: GPL-3.0+
 On Debian systems, the full text of the GNU General Public License
 version 3 can be found in /usr/share/common-licenses/GPL-3.
COPY
{
    echo "$PKG ($VERSION) stable; urgency=medium"
    echo
    echo "  * Automated build of $(git rev-parse --short=7 HEAD 2>/dev/null || echo 'a local tree')."
    echo
    echo " -- credativ GmbH <support@credativ.de>  $(date -u -R)"
} | gzip -9n > "$DOC/changelog.gz"

# --- permissions -------------------------------------------------------------
find "$TREE" -type d -exec chmod 0755 {} +
find "$TREE" -type f -exec chmod 0644 {} +
chmod 0755 "$TREE/usr/sbin/"* \
    "$SHARE/src/contrib/proxmox/proxlb-lxc.sh" \
    "$SHARE/src/contrib/proxmox/proxlb-external.sh" \
    "$SHARE/src/contrib/proxmox/agent/install-agent.sh" \
    "$SHARE/src/contrib/proxmox/gui/install-gui.sh" \
    "$SHARE/src/contrib/proxmox/agent/proxlb-ctl"

# --- DEBIAN control area -----------------------------------------------------
mkdir -p "$TREE/DEBIAN"
(cd "$TREE" && find . -path ./DEBIAN -prune -o -type f -printf '%P\0' | sort -z | xargs -0 md5sum) > "$TREE/DEBIAN/md5sums"
SIZE=$(du -sk --exclude=DEBIAN "$TREE" | cut -f1)
sed -e "s/@VERSION@/$VERSION/" -e "s/@SIZE@/$SIZE/" "$HERE/debian/control.in" > "$TREE/DEBIAN/control"
install -m 0644 "$HERE/debian/triggers" "$TREE/DEBIAN/triggers"
install -m 0755 "$HERE/debian/postinst" "$HERE/debian/prerm" "$TREE/DEBIAN/"
chmod 0644 "$TREE/DEBIAN/md5sums" "$TREE/DEBIAN/control"

mkdir -p "$OUT"
DEB="$OUT/${PKG}_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$TREE" "$DEB" >/dev/null
(cd "$OUT" && sha256sum "$(basename "$DEB")" > "$(basename "$DEB").sha256")
echo "$DEB"
