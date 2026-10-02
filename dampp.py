#!/usr/bin/env python3
"""dampp - XAMPP pro Linux: TUI nad docker compose (MariaDB, nginx, PHP, phpMyAdmin)."""

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
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog

HERE = Path(__file__).resolve().parent
# nastaveni a web root ziji mimo slozku s aplikaci, aby je reinstalace nesmazala
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
# sluzba -> (klic verze, klic portu)
SERVICES = {
    "mariadb": ("MARIADB_TAG", "MARIADB_PORT"),
    "nginx": ("NGINX_TAG", "NGINX_PORT"),
    "php": ("PHP_TAG", None),
    "phpmyadmin": ("PMA_TAG", "PMA_PORT"),
}
# sekce nastaveni: nazev -> [(klic, popisek)]
SECTIONS = {
    "MariaDB": [("MARIADB_TAG", "Verze"), ("MARIADB_PORT", "Port"), ("MARIADB_ROOT_PASSWORD", "Root heslo")],
    "nginx": [("NGINX_TAG", "Verze"), ("NGINX_PORT", "Port"), ("WEB_ROOT", "Web root")],
    "PHP": [("PHP_TAG", "Verze")],
    "phpMyAdmin": [("PMA_TAG", "Verze"), ("PMA_PORT", "Port")],
}
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")  # platny tag docker image
PORTS = {"MARIADB_PORT": "MariaDB", "NGINX_PORT": "nginx", "PMA_PORT": "phpMyAdmin"}
LEVELS = {"INFO": "blue", "OK": "green", "WARN": "yellow", "ERROR": "red"}
LOG_TAIL = 20  # kolik radku historie ukaze klavesa l
INDENT = 29  # sirka sloupcu "cas  UROVEN  sluzba  " v konzoli
# typicke chyby dockeru -> rada (hleda se ve vystupu prikazu, malymi pismeny)
HINTS = [
    (("address already in use", "port is already allocated"), "port už používá něco jiného – změň ho v nastavení (s)"),
    (("manifest unknown", "manifest for", "not found: manifest"), "taková verze image neexistuje – zkontroluj verzi v nastavení (s)"),
    (("permission denied",), "chybí práva k Dockeru: sudo usermod -aG docker $USER, pak se odhlas a přihlas"),
    (("cannot connect", "failed to connect"), "Docker neběží? zkus: sudo systemctl start docker"),
    (("no such host", "timeout", "tls handshake"), "nejde se připojit k registru – zkontroluj internet"),
]
ACTIONS = {
    "start": ("startuji…", [["up", "-d"]]),
    "stop": ("zastavuji…", [["stop"]]),
    # stop + up misto `restart`, aby se projevila zmena nastaveni
    "restart": ("restartuji…", [["stop"], ["up", "-d"]]),
    "pull": ("stahuji…", [["pull"]]),
}


def find_runtime() -> tuple[list[str], str] | None:
    """Vrati (compose prikaz, binarka runtime) nebo None."""
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
    CONFIG.chmod(0o600)  # je v nem heslo k databazi


