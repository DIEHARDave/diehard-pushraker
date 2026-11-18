#!/usr/bin/env python3
"""Offline checks for pushraker_updates.py: python tests/test_updates.py

A fake
Moonraker (update manager, klippy connection, pushraker's destinations)
and a fake Discord (every HTTP call recorded, canned answers):

- which entries count as updates, and their order (Moonraker, OS last)
- check_time / check_days and the next run
- a check with a channel webhook: link buttons to the page; notify:
  changes sends once; force sends again
- a check with a bot DM: interactive buttons; the Gateway is wanted
- clicks: another printer's card, a stranger, an old card, confirm,
  cancel, while printing, the update itself and the edited card
- a list cut short by Moonraker's restart continues at start
"""

import asyncio
import glob
import importlib.util
import json
import os
import sys
import subprocess
import tempfile
import types
import datetime

# tests/ is one level below the plugin
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


path = [p for p in glob.glob(os.path.join(HERE, "**", "pushraker_updates.py"),
                             recursive=True) if "__pycache__" not in p][0]
spec = importlib.util.spec_from_file_location("pushraker_updates", path)
pu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pu)

# ---- the AD5X-2's status of 2026-11-18, trimmed, plus made-up entries ----
VI = {
    "moonraker": {"configured_type": "kiln_app", "version": "v0.11.0-4",
                  "remote_version": "v0.11.0-4", "is_valid": True},
    "klipper": {"configured_type": "kiln_app", "version": "v0.13.0-827",
                "remote_version": "v0.13.0-827", "is_valid": True},
    "fluidd": {"configured_type": "web", "version": "v1.38.0",
               "remote_version": "v1.38.1", "is_valid": True},
    "ff_nfc": {"configured_type": "git_repo", "version": "v0.2.6-2",
               "remote_version": "v0.2.6-2", "is_valid": True,
               "is_dirty": False, "commits_behind_count": 0},
    "Kiln OS": {"configured_type": "kiln_os", "version": "v1.0.0-rc3",
                "remote_version": "v1.0.0-rc3", "is_valid": True},
}

ups, probs = pu.pending(VI)
check([u["name"] for u in ups] == ["fluidd"] and not probs,
      "AD5X-2 status: only fluidd has an update")
check(ups[0]["text"] == "v1.38.0 → v1.38.1", "fluidd's text: " + ups[0]["text"])

more = json.loads(json.dumps(VI))
more["moonraker"]["remote_version"] = "v0.11.0-9"
more["Kiln OS"]["remote_version"] = "v1.0.0"
more["klipper"]["commits_behind_count"] = 3
more["klipper"]["remote_version"] = "v0.13.0-830"
more["ff_nfc"].update(remote_version="v0.2.7-0", is_dirty=True)
more["system"] = {"package_count": 2, "package_list": ["a", "b"]}
ups, probs = pu.pending(more, ignore={"fluidd"})
check([u["name"] for u in ups] == ["klipper", "system", "moonraker",
                                   "Kiln OS"],
      "order: klipper, system, moonraker, OS last; ignore works")
check([p["name"] for p in probs] == ["ff_nfc"] and
      probs[0]["why"] == "has local changes",
      "a dirty repo is a problem, not a button")
check("(+3 commits)" in ups[0]["text"], "commits behind shown")
check(ups[1]["text"] == "2 packages", "system packages counted")

# ---- schedule -------------------------------------------------------------
check(pu.parse_times("21:30, 9:00") == [(9, 0), (21, 30)], "check_time list")
check(pu.parse_days("weekdays") == [0, 1, 2, 3, 4] and
      pu.parse_days("Sat, sun") == [5, 6], "check_days names")
for bad in ("25:00", "nine"):
    try:
        pu.parse_times(bad)
        check(False, f"check_time {bad} refused")
    except ValueError:
        check(True, f"check_time {bad} refused")
utc = datetime.timezone.utc
wed = datetime.datetime(2026, 11, 18, 10, 0, tzinfo=utc)   # a Wednesday
check(pu.next_run(wed, [(9, 0)], list(range(7))) ==
      datetime.datetime(2026, 11, 19, 9, 0, tzinfo=utc),
      "09:00 daily at 10:00 Wed: Thu 09:00")
