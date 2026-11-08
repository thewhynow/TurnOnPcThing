#!/usr/bin/env python3
"""turnon.py - wake your dual-boot PC from this Mac, pick Linux or Windows, and log in.

    python3 turnon.py            # interactive menu
    python3 turnon.py linux
    python3 turnon.py windows
    python3 turnon.py status

What it does
  1. Sends Wake-on-LAN magic packets to the PC (if it's off).
  2. Gets the PC into the OS you picked (GRUB defaults to Linux; for Windows it
     runs `grub-reboot` + reboot over SSH; from Windows -> Linux it just reboots).
  3. Logs into the default account with the passwords in ~/.turnon.json. A lock screen can't be
     typed into remotely, so this enables *auto-login* on the PC the first time it
     can reach it over SSH (Windows: Winlogon registry keys; Linux: GDM/SDDM/LightDM).
     The password therefore sits in plain text on the PC (Windows registry).

One-time prerequisites on the PC (can't be done from here):
  * BIOS/UEFI: Wake-on-LAN / "Power on by PCI-E" on; Windows Fast Startup off.
  * Windows: OpenSSH Server installed + `sshd` running (Settings > Optional features).
  * Linux: `openssh-server`; `sudo ethtool -s <iface> wol g` for WoL.
  * The account password is set in ~/.turnon.json, and it's an admin / sudoer on both OSes.
  * Mac and PC on the same LAN.

Config (env vars override the defaults):
  LINUX_USER / WIN_USER  account name on each OS (asked once, remembered in ~/.turnon.json)
  PC_USER       shortcut if both OSes use the same name
  linux_password / win_password   keys in ~/.turnon.json (or LINUX_PASSWORD / WIN_PASSWORD env)
  PC_HOST       PC's IP; if unset it's looked up in the Mac's ARP table by MAC
                (works once the PC has been on the network recently), then ~/.turnon.json
  BROADCAST     e.g. 192.168.1.255 if 255.255.255.255 doesn't wake it
  SDDM_SESSION  only for SDDM autologin, e.g. plasma
"""
import atexit
import base64
import gzip
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

MAC = "d8:43:ae:c3:5f:c4"
BROADCAST = os.environ.get("BROADCAST", "255.255.255.255")
MOONLIGHT = "/Applications/Moonlight.app/Contents/MacOS/Moonlight"
MOONLIGHT_DELAY = int(os.environ.get("MOONLIGHT_DELAY", "5"))  # let Moonlight see the PC come online
BOOT_TIMEOUT = 300          # seconds to wait for the PC to come up
CONFIG = os.path.expanduser("~/.turnon.json")
KEY = os.path.expanduser("~/.ssh/turnon_ed25519")   # tried first; falls back to the password


# ---------------------------------------------------------------- config ----
def _load():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(**kv):
    cfg = _load()
    cfg.update(kv)
    with open(CONFIG, "w") as f:
        json.dump(cfg, f)


LINUX_PW = os.environ.get("LINUX_PASSWORD") or _load().get("linux_password", "")
WIN_PW = os.environ.get("WIN_PASSWORD") or _load().get("win_password", "")


def _norm_mac(m):
    try:
        return ":".join(f"{int(p, 16):02x}" for p in re.split(r"[:-]", m))
    except ValueError:
        return ""


def _account(key, label):
    u = os.environ.get(key.upper() + "_USER") or os.environ.get("PC_USER") or _load().get(key)
    if not u:
        try:
            u = input(f"{label} account name on the PC: ").strip()
        except EOFError:
            sys.exit(f"Set {key.upper()}_USER (env) or \"{key}\" in ~/.turnon.json")
        if not u:
            sys.exit("An account name is required.")
        _save(**{key: u})
    return u


def linux_user():
    return _account("linux", "Linux")


def win_user():
    return _account("win", "Windows")


