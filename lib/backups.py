"""Backup configuration for vibehost-setup."""

from rich.console import Console

from lib.config import VibehostConfig
from lib.ssh import SSHConnection

console = Console()


def generate_storagebox_key(ssh: SSHConnection, config: VibehostConfig) -> str | None:
    """Generate SSH key for Hetzner Storage Box if needed.

    Returns the public key if generated, None if key already exists.
    """
    if not config.backups.offsite.enabled:
        return None

    key_path = config.backups.offsite.ssh_key_path
    pub_key_path = f"{key_path}.pub"

    console.print("[cyan]Setting up Storage Box SSH key...[/cyan]")

    # Check if key already exists
    if ssh.file_exists(key_path):
        console.print("[yellow]Storage Box key already exists[/yellow]")
        return ssh.run(f"cat {pub_key_path}", hide=True)

    # Generate new ed25519 key
    ssh.sudo(f'ssh-keygen -t ed25519 -N "" -f {key_path}', hide=True)

    pub_key = ssh.run(f"cat {pub_key_path}", hide=True)
    console.print("[green]✓ Storage Box SSH key generated[/green]")
    console.print(f"\n[yellow]Add this public key to your Hetzner Storage Box:[/yellow]")
    console.print(f"[dim]{pub_key}[/dim]\n")

    return pub_key