check(pu.next_run(wed, [(9, 0), (21, 0)], list(range(7))) ==
      datetime.datetime(2026, 11, 18, 21, 0, tzinfo=utc),
      "second time the same day")
check(pu.next_run(wed, [(9, 0)], [6]) ==
      datetime.datetime(2026, 11, 22, 9, 0, tzinfo=utc),
      "sun only: next Sunday")
try:
    from zoneinfo import ZoneInfo
    den = ZoneInfo("America/Denver")
except Exception:
    den = None
if den is not None:
    at = pu.next_run(wed.astimezone(den), [(9, 0)], list(range(7)))
    check(at.astimezone(utc) == datetime.datetime(2026, 11, 18, 16, 0,
                                                   tzinfo=utc),
          "09:00 America/Denver = 16:00 UTC (10:00 UTC is 03:00 there)")

# ---- fake Moonraker -------------------------------------------------------


class ServerError(Exception):
    def __init__(self, msg, status_code=400):
        super().__init__(msg)
        self.status_code = status_code


class RequestType:
    GET = "GET"
    POST = "POST"


class APIDef:
    def __init__(self, fn):
        self.fn = fn

    async def request(self, args, rtype):
        return await self.fn(args, rtype)


fake_common = types.ModuleType("fake_moonraker_common")
fake_common.RequestType = RequestType


class APIDefinition:
    _cache = {}


fake_common.APIDefinition = APIDefinition
sys.modules["fake_moonraker_common"] = fake_common


class UpdateManager:
    def __init__(self):
        self.vi = json.loads(json.dumps(VI))
        self.upgraded = []
        self.refreshes = 0
        self.fail = {}
        c = APIDefinition._cache
        c["/machine/update/refresh"] = APIDef(self.refresh)
        c["/machine/update/status"] = APIDef(self.status)
        c["/machine/update/upgrade"] = APIDef(self.upgrade)

    async def refresh(self, args, rtype):
        self.refreshes += 1
        return {"version_info": self.vi, "busy": False}

    async def status(self, args, rtype):
        return {"version_info": self.vi, "busy": False}

    async def upgrade(self, args, rtype):
        name = args["name"]
        self.upgraded.append(name)
        if name in self.fail:
            raise ServerError(self.fail[name], 500)
        info = self.vi[name]
        info["version"] = info["remote_version"]
        return "ok"


class Loop:
    def __init__(self):
        self.aioloop = asyncio.get_event_loop()

    def create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def run_in_thread(self, fn, *args):
        return fn(*args)


class Kconn:
    printing = False

    def is_printing(self):
        return self.printing


class Srv:
    """An Apprise server object as pushraker builds them."""
    def __init__(self, schema, user=None, host=None, fullpath=None, qsd=None):
        self._default_args = {"schema": schema, "user": user, "host": host,
                              "fullpath": fullpath, "qsd": qsd or {}}


class Dest:
    def __init__(self, *srvs):
        self.ap = list(srvs)


class Push:
    name = "AD5X-2"
    tz = utc
    web_ui_url = ""
    rows = {}


class Server:
    error = ServerError

    def __init__(self, data):
        self.loop = Loop()
        self.data = data
        self.comps = {"klippy_connection": Kconn(), "pushraker": Push()}
        self.endpoints = {}
        self.remote = {}

    def get_event_loop(self):
        return self.loop

    def get_app_args(self):
        return {"data_path": self.data}

    def register_endpoint(self, path, methods, cb):
        self.endpoints[path] = cb

    def register_remote_method(self, name, cb):
        self.remote[name] = cb

    def lookup_component(self, name):
        return self.comps[name]


class Config:
    def __init__(self, server, opts):
        self.server, self.opts = server, opts
        self.error = ValueError

    def get_server(self):
        return self.server

    def get_name(self):
        return "pushraker_updates"

    def get(self, key, default=""):
        return self.opts.get(key, default)

    def getboolean(self, key, default):
        v = self.opts.get(key)
        return default if v is None else str(v).lower() in ("true", "1")

    def gettemplate(self, key):
        v = self.opts[key]
        return types.SimpleNamespace(render=lambda: v)


