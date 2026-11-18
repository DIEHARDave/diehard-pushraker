# pushraker_updates: software update notices on Discord, with buttons that
# start the updates. Part of diehard-pushraker (Kiln OS, Zmod and the
# FLSUN T1).
#
# On a schedule ([pushraker_updates] check_time / check_days) it asks
# Moonraker's update manager to look for updates (/machine/update/refresh)
# and posts one card listing every entry with a newer version (Klipper,
# Moonraker, the web UIs, plugins, the OS), each with an "Update" button
# beside it inside the card (Discord's Components V2 layout), and an "Open
# <printer> in browser" link to the web UI (web_ui_url) at the bottom.
# There is deliberately no remote "update all": one entry per click.
#
# How the buttons work depends on what sends the card:
#
#   A Discord bot (pushraker's "Discord DM" setting, or bot_token below):
#     real buttons. Discord delivers a click to the bot over its Gateway,
#     a websocket the printer opens to Discord (outbound only: no port to
#     forward). Only the DM's recipient, the bot's owner and allowed_users
#     may click; anyone else gets a private "not allowed" reply. A click
#     asks to confirm, then runs the update and edits the card with the
#     result. Updates are refused while printing.
#
#   A channel webhook (pushraker's "Discord" setting): Discord gives
#     webhooks no clicks, so the buttons are links to a confirm page on the
#     printer (page_url). They only work where the printer is reachable
#     (at home, or over a VPN).
#
# The card is sent to the Discord destinations of pushraker's settings
# (each once). other_services: True also sends a text notice, with the
# page link, to pushraker's other destinations.
#
# Updating Moonraker (or the OS) restarts it: the running update is saved
# in <data>/.pushraker_updates.json, and when Moonraker is back its
# version says how it went and the card is edited with the result.
#
# POST /server/pushraker_updates/check  check now and send (force=true)
# GET  /server/pushraker_updates/status the schedule, the last check
# Klipper: {action_call_remote_method("pushraker_check_updates")}
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import os
import random
import re
import secrets
import socket
import sys
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

LOG = "[pushraker_updates]"
VERSION = 1
API = "https://discord.com/api/v10"
GATEWAY = "wss://gateway.discord.gg"
USER_AGENT = "DiscordBot (https://github.com/DIEHARDave/diehard-pushraker, 1)"
TIMEOUT = (10., 30.)
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# Discord's limits (a Components V2 message holds 40 components: each
# entry with its button takes 3)
BUTTONS_PER_ROW = 5
SECTIONS_MAX = 10
LABEL_MAX = 80
COMPONENTS_V2 = 1 << 15
SEPARATOR = {"type": 14, "divider": True, "spacing": 1}
TEXT_MAX = 4096
# Moonraker refuses a refresh while printing: try again this often, for
# at most this long after the scheduled time
BUSY_RETRY = 15 * 60.
BUSY_GIVE_UP = 12 * 3600.
# After Moonraker restarts with a list still to run: how long to wait for
# the update manager
RESUME_WAIT = 300.
COLOUR = 0x3498db
COLOUR_DONE = 0x24743a
COLOUR_FAIL = 0xb02626
# Interaction callback types and flags
PONG_UPDATE = 7
REPLY = 4
EPHEMERAL = 1 << 6
# Gateway close codes that mean "don't come back with this token"
FATAL_CLOSE = {4004: "the bot token is wrong or was reset",
               4010: "invalid shard", 4011: "sharding required",
               4012: "invalid API version", 4013: "invalid intents",
               4014: "disallowed intents"}
RE_ID = re.compile(r"^\d{15,22}$")
# Mainsail's sidebar (config/.theme/navi.json): the entry this file adds,
# found again by its href. mdi-bell-outline.
NAVI_HREF = "/server/pushraker_addons/page?to=settings"
NAVI_ICON = ("M10 21H14C14 22.1 13.1 23 12 23S10 22.1 10 21M21 19V20H3V19L5 "
             "17V11C5 7.9 7 5.2 10 4.3V4C10 2.9 10.9 2 12 2S14 2.9 14 4V4.3C17 "
             "5.2 19 7.9 19 11V17L21 19M17 11C17 8.2 14.8 6 12 6S7 8.2 7 11V18H"
             "17V11Z")
# The page the sidebar link opens: it sends the browser on to the settings
# page at the address the browser used for Mainsail (the port differs per
# printer, the host doesn't)
REDIRECT = """<!doctype html><meta charset="utf-8"><title>Push notifications</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{background:#1a1a1a;color:#888;font:14px system-ui,sans-serif;padding:24px}</style>
<p>Opening the notification settings…</p>
<script>location.replace(%s.replace("{host}", location.hostname));</script>
"""
# The plugin this file ships in, by update manager entry: the settings
# page shows its version beside pushraker's
ADDON_REPOS = {
    "diehard-pushraker": "https://github.com/DIEHARDave/diehard-pushraker",
}


class FatalGateway(Exception):
    pass


# ---- pure helpers (no Moonraker, tested offline) ------------------------

