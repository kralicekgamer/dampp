#!/usr/bin/env python3
"""dampp - XAMPP for Linux: a TUI over docker compose (MariaDB, nginx, PHP, phpMyAdmin)."""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

HERE = Path(__file__).resolve().parent
# settings and the web root live outside the app folder so a reinstall cannot delete them
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dampp" / "config.env"

DEFAULTS = {
    "MARIADB_TAG": "latest",
    "MARIADB_PORT": "3306",
    "MARIADB_ROOT_PASSWORD": "root",
    "NGINX_TAG": "latest",
    "NGINX_PORT": "80",
    "PHP_TAG": "fpm",
    "PMA_TAG": "latest",
    "PMA_PORT": "8080",
    "WEB_ROOT": str(Path.home() / "dampp" / "www"),
}
# service -> (version key, port key)
SERVICES = {
    "mariadb": ("MARIADB_TAG", "MARIADB_PORT"),
    "nginx": ("NGINX_TAG", "NGINX_PORT"),
    "php": ("PHP_TAG", None),
    "phpmyadmin": ("PMA_TAG", "PMA_PORT"),
}
# settings sections (key n): name -> [(key, label)]
SECTIONS = {
    "MariaDB": [("MARIADB_TAG", "Version"), ("MARIADB_PORT", "Port"), ("MARIADB_ROOT_PASSWORD", "Root password")],
    "nginx": [("NGINX_TAG", "Version"), ("NGINX_PORT", "Port"), ("WEB_ROOT", "Web root")],
    "PHP": [("PHP_TAG", "Version")],
    "phpMyAdmin": [("PMA_TAG", "Version"), ("PMA_PORT", "Port")],
}
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")  # valid docker image tag
PORTS = {"MARIADB_PORT": "MariaDB", "NGINX_PORT": "nginx", "PMA_PORT": "phpMyAdmin"}
LEVELS = {"INFO": "blue", "OK": "green", "WARN": "yellow", "ERROR": "red"}
LOG_TAIL = 20  # how many lines of history the l key shows
INDENT = 29  # width of the "time  LEVEL  service  " columns in the console
# common docker errors -> hint (matched against the lowercased command output)
HINTS = [
    (("address already in use", "port is already allocated"), "the port is already in use – change it in settings (n)"),
    (("manifest unknown", "manifest for", "not found: manifest"), "no such image version – check the version in settings (n)"),
    (("permission denied",), "no permission to use Docker: sudo usermod -aG docker $USER, then log out and back in"),
    (("cannot connect", "failed to connect"), "is Docker running? try: sudo systemctl start docker"),
    (("no such host", "timeout", "tls handshake"), "cannot reach the registry – check your internet connection"),
]
# services with a web interface -> port key (key o)
WEB = {"nginx": "NGINX_PORT", "phpmyadmin": "PMA_PORT"}
# help (key h): group -> [(keys, description)]; lowercase = selected service, uppercase = all
HELP = {
    "Selection": [
        ("↑ ↓  k j", "previous / next service"),
        ("1 2 3 4", "jump to the first to fourth service"),
    ],
    "Selected service": [
        ("Enter  Space", "pull → start → stop"),
        ("s", "start"),
        ("x", "stop"),
        ("r", "restart"),
        ("p", "pull image"),
        ("l", "logs on / off"),
        ("o", "open in the browser (nginx, phpmyadmin)"),
    ],
    "All services": [
        ("S", "start all pulled"),
        ("X", "stop all"),
        ("R", "restart all running"),
        ("P", "pull all missing"),
    ],
    "Application": [
        ("n  F2", "settings"),
        ("c", "clear the output"),
        ("PgUp PgDn", "scroll the output"),
        ("h  ?  F1", "this help"),
        ("d", "quit, leave containers running"),
        ("q", "quit and stop containers"),
    ],
}
ACTIONS = {
    "start": ("starting…", [["up", "-d"]]),
    "stop": ("stopping…", [["stop"]]),
    # stop + up instead of `restart` so changed settings take effect
    "restart": ("restarting…", [["stop"], ["up", "-d"]]),
    "pull": ("pulling…", [["pull"]]),
}


