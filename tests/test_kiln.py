#!/usr/bin/env python3
"""Offline checks for diehard-pushraker on Kiln OS: python tests/test_kiln.py

- plugin.cfg's include points at the vendored macro file, which defines
  the hooks the PRINT_END / CANCEL_PRINT wrappers call
- the wrappers rename Kiln's macros and call the hook first
- the vendored packages import from python/ (Apprise and its requirements,
  pushraker's settings module), with no compiled files for this machine
- the settings page fix from tools/vendor.py is in place
- root/install.sh writes a config with no directive Kiln's nginx lacks
- pushraker_updates finds Discord destinations in the real pushraker /
  Apprise objects; the update confirm page is served
"""

import configparser
import json
import os
import re
import subprocess
import sys
import types

# tests/ is one level below the plugin
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(HERE, "python")
failures = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def macros(path):
    with open(path, encoding="utf-8") as f:
        return re.findall(r"^\[gcode_macro (\S+)\]", f.read(), re.M)


with open(os.path.join(HERE, "plugin.json"), encoding="utf-8") as f:
    meta = json.load(f)
check(meta["name"] == "diehard-pushraker", "plugin.json name is diehard-pushraker")
check("pushraker" in meta["conflicts"], "conflicts with the old pushraker-kiln plugin")
check(meta["requires_os"] >= "1.6", "requires_os 1.6+ (root scripts)")

cfg_path = os.path.join(HERE, "plugin.cfg")
with open(cfg_path, encoding="utf-8") as f:
    cfg_text = f.read()
inc = re.findall(r"^\[include (\S+)\]", cfg_text, re.M)
check(len(inc) == 1, "plugin.cfg includes one file")
inc_path = os.path.join(HERE, inc[0])
check(os.path.isfile(inc_path), f"included {inc[0]} exists")
hooks = macros(inc_path)
for h in ("_PRINT_END_NTFY", "_CANCEL_PRINT_NTFY", "_NTFY_HOOKS"):
    check(h in hooks, f"vendored macros define {h}")

# A [gcode_macro X] for a macro Kiln defines would be merged into Kiln's
# section (Klipper merges sections with the same name), not wrap it
check(not re.search(r"^\[gcode_macro (PRINT_END|CANCEL_PRINT)\]", cfg_text,
                    re.M), "plugin.cfg doesn't redefine Kiln's macros")
cp = configparser.RawConfigParser(strict=False)
cp.read_string(cfg_text.replace("[include ", "[x_include "))
check(cp.has_section("pushraker_hooks"), "plugin.cfg has [pushraker_hooks]")


# extras/pushraker_hooks.py against a stand-in Klipper
sys.path.insert(0, os.path.join(HERE, "extras"))
import pushraker_hooks  # noqa: E402


class FakeConfigError(Exception):
    pass


class FakeGCmd:
    def __init__(self, line):
        self.line = line
        self.info = []

    def respond_info(self, msg):
        self.info.append(msg)


class FakeGCode:
    def __init__(self, log, fail=()):
        self.log, self.fail = log, fail
        self.handlers = {}
        self.gcode_help = {}

    def register_command(self, cmd, func, when_not_ready=False, desc=None):
        if func is None:
            return self.handlers.pop(cmd, None)
        assert cmd not in self.handlers, cmd
        self.handlers[cmd] = func
        if desc is not None:
            self.gcode_help[cmd] = desc

    def run_script_from_command(self, script):
        self.log.append(script)
        if script in self.fail:
            raise RuntimeError("boom")


class FakePrinter:
    def __init__(self, gcode, macros):
        self.gcode, self.macros, self.events = gcode, macros, {}

    def register_event_handler(self, ev, cb):
        self.events[ev] = cb

    def lookup_object(self, name, default=Exception):
        if name == "gcode":
            return self.gcode
        if name.startswith("gcode_macro ") and name[12:] in self.macros:
            return object()
        if default is Exception:
            raise KeyError(name)
        return default


class FakeConfig:
    error = FakeConfigError

    def __init__(self, printer, wrap):
        self.printer, self.wrap = printer, wrap

    def get_printer(self):
        return self.printer

    def get(self, key, default=None):
        return self.wrap if key == "wrap" and self.wrap else default


def setup(fail=(), macros=("_PRINT_END_NTFY", "_CANCEL_PRINT_NTFY")):
    log = []
    gc = FakeGCode(log, fail)
    for cmd in ("PRINT_END", "CANCEL_PRINT"):
        gc.register_command(cmd, lambda g, c=cmd: log.append(c),
                            desc=cmd.lower())
    pr = FakePrinter(gc, macros)
    obj = pushraker_hooks.load_config(
        FakeConfig(pr, cp.get("pushraker_hooks", "wrap")))
    pr.events["klippy:ready"]()
    return obj, gc, log


