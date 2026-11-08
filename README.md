# PC Boot

A tiny macOS app (and CLI) that wakes my dual-boot PC over Wake-on-LAN, boots it into **Linux** or **Windows**, logs into the default account, and opens Moonlight.

## App
```
./build.sh          # builds "PC Boot.app" and installs it to /Applications
```
Needs Xcode command line tools (`swiftc`). Then launch **PC Boot** from Launchpad/Spotlight, or drag it to the Dock.

## CLI
```
python3 turnon.py            # menu
python3 turnon.py linux|windows|status
```

## Config
Create `~/.turnon.json` (never committed):
```json
{
  "host": "192.168.86.76",
  "linux": "<linux username>",
  "win": "<windows username>",
  "linux_password": "...",
  "win_password": "..."
}
```
Windows is controlled over SSH with a dedicated key (`~/.ssh/turnon_ed25519`, add its `.pub` to
`C:\ProgramData\ssh\administrators_authorized_keys`); Linux uses password SSH.
See the docstring at the top of `turnon.py` for PC-side prerequisites (WoL, OpenSSH, GRUB).

## Monitors
When the tool powers on or reboots the PC, it puts the monitors in DDC standby (fully black)
(Linux: `ddcutil`, installed automatically; Windows: a `TurnOnDim` scheduled task that runs
`C:\ProgramData\turnon\dim.ps1` in the desktop session, only when the tool asks). The monitors remember
the value. Disable with `NO_DIM=1` or `"dim": false` in `~/.turnon.json`.

## Sunshine (Moonlight host)
On Linux Mint, Sunshine's user service is tied to `graphical-session.target`, which Cinnamon never
reaches, so it doesn't start after boot and Moonlight shows the PC as offline. After login the tool now
starts it (and installs `~/.config/autostart/turnon-sunshine.desktop` so it also starts with the desktop).