def find_runtime() -> tuple[list[str], str] | None:
    """Return (compose command, runtime binary) or None."""
    for rt in ("docker", "podman"):
        if shutil.which(rt) and subprocess.run([rt, "compose", "version"], capture_output=True).returncode == 0:
            return [rt, "compose"], rt
    if shutil.which("docker-compose") and shutil.which("docker"):
        return ["docker-compose"], "docker"
    return None


def load_env() -> dict[str, str]:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() in cfg and value.strip():
                cfg[key.strip()] = value.strip()
    return cfg


def save_env(cfg: dict[str, str]) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text("".join(f"{k}={v}\n" for k, v in cfg.items()))
    CONFIG.chmod(0o600)  # it holds the database password


def ensure_web_root(cfg: dict[str, str]) -> None:
    """Create the web root (docker would create it as root) and put the landing page into an empty one."""
    root = Path(cfg["WEB_ROOT"])
    root.mkdir(parents=True, exist_ok=True)
    landing = HERE / "www" / "index.php"
    if landing.exists() and not any(root.iterdir()):
        shutil.copy(landing, root / "index.php")


async def run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, (out if proc.returncode == 0 else err).decode(errors="replace")


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


async def stream(*args: str, on_line) -> int:
    """Run a command and pass each output line to on_line. Terminates the process if cancelled."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        async for line in proc.stdout:
            on_line(ANSI.sub("", line.decode(errors="replace")).rstrip().rsplit("\r", 1)[-1])
        return await proc.wait()
    finally:
        if proc.returncode is None:
            proc.terminate()


class Settings(ModalScreen[dict | None]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("down", "app.focus_next", "Next field"),
        ("up", "app.focus_previous", "Previous field"),
    ]

    def __init__(self, cfg: dict[str, str]) -> None:
        super().__init__()
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        dialog = Vertical(id="dialog")
        dialog.border_title = "Settings"
        with dialog:
            for name, fields in SECTIONS.items():
                section = Vertical(classes="section")
                section.border_title = name
                with section:
                    for key, label in fields:
                        with Horizontal(classes="field"):
                            yield Label(label)
                            yield Input(self.cfg[key], id=key.lower(), compact=True)
            yield Label("", id="error")
            with Horizontal(id="buttons"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.save()

    def on_input_submitted(self) -> None:
        self.save()

    def save(self) -> None:
        cfg = {key: self.query_one(f"#{key.lower()}", Input).value.strip() or DEFAULTS[key] for key in DEFAULTS}
        error = self.query_one("#error", Label)
        used: dict[str, str] = {}  # port -> service
        for key, name in PORTS.items():
            if not (cfg[key].isdigit() and 0 < int(cfg[key]) < 65536):
                error.update(f"[red]✗ {name}: port must be a number 1–65535[/]")
                return
            if cfg[key] in used:
                error.update(f"[red]✗ {name} and {used[cfg[key]]} cannot share port {cfg[key]}[/]")
                return
            used[cfg[key]] = name
        for name, (tag_key, _) in SERVICES.items():
            if not TAG.fullmatch(cfg[tag_key]):
                error.update(f"[red]✗ {name}: invalid version (letters, digits, dot and dash only)[/]")
                return
        # the php image must be an fpm variant: "8.4" -> "8.4-fpm", "latest" -> "fpm"
        if "fpm" not in cfg["PHP_TAG"]:
            cfg["PHP_TAG"] = "fpm" if cfg["PHP_TAG"] == "latest" else cfg["PHP_TAG"] + "-fpm"
        # a relative path is taken from the home directory, not the app folder
        cfg["WEB_ROOT"] = str(Path.home() / Path(cfg["WEB_ROOT"]).expanduser())
        self.dismiss(cfg)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Help(ModalScreen[None]):
    BINDINGS = [("escape,h,q,question_mark,f1", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        text = Text()
        for group, keys in HELP.items():
            text.append(f"{group}\n", "bold")
            for key, description in keys:
                text.append(f"  {key:<17}", "bold yellow")
                text.append(f"{description}\n")
            text.append("\n")
        text.append("h / Esc = close", "dim")
        dialog = VerticalScroll(Static(text), id="help")
        dialog.border_title = "Keys"
        yield dialog


class Dampp(App):
    TITLE = "dampp"
    ENABLE_COMMAND_PALETTE = False
    SUB_TITLE = "MariaDB · nginx · PHP · phpMyAdmin"
    CSS = """
    .row { height: 3; margin: 1 2 0 2; padding: 0 2; background: $surface; border-left: thick $surface; }
    .row.selected { background: $boost; border-left: thick $accent; }
    .row Label { height: 3; content-align: left middle; margin-right: 2; }
    .name { width: 12; text-style: bold; }
    .image { width: 22; color: $text-muted; }
    .port { width: 7; color: $text-muted; }
    .status { width: 16; }
    .row Button { min-width: 11; margin-left: 2; }
    .row .restart { background: $surface-lighten-3; }
    .row .restart:hover { background: $surface-lighten-2; }
    RichLog { height: 1fr; margin: 1 2; padding: 0 1; border: round $primary; border-title-color: $text-muted; }
    Settings, Help { align: center middle; }
    #help { width: 66; height: auto; max-height: 100%; border: thick $primary; border-title-style: bold; background: $surface; padding: 1 2; }
    #dialog { width: 60; height: auto; max-height: 100%; overflow-y: auto; border: thick $primary; border-title-style: bold; background: $surface; padding: 1 2 0 2; }
    .section { height: auto; padding: 0 1; border: round $primary-darken-1; border-title-color: $accent; border-title-style: bold; }
    .field { height: 1; }
    .field Label { width: 14; color: $text-muted; }
    .field Input { width: 1fr; }
    #error { height: 1; }
    #buttons { height: 3; align: right middle; }
    #buttons Button { margin-left: 1; }
    """
    # the footer shows only the most used keys; help (h) lists all of them
    BINDINGS = [
        Binding("enter,space", "smart", "Action", key_display="Enter"),
        Binding("s,a", "service('start')", "Start"),
        Binding("x", "service('stop')", "Stop"),
        Binding("r", "service('restart')", "Restart"),
        Binding("l", "logs", "Logs"),
        Binding("n,f2", "settings", "Settings"),
        Binding("h,question_mark,f1", "help", "Help"),
        Binding("q", "quit", "Quit"),
        Binding("up,k", "move(-1)", show=False),
        Binding("down,j", "move(1)", show=False),
        *(Binding(str(i + 1), f"jump({i})", show=False) for i in range(len(SERVICES))),
        Binding("p", "service('pull')", show=False),
        Binding("o", "open", show=False),
        Binding("S", "all('start')", show=False),
        Binding("X", "all('stop')", show=False),
        Binding("R", "all('restart')", show=False),
        Binding("P", "all('pull')", show=False),
        Binding("c", "clear", show=False),
        Binding("pageup", "scroll_log(-1)", show=False),
        Binding("pagedown", "scroll_log(1)", show=False),
        Binding("d", "detach", show=False),
    ]

    def __init__(self, compose_cmd: list[str], runtime: str) -> None:
        super().__init__()
        self.compose_cmd = [*compose_cmd, "--project-directory", str(HERE)]
        self.runtime = runtime
        self.cfg = load_env()
        os.environ.update(self.cfg)  # compose.yaml reads versions and ports from the environment
        self.selected = 0
        self.state: dict[str, str] = {}  # service -> container state from `compose ps`
        self.pulled: dict[str, bool] = {}  # service -> is the image pulled
        self.busy: dict[str, str] = {}  # service -> label of the running action
        self.log_svc: str | None = None
        self.announced = False
        self.docker_error: str | None = None  # last docker error printed
        self.quitting = False

    def compose(self) -> ComposeResult:
        yield Header()
        for svc in SERVICES:
            with Horizontal(id=f"row-{svc}", classes="row"):
                yield Label(svc, classes="name")
                yield Label(classes="image")
                yield Label(classes="port")
                yield Label(classes="status")
                for action, variant in (("pull", "warning"), ("start", "success"), ("stop", "error"), ("restart", "default")):
                    button = Button(action.capitalize(), id=f"{action}-{svc}", variant=variant, classes=action)
                    button.can_focus = False  # mouse only; Enter belongs to the smart action, not a button
                    yield button
        out = RichLog(id="out")
        out.can_focus = False  # otherwise the console would steal the arrow keys
        yield out
        yield Footer()

    async def on_mount(self) -> None:
        self.out = self.query_one("#out", RichLog)
        self.out.border_title = "Output"
        self.render_rows()
        self.prepare_web_root()
        await self.refresh_state()
        self.set_interval(2, self.refresh_state)

    def prepare_web_root(self) -> None:
        try:
            ensure_web_root(self.cfg)
        except OSError as exc:
            self.error(f"cannot create web root {self.cfg['WEB_ROOT']}: {exc.strerror}", hint="change it in settings (n)")

    def check_action(self, action: str, parameters: tuple) -> bool:
        # while a dialog is open only its own controls (tab, focus) and quit work
        return len(self.screen_stack) == 1 or action in ("quit", "focus_next", "focus_previous")

    # --- console messages -------------------------------------------------

    def say(self, level: str, msg: str, svc: str = "dampp", hint: str | None = None) -> None:
        """A `time  LEVEL  service  text` line, optionally with a hint on the next line."""
        style = LEVELS[level]
        line = Text.assemble(
            (time.strftime("%H:%M:%S"), "dim"), "  ", (f"{level:<5}", f"bold {style}"), "  ",
            (f"{svc:<10}", "bold"), "  ", (msg, "" if level == "INFO" else style),
        )
        if hint:
            line.append(f"\n{'':{INDENT}}")
            line.append(f"→ {hint}", "dim")
        self.out.write(line)

    def info(self, msg: str, svc: str = "dampp") -> None:
        self.say("INFO", msg, svc)

    def ok(self, msg: str, svc: str = "dampp") -> None:
        self.say("OK", msg, svc)

    def warn(self, msg: str, svc: str = "dampp") -> None:
        self.say("WARN", msg, svc)

    def error(self, msg: str, svc: str = "dampp", hint: str | None = None) -> None:
        self.say("ERROR", msg, svc, hint)

    def raw(self, svc: str, line: str) -> None:
        """Raw docker output, indented so it is not mistaken for app messages."""
        self.out.write(Text.assemble(" " * 17, (f"{svc:<10}│ ", "dim"), line))

    # --- state ------------------------------------------------------------

    def image(self, svc: str) -> str:
        return f"{svc}:{self.cfg[SERVICES[svc][0]]}"

    async def refresh_state(self) -> None:
        rc, out = await run(*self.compose_cmd, "ps", "-a", "--format", "json")
        if rc != 0:
            message = (out.strip().splitlines() or ["compose failed"])[-1]
            # state refreshes every 2 s - print the same error only once
            if message != self.docker_error:
                self.docker_error = message
                if "permission denied" in out.lower():
                    self.error("no permission to use Docker", hint="sudo usermod -aG docker $USER, then log out "
                                                                   "and back in (do not run dampp with sudo)")
                else:
                    self.error("Docker is not available", hint="is the daemon running? try: sudo systemctl start docker")
                self.raw("docker", message)
            self.render_rows()
            return
        if self.docker_error:
            self.docker_error = None
            self.ok("Docker is available again")

        # compose returns either a JSON array or one object per line
        text = out.strip()
        try:
            items = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines() if l]
            state = {c["Service"]: c["State"] for c in items}
        except (ValueError, KeyError, TypeError):
            if self.docker_error != "format":
                self.docker_error = "format"
                self.error("cannot parse `compose ps` output", hint="a newer Docker Compose (v2) is required")
            self.render_rows()
            return
        # a service that was running and stopped without our action (crash, stopped or removed externally)
        codes = {c["Service"]: c.get("ExitCode", "?") for c in items}
        for svc, old in self.state.items():
            now = state.get(svc, "removed")
            if old == "running" and now != "running" and svc not in self.busy and not self.quitting:
                self.warn(f"stopped on its own (state {now}, exit code {codes.get(svc, '?')}) – logs: l", svc)
        self.state = state

        rc, out = await run(self.runtime, "images", "--format", "{{.Repository}}:{{.Tag}}")
        if rc == 0:
            have = out.split()
            for svc in SERVICES:
                img = self.image(svc)
                self.pulled[svc] = any(h == img or h.endswith("/" + img) for h in have)
            if not self.announced:
                self.announced = True
                missing = [self.image(s) for s in SERVICES if not self.pulled[s]]
                if missing:
                    self.warn(f"missing images: {', '.join(missing)} – pull them with Enter or P")
                else:
                    self.ok("all images are pulled")
        self.render_rows()

    def render_rows(self) -> None:
        for i, (svc, (_, port_key)) in enumerate(SERVICES.items()):
            row = self.query_one(f"#row-{svc}")
            row.set_class(i == self.selected, "selected")
            row.query_one(".image", Label).update(self.image(svc))
            row.query_one(".port", Label).update(self.cfg[port_key] if port_key else "–")

            busy = self.busy.get(svc)
            pulled = self.pulled.get(svc)
            running = self.state.get(svc) == "running"
            if busy:
                status = f"[yellow]◌ {busy}[/]"
            elif self.docker_error:
                status = "[red]✗ unavailable[/]"
            elif pulled is None:
                status = "[dim]?[/]"
            elif not pulled:
                status = "[yellow]✗ not pulled[/]"
            elif running:
                status = "[green]● running[/]"
            else:
                status = "[dim]○ stopped[/]"
            row.query_one(".status", Label).update(status)

            for action in ACTIONS:
                button = row.query_one(f"#{action}-{svc}", Button)
                button.display = self.wanted(action, svc) and not self.docker_error
                button.disabled = bool(busy)

    # --- actions ----------------------------------------------------------

    @property
    def current(self) -> str:
        return list(SERVICES)[self.selected]

    def action_move(self, delta: int) -> None:
        self.selected = (self.selected + delta) % len(SERVICES)
        self.render_rows()

    def action_jump(self, index: int) -> None:
        self.selected = index
        self.render_rows()

    def wanted(self, action: str, svc: str) -> bool:
        """Does the action make sense for the service right now? (same rules as button visibility)"""
        pulled, running = self.pulled.get(svc), self.state.get(svc) == "running"
        return {"pull": pulled is False, "start": bool(pulled) and not running,
                "stop": bool(pulled) and running, "restart": bool(pulled) and running}[action]

    def action_smart(self) -> None:
        """Enter: not pulled -> pull, stopped -> start, running -> stop."""
        svc = self.current
        self.do(next((a for a in ("pull", "stop") if self.wanted(a, svc)), "start"), svc)

    def action_all(self, action: str) -> None:
        targets = [svc for svc in SERVICES if self.wanted(action, svc)]
        if not targets:
            self.info(f"{action} all: nothing to do")
        for svc in targets:
            self.do(action, svc)

    def action_open(self) -> None:
        svc = self.current
        if svc not in WEB:
            self.warn("has no web interface – only nginx and phpmyadmin can be opened", svc)
        elif self.state.get(svc) != "running":
            self.warn("is not running – start it first (s)", svc)
        else:
            port = self.cfg[WEB[svc]]
            url = "http://localhost" if port == "80" else f"http://localhost:{port}"
            opener = shutil.which("xdg-open")
            if opener:
                # browser output must not reach the terminal, it would corrupt the TUI
                subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                self.info(f"opening {url}", svc)
            else:
                self.warn(f"xdg-open not found – open {url} yourself", svc)

    def action_clear(self) -> None:
        self.out.clear()

    def action_help(self) -> None:
        self.push_screen(Help())

    def action_detach(self) -> None:
        """Quit without stopping the containers."""
        self.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action, svc = event.button.id.split("-", 1)
        self.selected = list(SERVICES).index(svc)
        self.do(action, svc)

    def action_service(self, action: str) -> None:
        self.do(action, self.current)

    @work
    async def do(self, action: str, svc: str) -> None:
        if self.busy.get(svc):
            return
        if action != "pull" and self.pulled.get(svc) is False:
            self.warn(f"image {self.image(svc)} is not pulled – pull it first (p)", svc)
            return
        label, commands = ACTIONS[action]
        self.busy[svc] = label
        self.render_rows()
        started = time.monotonic()
        output: list[str] = []

        def on_line(line: str) -> None:
            output.append(line.lower())
            self.raw(svc, line.strip())

        try:
            for args in commands:
                self.info(f"{action}: compose {' '.join(args)} {svc}", svc)
                rc = await stream(*self.compose_cmd, *args, svc, on_line=on_line)
                if rc != 0:
                    text = "\n".join(output)
                    hint = next((h for needles, h in HINTS if any(n in text for n in needles)),
                                "see the output above for details")
                    self.error(f"{action} failed (exit code {rc})", svc, hint)
                    break
            else:
                self.ok(f"{action} done ({time.monotonic() - started:.0f} s)", svc)
        finally:
            self.busy.pop(svc, None)
            self.state.pop(svc, None)  # we caused this state change, do not report it as a crash
        await self.refresh_state()

    def action_scroll_log(self, direction: int) -> None:
        if direction < 0:
            self.out.scroll_page_up()
        else:
            self.out.scroll_page_down()

    def action_logs(self) -> None:
        svc = self.current
        self.workers.cancel_group(self, "logs")
        previous, self.log_svc = self.log_svc, None
        if previous:
            self.info("logs off", previous)
        if previous == svc:
            return
        self.log_svc = svc
        self.info(f"logs on – last {LOG_TAIL} lines, then live (l = off)", svc)
        self.follow_logs(svc)

    @work(group="logs")
    async def follow_logs(self, svc: str) -> None:
        await stream(*self.compose_cmd, "logs", "-f", "--tail", str(LOG_TAIL), "--no-log-prefix", svc,
                     on_line=lambda line: self.raw(svc, line))
        # only reached when the stream ended by itself (turning logs off cancels the worker)
        if self.log_svc == svc:
            self.log_svc = None
            self.info("logs ended – the container stopped (l = follow again)", svc)

    async def action_quit(self) -> None:
        """Stop all containers before quitting."""
        if self.quitting:
            return
        self.quitting = True
        self.workers.cancel_group(self, "logs")
        self.info("quitting – stopping all containers…")
        for svc in SERVICES:
            self.busy[svc] = "stopping…"
        self.render_rows()
        await stream(*self.compose_cmd, "stop", on_line=lambda line: self.raw("docker", line))
        self.exit()

    def action_settings(self) -> None:
        def done(cfg: dict[str, str] | None) -> None:
            if cfg is None or cfg == self.cfg:
                return
            changes = ", ".join(
                f"{key} changed" if "PASSWORD" in key else f"{key} {self.cfg[key]} → {value}"
                for key, value in cfg.items() if value != self.cfg[key]
            )
            self.cfg = cfg
            os.environ.update(cfg)
            try:
                save_env(cfg)
            except OSError as exc:
                self.error(f"cannot save settings to {CONFIG}: {exc.strerror}")
                return
            self.prepare_web_root()
            self.ok(f"settings saved: {changes} – applies after the service is started or restarted")
            self.run_worker(self.refresh_state())

        self.push_screen(Settings(self.cfg), done)


def main() -> None:
    found = find_runtime()
    if not found:
        sys.exit("Neither `docker compose` nor `podman compose` was found. Install Docker or Podman with compose.")
    Dampp(*found).run()


if __name__ == "__main__":
    main()