obj, gc, log = setup()
check(obj.get_status(0)["wrapped"] == ["PRINT_END", "CANCEL_PRINT"],
      "wraps PRINT_END and CANCEL_PRINT")
gc.handlers["PRINT_END"](FakeGCmd("PRINT_END"))
gc.handlers["CANCEL_PRINT"](FakeGCmd("CANCEL_PRINT"))
check(log == ["_PRINT_END_NTFY", "PRINT_END",
              "_CANCEL_PRINT_NTFY", "CANCEL_PRINT"],
      "hook runs before the original command")
check(gc.gcode_help.get("PRINT_END") == "print_end", "help text kept")

obj, gc, log = setup(fail=("_PRINT_END_NTFY",))
g = FakeGCmd("PRINT_END")
gc.handlers["PRINT_END"](g)
check(log == ["_PRINT_END_NTFY", "PRINT_END"] and g.info,
      "a failing hook still runs the original, with a console note")

obj, gc, log = setup(macros=())
check(obj.get_status(0)["wrapped"] == [], "no hook macros: nothing wrapped")
gc.handlers["PRINT_END"](FakeGCmd("PRINT_END"))
check(log == ["PRINT_END"], "unwrapped command still works")

try:
    pushraker_hooks.load_config(FakeConfig(FakePrinter(None, ()), "BAD"))
    check(False, "bad wrap option rejected")
except FakeConfigError:
    check(True, "bad wrap option rejected")

# The imports run in a child Python so this one stays clean
code = ("import sys; sys.path.insert(0, sys.argv[1]);"
        "import apprise, requests, yaml, markdown, requests_oauthlib;"
        "import pushraker.settings;"
        "print(apprise.__version__)")
r = subprocess.run([sys.executable, "-I", "-c", code, PY],
                   capture_output=True, text=True)
check(r.returncode == 0,
      f"vendored packages import (apprise {r.stdout.strip()})"
      + ("" if r.returncode == 0 else "\n" + r.stderr[-800:]))

# moonraker/components/pushraker_cards.py on pushraker's real
# discord._payload, with a stand-in Moonraker and printer state
import importlib.util  # noqa: E402

sys.path.insert(0, PY)
# By file: moonraker/components/pushraker.py (the shim) would shadow the
# package if the folder were on sys.path
_spec = importlib.util.spec_from_file_location(
    "pushraker_cards",
    os.path.join(HERE, "moonraker", "components", "pushraker_cards.py"))
pushraker_cards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pushraker_cards)
from pushraker import discord as pr_discord  # noqa: E402

real_payload = pr_discord._payload


class FakeLoop:
    aioloop = None


class FakeServer:
    def __init__(self, push):
        self.push = push

    def get_event_loop(self):
        return FakeLoop()

    def lookup_component(self, name):
        if name == "pushraker" and self.push is not None:
            return self.push
        raise KeyError(name)


class FakeMRConfig:
    def __init__(self, server):
        self.server = server

    def get_server(self):
        return self.server

    def getboolean(self, key, default):
        return default


class FakePush:
    name = "AD5X-2"

    async def _attempts(self, rid, dest, t, title, body, headers, path,
                        result=None):
        return pr_discord._payload(body, title, "info", {})


import asyncio  # noqa: E402

push = FakePush()
cards = pushraker_cards.load_component(FakeMRConfig(FakeServer(push)))
check(pr_discord._payload is not real_payload, "cards wrap discord._payload")
asyncio.run(cards.component_init())
check(getattr(push._attempts, "_kiln_card", False),
      "cards wrap pushraker's _attempts")

printing = {"status": {
    "print_stats": {"state": "printing", "filename": "cube.gcode",
                    "print_duration": 600, "info": {"current_layer": 12,
                                                    "total_layer": 50}},
    "display_status": {"progress": 0.4},
    "extruder": {"temperature": 220.0, "target": 220.0},
    "heater_bed": {"temperature": 60.0, "target": 60.0}},
    "meta": {"estimated_time": 1500}, "name": "AD5X-2"}
cards._state = lambda: printing
p = pr_discord._payload("40% done\ncube.gcode", "Progress", "info", {})
e = p["embeds"][0]
names = [f["name"] for f in e.get("fields", [])]
check(names == ["Status", "Extruder Temp", "Bed Temp", "Progress",
                "Print Time", "ETA", "Layer", "File"],
      f"printing card fields ({names})")