def ensure_web_root(cfg: dict[str, str]) -> None:
    """Vytvori web root (jinak by ho docker zalozil jako root) a do prazdneho da landing page."""
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
    """Spusti prikaz a kazdy radek vystupu preda on_line. Pri zruseni proces ukonci."""
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
        ("escape", "cancel", "Zrušit"),
        ("down", "app.focus_next", "Další pole"),
        ("up", "app.focus_previous", "Předchozí pole"),
    ]

    def __init__(self, cfg: dict[str, str]) -> None:
        super().__init__()
        self.cfg = cfg

    def compose(self) -> ComposeResult:
        dialog = Vertical(id="dialog")
        dialog.border_title = "Nastavení"
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
                yield Button("Uložit", id="save", variant="primary")
                yield Button("Zrušit", id="cancel")

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
        used: dict[str, str] = {}  # port -> sluzba
        for key, name in PORTS.items():
            if not (cfg[key].isdigit() and 0 < int(cfg[key]) < 65536):
                error.update(f"[red]✗ {name}: port musí být číslo 1–65535[/]")
                return
            if cfg[key] in used:
                error.update(f"[red]✗ {name} a {used[cfg[key]]} nemůžou mít stejný port {cfg[key]}[/]")
                return
            used[cfg[key]] = name
        for name, (tag_key, _) in SERVICES.items():
            if not TAG.fullmatch(cfg[tag_key]):
                error.update(f"[red]✗ {name}: neplatná verze (povolená jsou písmena, číslice, tečka, pomlčka)[/]")
                return
        # php image musi byt fpm varianta: "8.4" -> "8.4-fpm", "latest" -> "fpm"
        if "fpm" not in cfg["PHP_TAG"]:
            cfg["PHP_TAG"] = "fpm" if cfg["PHP_TAG"] == "latest" else cfg["PHP_TAG"] + "-fpm"
        # relativni cestu ber od domovske slozky, ne od slozky aplikace
        cfg["WEB_ROOT"] = str(Path.home() / Path(cfg["WEB_ROOT"]).expanduser())
        self.dismiss(cfg)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Dampp(App):
    TITLE = "dampp"
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
    Settings { align: center middle; }
    #dialog { width: 60; height: auto; max-height: 100%; overflow-y: auto; border: thick $primary; border-title-style: bold; background: $surface; padding: 1 2 0 2; }
    .section { height: auto; padding: 0 1; border: round $primary-darken-1; border-title-color: $accent; border-title-style: bold; }
    .field { height: 1; }
    .field Label { width: 14; color: $text-muted; }
    .field Input { width: 1fr; }
    #error { height: 1; }
    #buttons { height: 3; align: right middle; }
    #buttons Button { margin-left: 1; }
    """
    BINDINGS = [
        ("up,k", "move(-1)", "Nahoru"),
        ("down,j", "move(1)", "Dolů"),
        ("a", "service('start')", "Start"),
        ("x", "service('stop')", "Stop"),
        ("r", "service('restart')", "Restart"),
        ("p", "service('pull')", "Pull"),
        ("l", "logs", "Logy"),
        ("pageup", "scroll_log(-1)", "Výstup ↑"),
        ("pagedown", "scroll_log(1)", "Výstup ↓"),
        ("s", "settings", "Nastavení"),
        ("q", "quit", "Konec"),
    ]

    def __init__(self, compose_cmd: list[str], runtime: str) -> None:
        super().__init__()
        self.compose_cmd = [*compose_cmd, "--project-directory", str(HERE)]
        self.runtime = runtime
        self.cfg = load_env()
        os.environ.update(self.cfg)  # compose.yaml bere verze a porty z prostredi
        self.selected = 0
        self.state: dict[str, str] = {}  # sluzba -> stav kontejneru z `compose ps`
        self.pulled: dict[str, bool] = {}  # sluzba -> je image stazeny
        self.busy: dict[str, str] = {}  # sluzba -> popis bezici akce
        self.log_svc: str | None = None
        self.announced = False
        self.docker_error: str | None = None  # posledni vypsana chyba dockeru
        self.quitting = False

    def compose(self) -> ComposeResult:
        yield Header()
        for svc in SERVICES:
            with Horizontal(id=f"row-{svc}", classes="row"):
                yield Label(svc, classes="name")
                yield Label(classes="image")
                yield Label(classes="port")
                yield Label(classes="status")
                yield Button("Pull", id=f"pull-{svc}", variant="warning")
                yield Button("Start", id=f"start-{svc}", variant="success")
                yield Button("Stop", id=f"stop-{svc}", variant="error")
                yield Button("Restart", id=f"restart-{svc}", classes="restart")
        out = RichLog(id="out")
        out.can_focus = False  # jinak by konzole sebrala sipky pro vyber sluzby
        yield out
        yield Footer()

    async def on_mount(self) -> None:
        self.out = self.query_one("#out", RichLog)
        self.out.border_title = "Výstup"
        self.render_rows()
        self.prepare_web_root()
        await self.refresh_state()
        self.set_interval(2, self.refresh_state)

    def prepare_web_root(self) -> None:
        try:
            ensure_web_root(self.cfg)
        except OSError as exc:
            self.error(f"web root {self.cfg['WEB_ROOT']} nejde vytvořit: {exc.strerror}", hint="změň ho v nastavení (s)")

    def check_action(self, action: str, parameters: tuple) -> bool:
        # nad otevrenym nastavenim vypni jen ovladani sluzeb (tab/focus_next musi fungovat dal)
        return len(self.screen_stack) == 1 or action not in ("move", "service", "logs", "settings", "scroll_log")

    # --- hlasky do konzole ------------------------------------------------

    def say(self, level: str, msg: str, svc: str = "dampp", hint: str | None = None) -> None:
        """Radek `cas  UROVEN  sluzba  text`, pripadne s radou na dalsim radku."""
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
        """Surovy vystup dockeru - odsazeny, aby se nepletl s hlaskami aplikace."""
        self.out.write(Text.assemble(" " * 17, (f"{svc:<10}│ ", "dim"), line))

    # --- stav -------------------------------------------------------------

    def image(self, svc: str) -> str:
        return f"{svc}:{self.cfg[SERVICES[svc][0]]}"

    async def refresh_state(self) -> None:
        rc, out = await run(*self.compose_cmd, "ps", "-a", "--format", "json")
        if rc != 0:
            message = (out.strip().splitlines() or ["compose selhal"])[-1]
            # stav se obnovuje kazde 2 s - stejnou chybu vypis jen jednou
            if message != self.docker_error:
                self.docker_error = message
                if "permission denied" in out.lower():
                    self.error("nemáš práva k Dockeru", hint="sudo usermod -aG docker $USER, pak se odhlas "
                                                             "a přihlas (aplikaci nepouštěj přes sudo)")
                else:
                    self.error("Docker není dostupný", hint="běží daemon? zkus: sudo systemctl start docker")
                self.raw("docker", message)
            self.render_rows()
            return
        if self.docker_error:
            self.docker_error = None
            self.ok("Docker je zase dostupný")

        # compose vraci bud JSON pole, nebo jeden objekt na radek
        text = out.strip()
        try:
            items = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines() if l]
            state = {c["Service"]: c["State"] for c in items}
        except (ValueError, KeyError, TypeError):
            if self.docker_error != "format":
                self.docker_error = "format"
                self.error("nerozumím výstupu `compose ps`", hint="je potřeba novější Docker Compose (v2)")
            self.render_rows()
            return
        # sluzba, ktera bezela a prestala bez nasi akce (pad, zastaveni nebo smazani zvenci)
        codes = {c["Service"]: c.get("ExitCode", "?") for c in items}
        for svc, old in self.state.items():
            now = state.get(svc, "odstraněna")
            if old == "running" and now != "running" and svc not in self.busy and not self.quitting:
                self.warn(f"přestala běžet sama od sebe (stav {now}, kód {codes.get(svc, '?')}) – logy: l", svc)
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
                    self.warn(f"chybí image: {', '.join(missing)} – stáhni je tlačítkem Pull (p)")
                else:
                    self.ok("všechny image jsou stažené")
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
                status = "[red]✗ nedostupné[/]"
            elif pulled is None:
                status = "[dim]?[/]"
            elif not pulled:
                status = "[yellow]✗ nestaženo[/]"
            elif running:
                status = "[green]● běží[/]"
            else:
                status = "[dim]○ zastaveno[/]"
            row.query_one(".status", Label).update(status)

            show = {"pull": pulled is False, "start": bool(pulled) and not running,
                    "stop": bool(pulled) and running, "restart": bool(pulled) and running}
            for action, visible in show.items():
                button = row.query_one(f"#{action}-{svc}", Button)
                button.display = visible and not self.docker_error
                button.disabled = bool(busy)

    # --- akce -------------------------------------------------------------

    @property
    def current(self) -> str:
        return list(SERVICES)[self.selected]

    def action_move(self, delta: int) -> None:
        self.selected = (self.selected + delta) % len(SERVICES)
        self.render_rows()

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
            self.warn(f"image {self.image(svc)} není stažený – nejdřív Pull (p)", svc)
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
                                "podrobnosti jsou ve výpisu výše")
                    self.error(f"{action} selhal (kód {rc})", svc, hint)
                    break
            else:
                self.ok(f"{action} hotov ({time.monotonic() - started:.0f} s)", svc)
        finally:
            self.busy.pop(svc, None)
            self.state.pop(svc, None)  # zmenu stavu jsme zpusobili my, nehlasit ji jako pad
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
            self.info("logy vypnuty", previous)
        if previous == svc:
            return
        self.log_svc = svc
        self.info(f"logy zapnuty – posledních {LOG_TAIL} řádků + nové (l = vypnout)", svc)
        self.follow_logs(svc)

    @work(group="logs")
    async def follow_logs(self, svc: str) -> None:
        await stream(*self.compose_cmd, "logs", "-f", "--tail", str(LOG_TAIL), "--no-log-prefix", svc,
                     on_line=lambda line: self.raw(svc, line))
        # sem se dojde jen kdyz proud skoncil sam (pri vypnuti je worker zrusen)
        if self.log_svc == svc:
            self.log_svc = None
            self.info("logy skončily – kontejner se zastavil (l = zapnout znovu)", svc)

    async def action_quit(self) -> None:
        """Pred ukoncenim zastavi vsechny kontejnery."""
        if self.quitting:
            return
        self.quitting = True
        self.workers.cancel_group(self, "logs")
        self.info("ukončuji – zastavuji všechny kontejnery…")
        for svc in SERVICES:
            self.busy[svc] = "zastavuji…"
        self.render_rows()
        await stream(*self.compose_cmd, "stop", on_line=lambda line: self.raw("docker", line))
        self.exit()

    def action_settings(self) -> None:
        def done(cfg: dict[str, str] | None) -> None:
            if cfg is None or cfg == self.cfg:
                return
            changes = ", ".join(
                f"{key} změněno" if "PASSWORD" in key else f"{key} {self.cfg[key]} → {value}"
                for key, value in cfg.items() if value != self.cfg[key]
            )
            self.cfg = cfg
            os.environ.update(cfg)
            try:
                save_env(cfg)
            except OSError as exc:
                self.error(f"nastavení nejde uložit do {CONFIG}: {exc.strerror}")
                return
            self.prepare_web_root()
            self.ok(f"nastavení uloženo: {changes} – projeví se po Start/Restart služby")
            self.run_worker(self.refresh_state())

        self.push_screen(Settings(self.cfg), done)


def main() -> None:
    found = find_runtime()
    if not found:
        sys.exit("Nenašel jsem `docker compose` ani `podman compose`. Nainstaluj Docker nebo Podman s compose.")
    Dampp(*found).run()


if __name__ == "__main__":
    main()