# ---- fake Discord ---------------------------------------------------------
calls = []
msg_ids = iter(range(900000000000000000, 900000000000001000))


async def fake_http(self, method, url, headers=None, body=None):
    calls.append((method, url, headers or {}, body))
    if url.endswith("/users/@me/channels"):
        return 200, {"id": "111111111111111111"}
    if "/channels/" in url and url.endswith("/messages") and method == "POST":
        return 200, {"id": str(next(msg_ids))}
    if url.endswith("/oauth2/applications/@me"):
        return 200, {"id": "5", "owner": {"id": "222222222222222222"}}
    return 200, {}

pu.PushrakerUpdates._http = fake_http
started_gateways = []


async def no_gateway(self):
    started_gateways.append(self.key)

real_run = pu.Gateway.run
pu.Gateway.run = no_gateway
pu.local_address = lambda: "10.0.1.179"


made = []


def make(opts, rows, data=None):
    data = data or tempfile.mkdtemp()
    server = Server(data)
    server.comps["pushraker"].rows = rows
    um = UpdateManager()
    comp = pu.PushrakerUpdates(Config(server, opts))
    made.append(comp)
    return comp, server, um, data


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def walk(payload):
    out = []

    def go(c):
        out.append(c)
        for k in ("components",):
            for x in c.get(k) or []:
                go(x)
        if c.get("accessory"):
            go(c["accessory"])
    for c in payload.get("components") or []:
        go(c)
    return out


def buttons(payload):
    return [c for c in walk(payload) if c.get("type") == 2]


def text(payload):
    parts = [c["content"] for c in walk(payload) if c.get("type") == 10]
    for e in payload.get("embeds") or []:
        parts += [e.get("title", ""), e.get("description", "")]
    return "\n".join(parts)


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)

asyncio.set_event_loop(asyncio.new_event_loop())

# ---- webhook: link buttons ------------------------------------------------
hook_rows = {"complete": ("render", [Dest(Srv(
    "discordhook", host="123456789012345678", fullpath="/tok-en",
    qsd={"name": "AD5X-2"}))])}
comp, server, um, data = make({}, hook_rows)
run(comp.component_init())
calls.clear()
res = run(comp.check())
check(res["sent"] and res["to"] == ["discord webhook"], f"webhook check sent: {res.get('to')}")
post = [c for c in calls if "/webhooks/" in c[1]]
check(len(post) == 1 and "with_components=true" in post[0][1],
      "one webhook post, with_components=true")
card = post[0][3]
btns = buttons(card)
check(card.get("flags") == 1 << 15 and card["components"][0]["type"] == 17,
      "one card: a Components V2 container")
sec = [c for c in walk(card) if c.get("type") == 9]
check(len(sec) == 1 and "fluidd" in sec[0]["components"][0]["content"] and
      sec[0]["accessory"]["style"] == 5 and sec[0]["accessory"]["url"] ==
      "http://10.0.1.179:82/pushraker-updates/?name=fluidd",
      "fluidd's link button sits beside it in the card")
check([b["label"] for b in btns] == ["Update", "Open AD5X-2 in browser"] and
      btns[-1]["url"] == "http://10.0.1.179/",
      "no Update all; a link to the web UI instead: "
      + str([b.get("label") for b in btns]))
check(card["username"] == "AD5X-2", "webhook keeps the printer name")
calls.clear()
res = run(comp.check())
check(not res["sent"] and "already" in res["why"] and not calls,
      "notify: changes: same updates not sent again")
res = run(comp.check(force=True))
check(res["sent"], "force sends again")
check(not started_gateways, "no Gateway for a webhook-only setup")
um.vi["fluidd"]["version"] = "v1.38.1"
res = run(comp.check())
check(not res["sent"] and res["why"] == "up to date" and
      comp.state["notice"] is None, "up to date clears the notice")
server.comps["klippy_connection"].printing = True
try:
    run(comp.check())
    check(False, "check refused while printing")
except pu.Busy:
    check(True, "check refused while printing (retried later)")
