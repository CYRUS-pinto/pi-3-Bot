# TARS Backup — How to Send & Restore

This folder (`TARS_Phase1_Pygame`) is a full backup point. Everything is also
versioned in git and on GitHub, so there are 3 independent copies.

## What's what (in `C:\Users\Cyrus\Downloads\`)

| File | Contents |
|------|----------|
| `TARS-backup-NEW-flickevent-20260912.zip` | Flick-event speed model (fast repeats) |
| `TARS-backup-OLD-version-20260912.zip` | Pre-flick-event version (stable timing) |
| `TARS-backup-HOLDBASE-20260912.zip` | Crowds + accuracy + walk-veto base |
| `TARS-backup-PREHYSTERESIS-20260912.zip` | Pre-hysteresis (stable holds v1) |
| `TARS-backup-AUDIT2-20260912.zip` | Current best: audit hardening II (banked dwell, teleport cut, kind toggles) |

## Git tags (exact snapshots, on GitHub `CYRUS-pinto/pi-3-Bot`)

| Tag | Contents |
|-----|----------|
| `backup-flick-event-20260912` | Flick-event model |
| `backup-oldversion-20260912` | Old stable version |
| `backup-holdbase-20260912` | Base before hold-gestures |
| `backup-prehysteresis-20260912` | Pre-hysteresis (stable holds v1) |
| `backup-audit2-20260912` | Current best: audit hardening II |

## How to SEND a backup to someone

**Option A — zip file (simplest):**
1. Copy the `.zip` from `C:\Users\Cyrus\Downloads\` to a pen drive / Google Drive / WhatsApp.
2. The other person unzips it anywhere and copies the contents to their
   `TARS_Phase1_Pygame` folder (or straight to the Pi: `/home/cyrus/TARS/`).

**Option B — git tag (exact, tiny):**
1. Make sure it's pushed: `git push origin <tag-name>` (all tags above are pushed).
2. The other person runs:
   ```
   git fetch origin
   git checkout <tag-name>
   ```
   They now have the exact snapshot. To make it live again:
   `git checkout -b restore-<name>` then deploy to the Pi as usual.

## How to RESTORE on the Pi (rollback)

```
cd "C:\Users\Cyrus\Downloads\New folder (76) - Copy\TARS_Phase1_Pygame"
git checkout <tag-name> -- config.py vision.py main.py bridge.py
git commit -m "Restore <tag-name>"
git push origin main
```
Then copy the 4 files to `/home/cyrus/TARS/` on the Pi (or run the usual
deploy), restore timing in Pi `calibration.json` if needed, and:
```
sudo systemctl restart tars
```
Verify: `sudo journalctl -u tars --since '-2 min' | grep -E 'locked|HEARTBEAT'`

## Golden rule

**The Pi (`/home/cyrus/TARS/`) is the live robot. GitHub is the vault.
This folder is the workbench. Never lose all three at once.**
