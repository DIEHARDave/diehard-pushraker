#!/usr/bin/env python3
"""Refill python/ with pushraker and the pure-Python packages it needs.

Kiln OS has no pip, so the plugin ships its Python packages, the way
kiln-notify ships Apprise. Kiln's own Python already has Pillow and Jinja2
(pushraker's other two requirements), so they are left out.

    python tools/vendor.py              # latest pushraker from PyPI
    python tools/vendor.py 0.3.6        # a given version

Runs on any machine with pip (Windows, Linux, macOS): only wheels that
work on any platform are taken (py3-none-any), so nothing in python/ is
built for the machine it ran on. PyYAML has no such wheel: it is built
from source with its C extension off.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TARGET = os.path.join(REPO, "python")

# Apprise's requirements, plus requests' (pushraker needs requests too)
PURE = ["apprise", "requests", "urllib3", "certifi", "idna",
        "charset_normalizer", "click", "markdown", "requests_oauthlib",
        "oauthlib"]

# Fixes to pushraker that upstream doesn't have yet: (file, old, new).
# A fix that no longer applies (upstream changed the line) is reported.
PATCHES = [
    # The settings page reads the filament change checkboxes, which it
    # leaves out on printers other than the Snapmaker U1: Send Test and
    # Save & Apply then fail with "Cannot read properties of null".
    # Upstream PR: noon92/pushraker, branch fix/hidden-filament-change-row.
    ("pushraker/web/index.html",
     "filament_change_events: ['loaded', 'unloaded'].filter(e => "
     "el('nt-fc-' + e).checked).join(','),",
     "filament_change_events: el('nt-fc-loaded') ? ['loaded', 'unloaded']"
     ".filter(e => el('nt-fc-' + e).checked).join(',') : "
     "[].concat(state.filament_change_events || []).join(','),"),
    # Beside pushraker's version at the top of the page: the version of
    # DIEHARDave's add-ons around it (Discord cards, update notices), from
    # /server/pushraker_addons/info, so it's clear which one changed.
    ("pushraker/web/index.html",
     "el('pr-version').textContent = data.meta.version ? `pushraker ${data.meta.version}` : '';",
     'el(\'pr-version\').textContent = data.meta.version ? `pushraker ${data.meta.version}` : \'\'; fetch(\'/server/pushraker_addons/info\').then(r => r.ok ? r.json() : null).then(j => { const a = j && j.result; const o = el(\'pr-addons\'); if (o) o.remove(); if (!a || !a.version) return; const s = document.createElement(\'span\'); s.id = \'pr-addons\'; const l = document.createElement(\'a\'); l.href = a.url; l.target = \'_blank\'; l.style.margin = \'0\'; l.textContent = "DIEHARDave\'s plugins"; s.append(\' - with \', l, \' \' + a.version); el(\'pr-version\').after(s); }).catch(() => {});'),
    # The printer's name after "Push notifications" at the top of the
    # page (and in the browser tab), so each printer's page is easy to
    # tell apart.
    ("pushraker/web/index.html",
     "el('nt-name').value = data.name;",
     "el('nt-name').value = data.name; { const n = data.name || (data.meta && data.meta.default_name) || ''; const h = document.querySelector('h1 > span'); if (h) h.textContent = 'Push notifications' + (n ? ' - ' + n : ''); document.title = n ? `${n} · Push notifications` : 'Push notifications'; }"),
    # First setup fills in the printer URL for the buttons from this
    # page's address, without its port (:82 on Kiln OS and Zmod, a page of
    # its own): the buttons open the printer's web UI. The Fill in
    # choices are unchanged.
    ("pushraker/web/index.html",
     "el('nt-printer-url').value = location.protocol + '//' + location.host;",
     "el('nt-printer-url').value = location.protocol + '//' + location.hostname;"),
    # The System section (software update notices) above Custom:
    # web/system.js, served at /pushraker-updates/ next to the update
    # confirm page.
    # pushraker.conf in Moonraker's "config" root, not next to
    # moonraker.conf: Kiln OS runs Moonraker with -c /run/moonraker/
    # moonraker.conf, and /run is emptied at every boot (the settings went
    # back to the defaults; the token, kept in the data folder, then
    # "was entered for another service"). A file left in the old place is
    # copied over once.
    ("pushraker/component.py",
     '        config_dir = os.path.dirname(app_args.get("config_file") or "") \\\n'
     '            or os.path.join(data_path, "config")\n',
     '        config_dir = os.path.dirname(app_args.get("config_file") or "") \\\n'
     '            or os.path.join(data_path, "config")\n'
     '        # pushraker.conf goes in Moonraker\'s "config" root (what Mainsail\n'
     '        # and Fluidd show), which isn\'t always where moonraker.conf is:\n'
     '        # Kiln OS starts Moonraker with -c /run/moonraker/moonraker.conf,\n'
     '        # and /run is emptied at every boot\n'
     '        try:\n'
     '            root = self.server.lookup_component("file_manager") \\\n'
     '                .get_directory("config")\n'
     '        except Exception:\n'
     '            root = ""\n'
     '        if root and os.path.realpath(root) != os.path.realpath(config_dir):\n'
     '            old = os.path.join(config_dir, cfgmod.CONF_NAME)\n'
     '            new = os.path.join(root, cfgmod.CONF_NAME)\n'
     '            if os.path.isfile(old) and not os.path.exists(new):\n'
     '                try:\n'
     '                    shutil.copy2(old, new)\n'
     '                except OSError:\n'
     '                    pass\n'
     '            config_dir = root\n'),
    # Klipper restarts: the hook variables were set with one
    # SET_GCODE_VARIABLE line each in the console (4 per restart). Now
    # only the values Klipper doesn't have already, in one line through
    # _NTFY_HOOKS_SET (the per-variable lines when the macro file has no
    # _NTFY_HOOKS_SET, e.g. the Snapmaker U1's own copy).
    ('pushraker/klipper/pushraker.cfg',
     '[gcode_macro _NTFY_LIFT]',
     '[gcode_macro _NTFY_HOOKS_SET]\ndescription: Set the pushraker print hook variables in one line (the component)\ngcode:\n    {% for key in ["complete", "cancelled", "fault", "lift_mm", "lift_ceiling"] %}\n    {% if key|upper in params %}\n    SET_GCODE_VARIABLE MACRO=_NTFY_HOOKS VARIABLE={key} VALUE={params[key|upper]|float}\n    {% endif %}\n    {% endfor %}\n\n[gcode_macro _NTFY_LIFT]'),
    ('pushraker/component.py',
     '                return\n            for key, value in values.items():\n                await kapis.run_gcode(\n                    f"SET_GCODE_VARIABLE MACRO={HOOKS_MACRO} "\n                    f"VARIABLE={key} VALUE={value}")\n',
     '                return\n            # Only what Klipper doesn\'t have already, in one line when the\n            # macro file has _NTFY_HOOKS_SET (each command shows in the\n            # console, after every Klipper restart)\n            res = await kapis.query_objects(\n                {f"gcode_macro {HOOKS_MACRO}": None}, default={})\n            have = res.get(f"gcode_macro {HOOKS_MACRO}") or {}\n\n            def same(a: Any, b: Any) -> bool:\n                try:\n                    return float(a) == float(b)\n                except (TypeError, ValueError):\n                    return False\n            todo = {k: v for k, v in values.items()\n                    if not same(have.get(k), v)}\n            if todo and f"gcode_macro {HOOKS_MACRO}_SET" in objs:\n                args = " ".join(f"{k.upper()}={v}" for k, v in todo.items())\n                await kapis.run_gcode(f"{HOOKS_MACRO}_SET {args}")\n            else:\n                for key, value in todo.items():\n                    await kapis.run_gcode(\n                        f"SET_GCODE_VARIABLE MACRO={HOOKS_MACRO} "\n                        f"VARIABLE={key} VALUE={value}")\n'),
    ("pushraker/web/index.html",
     '</body>',
     '<script src="/pushraker-updates/system.js"></script>\n</body>'),
]


def pip(*args, env=None):
    cmd = [sys.executable, "-m", "pip", "--disable-pip-version-check",
           "-q", *args]
    print("+", " ".join(cmd[3:]))
    subprocess.run(cmd, check=True, env=env)


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else None
    tmp = tempfile.mkdtemp(prefix="pushraker-vendor-")
    out = os.path.join(tmp, "python")
    try:
        pkg = f"pushraker=={version}" if version else "pushraker"
        pip("install", "--target", out, "--no-deps", "--only-binary=:all:",
            "--platform", "any", "--implementation", "py",
            "--python-version", "3.14", pkg, *PURE)
        env = dict(os.environ, PYYAML_FORCE_LIBYAML="0")
        pip("install", "--target", out, "--no-deps", "--no-binary", "pyyaml",
            "pyyaml", env=env)
        # Leftovers that don't belong on the printer: compiled extensions
        # built for this machine, caches, console scripts
        for root, dirs, files in os.walk(out, topdown=False):
            for f in files:
                if f.endswith((".so", ".pyd", ".pyc")):
                    os.remove(os.path.join(root, f))
            for d in dirs:
                if d == "__pycache__":
                    shutil.rmtree(os.path.join(root, d))
        shutil.rmtree(os.path.join(out, "bin"), ignore_errors=True)

        failed = []
        for rel, old, new in PATCHES:
            path = os.path.join(out, rel)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            if new in text:
                continue
            if old not in text:
                failed.append(rel)
                continue
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(text.replace(old, new, 1))
            print(f"patched {rel}")

        shutil.rmtree(TARGET, ignore_errors=True)
        shutil.copytree(out, TARGET)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for name in sorted(os.listdir(TARGET)):
        if name.endswith(".dist-info"):
            print("  ", name[:-len(".dist-info")])
    if failed:
        print("WARNING: these fixes no longer apply (check whether upstream "
              "fixed them, then drop them from PATCHES):", *failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