check(e.get("color") == 0x00ff00, "printing card is green")
check(e.get("footer", {}).get("text") == "AD5X-2", "printer name in footer")
check("cube.gcode" not in e.get("description", ""),
      "file line moved from the text to the File field")
check(dict((f["name"], f["value"]) for f in e["fields"])["Layer"]
      == "12 / 50", "layer from print_stats.info")

# "complete" comes from the print-end hook while Klipper still says printing
title = "Print complete"
p = asyncio.run(push._attempts("complete", None, None, title, "done",
                               {}, None))
e = p["embeds"][0]
fields = dict((f["name"], f["value"]) for f in e.get("fields", []))
check(fields.get("Status") == "✅ Completed",
      "complete card from the notification id, not Klipper's state")
check("Progress" not in fields and fields.get("File") == "cube.gcode",
      "complete card: no progress bar, file kept")

p = pr_discord._payload("boom", "Error", "failure", {})
check(p["embeds"][0]["color"] == 0xb02626, "failure card is red")

cards._state = lambda: (_ for _ in ()).throw(RuntimeError("no state"))
p = pr_discord._payload("plain", "Plain", "info", {})
check(p["embeds"][0]["title"] == "Plain" and "fields" not in p["embeds"][0],
      "a card error sends pushraker's own embed")
cards.close()
check(pr_discord._payload is real_payload, "close() puts _payload back")

# If pushraker isn't importable when the cards load (Zmod, the T1: the
# pushraker component puts the package on sys.path), they wrap at
# component_init
import pushraker as _pkg  # noqa: E402

_saved = sys.modules.get("pushraker.discord")
sys.modules["pushraker.discord"] = None
delattr(_pkg, "discord")
push = FakePush()
late = pushraker_cards.load_component(FakeMRConfig(FakeServer(push)))
sys.modules["pushraker.discord"] = _saved
_pkg.discord = _saved
check(pr_discord._payload is real_payload,
      "pushraker not importable yet: nothing wrapped at load")
asyncio.run(late.component_init())
check(getattr(pr_discord._payload, "_kiln_card", False)
      and getattr(push._attempts, "_kiln_card", False),
      "wrapped at component_init once pushraker is importable")
late.close()

# moonraker/components/pushraker_updates.py reads pushraker's destinations:
# its rows, and the Discord URLs inside Apprise's plugin objects
with open(os.path.join(PY, "pushraker", "component.py"),
          encoding="utf-8") as f:
    comp_src = f.read()
check("self.rows = rows" in comp_src and
      re.search(r"class Destination\(NamedTuple\):\s*\n\s*channel: str"
                r"[^\n]*\n\s*ap: apprise\.Apprise", comp_src) is not None,
      "pushraker keeps rows of Destination(channel, ap, ...)")

# pushraker.conf is kept in Moonraker's "config" root, not next to
# moonraker.conf: Kiln OS runs Moonraker with -c /run/moonraker/
# moonraker.conf, and /run is emptied at every boot. A file left in the
# old place is copied over once.
import shutil  # noqa: E402
import tempfile  # noqa: E402

_m = re.search(r"\n( +config_dir = os\.path\.dirname\(app_args.*?"
               r"\n +config_dir = root\n)", comp_src, re.S)
check(_m is not None, "pushraker.conf location: config root block found")
if _m:
    import textwrap  # noqa: E402
    from pushraker import settings as _cfgmod  # noqa: E402
    _block = textwrap.dedent(_m.group(1))

    class _FM:
        def __init__(self, root):
            self.root = root

        def get_directory(self, name):
            return self.root if name == "config" else ""

    class _Srv:
        def __init__(self, root):
            self.fm = _FM(root)

        def lookup_component(self, name):
            if name == "file_manager":
                return self.fm
            raise KeyError(name)

    def _where(run_dir, root, data):
        ns = {"os": os, "shutil": shutil, "cfgmod": _cfgmod,
              "data_path": data,
              "app_args": {"config_file":
                           os.path.join(run_dir, "moonraker.conf")},
              "self": types.SimpleNamespace(server=_Srv(root))}
        exec(_block, ns)
        return ns["config_dir"]

    _tmp = tempfile.mkdtemp()
    try:
        _run = os.path.join(_tmp, "run")
        _data = os.path.join(_tmp, "printer_data")
        _cfg = os.path.join(_data, "config")
        os.makedirs(_run)
        os.makedirs(_cfg)
        with open(os.path.join(_run, "pushraker.conf"), "w") as f:
            f.write("[pushraker]\nprovider: discord\n")
        check(_where(_run, _cfg, _data) == _cfg,
              "Kiln: pushraker.conf in the config root, not /run/moonraker")
        with open(os.path.join(_cfg, "pushraker.conf")) as f:
            check("discord" in f.read(),
                  "a pushraker.conf left in /run/moonraker is copied over")
        with open(os.path.join(_run, "pushraker.conf"), "w") as f:
            f.write("[pushraker]\nprovider: ntfy\n")
        _where(_run, _cfg, _data)
        with open(os.path.join(_cfg, "pushraker.conf")) as f:
            check("discord" in f.read(),
                  "one in the config root is never overwritten")
        check(_where(_cfg, _cfg, _data) == _cfg,
              "Zmod/T1 (moonraker.conf in the config root): unchanged")
        check(_where(_run, "", _data) == _run,
              "no file_manager config root: next to moonraker.conf")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