server.comps["klippy_connection"].printing = False

# Discord refuses the link buttons: the links go in the text
um.vi["fluidd"]["version"] = "v1.38.0"
calls.clear()
orig = pu.PushrakerUpdates._http


async def refuse_components(self, method, url, headers=None, body=None):
    calls.append((method, url, headers or {}, body))
    if "/webhooks/" in url and body.get("components"):
        return 400, {"message": "Invalid Form Body"}
    return 200, {}

pu.PushrakerUpdates._http = refuse_components
res = run(comp.check(force=True))
check(res["sent"] and len(calls) == 2 and
      "[update](http://10.0.1.179:82/pushraker-updates/?name=fluidd)" in
      calls[1][3]["embeds"][0]["description"] and
      "in browser](http://10.0.1.179/)" in calls[1][3]["embeds"][0]["description"],
      "refused link buttons: sent again with the links in the text")
pu.PushrakerUpdates._http = orig

# ---- bot DM: real buttons ---------------------------------------------------
USER = "333333333333333333"
dm_rows = {"complete": ("render", [Dest(Srv(
    "discorddm", user="BOT.TOKEN", host=USER))]),
    "error": ("render", [Dest(Srv(
        "discorddm", user="BOT.TOKEN", host=USER))])}
um_vi_more = json.loads(json.dumps(VI))
um_vi_more["moonraker"]["remote_version"] = "v0.11.0-9"
comp, server, um, data = make({}, dm_rows)
um.vi = um_vi_more
run(comp.component_init())
calls.clear()
res = run(comp.check())
posts = [c for c in calls if c[1].endswith("/messages")]
check(res["sent"] and len(posts) == 1,
      "bot DM: one message for two rows with the same DM")
check(posts[0][2].get("Authorization") == "Bot BOT.TOKEN", "sent as the bot")
btns = buttons(posts[0][3])
pid, nonce = comp.state["printer"], comp.state["notice"]["nonce"]
check([b.get("custom_id") for b in btns] ==
      [f"pru:{pid}:{nonce}:u:0", f"pru:{pid}:{nonce}:u:1", None] and
      btns[-1]["style"] == 5,
      "buttons: Update fluidd, Update moonraker (in the card), web UI link")
check(started_gateways == [pu.key_of("BOT.TOKEN")],
      "Gateway opened for the bot with buttons out")
gw = comp._gateways[pu.key_of("BOT.TOKEN")]
msg_id = comp.state["notice"]["messages"][0]["message"]


def click(cid, user=USER):
    return {"id": "1", "token": "itok", "type": 3,
            "channel_id": "111111111111111111",
            "message": {"id": msg_id}, "user": {"id": user},
            "data": {"custom_id": cid}}


def replies():
    return [c for c in calls if "/interactions/" in c[1]]

calls.clear()
run(comp.on_interaction(gw, click(f"pru:ffffff:{nonce}:u:0")))
check(not calls, "another printer's card: no answer (that printer answers)")
run(comp.on_interaction(gw, click(f"pru:{pid}:{nonce}:y:0",
                                  user="444444444444444444")))
r = replies()
check(len(r) == 1 and r[0][3]["type"] == 4 and
      r[0][3]["data"]["flags"] == 64 and not um.upgraded,
      "a stranger gets a private refusal; nothing runs")
calls.clear()
run(comp.on_interaction(gw, click(f"pru:{pid}:00000000:y:0")))
check("out of date" in replies()[0][3]["data"]["content"],
      "an old card's button is refused")
calls.clear()
run(comp.on_interaction(gw, click(f"pru:{pid}:{nonce}:y:*")))
check("Unknown" in replies()[0][3]["data"]["content"] and not um.upgraded,
      "an Update all click (first cards) runs nothing")
calls.clear()
run(comp.on_interaction(gw, click(f"pru:{pid}:{nonce}:u:1")))
r = replies()[0][3]
cbtn = buttons(r["data"])
check(r["type"] == 7 and cbtn[0]["custom_id"] == f"pru:{pid}:{nonce}:y:1"
      and len(cbtn) == 2 and "restart" in text(r["data"]),
      "Update moonraker asks to confirm, says Moonraker restarts")
