#!/usr/bin/env python3
"""Smoke test: drives the TUI headlessly against a fake `docker` binary.

Run with:  .venv/bin/python tests/smoke.py
"""

import asyncio
import os
import stat
import sys
import tempfile
from pathlib import Path

FAKE_DOCKER = r"""#!/bin/sh
# minimal docker stand-in; state lives in $FAKE_STATE (img-<svc>, run-<svc>)
ST="$FAKE_STATE"
if [ "$1" = images ]; then cat "$ST"/img-* 2>/dev/null; exit 0; fi
shift
[ "$1" = version ] && exit 0
shift 2
case "$1" in
  ps) for f in "$ST"/run-*; do [ -e "$f" ] && echo "{\"Service\":\"${f##*run-}\",\"State\":\"running\"}"; done ;;
  pull) echo "pulling $2"; echo "$2:latest" > "$ST/img-$2" ;;
  up) if [ -e "$ST/fail-$3" ]; then echo "Bind for 127.0.0.1:80 failed: port is already allocated"; exit 1; fi
      echo "Container dampp-$3 Started"; : > "$ST/run-$3" ;;
  stop) if [ -n "$2" ]; then rm -f "$ST/run-$2"; else rm -f "$ST"/run-*; fi; echo stopped ;;
  logs) for last; do :; done; echo "log line for $last"; sleep 30 ;;
esac
exit 0
"""


def setup_environment(tmp: Path) -> Path:
    state = tmp / "state"
    bindir = tmp / "bin"
    for d in (state, bindir, tmp / "home"):
        d.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    os.environ.update(
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        HOME=str(tmp / "home"),
        XDG_CONFIG_HOME=str(tmp / "home" / ".config"),
        FAKE_STATE=str(state),
    )
    (state / "img-nginx").write_text("nginx:latest\n")
    return state


async def main(tmp: Path) -> None:
    state = setup_environment(tmp)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import dampp  # imported after HOME is redirected
    from textual.widgets import Input, Label, RichLog

    app = dampp.Dampp(*dampp.find_runtime())

    def console() -> str:
        return "\n".join(line.text for line in app.query_one(RichLog).lines)

    def status(svc: str) -> str:
        return str(app.query_one(f"#row-{svc} .status", Label).content)

    async with app.run_test(size=(120, 40)) as pilot:
        async def settle() -> None:
            for _ in range(100):
                await pilot.pause(0.05)
                if not app.busy:
                    break
            await pilot.pause(0.2)

        await pilot.pause(0.5)
        web_root = Path(app.cfg["WEB_ROOT"])
        assert web_root == tmp / "home" / "dampp" / "www", web_root
        assert (web_root / "index.php").exists(), "landing page not copied into web root"
        assert "chybí image" in console()
        assert "nestaženo" in status("mariadb") and "zastaveno" in status("nginx")

        # start without image -> warning, then pull + start
        await pilot.press("a"); await settle()
        assert "není stažený" in console()
        await pilot.press("p"); await settle()
        assert "pull hotov" in console() and "zastaveno" in status("mariadb")
        await pilot.press("a"); await settle()
        assert "běží" in status("mariadb"), status("mariadb")

        # arrow keys select a service even though the console exists
        await pilot.press("down")
        assert app.current == "nginx"

        # logs on / off
        await pilot.press("l"); await pilot.pause(0.4)
        assert "logy zapnuty" in console() and "log line for nginx" in console()
        await pilot.press("l"); await pilot.pause(0.2)
        assert "logy vypnuty" in console()

        # failing start -> error with a hint
        (state / "fail-nginx").touch()
        await pilot.press("a"); await settle()
        assert "start selhal" in console() and "port už používá" in console()
        (state / "fail-nginx").unlink()

        # service stopped behind our back -> warning
        (state / "run-mariadb").unlink()
        await pilot.pause(2.5)
        assert "přestala běžet" in console()

        # settings: keyboard navigation, validation, save
        await pilot.press("s"); await pilot.pause(0.3)
        assert app.focused.id == "mariadb_tag"
        await pilot.press("tab"); assert app.focused.id == "mariadb_port"
        await pilot.press("down"); assert app.focused.id == "mariadb_root_password"
        await pilot.press("up", "shift+tab"); assert app.focused.id == "mariadb_tag"
        error = lambda: str(app.screen.query_one("#error", Label).content)
        field = lambda name: app.screen.query_one(f"#{name}", Input)
        field("nginx_port").value = "abc"; await pilot.click("#save"); await pilot.pause(0.1)
        assert "port musí být" in error()
        field("nginx_port").value = "8080"; await pilot.click("#save"); await pilot.pause(0.1)
        assert "stejný port" in error()
        field("nginx_port").value = "81"; field("nginx_tag").value = "bad tag!"
        await pilot.click("#save"); await pilot.pause(0.1)
        assert "neplatná verze" in error()
        field("nginx_tag").value = "1.27"; field("php_tag").value = "8.4"
        field("nginx_tag").focus(); await pilot.pause(0.1)
        await pilot.press("enter"); await pilot.pause(0.5)  # Enter in a field saves
        assert len(app.screen_stack) == 1, f"settings did not close on save: {error()} / focus {app.focused}"
        saved = dampp.CONFIG.read_text()
        assert "NGINX_PORT=81" in saved and "PHP_TAG=8.4-fpm" in saved and "NGINX_TAG=1.27" in saved, saved
        assert os.environ["NGINX_PORT"] == "81"
        assert "NGINX_PORT 80 → 81" in console()

        # quit stops everything
        (state / "run-mariadb").touch()
        await pilot.press("q"); await pilot.pause(0.5)
    assert not list(state.glob("run-*")), "containers were not stopped on quit"
    print("smoke test OK")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(main(Path(tmp)))