import apprise  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "pushraker_updates",
    os.path.join(HERE, "moonraker", "components", "pushraker_updates.py"))
pushraker_updates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pushraker_updates)
ap_hook, ap_dm = apprise.Apprise(), apprise.Apprise()
ap_hook.add("discordhook://123456789012345678/tok-en?name=AD5X-2")
ap_dm.add("discorddm://BOT.TOKEN@333333333333333333")


class UpdPush:
    rows = {"complete": ("render", [
        types.SimpleNamespace(channel="discord", ap=ap_hook, email=False),
        types.SimpleNamespace(channel="discord", ap=ap_dm, email=False)])}


upd = pushraker_updates.PushrakerUpdates.__new__(
    pushraker_updates.PushrakerUpdates)
upd.server = FakeServer(UpdPush())
upd.allowed, upd.bot_token = set(), ""
dests = upd._destinations()
bots = list(dests["bots"].values())
check(dests["hooks"] == [{"id": "123456789012345678", "token": "tok-en",
                          "name": "AD5X-2"}],
      "update notices find the webhook in pushraker's Apprise objects")
check(len(bots) == 1 and bots[0]["token"] == "BOT.TOKEN" and
      bots[0]["targets"] == [("dm", "333333333333333333")],
      "update notices find the DM bot in pushraker's Apprise objects")

compiled = [os.path.join(dp, f) for dp, _, fs in os.walk(PY) for f in fs
            if f.endswith((".so", ".pyd"))]
check(not compiled, "no compiled extensions in python/")

with open(os.path.join(PY, "pushraker", "web", "index.html"),
          encoding="utf-8") as f:
    page = f.read()
check("el('nt-fc-loaded') ?" in page, "settings page filament-change fix")
check("/server/pushraker_addons/info" in page, "settings page: add-on version line")
check("'Push notifications' + (n ? ' - ' + n : '')" in page,
      "settings page: printer name in the page header")
check("value = location.protocol + '//' + location.hostname;" in page and
      "value = location.protocol + '//' + location.host;" not in page,
      "settings page: first-setup printer URL without the port")
check('<script src="/pushraker-updates/system.js"></script>' in page and
      os.path.isfile(os.path.join(HERE, "web", "system.js")),
      "settings page loads the System section (web/system.js)")

with open(os.path.join(HERE, "root", "install.sh"), encoding="utf-8") as f:
    inst = f.read()
# Every heredoc: the port 81 snippet and the port 82 server
conf = "\n".join(part.split("\nEOF", 1)[0]
                 for part in inst.split("<<EOF")[1:])
conf_lines = [ln for ln in conf.splitlines()
              if not ln.strip().startswith("#")]
check(not re.search(r"^\s*return\b", "\n".join(conf_lines), re.M),
      "nginx config has no return")
check(not re.search(r"location\s+~", conf), "no regex locations")
check("location /notifications/" in conf, "serves /notifications/")
check("location /pushraker-updates/" in conf and
      os.path.isfile(os.path.join(HERE, "web", "index.html")),
      "serves the update confirm page /pushraker-updates/")
check("location /server/" in conf, "proxies /server/ to Moonraker")
check('grep -qs "include $X81/\\*\\.conf;" $MS81' in inst and
      "cat > $SNIPPET" in inst and "include $SNIPPET;" in conf,
      "pages in port81.d (mainsail-kiln's port 81), port 82 only without it")

print(f"\n{'FAILED: ' + str(len(failures)) if failures else 'all passed'}")
sys.exit(1 if failures else 0)
