#!/usr/bin/env python3
"""Offline checks for diehard-pushraker on the FLSUN T1: python tests/test_flsun.py

- the scripts parse (bash -n, where bash is found) and are executable in git
- the service runs the plugin's sync.sh at the path install.sh insists on
- the config template has the update entry (with the service as its
  managed service) and the sections sync.sh appends, in that order
- sync.sh runs pushraker's installer from the plugin's python/, links the
  components from moonraker/components and serves both pages
"""

import configparser
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FL = os.path.join(HERE, "platform", "flsun")
failures = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


bash = shutil.which("bash")
for name in ("install.sh", "uninstall.sh", "sync.sh"):
    path = os.path.join(FL, name)
    if bash:
        r = subprocess.run([bash, "-n", path], capture_output=True, text=True)
        check(r.returncode == 0, f"{name} parses" + ("" if r.returncode == 0
                                                     else "\n" + r.stderr))
modes = subprocess.run(["git", "ls-files", "-s", "platform/flsun/install.sh",
                        "platform/flsun/uninstall.sh", "platform/flsun/sync.sh",
                        "install.sh", "uninstall.sh"], cwd=HERE,
                       capture_output=True, text=True).stdout
check(modes.count("100755") == 5, "the scripts are executable in git")

svc = read("platform", "flsun", "diehard-pushraker.service")
check("ExecStart=/home/pi/diehard-pushraker/platform/flsun/sync.sh" in svc,
      "service runs platform/flsun/sync.sh")
inst = read("platform", "flsun", "install.sh")
check('"${HOME}/diehard-pushraker"' in inst, "install.sh wants ~/diehard-pushraker")
check("flsun_pushraker" in inst, "install.sh stops while FLSUN_pushraker is on")

tmpl = read("platform", "flsun", "diehard-pushraker.conf")
cp = configparser.RawConfigParser(strict=True)
cp.read_string(tmpl)
um = "update_manager diehard-pushraker"
check(cp.has_section(um) and cp.get(um, "managed_services") ==
      "diehard-pushraker" and cp.get(um, "path") == "~/diehard-pushraker",
      "update entry: path and managed service")
check(cp.sections() == [um, "pushraker_cards", "pushraker_updates"],
      f"template sections in append order ({cp.sections()})")
check(cp.get("pushraker_updates", "page_url") ==
      "http://{host}/pushraker-updates/", "page on port 80 (and 81)")

sync = read("platform", "flsun", "sync.sh")
check('PYTHONPATH="$PKG" "$PY" -m pushraker install' in sync and
      '--site-packages "$PKG"' in sync,
      "pushraker's installer runs from python/, its shim finds python/")
check("pip\" uninstall" in sync or "pip uninstall" in sync or
      '"${VENV}/bin/pip" uninstall' in sync, "a pip-installed pushraker is removed")
check('COMPONENTS="pushraker_cards pushraker_updates"' in sync and
      '${REPO_DIR}/moonraker/components/${comp}.py' in sync,
      "components linked from moonraker/components")
check("for section in pushraker_cards pushraker_updates" in sync,
      "sync.sh appends the template's later sections")
check('WEB="${PKG}/pushraker/web"' in sync and
      "location /pushraker-updates/" in sync and
      "alias ${REPO_DIR}/web/;" in sync, "serves the settings and confirm pages")
check('SNIPPET="/etc/nginx/snippets/flsun-pushraker.conf"' in sync,
      "snippet name matches FLSUN_Updater's Fluidd include (flsun-pushraker*.conf)")
un = read("platform", "flsun", "uninstall.sh")
check('PYTHONPATH="${REPO_DIR}/python"' in un and "pushraker uninstall" in un,
      "uninstall.sh runs pushraker's uninstaller from python/")
root = read("install.sh")
check('"$DIR/platform/flsun/install.sh"' in root and
      '"$DIR/platform/zmod/install.sh"' in root, "install.sh hands over per printer")

print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
