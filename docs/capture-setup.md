# Capture setup

Run these commands from the Menagerie repository root. Capture is an independent
desktop application that connects to Menagerie over HTTP, including when both
are installed on the same machine. It never opens the server's database directly.

## Desktop

Use Python 3.10+ with Tkinter (`python3-tk` on Ubuntu):

```bash
python3 -m venv capture/.venv
capture/.venv/bin/python -m pip install -e ./capture
capture/.venv/bin/mini-scan
# Hardware setup and diagnostics:
capture/.venv/bin/menagerie-capture
```

The equivalent module launch commands are `capture/.venv/bin/python -m mini_scan`
and `capture/.venv/bin/python -m capture_station`. Select Simulator to work
without a camera or Pico. The web app continues to use its root environment and
`requirements.txt`.

## Pico

```bash
capture/.venv/bin/python -m pip install -e './capture[pico]'
capture/.venv/bin/python tools/flash_pico.py --port /dev/ttyACM0 --dry-run
# Disconnect desktop apps and serial terminals, then upload explicitly:
capture/.venv/bin/python tools/flash_pico.py --port /dev/ttyACM0
```

VS Code tasks are configured for the repository root. Firmware stays in
`firmware/pico/`; files are copied to the Pico filesystem root. See the
[wiring guide](wiring.md) and [full controls guide](../capture/README.md).

## Tests and packaging

```bash
.venv/bin/python -m unittest discover -s tests
capture/.venv/bin/python -m unittest discover -s capture/tests
# To run desktop UI tests without a display (Ubuntu: xvfb and xauth):
xvfb-run -a capture/.venv/bin/python -m unittest discover -s capture/tests
# Integration tests need web dependencies in the test environment too:
capture/.venv/bin/python -m pip install -r requirements.txt
PYTHONPATH=.:tests capture/.venv/bin/python -m unittest discover -s tests/integration
# Build the desktop wheel from the complete checkout:
capture/.venv/bin/python -m pip wheel --no-deps ./capture --wheel-dir /tmp/menagerie-wheels
```

Desktop packaging includes the portable Pico code as `menagerie_pico` for the
simulator. No Flask dependency is needed to install or run the desktop apps.
The firmware uploader remains a repository tool and is not an installed desktop
command. The wheel can be installed independently; editable development and
wheel builds require the complete checkout, including `firmware/pico/`.

## Local migration

On the migrated workstation, the former `../menagerie_capture` workspace is retained as a
compatibility symlink to this repository's `capture/` directory. Its virtual
environment is now `capture/.venv/`, and its launch commands resolve to the new
sources. Use the Menagerie root for VS Code tasks and firmware upload commands.
The previous workspace is retained as `../menagerie_capture.migration-backup-20261009` for rollback.
Neither the environment nor the backup is committed.

Application data remains outside the repository:

- Capture Station: `$XDG_DATA_HOME/menagerie_capture` (default `~/.local/share/menagerie_capture`).
- Mini Scan: `$XDG_DATA_HOME/mini_scan` (default `~/.local/share/mini_scan`).
- API credentials: the existing operating-system keyring entry.