def host():
    h = os.environ.get("PC_HOST")
    if h:
        return h
    out = subprocess.run(["arp", "-an"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        m = re.search(r"\(([\d.]+)\) at ([0-9a-f:]+)", line)
        if m and _norm_mac(m.group(2)) == _norm_mac(MAC):
            _save(host=m.group(1))
            return m.group(1)
    return _load().get("host")


# ------------------------------------------------------------ wake on lan ---
def wake():
    mac = bytes.fromhex(_norm_mac(MAC).replace(":", ""))
    pkt = b"\xff" * 6 + mac * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for _ in range(3):  # UDP is lossy
            for port in (9, 7):
                s.sendto(pkt, (BROADCAST, port))


# -------------------------------------------------------------------- ssh ---
_askpass_dir = None


def _askpass_env(pw):
    """ssh can't take a password as an argument; feed it through SSH_ASKPASS."""
    global _askpass_dir
    if _askpass_dir is None:
        _askpass_dir = tempfile.mkdtemp(prefix="turnon-")
        atexit.register(shutil.rmtree, _askpass_dir, True)
        path = os.path.join(_askpass_dir, "askpass.sh")
        with open(path, "w") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' \"$TURNON_PW\"\n")
        os.chmod(path, 0o700)
    return {**os.environ, "TURNON_PW": pw, "SSH_ASKPASS": os.path.join(_askpass_dir, "askpass.sh"),
            "SSH_ASKPASS_REQUIRE": "force", "DISPLAY": os.environ.get("DISPLAY", ":0")}


def ssh(who, cmd, timeout=60, pw=LINUX_PW):
    return subprocess.run(
        ["ssh", "-i", KEY, "-o", "NumberOfPasswordPrompts=1",
         "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
         f"{who}@{host()}", cmd],
        capture_output=True, text=True, timeout=timeout,
        env=_askpass_env(pw), stdin=subprocess.DEVNULL)


def wssh(cmd, timeout=60):
    return ssh(win_user(), cmd, timeout, WIN_PW)


def port22():
    h = host()
    if not h:
        return False
    try:
        with socket.create_connection((h, 22), timeout=2):
            return True
    except OSError:
        return False


def detect():
    """'off' | 'linux' | 'windows' | 'unknown' (sshd answers but login failed / not ready)."""
    if not port22():
        return "off"
    try:
        r = ssh(linux_user(), "uname -s", timeout=15)
        if r.returncode == 0 and "Linux" in r.stdout:
            return "linux"
        if win_user() != linux_user() or r.returncode != 255:
            r = wssh("ver", timeout=15)
            if r.returncode == 0 and "Windows" in r.stdout:
                return "windows"
    except subprocess.TimeoutExpired:
        pass
    return "unknown"


def wait_for_os(timeout=BOOT_TIMEOUT):
    end = time.time() + timeout
    while time.time() < end:
        cur = detect()
        if cur in ("linux", "windows"):
            return cur
        print(".", end="", flush=True)
        time.sleep(4)
    return None


def wait_for_shutdown(timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if not port22():
            return True
        time.sleep(2)
    return False


# ---------------------------------------------------------- Linux (remote) --
# Runs on the PC as root (via sudo). Modes: autologin | windows | dim | sunshine
REMOTE = r'''
import os, re, shutil, subprocess, sys, time

mode, user = sys.argv[1], sys.argv[2]
session = sys.argv[3] if len(sys.argv) > 3 else ""

def run(*a):
    return subprocess.run(a, capture_output=True, text=True)

def read(p):
    return open(p).read() if os.path.exists(p) else ""

def put(p, t):
    if read(p) == t:
        return False
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(t)
    return True

def gui_logged_in():
    for line in run("loginctl", "list-sessions", "--no-legend").stdout.splitlines():
        p = run("loginctl", "show-session", line.split()[0], "-p", "Type", "-p", "Class").stdout
        if re.search(r"Type=(x11|wayland)", p) and "Class=user" in p:
            return True
    return False

def autologin():
    dm = ""
    for p in ("/etc/X11/default-display-manager", "/etc/systemd/system/display-manager.service"):
        if os.path.exists(p):
            dm = os.path.basename(read(p).strip() if p.endswith("-manager") else os.path.realpath(p)); break
    if "gdm" in dm:
        p = next((p for p in ("/etc/gdm3/custom.conf", "/etc/gdm/custom.conf") if os.path.exists(p)), "/etc/gdm3/custom.conf")
        lines = [l for l in read(p).splitlines() if not re.match(r"\s*AutomaticLogin(Enable)?\s*=", l)]
        new = ["AutomaticLoginEnable=true", "AutomaticLogin=" + user]
        stripped = [l.strip() for l in lines]
        if "[daemon]" in stripped:
            i = stripped.index("[daemon]") + 1
            lines[i:i] = new
        else:
            lines += ["[daemon]"] + new
        return put(p, "\n".join(lines) + "\n")
    if "lightdm" in dm:
        return put("/etc/lightdm/lightdm.conf.d/50-turnon-autologin.conf",
                   "[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n" % user)
    if "sddm" in dm:
        return put("/etc/sddm.conf.d/50-turnon-autologin.conf",
                   "[Autologin]\nUser=%s\n" % user + ("Session=%s\n" % session if session else ""))
    print("UNSUPPORTED display manager: %r" % dm); sys.exit(3)

def windows():
    p = "/etc/default/grub"
    t = read(p)
    if not re.search(r"^GRUB_DEFAULT=saved\s*$", t, re.M):
        if re.search(r"^GRUB_DEFAULT=", t, re.M):
            t = re.sub(r"^GRUB_DEFAULT=.*$", "GRUB_DEFAULT=saved", t, flags=re.M)
        else:
            t += "\nGRUB_DEFAULT=saved\n"
        t = re.sub(r"^GRUB_SAVEDEFAULT=true", "GRUB_SAVEDEFAULT=false", t, flags=re.M)
        open(p, "w").write(t)
        print("set GRUB_DEFAULT=saved in /etc/default/grub")
        mk = shutil.which("update-grub")
        cmd = [mk] if mk else [shutil.which("grub2-mkconfig") or "grub-mkconfig", "-o",
                               "/boot/grub2/grub.cfg" if os.path.exists("/boot/grub2/grub.cfg") else "/boot/grub/grub.cfg"]
        if subprocess.run(cmd, capture_output=True).returncode:
            print("update-grub failed"); sys.exit(4)
    cfg = next((c for c in ("/boot/grub/grub.cfg", "/boot/grub2/grub.cfg") if os.path.exists(c)), None)
    m = re.search(r"menuentry\s+'([^']*Windows[^']*)'", read(cfg) if cfg else "")
    if not m:
        print("no Windows entry found in grub.cfg"); sys.exit(5)
    rb = shutil.which("grub-reboot") or shutil.which("grub2-reboot")
    if subprocess.run([rb, m.group(1)]).returncode:
        print("grub-reboot failed"); sys.exit(6)
    subprocess.Popen("sleep 2; systemctl reboot", shell=True, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("REBOOTING into: " + m.group(1))

def dim():
    """Put every DDC/CI monitor in standby (VCP d6=4) so the panels go fully dark."""
    if not shutil.which("ddcutil"):
        apt = shutil.which("apt-get")
        if not apt or subprocess.run([apt, "install", "-y", "ddcutil"], capture_output=True,
                                     env=dict(os.environ, DEBIAN_FRONTEND="noninteractive")).returncode:
            print("ddcutil isn't installed and couldn't be installed"); sys.exit(7)
    put("/etc/modules-load.d/i2c-dev.conf", "i2c-dev\n")
    run("modprobe", "i2c-dev")
    n = done = 0
    for _ in range(5):  # displays can take a moment to answer right after boot
        ids = re.findall(r"^Display (\d+)", run("ddcutil", "detect", "--brief").stdout, re.M)
        n = len(ids)
        done = sum(run("ddcutil", "setvcp", "d6", "4", "--display", i).returncode == 0 for i in ids)
        if n and done == n:
            break
        time.sleep(3)
    print("DIMMED %d/%d monitors" % (done, n))
    if not n or done < n:
        sys.exit(8)

SUNSHINE_UNIT = "app-dev.lizardbyte.app.Sunshine.service"

def sunshine():
    """Make sure Sunshine (the Moonlight host) is running in the user's desktop session.
    Its user unit is WantedBy=graphical-session.target, which Cinnamon/MATE (Linux Mint) never
    activates, so after a boot nothing starts it and Moonlight shows the PC as offline."""
    if not shutil.which("sunshine"):
        print("sunshine isn't installed"); return
    import pwd
    pw = pwd.getpwnam(user)
    d = os.path.join(pw.pw_dir, ".config/autostart")
    f = os.path.join(d, "turnon-sunshine.desktop")
    if put(f, "[Desktop Entry]\nType=Application\nName=Sunshine (turnon)\n"
           "Exec=sh -c 'systemctl --user import-environment DISPLAY XAUTHORITY WAYLAND_DISPLAY; "
           "systemctl --user start %s'\nX-GNOME-Autostart-enabled=true\n" % SUNSHINE_UNIT):
        for q in (d, f):
            os.chown(q, pw.pw_uid, pw.pw_gid)
    for _ in range(45):  # wait for the desktop session
        if gui_logged_in():
            break
        time.sleep(2)
    else:
        print("no desktop session yet; Sunshine will start with the desktop autostart"); return
    env = ["env", "XDG_RUNTIME_DIR=/run/user/%d" % pw.pw_uid]
    def uctl(*a):
        return run("runuser", "-u", user, "--", *env, "systemctl", "--user", *a)
    time.sleep(5)  # let the session export DISPLAY etc.
    if "DISPLAY=" not in uctl("show-environment").stdout:
        uctl("set-environment", "DISPLAY=:0")
    for _ in range(3):
        if uctl("is-active", SUNSHINE_UNIT).stdout.strip() != "active":
            uctl("start", SUNSHINE_UNIT)
        for _ in range(15):
            if ":47989" in run("ss", "-ltn").stdout:
                print("SUNSHINE running"); return
            time.sleep(2)
    print("SUNSHINE didn't start"); sys.exit(9)

if mode == "sunshine":
    sunshine()
elif mode == "dim":
    dim()
elif mode == "autologin":
    if autologin():
        if gui_logged_in():
            print("CHANGED (someone is logged in; takes effect on next boot)")
        else:
            subprocess.run(["systemctl", "restart", "display-manager"])
            print("CHANGED (display manager restarted)")
    else:
        print("OK")
else:
    windows()
'''


def linux_remote(mode):
    b64 = base64.b64encode(REMOTE.encode()).decode()
    args = " ".join(shlex.quote(a) for a in (mode, linux_user(), os.environ.get("SDDM_SESSION", "")))
    cmd = (f"printf '%s\\n' {shlex.quote(LINUX_PW)} | sudo -S -p '' "
           f"python3 -c \"import base64;exec(base64.b64decode('{b64}'))\" {args}")
    r = ssh(linux_user(), cmd, timeout=180)
    out = (r.stdout + r.stderr).strip()
    if r.returncode:
        sys.exit(f"Remote step '{mode}' failed ({r.returncode}): {out}")
    return out


# --------------------------------------------------------- Windows (remote) -
WINLOGON = r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"


def windows_autologin():
    """Enable Windows auto-login. Returns True if anything had to change."""
    r = wssh(f'reg query "{WINLOGON}"')
    if r.returncode:
        sys.exit(f"Couldn't read the Windows registry (need an admin account over SSH): {r.stdout}{r.stderr}")
    have = {m.group(1): m.group(2).strip()
            for m in re.finditer(r"^\s+(\w+)\s+REG_\w+\s*(.*)$", r.stdout, re.M)}
    want = {"AutoAdminLogon": "1", "DefaultUserName": win_user(), "DefaultPassword": WIN_PW}
    changed = False
    for name, val in want.items():
        if have.get(name) != val:
            wssh(f'reg add "{WINLOGON}" /v {name} /t REG_SZ /d "{val}" /f')
            changed = True
    if "AutoLogonCount" in have:
        wssh(f'reg delete "{WINLOGON}" /v AutoLogonCount /f')
        changed = True
    if changed:  # Win11: lets password auto-login work when "passwordless" is on
        wssh(r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device" '
                        r'/v DevicePasswordLessBuildVersion /t REG_DWORD /d 0 /f')
    return changed


# Windows monitor control (DDC/CI via dxva2). SSH sessions can't see the monitors, so the PowerShell
# script is run through an interactive scheduled task (runs in the logged-in desktop session).
WIN_DIM_PS1 = r'''Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Dim {
  delegate bool Cb(IntPtr m, IntPtr dc, IntPtr r, IntPtr d);
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  struct P { public IntPtr h; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string s; }
  [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr dc, IntPtr c, Cb f, IntPtr d);
  [DllImport("dxva2.dll")] static extern bool GetNumberOfPhysicalMonitorsFromHMONITOR(IntPtr m, out uint n);
  [DllImport("dxva2.dll")] static extern bool GetPhysicalMonitorsFromHMONITOR(IntPtr m, uint n, [Out] P[] a);
  [DllImport("dxva2.dll")] static extern bool GetMonitorBrightness(IntPtr h, out uint lo, out uint cur, out uint hi);
  [DllImport("dxva2.dll")] static extern bool SetMonitorBrightness(IntPtr h, uint v);
  [DllImport("dxva2.dll")] static extern bool DestroyPhysicalMonitor(IntPtr h);
  [DllImport("dxva2.dll")] static extern bool SetVCPFeature(IntPtr h, byte code, uint v);
  public static int Total, Done;
  public static void Run() {
    Total = 0; Done = 0;
    EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (m, dc, r, d) => {
      uint n; if (!GetNumberOfPhysicalMonitorsFromHMONITOR(m, out n) || n == 0) return true;
      var a = new P[n]; if (!GetPhysicalMonitorsFromHMONITOR(m, n, a)) return true;
      foreach (var p in a) {
        uint lo, cur, hi; Total++;
        if (SetVCPFeature(p.h, 0xD6, 4)) Done++;
        DestroyPhysicalMonitor(p.h);
      }
      return true;
    }, IntPtr.Zero);
  }
}
'@
for ($i = 0; $i -lt 5; $i++) { [Dim]::Run(); if ([Dim]::Total -gt 0 -and [Dim]::Done -eq [Dim]::Total) { break }; Start-Sleep 3 }
$ok = [Dim]::Total -gt 0 -and [Dim]::Done -eq [Dim]::Total
"DIMMED $([Dim]::Done)/$([Dim]::Total) monitors" | Out-File C:\ProgramData\turnon\dim.log -Encoding ascii -Force
if (-not $ok) { exit 8 }
'''
WIN_DIM_DIR = r"C:\ProgramData\turnon"
WIN_DIM_TASK = "TurnOnDim"


def windows_dim(script=None):
    """Put every monitor in DDC standby from inside the Windows desktop session."""
    gz = base64.b64encode(gzip.compress((script or WIN_DIM_PS1).encode())).decode()
    install = (f"$d='{WIN_DIM_DIR}'; New-Item -ItemType Directory -Force $d | Out-Null; "
               f"$g=New-Object IO.MemoryStream (,[Convert]::FromBase64String('{gz}')); "
               "$z=New-Object IO.Compression.GzipStream($g,[IO.Compression.CompressionMode]::Decompress); "
               "$o=New-Object IO.MemoryStream; $z.CopyTo($o); "
               "[IO.File]::WriteAllBytes(\"$d\\dim.ps1\", $o.ToArray())")
    enc = base64.b64encode(install.encode("utf-16-le")).decode()
    r = wssh("powershell -NoProfile -EncodedCommand " + enc)
    if r.returncode:
        return print(f"Couldn't install the monitor script on Windows: {r.stdout}{r.stderr}".strip())
    log = rf"{WIN_DIM_DIR}\dim.log"
    wssh(f'del /q "{log}"')
    # never-firing trigger: the task only runs when we ask (/it = only in the user's logged-in session)
    tr = rf'powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File {WIN_DIM_DIR}\dim.ps1'
    r = wssh(f'schtasks /create /tn {WIN_DIM_TASK} /tr "{tr}" /sc once /sd 01/01/2000 /st 00:00 '
             f'/ru {win_user()} /it /rl highest /f')
    if r.returncode:
        return print(f"Couldn't create the monitor task: {r.stdout}{r.stderr}".strip())
    wssh(f"schtasks /run /tn {WIN_DIM_TASK}")
    for _ in range(20):
        time.sleep(2)
        out = wssh(f'type "{log}"').stdout.strip()
        if out.startswith("DIMMED"):
            return print(f"Windows: {out}")
    print("Windows: monitor dimming didn't report back (is the desktop logged in?)")


# ------------------------------------------------------------------- flow ---
def reboot_and_wait(label):
    print(f"{label} ...", end="", flush=True)
    if not wait_for_shutdown():
        sys.exit("\nPC didn't go down for the reboot.")
    print(" down. Waiting for it to come back", end="", flush=True)
    cur = wait_for_os()
    print()
    if not cur:
        sys.exit("Timed out waiting for the PC to come back up (is sshd running on that OS?).")
    return cur


def dim_monitors(cur):
    """DDC standby on the PC's monitors (fully black).
    Disable with NO_DIM=1 or "dim": false in ~/.turnon.json."""
    if os.environ.get("NO_DIM") or _load().get("dim") is False:
        return
    if cur == "linux":
        print(f"Monitors: {linux_remote('dim')}")
    else:
        windows_dim()


def boot(target):
    if not LINUX_PW or not WIN_PW:
        sys.exit("Set \"linux_password\" and \"win_password\" in ~/.turnon.json")
    if not host():
        wake()
        print("Magic packet sent, but I don't know the PC's IP yet (looking for it on the network).")
        for _ in range(30):  # PC appears in ARP once it's up
            time.sleep(4)
            if host():
                break
        else:
            sys.exit("Couldn't find the PC. Set PC_HOST=<ip>.")
    cur = detect()
    fresh = cur == "off"  # the PC (re)booted during this run
    print(f"PC is currently: {cur}")
    if cur == "off":
        wake()
        print("Wake-on-LAN sent. Waiting for the PC to boot", end="", flush=True)
        cur = wait_for_os()
        print()
        if not cur:
            sys.exit("PC never came up. Check WoL (BIOS, NIC, BROADCAST) and that sshd runs on both OSes.")
        print(f"PC is up in {cur}.")
    elif cur == "unknown":
        sys.exit("PC answers on port 22 but SSH login failed. Check LINUX_USER/WIN_USER (or ~/.turnon.json) and the password.")

    for _ in range(4):
        if cur == "linux":
            if target == "linux":
                print(f"Linux auto-login: {linux_remote('autologin')}")
                print(f"Sunshine: {linux_remote('sunshine')}")
                if fresh:
                    dim_monitors("linux")
                return print("Done: PC is in Linux and logged in.")
            print("Setting GRUB next-boot to Windows and rebooting ...")
            print(linux_remote("windows"))
            cur = reboot_and_wait("Rebooting")
            fresh = True
        else:  # windows
            changed = windows_autologin()
            if target == "windows" and not changed:
                if fresh:
                    dim_monitors("windows")
                return print("Done: PC is in Windows and logged in.")
            print("Enabled Windows auto-login; " if changed else "", end="")
            print("rebooting Windows" + (" (GRUB then boots Linux)" if target == "windows" else " into Linux"))
            wssh("shutdown /r /t 0")
            cur = reboot_and_wait("Rebooting")
            fresh = True
    sys.exit("Gave up: PC didn't settle into the requested OS.")


def launch_moonlight(target):
    """Open Moonlight's host list (click the PC named 'linux' / 'windows' to pick an app)."""
    if os.environ.get("NO_MOONLIGHT") or not os.path.exists(MOONLIGHT):
        return print("Moonlight not found; skipping.")
    print(f"Waiting {MOONLIGHT_DELAY}s for Moonlight to see the PC ...", flush=True)
    time.sleep(MOONLIGHT_DELAY)
    print(f"Opening Moonlight (pick '{target}' to choose an app)")
    subprocess.run(["open", "-a", "Moonlight"])


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if not arg:
        print("Boot the PC into:\n  1) Linux\n  2) Windows")
        arg = {"1": "linux", "2": "windows", "l": "linux", "w": "windows"}.get(input("> ").strip().lower(), "")
    if arg == "status":
        print(detect())
    elif arg in ("linux", "windows"):
        boot(arg)
        launch_moonlight(arg)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nAborted.")
