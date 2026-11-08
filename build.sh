#!/bin/sh
# Builds "PC Boot.app" and installs it to /Applications (pass --no-install to skip).
set -e
cd "$(dirname "$0")"
APP="build/PC Boot.app"
rm -rf build && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" build/icon.iconset

swiftc -O -swift-version 5 -parse-as-library app/PCBoot.swift -o "$APP/Contents/MacOS/PCBoot"
cp turnon.py "$APP/Contents/Resources/turnon.py"

swiftc app/make_icon.swift -o build/make_icon && build/make_icon build/icon.png
for s in 16 32 128 256 512; do
  sips -z $s $s build/icon.png --out build/icon.iconset/icon_${s}x${s}.png >/dev/null
  sips -z $((s*2)) $((s*2)) build/icon.png --out build/icon.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns build/icon.iconset -o "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'P'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>PC Boot</string>
<key>CFBundleDisplayName</key><string>PC Boot</string>
<key>CFBundleIdentifier</key><string>com.thewhynow.pcboot</string>
<key>CFBundleExecutable</key><string>PCBoot</string>
<key>CFBundleIconFile</key><string>AppIcon</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSLocalNetworkUsageDescription</key><string>Wake and control your PC on the local network.</string>
</dict></plist>
P
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

if [ "$1" != "--no-install" ]; then
  rm -rf "/Applications/PC Boot.app"
  cp -R "$APP" /Applications/
  echo "Installed /Applications/PC Boot.app"
fi
