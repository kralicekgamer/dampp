# dampp

XAMPP for Linux, in containers. A small terminal UI that starts, stops and restarts MariaDB, nginx, PHP and phpMyAdmin, each in its own Docker container, so nothing is installed on your system.

## Install

You need [Docker](https://docs.docker.com/engine/install/) with the compose plugin (Podman works too), and your user must be allowed to use it:

```sh
sudo usermod -aG docker $USER
```

Log out and back in, then install dampp:

```sh
curl -s https://raw.githubusercontent.com/kralicekgamer/dampp/refs/heads/master/install.sh | bash
```

The installer creates an isolated environment in `$HOME/.local/share/dampp` and installs the command as `$HOME/.local/bin/dampp`. Add `$HOME/.local/bin` to your `PATH` if the installer tells you to. It needs `python3`, `curl` and `tar`.

## Run

```sh
dampp
```

Pull the images you are missing, then start the services you want. The interface is in Czech.

| Key | Action |
|---|---|
| `↑` `↓` or `1`–`4` | select a service |
| `Enter` | do the obvious thing: pull, then start, then stop |
| `s` / `x` / `r` / `p` | start / stop / restart / pull the selected service |
| `S` / `X` / `R` / `P` | the same for all services |
| `l` | follow the service's logs (last 20 lines, then live) |
| `o` | open the service in your browser |
| `n` | settings: versions, ports, web root, database password |
| `h` | show all keys |
| `q` | quit and stop all containers (`d` quits and leaves them running) |

## Defaults

| What | Where |
|---|---|
| Your site | <http://localhost>, files in `~/dampp/www` |
| phpMyAdmin | <http://localhost:8080> |
| MariaDB | `127.0.0.1:3306`, user `root`, password `root` (host `mariadb` from PHP) |
| Versions | latest |

All ports listen on `127.0.0.1` only. This is a development tool, do not expose it to the internet.

## Uninstall

```sh
curl -s https://raw.githubusercontent.com/kralicekgamer/dampp/refs/heads/master/uninstall.sh | bash
```

The uninstall script removes the containers, the command, the installation directory and your settings. Your web root and the database volume are kept.
