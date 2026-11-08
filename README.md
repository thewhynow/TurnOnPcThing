# PC Boot

A tiny macOS app (and CLI) that wakes a dual-boot (Linux + Windows, GRUB) PC over Wake-on-LAN, boots it into **Linux** or **Windows**, logs into the default account, and opens Moonlight.

## App
```
./build.sh          # builds "PC Boot.app" and installs it to /Applications
```
Needs Xcode command line tools (`swiftc`). Then launch **PC Boot** from Launchpad/Spotlight, or drag it to the Dock.

## CLI
```
python3 turnon.py            # menu
python3 turnon.py init|linux|windows|status|off
```

## Config
Run the setup wizard, which writes `~/.turnon.json` (mode 600, never committed):
```
python3 turnon.py init
```
Or copy `turnon.example.json` there and edit it. Required: `mac` (your PC's MAC address), `linux` / `win`
(account names) and `linux_password` / `win_password`. Everything else is optional:

| Key | Default | Purpose |
|---|---|---|
| `host` | auto (ARP lookup by MAC) | PC's IP |
| `broadcast` | `255.255.255.255` | WoL broadcast address, e.g. `192.168.1.255` if the default doesn't wake it |
| `boot_timeout` | `300` | Seconds to wait for the PC to come up |
| `ssh_key` | `~/.ssh/turnon_ed25519` | Key tried before the password |
| `windows_grub_entry` | `Windows` | Substring of the GRUB menu entry that boots Windows |
| `sddm_session` | – | Session for SDDM autologin, e.g. `plasma` |
| `sunshine` / `sunshine_unit` | `true` / LizardByte Flatpak-style unit | Start Sunshine after Linux login |
| `dim` | `true` | Put monitors in DDC standby after the tool boots the PC |
| `moonlight` / `moonlight_path` / `moonlight_delay` | `true` / `/Applications/Moonlight.app/...` / `5` | Open Moonlight when ready |

Any key can also be set as an upper-case env var (`BROADCAST=...`), which wins over the file; `TURNON_CONFIG`
points at a different config file. `NO_DIM=1` and `NO_MOONLIGHT=1` still work.

Windows is controlled over SSH with a dedicated key (`~/.ssh/turnon_ed25519`, add its `.pub` to
`C:\ProgramData\ssh\administrators_authorized_keys`); Linux uses password SSH.
See the docstring at the top of `turnon.py` for PC-side prerequisites (WoL, OpenSSH, GRUB).

## Monitors
When the tool powers on or reboots the PC, it puts the monitors in DDC standby (fully black)
(Linux: `ddcutil`, installed automatically; Windows: a `TurnOnDim` scheduled task that runs
`C:\ProgramData\turnon\dim.ps1` in the desktop session, only when the tool asks). The monitors remember
the value. Disable with `NO_DIM=1` or `"dim": false` in the config.

## Sunshine (Moonlight host)
On Linux Mint, Sunshine's user service is tied to `graphical-session.target`, which Cinnamon never
reaches, so it doesn't start after boot and Moonlight shows the PC as offline. After login the tool now
starts it (and installs `~/.config/autostart/turnon-sunshine.desktop` so it also starts with the desktop).