def pending(version_info: Dict[str, Any], ignore: Any = ()
            ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(updates, problems) from /machine/update/status's version_info.

    An update: a newer remote version, commits behind, or system packages.
    A problem: an entry with an update that Moonraker would refuse (not a
    valid repo, local changes)."""
    updates: List[Dict[str, Any]] = []
    problems: List[Dict[str, Any]] = []
    for name, info in (version_info or {}).items():
        if name in ignore or not isinstance(info, dict):
            continue
        kind = str(info.get("configured_type") or "")
        if name == "system" or kind == "system":
            count = int(info.get("package_count") or 0)
            if count:
                updates.append({"name": name, "kind": "system",
                                "text": f"{count} package"
                                        f"{'s' if count != 1 else ''}"})
            continue
        version = str(info.get("version") or "?")
        remote = str(info.get("remote_version") or "?")
        behind = info.get("commits_behind_count")
        if behind is None:
            behind = len(info.get("commits_behind") or [])
        behind = int(behind or 0)
        newer = remote not in ("?", "") and remote != version
        if not newer and behind <= 0:
            continue
        text = f"{version} → {remote}" if newer else version
        if behind > 0:
            text += f" (+{behind} commit{'s' if behind != 1 else ''})"
        item = {"name": name, "kind": kind, "text": text,
                "version": version, "remote": remote}
        why = ""
        if info.get("is_valid") is False:
            why = "not a valid install"
        elif info.get("is_dirty"):
            why = "has local changes"
        elif info.get("corrupt"):
            why = "repo is corrupt"
        if why:
            item["why"] = why
            problems.append(item)
        else:
            updates.append(item)
    updates.sort(key=lambda i: (order(i), i["name"].lower()))
    return updates, problems


def order(item: Dict[str, Any]) -> int:
    """The order entries are listed in: what restarts Moonraker (or the
    printer) last."""
    name, kind = item["name"].lower(), str(item.get("kind") or "").lower()
    if kind.endswith("_os") or kind == "os" or name.endswith(" os"):
        return 4
    if name == "moonraker":
        return 3
    if name == "system":
        return 2
    if name == "klipper":
        return 1
    return 0


def addons_version(version: str) -> str:
    """Moonraker's git version for the page: v1.2.0-0 (on the tag) is
    1.2.0, v1.2.0-3 (3 commits after it) 1.2.0-3; "?" is unknown."""
    v = version.strip()
    if not v or v == "?":
        return ""
    if v[:1] in "vV" and v[1:2].isdigit():
        v = v[1:]
    return v[:-2] if v.endswith("-0") else v


# The Fluidd/Mainsail servers on port 81 that take other plugins' pages,
# and the snippet the plugin puts there: (server config, snippet)
PORT81 = (("/root/nginx/dualweb/nginx.conf",
           "/opt/config/mod_data/dualweb.d/diehard-pushraker.conf"),
          ("/data/ad5x/nginx/mainsail.conf",
           "/data/ad5x/nginx/port81.d/diehard-pushraker.conf"))


def pages_port(port81: Any = PORT81) -> str:
    """:81 when a port 81 server includes the plugin's pages, else :82."""
    for conf, snippet in port81:
        try:
            with open(conf) as f:
                text = f.read()
        except OSError:
            continue
        if os.path.exists(snippet) and                 f"include {os.path.dirname(snippet)}/*.conf;" in text:
            return ":81"
    return ":82"


def sidebar_entry(config_dir: str, want: bool) -> str:
    """Adds (or, want False, removes) the "Push notifications" entry in
    Mainsail's config/.theme/navi.json, keeping the others. .theme may be a
    theme plugin's folder (FF_Themes, FLSUN_theme link it into their repo):
    navi.json is then kept out of that repo's git status
    (.git/info/exclude), unless the theme tracks a navi.json of its own,
    which is left alone. Returns what it did, "" if nothing."""
    import subprocess
    theme = os.path.join(config_dir, ".theme")
    if not os.path.isdir(theme):
        if not want:
            return ""
        os.makedirs(theme, exist_ok=True)
    real = os.path.realpath(theme)
    navi = os.path.join(real, "navi.json")
    # The git repo .theme is in, if any
    repo, d = "", real
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")):
            repo = d
            break
        d = os.path.dirname(d)
    rel = os.path.relpath(navi, repo).replace(os.sep, "/") if repo else ""
    if repo:
        r = subprocess.run(["git", "-C", repo, "ls-files", "--error-unmatch",
                            rel], capture_output=True, text=True)
        if r.returncode == 0:
            return (f"{rel} belongs to the theme in {repo}; sidebar link "
                    "not added")
    entries: List[Any] = []
    if os.path.exists(navi):
        try:
            with open(navi, encoding="utf-8") as f:
                entries = json.load(f)
            if not isinstance(entries, list):
                return "navi.json isn't a list; sidebar link not added"
        except (OSError, ValueError) as e:
            return f"navi.json unreadable ({e}); sidebar link not added"
    others = [e for e in entries if not (isinstance(e, dict) and str(
        e.get("href", "")).startswith("/server/pushraker_addons/page"))]
    new = others + ([{"title": "Push notifications", "href": NAVI_HREF,
                      "target": "_blank", "position": 85,
                      "icon": NAVI_ICON}] if want else [])
    if new == entries:
        return ""
    if not new and not os.path.exists(navi):
        return ""
    if new:
        tmp = navi + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=2)
            f.write("\n")
        os.replace(tmp, navi)
    else:
        os.remove(navi)
    if repo and want:
        exclude = os.path.join(repo, ".git", "info", "exclude")
        os.makedirs(os.path.dirname(exclude), exist_ok=True)
        lines = open(exclude).read().splitlines() \
            if os.path.exists(exclude) else []
        if "/" + rel not in lines:
            with open(exclude, "a") as f:
                f.write(("" if not lines or lines[-1] == "" else "\n") +
                        "/" + rel + "\n")
    return (f"Mainsail sidebar link {'added to' if want else 'removed from'}"
            f" {navi}")