calls.clear()
run(comp.on_interaction(gw, click(f"pru:{pid}:{nonce}:n:-")))
r = replies()[0][3]
check(r["type"] == 7 and len(buttons(r["data"])) == 3,
      "Cancel puts the buttons back")
server.comps["klippy_connection"].printing = True
calls.clear()
run(comp.on_interaction(gw, click(f"pru:{pid}:{nonce}:y:0")))
check("printing" in replies()[0][3]["data"]["content"] and not um.upgraded,
      "confirm while printing: refused")
server.comps["klippy_connection"].printing = False
calls.clear()
run(comp.on_interaction(gw, click(f"pru:{pid}:{nonce}:y:0")))
run(settle())
check(um.upgraded == ["fluidd"], "confirm runs the fluidd update")
edits = [c for c in calls if c[0] == "PATCH"]
final = edits[-1][3]
desc = text(final)
check("✅ **fluidd** v1.38.1" in desc and "Still available" in desc and
      "moonraker" in desc, "the card shows the result and what is left")
running = [c for c in calls if c[0] == "POST" and "/interactions/" in c[1]]
check(running and not [b for b in buttons(running[-1][3]["data"])],
      "no buttons while it runs")
fbtn = [b for b in buttons(final) if b.get("custom_id")]
new_nonce = comp.state["notice"]["nonce"]
check(new_nonce != nonce and len(fbtn) == 1 and
      fbtn[0]["custom_id"] == f"pru:{pid}:{new_nonce}:u:0",
      "new buttons (new nonce) for moonraker only")
check(comp.state["queue"] is None, "the list is done")

# ---- Moonraker's own update: cut short by its restart, finished at start ---
comp.state["queue"] = {"names": [], "results": [],
    "current": "moonraker", "user": USER,
    "message": {"bot": pu.key_of("BOT.TOKEN"),
                "channel": "111111111111111111", "message": msg_id}}
comp._save()
um.vi["moonraker"]["version"] = "v0.11.0-9"     # it did update
comp2, server2, um2, _ = make({}, dm_rows, data=data)
um2.vi = um.vi
calls.clear()
run(comp2.component_init())
run(settle())
run(asyncio.sleep(0.05))
run(settle())
check(um2.upgraded == [], "after the restart: nothing runs again")
desc = text([c for c in calls if c[0] == "PATCH"][-1][3])
check("✅ **moonraker** v0.11.0-9" in desc and "Update done" in desc,
      "the card shows moonraker's result, from its version")
check(comp2.state["queue"] is None and comp2.state["notice"] is None,
      "everything updated: no notice left")

# a failing update
comp3, server3, um3, _ = make({}, dm_rows)
um3.vi["fluidd"]["remote_version"] = "v1.38.1"
um3.fail["fluidd"] = "Error updating fluidd: download failed"
run(comp3.component_init())
run(comp3.check())
n3 = comp3.state["notice"]["nonce"]
gw3 = comp3._gateways[pu.key_of("BOT.TOKEN")]
msg_id = comp3.state["notice"]["messages"][0]["message"]
calls.clear()
run(comp3.on_interaction(gw3, click(f"pru:{comp3.state['printer']}:{n3}:y:0")))
run(settle())
final = [c for c in calls if c[0] == "PATCH"][-1][3]
check("⚠️ Update failed" in text(final) and
      "download failed" in text(final),
      "a failed update shows the error")

# confirm: False: one click runs it
comp4, _, um4, _ = make({"confirm": "False"}, dm_rows)
run(comp4.component_init())
run(comp4.check())
b4 = buttons(comp4._card(comp4.state["notice"], "bot"))
check(b4[0]["custom_id"].split(":")[3] == "y", "confirm: False skips the question")

# many updates: at most SECTIONS_MAX entries, under Discord's 40 components
many = {"nonce": "n", "items": [{"name": f"p{i}", "text": "a → b", "kind": ""}
                                for i in range(30)], "problems": []}
c = comp4._card(many, "bot")
check(len(walk(c)) <= 40 and "and 20 more" in text(c),
      f"30 updates: {len(walk(c))} components, the rest in the web UI")

