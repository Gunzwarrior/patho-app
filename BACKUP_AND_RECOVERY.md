# Operational backup and pending-Case rescue

`backup_operational_db.py` is deliberately independent of Streamlit and the
PathoPilot database module. It uses SQLite's online `Connection.backup()` API,
never copies a live database file, and requires explicit source and destination
paths. It is intended to run from `systemd` every ten minutes.

## Behavior and retention

Each successful run:

1. makes an online SQLite snapshot in the configured backup directory;
2. runs `PRAGMA integrity_check` against that temporary snapshot;
3. generates `latest_pending_cases.html` from **the verified snapshot**, using
   only saved Case fields (`rendered_html`, structured input, clinical info,
   pending reason, frozen Preset identity, and timestamps); it never renders
   against current templates/content;
4. atomically publishes the new uniquely named backup and replaces the rescue
   HTML only after both artifacts are complete; and
5. removes only its own old `pathopilot-YYYYMMDDTHHMMSSZ.sqlite` files.

The supplied initial retention is 288 frequent snapshots (48 hours at ten
minutes) plus one older snapshot per UTC day for 14 days. This is deliberately
modest and configurable in `/etc/pathopilot/backup.env`. A failed snapshot or
rescue generation leaves the previously published rescue file and all prior
backups intact.

## Accepted production deployment

The accepted LXC deployment is:

- application and operational DB: `/root/patho-app/pathology.db`;
- backup mount in the LXC: `/mnt/pathopilot-backups`;
- host backing path: `/mnt/storage/work/anapath/pathopilot-backups`;
- systemd service account: `root` in the unprivileged LXC; and
- retention: 288 frequent snapshots plus 14 daily snapshots.

The mount is outside the LXC filesystem, so it protects against loss of the
LXC's local storage. It remains important to include the host-backed storage
in the Proxmox/storage backup policy appropriate to its failure domain.

The timer is enabled and a timer-triggered backup has completed successfully.
The checked-in environment template and service unit match this deployment.

The host's existing daily off-site backup includes the newest PathoPilot SQLite
snapshot and `latest_pending_cases.html` only, rather than the local rolling
snapshot history. A downloaded Google Drive archive was checked and contained
both artifacts; the extracted SQLite snapshot independently returned
`PRAGMA integrity_check = ok`.

For a rebuild of this configuration:

```bash
sudo install -d -o root -g root -m 0700 /etc/pathopilot
sudo install -m 0600 deploy/systemd/backup.env.example /etc/pathopilot/backup.env
sudo install -m 0644 deploy/systemd/pathopilot-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/pathopilot-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pathopilot-backup.timer
sudo systemctl start pathopilot-backup.service
sudo systemctl status pathopilot-backup.timer pathopilot-backup.service
```

Check the configured destination contains an independently readable `.sqlite`
backup and `latest_pending_cases.html`. `systemctl list-timers pathopilot-backup.timer`
should show the next run. If the deployment paths change, update
`/etc/pathopilot/backup.env` rather than relying on these recorded values.

## Manual restore / disaster drill

Never overwrite the live database for the first drill. With PathoPilot stopped
only if you are performing an actual replacement, use a copy of a verified
backup:

```bash
cp /mnt/pathopilot-backups/pathopilot-YYYYMMDDTHHMMSSZ.sqlite /tmp/pathopilot-restore-drill.db
sqlite3 /tmp/pathopilot-restore-drill.db 'PRAGMA integrity_check;'
PATHOPILOT_DB_NAME=/tmp/pathopilot-restore-drill.db /root/patho-app/venv/bin/streamlit run /root/patho-app/app.py
```

For the required PR3 drill, first save a recognizable pending Case, trigger
`sudo systemctl start pathopilot-backup.service`, inspect the rescue page for
that saved Case, then run the commands above. In the disposable app, open that
pending Case and a representative validated Case. Confirm the pending report
and saved inputs are present and the validated report is frozen as expected.
Stop the disposable Streamlit process and remove only
`/tmp/pathopilot-restore-drill.db` afterwards.

For a real restoration, stop PathoPilot, preserve the failed live database as
a separately named forensic copy, copy the selected backup into the configured
live database path, verify `PRAGMA integrity_check`, then start PathoPilot and
perform the same representative checks. If SQLite recovery is impossible, use
`latest_pending_cases.html` to recover the saved pending findings manually;
it is not an automated reconstruction mechanism.

## Accepted manual drill

On 2026-09-16, a real backup independently returned `PRAGMA integrity_check =
ok`; the rescue HTML was readable; and a newly saved recognizable pending Case
appeared in both a fresh backup and rescue file. That backup was copied to
`/tmp/pathopilot-restore-drill.db`, passed integrity verification, and booted
PathoPilot via `PATHOPILOT_DB_NAME`. The pending Case was present and correct.
The disposable instance was stopped, and normal PathoPilot was restarted
successfully against the operational database. The daily off-site archive was
also downloaded and inspected: it contained the newest SQLite snapshot and
rescue HTML, and its extracted SQLite snapshot passed independent integrity
verification.
