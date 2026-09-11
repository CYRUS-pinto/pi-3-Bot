#!/bin/bash
# TARS USB-tether watchdog: gives the mini phone an IP every time it (re)connects.
# Works whether the Pi uses dhcpcd or dhclient, and whether the phone enumerates
# as rndis_host (Samsung/Pixel) or cdc_ether. Called by 99-tars-tether.rules.
# TARS itself already rediscovers the camera stream (FreshFrameGrabber auto-reconnect),
# so this script only fixes the IP layer; the app heals the rest.
IFACE="${1:-usb0}"
for _ in $(seq 1 10); do
    ip link show "$IFACE" >/dev/null 2>&1 && break
    sleep 1
done
if command -v dhcpcd >/dev/null 2>&1; then
    dhcpcd --rebind "$IFACE" 2>/dev/null || dhcpcd "$IFACE" 2>/dev/null
elif command -v dhclient >/dev/null 2>&1; then
    dhclient -r "$IFACE" 2>/dev/null
    dhclient "$IFACE" 2>/dev/null
fi
# fallback: link-local so the phone is at least reachable for diagnostics
ip addr show "$IFACE" 2>/dev/null | grep -q "inet " || ip addr add 192.168.42.137/24 dev "$IFACE" 2>/dev/null
logger -t tars-tether "tether iface $IFACE ready: $(ip -o -4 addr show "$IFACE" 2>/dev/null | awk '{print $4}')"