# own bot in a server channel; only the owner and allowed_users may click
comp5, _, _, _ = make({"bot_token": "OWN.BOT", "channel": "555555555555555555",
                       "allowed_users": "666666666666666666"}, hook_rows)
run(comp5.component_init())
calls.clear()
res = run(comp5.check())
urls = [c[1] for c in calls]
check(any(u.endswith("/channels/555555555555555555/messages") for u in urls)
      and not any("/webhooks/" in u for u in urls),
      "own bot posts in its channel; webhooks: auto skips the webhook")
b = comp5._destinations()["bots"][pu.key_of("OWN.BOT")]
allowed = run(comp5._allowed(b))
check(allowed == {"222222222222222222", "666666666666666666"},
      "allowed: the bot's owner and allowed_users")

# the settings page's add-on version
check([pu.addons_version(v) for v in ("v1.2.0-0", "v1.2.0-3", "?", "", "0.3")]
      == ["1.2.0", "1.2.0-3", "", "", "0.3"], "add-on version text")
comp6, server6, um5, _ = make({}, hook_rows)
um5.vi["diehard-pushraker"] = {"configured_type": "git_repo",
                            "version": "v1.1.0-2", "remote_version": "v1.1.0-2"}
info = run(server6.endpoints["/server/pushraker_addons/info"](None))
check(info == {"name": "diehard-pushraker", "version": "1.1.0-2",
               "url": "https://github.com/DIEHARDave/diehard-pushraker"},
      f"add-on info finds the installed plugin's entry: {info}")
pages = [p for p in glob.glob(os.path.join(HERE, "**", "*.py"), recursive=True)
         if os.path.basename(p) == "vendor.py"]
check(pages and all("/server/pushraker_addons/info" in open(p, encoding="utf-8").read()
                    for p in pages),
      "the page patch (tools/vendor.py) asks for the add-on version")

# ---- the settings page's System section ---------------------------------
class Req:
    def __init__(self, method, args=None):
        self.method, self.args = method, args or {}

    def get_request_type(self):
        return types.SimpleNamespace(name=self.method)

    def get_args(self):
        return self.args

    def get_boolean(self, key, default):
        return default


check(pu.normalize({"check_time": "21:00,9:00", "check_days": ["sun", "mon"],
                    "ignore": "b, a"}) ==
      {"enabled": True, "check_time": "09:00, 21:00",
       "check_days": "mon, sun", "timezone": "", "notify": "changes",
       "ignore": ["a", "b"], "confirm": True, "other_services": False},
      "settings normalized: times sorted, days by name, ignore a list")
check(pu.normalize({"check_time": "07:30", "check_days": [d for d, _ in
      [("mon", 0), ("tue", 0), ("wed", 0), ("thu", 0), ("fri", 0),
       ("sat", 0), ("sun", 0)]]})["check_days"] == "daily",
      "all seven days are daily")
for bad, why in (({"check_time": "25:00"}, "check_time"),
                 ({"check_days": []}, "check_days"),
                 ({"notify": "sometimes"}, "notify"),
                 ({"timezone": "Mars/Base"}, "timezone")):
    try:
        pu.normalize(dict({"check_time": "09:00"}, **bad))
        check(False, f"bad {why} refused")
    except ValueError as e:
        check(why in str(e), f"bad {why} refused: {e}")

data7 = tempfile.mkdtemp()
comp7, server7, um7, _ = make({"check_time": "09:00", "notify": "changes"},
                              hook_rows, data=data7)
run(comp7.component_init())
ep = server7.endpoints["/server/pushraker_updates/settings"]
got = run(ep(Req("GET")))
check(got["settings"]["check_time"] == "09:00" and got["settings"]["enabled"]
      and "fluidd" in got["entries"] and got["defaults"] == got["settings"],
      "GET: the moonraker.conf values, and the update manager's entries")
try:
    run(ep(Req("POST", {"check_time": "nope"})))
    check(False, "POST with a bad time refused")
except ServerError as e:
    check(e.status_code == 400 and comp7.settings["check_time"] == "09:00",
          "POST with a bad time refused (400), nothing changed")
