#!/usr/bin/env bash
# Regenerate docs/images/proxlb-gui.png from the real plugin code with sample
# data. Needs npm, python3 with playwright and a Chromium/Chrome browser.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d extjs ]; then
    npm pack extjs-gpl@6.2.0 --silent && tar xzf extjs-gpl-6.2.0.tgz && mv package extjs && rm extjs-gpl-6.2.0.tgz
fi
if [ ! -d fa ]; then
    npm pack font-awesome@4.7.0 --silent && tar xzf font-awesome-4.7.0.tgz && mv package fa && rm font-awesome-4.7.0.tgz
fi
python3 - "$PWD/preview.html" "$PWD/../../../../docs/images" <<'PY'
import os
import sys
from playwright.sync_api import sync_playwright
shots = {"proxlb-gui.png": "", "proxlb-gui-degraded.png": "?degraded", "proxlb-gui-external.png": "?external",
         None: "?norun"}  # None: only checked, no screenshot
LAYOUT_CHECK = """(() => {
  const v = Ext.ComponentQuery.query('pveDcProxLBView')[0];
  if (!v) { return 'no ProxLB panel'; }
  // what the user sees: the element painted at the centre of each part must belong to it
  const shown = (cmp) => {
    const b = cmp.el.dom.getBoundingClientRect();
    if (b.height < 20) { return false; }
    const at = document.elementFromPoint(b.left + b.width / 2, b.top + Math.min(b.height / 2, 40));
    return !!at && cmp.el.dom.contains(at);
  };
  if (!v.summary.el.dom.innerText.trim() || !shown(v.summary)) { return 'status box not visible'; }
  const grids = v.query('grid');
  if (grids.length < 2) { return 'grids missing'; }
  for (const g of grids) { if (!shown(g)) { return 'grid "' + g.getTitle() + '" not visible'; } }
  return '';
})()"""
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROME") or None,
                                args=["--allow-file-access-from-files"])
    for name, query in shots.items():
        page = browser.new_page(viewport={"width": 1400, "height": 640}, device_scale_factor=2)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file://" + sys.argv[1] + query)
        page.wait_for_function("window.READY === true", timeout=20000)
        problem = page.evaluate(LAYOUT_CHECK)
        if problem:
            sys.exit(f"{query or 'default'}: tab not rendered correctly: {problem}")
        if name:
            page.screenshot(path=os.path.join(sys.argv[2], name))
        if errors:
            sys.exit("page errors: " + "; ".join(errors))
        print("checked", query or "default", "-> written " + name if name else "")
    browser.close()
PY