def create_snapshot_script(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create the local snapshot backup script."""
    console.print("[cyan]Creating snapshot backup script...[/cyan]")

    retention_days = config.backups.snapshots.retention_days

    script = f"""#!/bin/bash
# vibehost snapshot backup script
# Runs daily, keeps snapshots for {retention_days} days

set -e

DATE=$(date +%Y%m%d)
RETENTION_DAYS={retention_days}

# Containers to snapshot
CONTAINERS="dev staging prod postgres"

echo "Starting snapshot backup - $DATE"

for CONTAINER in $CONTAINERS; do
    echo "Snapshotting $CONTAINER..."

    # Create snapshot
    incus snapshot create $CONTAINER daily-$DATE

    # Clean up old snapshots
    incus snapshot list $CONTAINER -f csv | grep "^daily-" | while read SNAP; do
        SNAP_NAME=$(echo $SNAP | cut -d',' -f1)
        SNAP_DATE=$(echo $SNAP_NAME | sed 's/daily-//')

        # Calculate age in days
        SNAP_EPOCH=$(date -d "$SNAP_DATE" +%s 2>/dev/null || echo 0)
        NOW_EPOCH=$(date +%s)
        AGE_DAYS=$(( (NOW_EPOCH - SNAP_EPOCH) / 86400 ))

        if [ $AGE_DAYS -gt $RETENTION_DAYS ]; then
            echo "Deleting old snapshot: $SNAP_NAME (age: $AGE_DAYS days)"
            incus snapshot delete $CONTAINER $SNAP_NAME
        fi
    done
done

# Also dump postgres databases
echo "Dumping PostgreSQL databases..."
mkdir -p /var/lib/incus/backups/postgres

for DB in app_dev app_staging app_prod; do
    incus exec postgres -- sudo -u postgres pg_dump $DB > /var/lib/incus/backups/postgres/$DB-$DATE.sql

    # Compress
    gzip -f /var/lib/incus/backups/postgres/$DB-$DATE.sql

    # Keep only last {retention_days} dumps
    ls -t /var/lib/incus/backups/postgres/$DB-*.sql.gz 2>/dev/null | tail -n +$((RETENTION_DAYS + 1)) | xargs -r rm
done

echo "Snapshot backup complete!"
"""

    ssh.write_file("/usr/local/bin/vibehost-snapshot", script, mode="755")
    console.print("[green]✓ Snapshot script created[/green]")


def create_offsite_script(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create the offsite backup script for Hetzner Storage Box."""
    if not config.backups.offsite.enabled:
        console.print("[dim]Offsite backups disabled, skipping...[/dim]")
        return

    console.print("[cyan]Creating offsite backup script...[/cyan]")

    offsite = config.backups.offsite
    retention_weeks = offsite.retention_weeks

    script = f"""#!/bin/bash
# vibehost offsite backup script
# Runs weekly, keeps backups for {retention_weeks} weeks

set -e

DATE=$(date +%Y%m%d)
RETENTION_WEEKS={retention_weeks}
STORAGEBOX_HOST="{offsite.storagebox_host}"
STORAGEBOX_USER="{offsite.storagebox_user}"
SSH_KEY="{offsite.ssh_key_path}"
BACKUP_DIR="/var/lib/incus/backups/offsite"

mkdir -p $BACKUP_DIR

echo "Starting offsite backup - $DATE"

# Export containers
CONTAINERS="dev staging prod postgres"

for CONTAINER in $CONTAINERS; do
    echo "Exporting $CONTAINER..."

    EXPORT_FILE="$BACKUP_DIR/$CONTAINER-$DATE.tar.gz"
    incus export $CONTAINER $EXPORT_FILE --optimized-storage

    echo "Uploading $CONTAINER to Storage Box..."
    sftp -i $SSH_KEY -oBatchMode=yes $STORAGEBOX_USER@$STORAGEBOX_HOST << EOF
mkdir backups
mkdir backups/$CONTAINER
put $EXPORT_FILE backups/$CONTAINER/
EOF

    # Clean up local export
    rm -f $EXPORT_FILE
done

# Upload postgres dumps
echo "Uploading PostgreSQL dumps..."
sftp -i $SSH_KEY -oBatchMode=yes $STORAGEBOX_USER@$STORAGEBOX_HOST << EOF
mkdir backups/postgres-dumps
put /var/lib/incus/backups/postgres/*.sql.gz backups/postgres-dumps/
EOF

# Clean up old backups on Storage Box (keeping last {retention_weeks} weeks)
echo "Cleaning up old backups on Storage Box..."
ssh -i $SSH_KEY $STORAGEBOX_USER@$STORAGEBOX_HOST << 'REMOTE_EOF'
cd backups
for dir in dev staging prod postgres; do
    if [ -d "$dir" ]; then
        cd $dir
        ls -t *.tar.gz 2>/dev/null | tail -n +{retention_weeks + 1} | xargs -r rm
        cd ..
    fi
done
cd postgres-dumps
ls -t *.sql.gz 2>/dev/null | tail -n +{retention_weeks * 7 + 1} | xargs -r rm
REMOTE_EOF

echo "Offsite backup complete!"
"""

    ssh.write_file("/usr/local/bin/vibehost-offsite-backup", script, mode="755")
    console.print("[green]✓ Offsite backup script created[/green]")


def setup_cron_jobs(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Set up cron jobs for automated backups."""
    console.print("[cyan]Setting up backup cron jobs...[/cyan]")

    cron_entries = []

    # Snapshot schedule
    if config.backups.snapshots.enabled:
        schedule = config.backups.snapshots.schedule
        cron_entries.append(f"{schedule} /usr/local/bin/vibehost-snapshot >> /var/log/vibehost-snapshot.log 2>&1")

    # Offsite schedule
    if config.backups.offsite.enabled:
        schedule = config.backups.offsite.schedule
        cron_entries.append(f"{schedule} /usr/local/bin/vibehost-offsite-backup >> /var/log/vibehost-offsite.log 2>&1")

    if cron_entries:
        cron_content = "# vibehost backup jobs\n" + "\n".join(cron_entries) + "\n"
        ssh.write_file("/etc/cron.d/vibehost-backups", cron_content)

    console.print("[green]✓ Backup cron jobs configured[/green]")


def test_backup_connectivity(ssh: SSHConnection, config: VibehostConfig) -> bool:
    """Test connectivity to Hetzner Storage Box."""
    if not config.backups.offsite.enabled:
        return True

    console.print("[cyan]Testing Storage Box connectivity...[/cyan]")

    offsite = config.backups.offsite
    result = ssh.conn.run(
        f"ssh -i {offsite.ssh_key_path} -o BatchMode=yes -o ConnectTimeout=10 "
        f"{offsite.storagebox_user}@{offsite.storagebox_host} echo 'connection ok'",
        warn=True,
        hide=True,
    )

    if result.ok:
        console.print("[green]✓ Storage Box connectivity verified[/green]")
        return True
    else:
        console.print("[yellow]⚠ Could not connect to Storage Box[/yellow]")
        console.print("[dim]Please add the SSH public key to your Storage Box and run backups manually[/dim]")
        return False


def run_initial_snapshot(ssh: SSHConnection) -> None:
    """Run an initial snapshot backup."""
    console.print("[cyan]Running initial snapshot backup...[/cyan]")

    result = ssh.conn.run("/usr/local/bin/vibehost-snapshot", warn=True, hide=False)

    if result.ok:
        console.print("[green]✓ Initial snapshot complete[/green]")
    else:
        console.print("[yellow]⚠ Initial snapshot had issues (check logs)[/yellow]")


def setup_backups(ssh: SSHConnection, config: VibehostConfig) -> str | None:
    """Run all backup setup steps.

    Returns the Storage Box public key if generated.
    """
    console.print("\n[bold blue]Phase 10: Backup Configuration[/bold blue]\n")

    # Create backup directories
    ssh.sudo("mkdir -p /var/lib/incus/backups/postgres", hide=True)
    ssh.sudo("mkdir -p /var/lib/incus/backups/offsite", hide=True)

    pub_key = generate_storagebox_key(ssh, config)
    create_snapshot_script(ssh, config)
    create_offsite_script(ssh, config)
    setup_cron_jobs(ssh, config)
    test_backup_connectivity(ssh, config)
    run_initial_snapshot(ssh)

    console.print("\n[bold green]✓ Backup configuration complete[/bold green]")

    return pub_key