got = run(ep(Req("POST", {"enabled": False, "check_time": "06:15, 18:45",
                          "check_days": ["sat", "sun"], "notify": "always",
                          "ignore": ["mainsail"], "junk": 1})))
check(not comp7.enabled and comp7.times == [(6, 15), (18, 45)] and
      comp7.days == [5, 6] and comp7.repeat == "always" and
      comp7.ignore == {"mainsail"}, "POST applies at once")
check(got["next_check"] is None, "off: no next check")
got = run(ep(Req("POST", {"enabled": True})))
check(got["next_check"] and got["next_check"][11:16] in ("06:15", "18:45"),
      f"the answer already has the new next check: {got['next_check']}")
run(ep(Req("POST", {"enabled": False})))
saved = json.load(open(os.path.join(data7, ".pushraker_updates.json")))
check(saved["settings"] == {"enabled": False, "check_time": "06:15, 18:45",
                            "check_days": "sat, sun", "notify": "always",
                            "ignore": ["mainsail"]},
      "only what differs from moonraker.conf is kept")
run(comp7.close())
comp8, server8, _, _ = make({"check_time": "09:00"}, hook_rows, data=data7)
check(comp8.settings["check_time"] == "06:15, 18:45" and not comp8.enabled,
      "saved settings win over moonraker.conf after a restart")
# The scheduler follows a change at once
run(comp8.component_init())
run(settle())
check(comp8._next is None, "off: the scheduler waits")
run(server8.endpoints["/server/pushraker_updates/settings"](
    Req("POST", {"enabled": True})))
run(settle())
run(asyncio.sleep(0.01))
run(settle())
check(comp8._next is not None and (comp8._next.hour, comp8._next.minute)
      in ((6, 15), (18, 45)), f"on again: next check {comp8._next}")
run(comp8.close())

# ---- which port the pages are on; Mainsail's sidebar link -----------------
tmp9 = tempfile.mkdtemp()
srv81 = os.path.join(tmp9, "nginx.conf")
snip = os.path.join(tmp9, "port81.d", "diehard-pushraker.conf")
pairs = ((srv81, snip),)
check(pu.pages_port(pairs) == ":82", "no port 81 server: :82")
open(srv81, "w").write("server {\n  listen 81;\n  include %s/*.conf;\n}\n"
                       % os.path.dirname(snip))
check(pu.pages_port(pairs) == ":82", "port 81 includes the folder, no snippet yet: :82")
os.makedirs(os.path.dirname(snip))
open(snip, "w").write("location /notifications/ {}\n")
check(pu.pages_port(pairs) == ":81", "port 81 server with the snippet: :81")
open(srv81, "w").write("server {\n  listen 81;\n}\n")
check(pu.pages_port(pairs) == ":82", "an older port 81 server without the include: :82")

cfg = tempfile.mkdtemp()
msg = pu.sidebar_entry(cfg, True)
navi = json.load(open(os.path.join(cfg, ".theme", "navi.json")))
check(len(navi) == 1 and navi[0]["href"] == pu.NAVI_HREF and
      navi[0]["target"] == "_blank" and "added" in msg,
      "no .theme yet: created, with the entry")
mine = {"title": "Spoolman", "href": "http://x:7912"}
json.dump([mine] + navi, open(os.path.join(cfg, ".theme", "navi.json"), "w"))
check(pu.sidebar_entry(cfg, True) == "", "already there: nothing written")
pu.sidebar_entry(cfg, False)
check(json.load(open(os.path.join(cfg, ".theme", "navi.json"))) == [mine],
      "removed again, other entries kept")
