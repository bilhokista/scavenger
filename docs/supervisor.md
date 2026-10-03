# Running the supervisor

The supervisor watches the daemon from the outside. It must never run
inside the daemon process: a daemon that supervises itself cannot kill
itself when it spins.

What it checks every run: daemon heartbeat freshness, money and token
spend in the last hour, and spin (same action fingerprint with the same
non-pending result that many rounds in a row). On any trigger it kills
the daemon pid, marks active missions `stopped_emergency`, writes
REPORT.md, and notifies. Exit code 0 when quiet, 3 when it acted.

## cron (Linux, macOS)

```cron
*/5 * * * * scavenger supervise --once --config /path/scavenger.toml
```

## systemd service + timer (Linux)

`/etc/systemd/system/scavenger-supervise.service`:

```ini
[Unit]
Description=Scavenger supervisor check

[Service]
Type=oneshot
ExecStart=/usr/local/bin/scavenger supervise --once --config /path/scavenger.toml
```

`/etc/systemd/system/scavenger-supervise.timer`:

```ini
[Unit]
Description=Scavenger supervisor every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
```

Enable with `systemctl enable --now scavenger-supervise.timer`.

## Windows Task Scheduler

```cmd
schtasks /Create /TN "ScavengerSupervisor" /TR "scavenger supervise --once --config C:\path\scavenger.toml" /SC MINUTE /MO 5
```