def git_version() -> str:
    """git describe of the repo this file is in (it's linked into
    Moonraker's components from the plugin's clone)."""
    import subprocess
    here = os.path.dirname(os.path.realpath(__file__))
    try:
        r = subprocess.run(["git", "-C", here, "describe", "--tags",
                            "--always"], capture_output=True, text=True,
                           timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    v = r.stdout.strip() if r.returncode == 0 else ""
    # v1.1.1-6-g3a2b6cc: 6 commits after v1.1.1
    m = re.fullmatch(r"v?(\d[\w.]*)-(\d+)-g[0-9a-f]+", v)
    if m:
        return m.group(1) if m.group(2) == "0" else \
            f"{m.group(1)}-{m.group(2)}"
    return addons_version(v)


def signature(updates: List[Dict[str, Any]]) -> List[str]:
    return sorted(f"{i['name']}={i['text']}" for i in updates)


def parse_times(text: str) -> List[Tuple[int, int]]:
    out = []
    for part in re.split(r"[,\s]+", text or ""):
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", part)
        if m is None or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise ValueError(f"check_time: '{part}' is not HH:MM")
        out.append((int(m.group(1)), int(m.group(2))))
    if not out:
        raise ValueError("check_time: no time given")
    return sorted(set(out))


def parse_days(text: str) -> List[int]:
    text = (text or "daily").strip().lower()
    if text in ("daily", "every day", "all", "*"):
        return list(range(7))
    if text == "weekdays":
        return list(range(5))
    if text == "weekends":
        return [5, 6]
    out = []
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        if part[:3] not in DAYS:
            raise ValueError(f"check_days: '{part}' is not a day "
                             "(mon..sun, daily, weekdays, weekends)")
        out.append(DAYS.index(part[:3]))
    return sorted(set(out))


def normalize(st: Dict[str, Any]) -> Dict[str, Any]:
    """The System section's settings, checked and in one form: check_time
    "09:00, 21:00", check_days "daily" or "mon, wed", notify changes or
    always, ignore a sorted list. ValueError says what's wrong."""
    def boolean(v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    times = parse_times(", ".join(st.get("check_time"))
                        if isinstance(st.get("check_time"), list)
                        else str(st.get("check_time") or ""))
    days_in = st.get("check_days")
    if isinstance(days_in, list) and not days_in:
        raise ValueError("check_days: pick at least one day")
    days = parse_days(", ".join(days_in) if isinstance(days_in, list)
                      else str(days_in or "daily"))
    if not days:
        raise ValueError("check_days: pick at least one day")
    notify = str(st.get("notify") or "changes").strip().lower()
    if notify not in ("changes", "always"):
        raise ValueError("notify: changes or always")
    zone = str(st.get("timezone") or "").strip()
    if zone:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(zone)
        except Exception:
            raise ValueError(f"timezone: no such time zone: {zone}")
    ignore = st.get("ignore") or []
    if isinstance(ignore, str):
        ignore = ignore.split(",")
    return {
        "enabled": boolean(st.get("enabled", True)),
        "check_time": ", ".join("%02d:%02d" % t for t in times),
        "check_days": "daily" if len(days) == 7 else
        ", ".join(DAYS[d] for d in days),
        "timezone": zone,
        "notify": notify,
        "ignore": sorted({str(n).strip() for n in ignore if str(n).strip()},
                         key=str.lower),
        "confirm": boolean(st.get("confirm", True)),
        "other_services": boolean(st.get("other_services", False)),
    }


def next_run(now: datetime.datetime, times: List[Tuple[int, int]],
             days: List[int]) -> datetime.datetime:
    """The first scheduled time after now (now carries the time zone)."""
    for add in range(8):
        day = (now + datetime.timedelta(days=add)).date()
        if day.weekday() not in days:
            continue
        for h, m in times:
            at = datetime.datetime.combine(
                day, datetime.time(h, m), tzinfo=now.tzinfo)
            if at > now:
                return at
    return now + datetime.timedelta(days=1)


def _td(text: str) -> Dict[str, Any]:
    """A Components V2 text display."""
    return {"type": 10, "content": text}


def _row(buttons: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": 1, "components": buttons[:BUTTONS_PER_ROW]}


def label(text: str) -> str:
    return text if len(text) <= LABEL_MAX else text[:LABEL_MAX - 1] + "…"


def escape(text: str) -> str:
    for c in "\\*_~`|>[]#":
        text = text.replace(c, "\\" + c)
    return text


def page_link(base: str, names: Optional[List[str]]) -> str:
    sep = "&" if "?" in base else "?"
    if names is None:
        return f"{base}{sep}all=1"
    return f"{base}{sep}name={urllib.parse.quote(names[0], safe='')}"


def key_of(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def local_address() -> str:
    """The printer's LAN address (a UDP socket only picks the route; it
    sends nothing)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostname()
    finally:
        s.close()


# ---- the component -------------------------------------------------------

class PushrakerUpdates:
    def __init__(self, config: Any) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        # The settings the page's System section edits: this section's
        # values are the defaults, what the page saved (state "settings")
        # wins
        self.defaults = {
            "enabled": config.getboolean("enabled", True),
            "check_time": config.get("check_time", "09:00"),
            "check_days": config.get("check_days", "daily"),
            "timezone": config.get("timezone", "").strip(),
            "notify": config.get("notify", "changes").strip().lower(),
            "ignore": [n.strip() for n in config.get("ignore", "").split(",")
                       if n.strip()],
            "confirm": config.getboolean("confirm", True),
            "other_services": config.getboolean("other_services", False),
        }
        try:
            self.settings = normalize(self.defaults)
        except ValueError as e:
            raise config.error(f"[{config.get_name()}] {e}")
        self.buttons = config.getboolean("buttons", True)
        # {port}: where the pages are (web_port, else found: :81 inside the
        # Fluidd/Mainsail server of zmod_dualweb or mainsail-kiln, :82 a
        # server of their own)
        self.page_url = config.get(
            "page_url", "http://{host}{port}/pushraker-updates/")
        self.web_port = config.get("web_port", "").strip()
        # The card's "Open in browser" link (pushraker's web_ui_url wins
        # when set on its page)
        self.web_ui_url = config.get("web_ui_url", "http://{host}/")
        # The settings page, for Mainsail's sidebar link: like page_url
        self.settings_url = config.get(
            "settings_url", self.page_url.replace("/pushraker-updates/",
                                                  "/notifications/"))
        # "Push notifications" in Mainsail's sidebar (.theme/navi.json)
        self.sidebar_link = config.getboolean("sidebar_link", True)
        self.config_dir = ""
        # The plugin around pushraker (this file, the Discord cards): its
        # update manager entry gives the version the settings page shows
        self.addons_entry = config.get("addons_entry", "")
        self.addons_url = config.get(
            "addons_url", "https://github.com/DIEHARDave")
        self.webhooks = config.get("webhooks", "auto").strip().lower()
        if self.webhooks not in ("auto", "always", "never"):
            raise config.error(
                f"[{config.get_name()}] webhooks: auto, always or never")
        # Optional: a bot of the update notices' own (real buttons in a
        # server channel, or a DM): its token (Moonraker's secrets work:
        # {secrets.pushraker_updates.bot_token}), and a channel ID or "dm"
        self.bot_token = self._template(config, "bot_token")
        self.bot_channel = config.get("channel", "dm").strip()
        self.allowed = {u.strip() for u in
                        config.get("allowed_users", "").split(",")
                        if u.strip()}
        for u in self.allowed:
            if not RE_ID.match(u):
                raise config.error(f"[{config.get_name()}] allowed_users: "
                                   f"'{u}' is not a Discord user ID")
        try:
            data_path = self.server.get_app_args().get("data_path") or ""
        except Exception:
            data_path = ""
        data_path = data_path or os.path.expanduser("~/printer_data")
        self.state_path = os.path.join(data_path, ".pushraker_updates.json")
        try:
            cfg_file = self.server.get_app_args().get("config_file") or ""
        except Exception:
            cfg_file = ""
        self.config_dir = os.path.dirname(cfg_file) or \
            os.path.join(data_path, "config")
        self.state: Dict[str, Any] = self._load()
        self.state.setdefault("printer", secrets.token_hex(3))
        saved = self.state.get("settings")
        if isinstance(saved, dict):
            try:
                self.settings = normalize(dict(self.defaults, **saved))
            except ValueError as e:
                logging.warning(f"{LOG} saved settings ignored: {e}")
        self._apply_settings()
        # Set when the settings change: the scheduler starts over
        self._wake = asyncio.Event()
        self._tasks: set = set()
        self._gateways: Dict[str, Gateway] = {}
        self._owners: Dict[str, str] = {}
        self._running = False
        self._checking = False
        self._next: Optional[datetime.datetime] = None
        self._api_cache: Dict[str, Any] = {}
        self.server.register_endpoint(
            "/server/pushraker_updates/check", ["POST"], self._ep_check)
        self.server.register_endpoint(
            "/server/pushraker_updates/status", ["GET"], self._ep_status)
        # The settings page's System section
        self.server.register_endpoint(
            "/server/pushraker_updates/settings", ["GET", "POST"],
            self._ep_settings)
        # The settings page shows this beside pushraker's version, so it's
        # clear whether pushraker or the add-ons changed
        self.server.register_endpoint(
            "/server/pushraker_addons/info", ["GET"], self._ep_addons)
        # Mainsail's sidebar link: a page (not JSON) that sends the browser
        # on to the settings page
        try:
            self.server.register_endpoint(
                "/server/pushraker_addons/page", ["GET"], self._ep_page,
                transports=["http"], wrap_result=False,
                content_type="text/html; charset=UTF-8",
                auth_required=False)
        except TypeError:
            logging.info(f"{LOG} this Moonraker can't serve the sidebar "
                         "link's page")
            self.sidebar_link = False
        self.server.register_remote_method(
            "pushraker_check_updates", self._remote_check)

    @staticmethod
    def _template(config: Any, key: str) -> str:
        if config.get(key, None) is None:
            return ""
        try:
            return str(config.gettemplate(key).render()).strip()
        except Exception as e:
            raise config.error(f"[{config.get_name()}] {key}: {e}")

    def _apply_settings(self) -> None:
        st = self.settings
        self.enabled = st["enabled"]
        self.times = parse_times(st["check_time"])
        self.days = parse_days(st["check_days"])
        self.timezone: Optional[datetime.tzinfo] = None
        if st["timezone"]:
            from zoneinfo import ZoneInfo
            self.timezone = ZoneInfo(st["timezone"])
        self.repeat = st["notify"]
        self.ignore = set(st["ignore"])
        self.confirm = st["confirm"]
        self.other_services = st["other_services"]

    async def component_init(self) -> None:
        self._save()
        # Mainsail's sidebar link, or (sidebar_link: False) none
        try:
            changed = await self.eventloop.run_in_thread(
                sidebar_entry, self.config_dir, self.sidebar_link)
            if changed:
                logging.info(f"{LOG} {changed}")
        except Exception as e:
            logging.info(f"{LOG} Mainsail sidebar link not changed: {e}")
        if not self.enabled:
            logging.info(f"{LOG} update notices are off")
        self._spawn(self._schedule())
        if self.state.get("queue"):
            self._spawn(self._resume())
        self._reconcile()

    async def _ep_settings(self, web_request: Any) -> Dict[str, Any]:
        method = str(getattr(web_request.get_request_type(), "name", "")
                     or web_request.get_request_type()).upper()
        if method.endswith("POST"):
            args = dict(web_request.get_args() or {})
            try:
                new = normalize(dict(self.settings, **{
                    k: v for k, v in args.items() if k in self.defaults}))
            except ValueError as e:
                raise self.server.error(str(e), 400)
            self.settings = new
            self._apply_settings()
            # What differs from moonraker.conf's section is kept
            self.state["settings"] = {k: v for k, v in new.items()
                                      if v != normalize(self.defaults)[k]}
            self._save()
            # The answer shows the new next check (the scheduler, woken,
            # works out the same one)
            self._next = next_run(
                datetime.datetime.now(datetime.timezone.utc).astimezone(
                    self._tz()), self.times, self.days) \
                if self.enabled else None
            self._wake.set()
            self._reconcile()
            logging.info(f"{LOG} settings saved from the page: {new}")
        entries: List[str] = []
        try:
            status = await self._status(refresh=False)
            entries = sorted((status.get("version_info") or {}).keys(),
                             key=str.lower)
        except Exception:
            pass
        tz = self._tz()
        return {
            "settings": self.settings,
            "defaults": normalize(self.defaults),
            "entries": entries,
            "next_check": self._next.isoformat()
            if self._next and self.enabled else None,
            "last_check": self.state.get("last_check"),
            "timezone": str(getattr(tz, "key", "") or tz or "") or
            time.strftime("%Z"),
            "updates": len((self.state.get("notice") or {}).get("items")
                           or []),
        }

    async def close(self) -> None:
        for gw in list(self._gateways.values()):
            gw.stop()
        for t in list(self._tasks):
            t.cancel()

    def _spawn(self, coro: Any) -> None:
        task = self.eventloop.create_task(coro)
        self._tasks.add(task)

        def done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logging.error(f"{LOG} task failed: {t.exception()}",
                              exc_info=t.exception())
        task.add_done_callback(done)

    # ---- state ----------------------------------------------------------

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.state_path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = self.state_path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, self.state_path)
        except OSError as e:
            logging.warning(f"{LOG} state not saved: {e}")

    # ---- Moonraker -------------------------------------------------------

    def _api(self, endpoint: str) -> Tuple[Any, Any]:
        """Moonraker's own definition of an endpoint, to call it in
        process (no HTTP, no login)."""
        if endpoint in self._api_cache:
            return self._api_cache[endpoint]
        for mod in list(sys.modules.values()):
            cls = getattr(mod, "APIDefinition", None)
            rtype = getattr(mod, "RequestType", None)
            cache = getattr(cls, "_cache", None)
            if isinstance(cache, dict) and rtype is not None and \
                    endpoint in cache:
                self._api_cache[endpoint] = (cache[endpoint], rtype)
                return self._api_cache[endpoint]
        raise self.server.error(f"{endpoint} not available (is "
                                "[update_manager] configured?)", 503)

    async def _call(self, endpoint: str, method: str,
                    args: Optional[Dict[str, Any]] = None) -> Any:
        api, rtype = self._api(endpoint)
        return await api.request(dict(args or {}), getattr(rtype, method))

    async def _status(self, refresh: bool) -> Dict[str, Any]:
        if refresh:
            return await self._call("/machine/update/refresh", "POST")
        return await self._call("/machine/update/status", "GET")

    def _printing(self) -> bool:
        try:
            return bool(self.server.lookup_component(
                "klippy_connection").is_printing())
        except Exception:
            return False

    def _push(self) -> Any:
        try:
            return self.server.lookup_component("pushraker")
        except Exception:
            return None

    def _name(self) -> str:
        return str(getattr(self._push(), "name", "") or "Printer")

    def _tz(self) -> Optional[datetime.tzinfo]:
        """This section's timezone, else pushraker's, else the printer's
        (None: astimezone() then uses the system's)."""
        if self.timezone is not None:
            return self.timezone
        tz = getattr(self._push(), "tz", None)
        return tz if isinstance(tz, datetime.tzinfo) else None

    def _host(self) -> str:
        web = str(getattr(self._push(), "web_ui_url", "") or "")
        host = urllib.parse.urlparse(web).hostname if web else ""
        return host or local_address()

    def _port(self) -> str:
        if self.web_port:
            return self.web_port if self.web_port.startswith(":")                 else ":" + self.web_port
        return pages_port()

    def _page(self) -> str:
        return self.page_url.replace("{host}", self._host()).replace(
            "{port}", self._port())

    def _web_ui(self) -> str:
        web = str(getattr(self._push(), "web_ui_url", "") or "")
        if web.startswith(("http://", "https://")):
            return web
        return self.web_ui_url.replace("{host}", self._host())

    # ---- where the card goes --------------------------------------------

    def _destinations(self) -> Dict[str, Any]:
        """{"bots": {key: {...}}, "hooks": [...], "others": [...]} from
        pushraker's settings and this section."""
        bots: Dict[str, Dict[str, Any]] = {}
        hooks: Dict[str, Dict[str, Any]] = {}
        others: Dict[str, Any] = {}

        def bot(token: str) -> Dict[str, Any]:
            k = key_of(token)
            if k not in bots:
                bots[k] = {"key": k, "token": token, "targets": [],
                           "allowed": set(self.allowed)}
            return bots[k]

        rows = getattr(self._push(), "rows", None) or {}
        for _rid, row in list(rows.items()):
            try:
                dests = row[1]
            except (IndexError, TypeError):
                continue
            for dest in dests:
                for srv in getattr(dest, "ap", None) or []:
                    meta = getattr(srv, "_default_args", None) or {}
                    schema = str(meta.get("schema") or "")
                    if schema == "discorddm":
                        token = str(meta.get("user") or "")
                        user = str(meta.get("host") or "")
                        if token and RE_ID.match(user):
                            b = bot(token)
                            if ("dm", user) not in b["targets"]:
                                b["targets"].append(("dm", user))
                            b["allowed"].add(user)
                    elif schema == "discordhook":
                        hid = str(meta.get("host") or "")
                        tok = str(meta.get("fullpath") or
                                  meta.get("path") or "").strip("/")
                        qsd = meta.get("qsd") or {}
                        if hid and tok:
                            hooks.setdefault(hid, {
                                "id": hid, "token": tok,
                                "name": str(qsd.get("name") or "")})
                    else:
                        try:
                            url = srv.url(privacy=False)
                        except Exception:
                            continue
                        others.setdefault(url, srv)
        if self.bot_token:
            b = bot(self.bot_token)
            target = ("channel", self.bot_channel) \
                if RE_ID.match(self.bot_channel) else ("dm", "owner")
            if target not in b["targets"]:
                b["targets"].append(target)
        return {"bots": bots, "hooks": list(hooks.values()),
                "others": list(others.values())}

    # ---- schedule --------------------------------------------------------

    async def _nap(self, seconds: float) -> bool:
        """Sleep; True if the settings changed meanwhile."""
        try:
            await asyncio.wait_for(self._wake.wait(), seconds)
        except asyncio.TimeoutError:
            return False
        self._wake.clear()
        return True

    async def _schedule(self) -> None:
        while True:
            if not self.enabled:
                self._next = None
                await self._nap(3600.)
                continue
            # The wait starts now; the next run is worked out again at
            # every wake, in the time zone of that moment (pushraker reads
            # its settings, time zone included, after this starts)
            after = datetime.datetime.now(datetime.timezone.utc)
            shown = None
            changed = False
            while True:
                tz = self._tz()
                self._next = next_run(after.astimezone(tz), self.times,
                                      self.days)
                if self._next != shown:
                    shown = self._next
                    logging.info(f"{LOG} next check "
                                 f"{self._next.isoformat()}")
                left = (self._next - datetime.datetime.now(
                    datetime.timezone.utc)).total_seconds()
                if left <= 0:
                    break
                if await self._nap(min(left, 300.)):
                    changed = True
                    break
            if changed or not self.enabled:
                continue
            started = time.time()
            while True:
                try:
                    await self.check(force=self.repeat == "always")
                    break
                except Busy as e:
                    if time.time() - started > BUSY_GIVE_UP:
                        logging.info(f"{LOG} still busy ({e}); skipping "
                                     "this check")
                        break
                    logging.info(f"{LOG} {e}; trying again in "
                                 f"{BUSY_RETRY / 60:.0f} min")
                    await asyncio.sleep(BUSY_RETRY)
                except Exception:
                    logging.exception(f"{LOG} check failed")
                    break

    async def _remote_check(self, force: Any = True) -> None:
        self._spawn(self._check_logged(str(force).lower()
                                       not in ("0", "false", "no")))

    async def _check_logged(self, force: bool) -> None:
        try:
            result = await self.check(force=force)
            logging.info(f"{LOG} check: {result}")
        except Exception as e:
            logging.warning(f"{LOG} check failed: {e}")

    async def _ep_check(self, web_request: Any) -> Dict[str, Any]:
        force = web_request.get_boolean("force", True)
        try:
            return await self.check(force=force)
        except Busy as e:
            raise self.server.error(str(e), 503)

    async def _ep_page(self, web_request: Any) -> str:
        to = web_request.get_str("to", "settings") \
            if hasattr(web_request, "get_str") else "settings"
        url = self.page_url if to == "updates" else self.settings_url
        return REDIRECT % json.dumps(url.replace("{port}", self._port()))

    async def _ep_addons(self, web_request: Any) -> Dict[str, Any]:
        version, name, url = "", self.addons_entry, self.addons_url
        try:
            status = await self._status(refresh=False)
            vi = status.get("version_info") or {}
            if not name:
                name = next((n for n in ADDON_REPOS if n in vi), "")
                url = ADDON_REPOS.get(name, url)
            version = addons_version(str((vi.get(name) or {}).get(
                "version") or ""))
        except Exception:
            pass
        if not version:
            # Moonraker says "?" on a branch without a tracking remote (a
            # test branch): git describe in the plugin's own folder
            version = await self.eventloop.run_in_thread(git_version)
        return {"name": name, "version": version, "url": url}

    async def _ep_status(self, web_request: Any) -> Dict[str, Any]:
        notice = self.state.get("notice") or {}
        return {
            "enabled": self.enabled,
            "next_check": self._next.isoformat() if self._next else None,
            "last_check": self.state.get("last_check"),
            "updates": notice.get("items", []),
            "problems": notice.get("problems", []),
            "queue": self.state.get("queue"),
            "gateways": {k: g.state for k, g in self._gateways.items()},
            "page_url": self._page(),
        }

    # ---- the check -------------------------------------------------------

    async def check(self, force: bool = False) -> Dict[str, Any]:
        if self._checking:
            return {"sent": False, "why": "a check is running"}
        if self._running:
            raise Busy("an update is running")
        if self._printing():
            raise Busy("the printer is printing")
        self._checking = True
        try:
            try:
                status = await self._status(refresh=True)
            except Exception as e:
                if getattr(e, "status_code", 0) == 503 or \
                        "busy" in str(e).lower():
                    raise Busy(f"Moonraker is busy ({e})")
                raise
            updates, problems = pending(
                status.get("version_info") or {}, self.ignore)
            self.state["last_check"] = datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds")
            sig = signature(updates)
            old = (self.state.get("notice") or {}).get("sig")
            if not updates:
                self.state["notice"] = None
                self._save()
                self._reconcile()
                return {"sent": False, "updates": [], "problems": problems,
                        "why": "up to date"}
            if sig == old and not force:
                self._save()
                return {"sent": False, "updates": updates,
                        "problems": problems,
                        "why": "already notified (notify: changes)"}
            sent = await self._send(updates, problems, sig)
            return {"sent": bool(sent), "to": sent, "updates": updates,
                    "problems": problems}
        finally:
            self._checking = False

    async def _send(self, updates: List[Dict[str, Any]],
                    problems: List[Dict[str, Any]],
                    sig: List[str]) -> List[str]:
        notice = {"nonce": secrets.token_hex(4), "items": updates,
                  "problems": problems, "sig": sig, "messages": [],
                  "sent": time.time()}
        dest = self._destinations()
        sent: List[str] = []
        page = self._page()
        for b in dest["bots"].values():
            for kind, target in b["targets"]:
                channel = await self._bot_channel(b, kind, target)
                if not channel:
                    continue
                payload = self._card(notice, mode="bot" if self.buttons
                                     else "none")
                msg = await self._discord(
                    "POST", f"/channels/{channel}/messages", b["token"],
                    payload)
                if msg and msg.get("id"):
                    notice["messages"].append({
                        "bot": b["key"], "channel": channel,
                        "message": str(msg["id"])})
                    sent.append(f"discord bot {kind}")
        use_hooks = self.webhooks == "always" or (
            self.webhooks == "auto" and not notice["messages"])
        if use_hooks:
            for h in dest["hooks"]:
                payload = self._card(notice, mode="link" if self.buttons
                                     else "none", page=page)
                if h["name"]:
                    payload["username"] = h["name"][:80]
                ok = await self._webhook(h, payload)
                if not ok and payload.get("components"):
                    # Discord refused the link buttons (an address it
                    # doesn't take): the links go in the text instead
                    payload = self._card(notice, mode="text", page=page)
                    if h["name"]:
                        payload["username"] = h["name"][:80]
                    ok = await self._webhook(h, payload)
                if ok:
                    sent.append("discord webhook")
        if self.other_services:
            body = self._text(notice, page)
            title = f"⬆️ {self._name()}: {len(updates)} update" \
                    f"{'s' if len(updates) != 1 else ''} available"
            for srv in dest["others"]:
                try:
                    ok = await self.eventloop.run_in_thread(
                        srv.notify, body, title)
                except Exception as e:
                    logging.info(f"{LOG} {srv.service_name}: {e}")
                    ok = False
                if ok:
                    sent.append(str(getattr(srv, "service_name", "other")))
        if not sent:
            logging.warning(f"{LOG} {len(updates)} update(s), but no "
                            "Discord destination took the notice (set "
                            "Discord on pushraker's page)")
        self.state["notice"] = notice
        self._save()
        self._reconcile()
        logging.info(f"{LOG} {len(updates)} update(s) sent to "
                     f"{', '.join(sent) or 'nobody'}")
        return sent

    # ---- the card --------------------------------------------------------

    def _card(self, notice: Dict[str, Any], mode: str, page: str = "",
              confirm: Optional[str] = None,
              progress: Optional[List[str]] = None,
              done: Optional[bool] = None) -> Dict[str, Any]:
        """The message, as one card (Discord's Components V2: a container
        with the colour bar, each entry a section with its button beside
        it, and a link to the printer's web UI at the bottom).

        mode: bot (interactive buttons), link (link buttons to the confirm
        page), none (no update buttons), text (the old embed with the links
        in its text, for a webhook that refuses the card)."""
        if mode == "text":
            return self._card_text(notice, page)
        items = notice.get("items") or []
        problems = notice.get("problems") or []
        nonce = notice.get("nonce", "")
        pid = self.state.get("printer", "")
        running = progress is not None and done is None
        n = len(items)
        title = f"⬆️ {n} update{'s' if n != 1 else ''} available"
        colour = COLOUR
        if running:
            title = "⏳ Updating…"
        elif progress is not None:
            title = "✅ Update done" if done else "⚠️ Update failed"
            colour = COLOUR_DONE if done else COLOUR_FAIL
        inner: List[Dict[str, Any]] = [_td(f"### {title}")]
        if progress:
            inner.append(_td("\n".join(progress)))
        if confirm is not None:
            i = items[int(confirm)]
            inner.append(_td(
                f"Update **{escape(i['name'])}** on "
                f"{escape(self._name())}?\n{escape(i['text'])}"
                + ("\nMoonraker or the printer will restart."
                   if order(i) >= 3 else "")))
            inner.append(_row([
                {"type": 2, "style": 3,
                 "label": label(f"Yes, update {i['name']}"),
                 "custom_id": f"pru:{pid}:{nonce}:y:{confirm}"},
                {"type": 2, "style": 2, "label": "Cancel",
                 "custom_id": f"pru:{pid}:{nonce}:n:-"}]))
        elif items and not running:
            if progress is not None:
                inner.append(SEPARATOR)
                inner.append(_td("**Still available:**"))
            shown = items[:SECTIONS_MAX]
            for idx, i in enumerate(shown):
                body = _td(f"**{escape(i['name'])}**\n"
                           f"{escape(i['text'])[:300]}")
                button = self._button(mode, i, idx, pid, nonce, page)
                inner.append({"type": 9, "components": [body],
                              "accessory": button} if button else body)
            if n > len(shown):
                inner.append(_td(f"…and {n - len(shown)} more in the "
                                 "web UI"))
        if problems and not running:
            inner.append(_td("\n".join(
                f"⚠️ **{escape(p['name'])}**  {escape(p['text'])} — "
                f"{p['why']}, update it by hand"
                for p in problems[:10])[:1000]))
        inner.append(SEPARATOR)
        now = int(time.time())
        inner.append(_td(f"-# {escape(self._name())} · <t:{now}:f>"))
        web = self._web_ui()
        if web and not running and confirm is None:
            inner.append(_row([{
                "type": 2, "style": 5,
                "label": label(f"Open {self._name()} in browser"),
                "url": web}]))
        return {"flags": COMPONENTS_V2,
                "components": [{"type": 17, "accent_color": colour,
                                "components": inner}],
                "allowed_mentions": {"parse": []}}

    def _button(self, mode: str, item: Dict[str, Any], idx: int, pid: str,
                nonce: str, page: str) -> Optional[Dict[str, Any]]:
        if mode == "bot":
            act = "u" if self.confirm else "y"
            return {"type": 2, "style": 1, "label": "Update",
                    "custom_id": f"pru:{pid}:{nonce}:{act}:{idx}"}
        if mode == "link" and page:
            return {"type": 2, "style": 5, "label": "Update",
                    "url": page_link(page, [item["name"]])}
        return None

    def _card_text(self, notice: Dict[str, Any], page: str
                   ) -> Dict[str, Any]:
        items = notice.get("items") or []
        lines = [f"**{escape(i['name'])}**  {escape(i['text'])}"
                 + (f"  [update]({page_link(page, [i['name']])})"
                    if page else "") for i in items]
        lines += [f"⚠️ **{escape(p['name'])}**  {escape(p['text'])} — "
                  f"{p['why']}, update it by hand"
                  for p in notice.get("problems") or []]
        web = self._web_ui()
        if web:
            lines.append(f"[Open {escape(self._name())} in browser]({web})")
        n = len(items)
        return {"embeds": [{
            "title": f"⬆️ {n} update{'s' if n != 1 else ''} available",
            "description": "\n".join(lines)[:TEXT_MAX] or "—",
            "color": COLOUR, "footer": {"text": self._name()},
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.000Z")}],
            "allowed_mentions": {"parse": []}}

    def _text(self, notice: Dict[str, Any], page: str) -> str:
        lines = [f"{i['name']}: {i['text']}" for i in notice["items"]]
        for p in notice.get("problems") or []:
            lines.append(f"{p['name']}: {p['text']} ({p['why']})")
        web = self._web_ui()
        if web:
            lines.append(f"Web UI: {web}")
        return "\n".join(lines)

    # ---- Discord REST ----------------------------------------------------

    async def _http(self, method: str, url: str,
                    headers: Optional[Dict[str, str]] = None,
                    body: Optional[Dict[str, Any]] = None
                    ) -> Tuple[int, Any]:
        import requests  # pushraker's dependency, there on every printer

        def go() -> Tuple[int, Any]:
            try:
                r = requests.request(
                    method, url, json=body, timeout=TIMEOUT,
                    headers=dict(headers or {}, **{
                        "User-Agent": USER_AGENT}))
            except requests.RequestException as e:
                return 0, type(e).__name__
            try:
                return r.status_code, r.json() if r.content else None
            except ValueError:
                return r.status_code, r.text[:200]
        return await self.eventloop.run_in_thread(go)

    async def _discord(self, method: str, path: str, token: str,
                       body: Optional[Dict[str, Any]] = None
                       ) -> Optional[Dict[str, Any]]:
        code, data = await self._http(method, API + path,
                                      {"Authorization": f"Bot {token}"},
                                      body)
        if 200 <= code < 300:
            return data if isinstance(data, dict) else {}
        msg = data.get("message") if isinstance(data, dict) else data
        logging.warning(f"{LOG} Discord {method} "
                        f"{path.split('/')[1]}: HTTP {code} {msg}")
        return None

    async def _webhook(self, hook: Dict[str, Any],
                       payload: Dict[str, Any]) -> bool:
        url = (f"{API}/webhooks/{hook['id']}/{hook['token']}"
               "?wait=true&with_components=true")
        code, data = await self._http("POST", url, None, payload)
        if code in (200, 204):
            return True
        msg = data.get("message") if isinstance(data, dict) else data
        logging.warning(f"{LOG} Discord webhook: HTTP {code} {msg}")
        return False

    async def _owner(self, b: Dict[str, Any]) -> str:
        if b["key"] not in self._owners:
            app = await self._discord("GET", "/oauth2/applications/@me",
                                      b["token"])
            owner = ""
            if app:
                team = app.get("team") or {}
                owner = str(team.get("owner_user_id") or
                            (app.get("owner") or {}).get("id") or "")
            if owner:
                self._owners[b["key"]] = owner
            return owner
        return self._owners[b["key"]]

    async def _bot_channel(self, b: Dict[str, Any], kind: str,
                           target: str) -> str:
        if kind == "channel":
            return target
        user = await self._owner(b) if target == "owner" else target
        if not user:
            return ""
        ch = await self._discord("POST", "/users/@me/channels", b["token"],
                                 {"recipient_id": user})
        return str((ch or {}).get("id") or "")

    async def _allowed(self, b: Dict[str, Any]) -> set:
        allowed = set(b["allowed"])
        owner = await self._owner(b)
        if owner:
            allowed.add(owner)
        return allowed

    # ---- Gateway (button clicks) -----------------------------------------

    def _reconcile(self) -> None:
        """Keep a Gateway open for each bot with buttons out (a notice or
        a running list); close the others."""
        want: Dict[str, str] = {}
        if self.enabled and self.buttons:
            refs = list((self.state.get("notice") or {}).get("messages")
                        or [])
            q = self.state.get("queue")
            if q and q.get("message"):
                refs.append(q["message"])
            bots = self._destinations()["bots"] if refs else {}
            for r in refs:
                b = bots.get(r.get("bot"))
                if b:
                    want[b["key"]] = b["token"]
        for k in list(self._gateways):
            if k not in want or self._gateways[k].token != want[k]:
                self._gateways.pop(k).stop()
        for k, token in want.items():
            if k not in self._gateways:
                gw = Gateway(self, k, token)
                self._gateways[k] = gw
                self._spawn(gw.run())

    async def _respond(self, d: Dict[str, Any], kind: int,
                       data: Dict[str, Any]) -> None:
        url = f"{API}/interactions/{d['id']}/{d['token']}/callback"
        code, body = await self._http("POST", url, None,
                                      {"type": kind, "data": data})
        if code not in (200, 204):
            logging.warning(f"{LOG} interaction reply: HTTP {code} {body}")

    async def _say(self, d: Dict[str, Any], text: str) -> None:
        await self._respond(d, REPLY, {"content": text,
                                       "flags": EPHEMERAL,
                                       "allowed_mentions": {"parse": []}})

    async def on_interaction(self, gw: "Gateway", d: Dict[str, Any]) -> None:
        if d.get("type") != 3:
            return
        cid = str((d.get("data") or {}).get("custom_id") or "")
        parts = cid.split(":")
        if len(parts) != 5 or parts[0] != "pru":
            return
        if parts[1] != self.state.get("printer"):
            # Another printer's card (one bot can serve several printers)
            return
        _, _, nonce, act, which = parts
        user = (d.get("member") or {}).get("user") or d.get("user") or {}
        uid = str(user.get("id") or "")
        b = self._destinations()["bots"].get(gw.key)
        if b is None or uid not in await self._allowed(b):
            logging.info(f"{LOG} click from user {uid} refused")
            await self._say(d, "Only the printer's owner can start "
                               "updates.")
            return
        notice = self.state.get("notice") or {}
        if nonce != notice.get("nonce"):
            await self._say(d, "This update notice is out of date; use "
                               "the newest one.")
            return
        items = notice.get("items") or []
        if which != "-" and not (
                which.isdigit() and int(which) < len(items)):
            # "*" was Update all on the first cards: not offered remotely
            await self._say(d, "Unknown update.")
            return
        if act == "n":
            await self._respond(d, PONG_UPDATE, self._card(notice, "bot"))
            return
        if self._running or self.state.get("queue"):
            await self._say(d, "An update is already running.")
            return
        if act == "u":
            await self._respond(d, PONG_UPDATE,
                                self._card(notice, "bot", confirm=which))
            return
        if act != "y":
            return
        if self._printing():
            await self._say(d, "The printer is printing; updates are "
                               "refused until it's done.")
            return
        names = [items[int(which)]["name"]]
        ref = {"bot": gw.key, "channel": str(d.get("channel_id") or ""),
               "message": str((d.get("message") or {}).get("id") or "")}
        self.state["queue"] = {"names": names, "results": [],
                               "current": None, "message": ref,
                               "user": uid}
        self._save()
        await self._respond(d, PONG_UPDATE, self._card(
            notice, "bot", progress=[f"⏳ {escape(n)}" for n in names]))
        logging.info(f"{LOG} user {uid} started updates: {names}")
        self._spawn(self._run_queue())

    # ---- running updates -------------------------------------------------

    async def _resume(self) -> None:
        """Moonraker restarted (its own update, or the OS's) with a list
        still to run."""
        deadline = time.time() + RESUME_WAIT
        while time.time() < deadline:
            try:
                status = await self._status(refresh=False)
                if not status.get("busy"):
                    break
            except Exception:
                pass
            await asyncio.sleep(5.)
        logging.info(f"{LOG} continuing the update list after a restart")
        await self._run_queue()

    async def _run_queue(self) -> None:
        if self._running:
            return
        self._running = True
        try:
            await self._run_queue_inner()
        finally:
            self._running = False

    async def _version_line(self, name: str, wanted: str,
                            error: str = "") -> Tuple[bool, str]:
        try:
            status = await self._status(refresh=False)
            info = (status.get("version_info") or {}).get(name) or {}
        except Exception:
            info = {}
        left, _ = pending({name: info} if info else {})
        if error:
            return False, f"❌ **{escape(name)}**: {escape(error)[:300]}"
        if info and not left:
            v = info.get("version") or ""
            if name == "system" or not v:
                return True, f"✅ **{escape(name)}** updated"
            return True, f"✅ **{escape(name)}** {escape(str(v))}"
        if not info:
            return True, f"✅ **{escape(name)}** done"
        return False, (f"⚠️ **{escape(name)}** still "
                       f"{escape(left[0]['text'])}")

    async def _run_queue_inner(self) -> None:
        q = self.state.get("queue")
        if not q:
            return
        notice = self.state.get("notice") or {}
        wanted = {i["name"]: i["text"] for i in notice.get("items") or []}
        if q.get("current"):
            # It was running when Moonraker went away: its version says how
            # it went
            name = q["current"]
            ok, line = await self._version_line(name, wanted.get(name, ""))
            q["results"].append({"name": name, "ok": ok, "line": line})
            q["current"] = None
            self._save()
        while q["names"]:
            name = q["names"][0]
            if self._printing():
                for n in q["names"]:
                    q["results"].append({
                        "name": n, "ok": False,
                        "line": f"⏸️ **{escape(n)}** skipped: printing"})
                q["names"] = []
                break
            q["names"].pop(0)
            q["current"] = name
            self._save()
            await self._edit_progress(q, notice)
            error = ""
            try:
                await self._call("/machine/update/upgrade", "POST",
                                 {"name": name})
            except Exception as e:
                error = str(e) or type(e).__name__
            ok, line = await self._version_line(name, wanted.get(name, ""),
                                                error)
            q["results"].append({"name": name, "ok": ok, "line": line})
            q["current"] = None
            self._save()
            logging.info(f"{LOG} {name}: {'ok' if ok else 'failed'}")
        # Done: the card shows the results and what is still available,
        # with new buttons (a new nonce: the old ones are out of date)
        try:
            status = await self._status(refresh=False)
            updates, problems = pending(status.get("version_info") or {},
                                        self.ignore)
        except Exception:
            updates, problems = [], notice.get("problems") or []
        new = {"nonce": secrets.token_hex(4), "items": updates,
               "problems": problems, "sig": signature(updates),
               "messages": [q["message"]] if q.get("message") else [],
               "sent": time.time()}
        all_ok = all(r["ok"] for r in q["results"])
        self.state["notice"] = new if updates else None
        self.state["queue"] = None
        self._save()
        await self._edit(q.get("message"), self._card(
            new, "bot", progress=[r["line"] for r in q["results"]],
            done=all_ok))
        self._reconcile()

    async def _edit_progress(self, q: Dict[str, Any],
                             notice: Dict[str, Any]) -> None:
        lines = [r["line"] for r in q["results"]]
        if q.get("current"):
            lines.append(f"⏳ **{escape(q['current'])}** updating…")
        lines += [f"▫️ {escape(n)}" for n in q["names"]]
        await self._edit(q.get("message"), self._card(
            notice, "bot", progress=lines))

    async def _edit(self, ref: Optional[Dict[str, Any]],
                    payload: Dict[str, Any]) -> None:
        if not ref or not ref.get("channel") or not ref.get("message"):
            return
        b = self._destinations()["bots"].get(ref.get("bot"))
        if b is None:
            logging.info(f"{LOG} the bot that sent the card is no longer "
                         "set; card not updated")
            return
        await self._discord(
            "PATCH", f"/channels/{ref['channel']}/messages/{ref['message']}",
            b["token"], payload)


class Busy(Exception):
    pass


class Gateway:
    """One bot's Discord Gateway session, for the button clicks
    (INTERACTION_CREATE comes with no intents)."""

    def __init__(self, owner: PushrakerUpdates, key: str,
                 token: str) -> None:
        self.owner = owner
        self.key = key
        self.token = token
        self.seq: Optional[int] = None
        self.session: Optional[str] = None
        self.resume_url: Optional[str] = None
        self.acked = True
        self.ws: Any = None
        self.stopped = False
        self.state = "connecting"

    def stop(self) -> None:
        self.stopped = True
        if self.ws is not None:
            try:
                self.ws.close(1000)
            except Exception:
                pass

    async def run(self) -> None:
        backoff = 5.
        while not self.stopped:
            try:
                await self._session()
                backoff = 5.
            except FatalGateway as e:
                self.state = f"stopped: {e}"
                logging.warning(f"{LOG} Discord Gateway: {e}; buttons "
                                "won't answer until the settings change")
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.state = f"retrying: {type(e).__name__}"
                logging.info(f"{LOG} Discord Gateway: {e!r}; again in "
                             f"{backoff:.0f} s")
            if self.stopped:
                break
            await asyncio.sleep(backoff + random.random() * 2)
            backoff = min(backoff * 2, 300.)
        self.state = "closed"

    def _send(self, op: int, d: Any) -> None:
        self.ws.write_message(json.dumps({"op": op, "d": d}))

    async def _session(self) -> None:
        from tornado.httpclient import HTTPRequest
        from tornado.websocket import websocket_connect
        ca = None
        try:
            import certifi
            ca = certifi.where()
        except Exception:
            pass
        base = self.resume_url if self.session else GATEWAY
        req = HTTPRequest(f"{base}/?v=10&encoding=json",
                          headers={"User-Agent": USER_AGENT},
                          ca_certs=ca, connect_timeout=20.,
                          request_timeout=30.)
        self.ws = await websocket_connect(req, max_message_size=16 << 20)
        hb: Optional[asyncio.Task] = None
        try:
            hello = json.loads(await self.ws.read_message() or "{}")
            if hello.get("op") != 10:
                raise RuntimeError("no HELLO")
            interval = float(hello["d"]["heartbeat_interval"]) / 1000.
            if self.session:
                self._send(6, {"token": self.token,
                               "session_id": self.session, "seq": self.seq})
            else:
                self._send(2, {"token": self.token, "intents": 0,
                               "properties": {"os": "linux",
                                              "browser": "pushraker",
                                              "device": "pushraker"}})
            self.acked = True
            hb = asyncio.ensure_future(self._heartbeat(interval))
            while True:
                msg = await self.ws.read_message()
                if msg is None:
                    code = self.ws.close_code
                    if code in FATAL_CLOSE:
                        raise FatalGateway(FATAL_CLOSE[code])
                    if code in (4007, 4009):
                        self.session = None
                    self.state = f"reconnecting (close {code})"
                    return
                p = json.loads(msg)
                if p.get("s") is not None:
                    self.seq = p["s"]
                op = p.get("op")
                if op == 0:
                    t = p.get("t")
                    if t == "READY":
                        self.session = p["d"].get("session_id")
                        self.resume_url = p["d"].get("resume_gateway_url")
                        self.state = "connected"
                        logging.info(f"{LOG} Discord Gateway connected; "
                                     "update buttons answer")
                    elif t == "RESUMED":
                        self.state = "connected"
                    elif t == "INTERACTION_CREATE":
                        self.owner._spawn(
                            self.owner.on_interaction(self, p["d"]))
                elif op == 1:
                    self._send(1, self.seq)
                elif op == 7:
                    self.state = "reconnecting"
                    return
                elif op == 9:
                    if not p.get("d"):
                        self.session = None
                    await asyncio.sleep(1 + random.random() * 4)
                    return
                elif op == 11:
                    self.acked = True
        finally:
            if hb is not None:
                hb.cancel()
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    async def _heartbeat(self, interval: float) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not self.acked:
                # No ACK since the last beat: a dead connection
                self.ws.close(4000)
                return
            self.acked = False
            self._send(1, self.seq)
            await asyncio.sleep(interval)


def load_component(config: Any) -> PushrakerUpdates:
    return PushrakerUpdates(config)