# .theme linked into a theme repo (FF_Themes): kept out of its git status
if subprocess.run(["git", "--version"], capture_output=True).returncode == 0:
    repo = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q", repo], check=True)
    os.makedirs(os.path.join(repo, "themes", "zmod-ad5x"))
    open(os.path.join(repo, "themes", "zmod-ad5x", "custom.css"), "w").write("")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    cfg2 = tempfile.mkdtemp()
    target = os.path.join(repo, "themes", "zmod-ad5x")
    try:
        os.symlink(target, os.path.join(cfg2, ".theme"),
                   target_is_directory=True)
        linked = True
    except OSError:
        linked = False      # Windows without symlink rights
    if linked:
        pu.sidebar_entry(cfg2, True)
        st = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                            capture_output=True, text=True).stdout
        check(os.path.exists(os.path.join(target, "navi.json")) and
              "navi.json" not in st,
              "inside a theme repo: written there, not in its git status")
        open(os.path.join(target, "navi.json"), "w").write("[]")
        subprocess.run(["git", "-C", repo, "add", "-f", "themes/zmod-ad5x/navi.json"], check=True)
        check("belongs to the theme" in pu.sidebar_entry(cfg2, True),
              "a theme's own tracked navi.json is left alone")
    else:
        print("skip theme repo check (no symlinks here)")

# status endpoint, remote method registered
st = run(server.endpoints["/server/pushraker_updates/status"](None))
check("next_check" in st and "pushraker_check_updates" in server.remote,
      "status endpoint and Klipper remote method")

# ---- the Gateway against a stand-in Discord Gateway (needs tornado) ------
try:
    import tornado.web
    import tornado.websocket
except ImportError:
    tornado = None
    print("skip Gateway checks (pip install tornado)")
if tornado is not None:
    seen = []
    clicks = []

    class FakeGateway(tornado.websocket.WebSocketHandler):
        def send(self, msg):
            try:
                self.write_message(msg)
            except tornado.websocket.WebSocketClosedError:
                pass    # the client already went (op 7: it reconnects)

        def open(self):
            self.n = len(seen)
            self.send(json.dumps(
                {"op": 10, "d": {"heartbeat_interval": 50}}))

        def on_message(self, raw):
            p = json.loads(raw)
            seen.append(p["op"])
            if p["op"] == 1:
                self.send(json.dumps({"op": 11}))
            elif p["op"] == 2:
                self.send(json.dumps({
                    "op": 0, "s": 1, "t": "READY",
                    "d": {"session_id": "S1",
                          "resume_gateway_url": URL}}))
                self.send(json.dumps({
                    "op": 0, "s": 2, "t": "INTERACTION_CREATE",
                    "d": {"type": 3, "id": "9", "token": "t",
                          "data": {"custom_id": "pru:x:y:u:0"}}}))
                # Discord asks to reconnect: the client resumes
                self.send(json.dumps({"op": 7}))
            elif p["op"] == 6:
                seen.append(("resume", p["d"]["session_id"], p["d"]["seq"]))
                self.close(4004)

    app = tornado.web.Application([(r"/", FakeGateway)])
    srv = app.listen(0, "127.0.0.1")
    port = list(srv._sockets.values())[0].getsockname()[1]
    URL = f"ws://127.0.0.1:{port}"
    pu.GATEWAY = URL
    pu.Gateway.run = real_run

    class Owner:
        def _spawn(self, coro):
            asyncio.ensure_future(coro)

        async def on_interaction(self, gw, d):
            clicks.append(d["data"]["custom_id"])

    g = pu.Gateway(Owner(), "k", "TOKEN")

    async def go():
        await asyncio.wait_for(g.run(), 20)
    orig_sleep = asyncio.sleep

    async def fast_sleep(t, *a):
        await orig_sleep(min(t, 0.05), *a)
    pu.asyncio.sleep = fast_sleep
    try:
        run(go())
    except asyncio.TimeoutError:
        pass
    pu.asyncio.sleep = orig_sleep
    check(seen[:1] == [2], "Gateway: IDENTIFY after HELLO")
    check(clicks == ["pru:x:y:u:0"], "Gateway: the click reaches the component")
    check(("resume", "S1", 2) in seen, "Gateway: op 7 -> RESUME with session and seq")
    check(g.state.startswith("stopped") and "token" in g.state,
          "Gateway: close 4004 stops it (wrong token): " + g.state)
    srv.stop()

for c in made:
    run(c.close())
run(settle())

print()
print(f"{len(failures)} failure(s)" if failures else "all passed")
sys.exit(1 if failures else 0)
