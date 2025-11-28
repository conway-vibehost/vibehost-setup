"""PostgreSQL setup for vibehost-setup."""

from rich.console import Console

from lib.config import VibehostConfig, DatabaseConfig
from lib.ssh import SSHConnection, ContainerExec

console = Console()


def install_postgres(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Install PostgreSQL in the postgres container."""
    console.print("[cyan]Installing PostgreSQL...[/cyan]")

    exec_cmd = ContainerExec(ssh, "postgres")
    version = config.postgres.version

    # Update and install postgres
    exec_cmd.run("apt-get update", hide=True)
    exec_cmd.run(f"apt-get install -y postgresql-{version} postgresql-contrib-{version}", hide=True)

    console.print(f"[green]✓ PostgreSQL {version} installed[/green]")


def parse_memory_to_mb(memory_str: str) -> int:
    """Parse memory string like '16GB' to megabytes."""
    memory_str = memory_str.upper().strip()
    if memory_str.endswith("GB"):
        return int(memory_str[:-2]) * 1024
    elif memory_str.endswith("MB"):
        return int(memory_str[:-2])
    elif memory_str.endswith("G"):
        return int(memory_str[:-1]) * 1024
    elif memory_str.endswith("M"):
        return int(memory_str[:-1])
    else:
        # Assume GB if no unit
        return int(memory_str) * 1024


def configure_postgres(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Configure PostgreSQL for network access and performance."""
    console.print("[cyan]Configuring PostgreSQL...[/cyan]")

    exec_cmd = ContainerExec(ssh, "postgres")
    version = config.postgres.version
    private_ip = config.network.private.postgres
    subnet = config.network.private.subnet

    # PostgreSQL config paths
    conf_dir = f"/etc/postgresql/{version}/main"

    # Configure postgresql.conf to listen on private network
    exec_cmd.run(f"sed -i \"s/#listen_addresses = 'localhost'/listen_addresses = 'localhost,{private_ip}'/\" {conf_dir}/postgresql.conf")

    # Configure pg_hba.conf to allow connections from the private subnet
    pg_hba_entry = f"""
# vibehost: Allow connections from private network
host    all             all             {subnet}            scram-sha-256
"""
    exec_cmd.append_file(f"{conf_dir}/pg_hba.conf", pg_hba_entry)

    console.print(f"[green]✓ PostgreSQL configured to listen on {private_ip}[/green]")

    # Performance tuning based on container memory allocation
    tune_postgres_performance(ssh, config)

    # Restart PostgreSQL to apply all changes
    exec_cmd.run("systemctl restart postgresql")


def tune_postgres_performance(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Tune PostgreSQL performance based on allocated resources.

    Settings based on container memory and SSD storage assumptions.
    See: https://pgtune.leopard.in.ua/ for reference calculations.
    """
    console.print("[cyan]Tuning PostgreSQL performance...[/cyan]")

    exec_cmd = ContainerExec(ssh, "postgres")
    version = config.postgres.version
    conf_dir = f"/etc/postgresql/{version}/main"

    # Get container memory allocation
    memory_mb = parse_memory_to_mb(config.resources.postgres.memory)

    # Calculate tuning parameters
    # shared_buffers: 25% of RAM (max reasonable ~8GB for most workloads)
    shared_buffers_mb = min(memory_mb // 4, 8192)

    # effective_cache_size: 75% of RAM (OS will cache the rest)
    effective_cache_size_mb = (memory_mb * 3) // 4

    # work_mem: RAM / max_connections / 4 (conservative for many concurrent queries)
    # Assuming ~100 active connections
    work_mem_mb = max(memory_mb // 400, 16)  # At least 16MB

    # maintenance_work_mem: RAM / 8 (max 2GB)
    maintenance_work_mem_mb = min(memory_mb // 8, 2048)

    # wal_buffers: 3% of shared_buffers (max 64MB)
    wal_buffers_mb = min((shared_buffers_mb * 3) // 100, 64)
    wal_buffers_mb = max(wal_buffers_mb, 4)  # At least 4MB

    # Create performance configuration
    perf_config = f"""
# vibehost: PostgreSQL performance tuning
# Based on {memory_mb}MB container allocation

# Memory Settings
shared_buffers = {shared_buffers_mb}MB
effective_cache_size = {effective_cache_size_mb}MB
work_mem = {work_mem_mb}MB
maintenance_work_mem = {maintenance_work_mem_mb}MB
wal_buffers = {wal_buffers_mb}MB

# Connection Settings
max_connections = 200

# Checkpoint Settings
checkpoint_completion_target = 0.9
min_wal_size = 1GB
max_wal_size = 4GB

# SSD-optimized Settings (assuming ZFS on SSD)
random_page_cost = 1.1
effective_io_concurrency = 200

# Query Planner
default_statistics_target = 100

# Logging (useful for debugging, not too verbose)
log_min_duration_statement = 1000
log_checkpoints = on
log_connections = on
log_disconnections = on
log_lock_waits = on

# Parallel Query (use available cores)
max_parallel_workers_per_gather = 4
max_parallel_workers = 8
max_parallel_maintenance_workers = 4
"""

    exec_cmd.write_file(f"{conf_dir}/conf.d/99-vibehost-tuning.conf", perf_config)

    console.print(f"[dim]  shared_buffers: {shared_buffers_mb}MB[/dim]")
    console.print(f"[dim]  effective_cache_size: {effective_cache_size_mb}MB[/dim]")
    console.print(f"[dim]  work_mem: {work_mem_mb}MB[/dim]")
    console.print(f"[dim]  maintenance_work_mem: {maintenance_work_mem_mb}MB[/dim]")
    console.print("[green]✓ PostgreSQL performance tuned for container resources[/green]")


def create_databases(ssh: SSHConnection, config: VibehostConfig) -> dict[str, str]:
    """Create databases and users as specified in config.

    Returns a dict mapping database names to their passwords.
    """
    console.print("[cyan]Creating databases and users...[/cyan]")

    exec_cmd = ContainerExec(ssh, "postgres")
    passwords = {}

    for db in config.postgres.databases:
        db_name = db.name
        db_user = db.user
        db_password = db.actual_password

        # Store the password for handoff doc
        passwords[db_name] = db_password

        # Create user if not exists
        exec_cmd.run(
            f"sudo -u postgres psql -c \"DO \\$\\$ BEGIN "
            f"IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{db_user}') THEN "
            f"CREATE ROLE {db_user} LOGIN PASSWORD '{db_password}'; "
            f"END IF; END \\$\\$;\"",
            hide=True,
        )

        # Update password in case user exists
        exec_cmd.run(
            f"sudo -u postgres psql -c \"ALTER USER {db_user} PASSWORD '{db_password}'\"",
            hide=True,
        )

        # Create database if not exists
        exec_cmd.run(
            f"sudo -u postgres psql -c \"SELECT 1 FROM pg_database WHERE datname = '{db_name}'\" | grep -q 1 || "
            f"sudo -u postgres createdb -O {db_user} {db_name}",
            hide=True,
        )

        # Grant privileges
        exec_cmd.run(
            f"sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {db_user}\"",
            hide=True,
        )

        console.print(f"[dim]  Created database: {db_name} (user: {db_user})[/dim]")

    console.print("[green]✓ Databases and users created[/green]")
    return passwords


def test_postgres_connectivity(ssh: SSHConnection, config: VibehostConfig) -> bool:
    """Test PostgreSQL connectivity from the dev container."""
    console.print("[cyan]Testing database connectivity from dev container...[/cyan]")

    dev_exec = ContainerExec(ssh, "dev")
    postgres_ip = config.network.private.postgres

    # Install postgresql client in dev container
    dev_exec.run("apt-get update && apt-get install -y postgresql-client", hide=True)

    # Test connection to first database
    if config.postgres.databases:
        db = config.postgres.databases[0]
        console.print(f"[dim]Testing connection to {db.name} as {db.user}[/dim]")
        cmd = f"incus exec dev -- bash -c 'PGPASSWORD={db.actual_password} psql -h {postgres_ip} -U {db.user} -d {db.name} -c \"SELECT 1\"'"
        result = ssh.conn.sudo(cmd, warn=True, hide=False)

        if result.ok:
            console.print("[green]✓ Database connectivity verified from dev container[/green]")
            return True
        else:
            console.print("[red]✗ Database connectivity test failed[/red]")
            return False

    return True


def setup_postgres(ssh: SSHConnection, config: VibehostConfig) -> dict[str, str]:
    """Run all PostgreSQL setup steps.

    Returns a dict mapping database names to their passwords.
    """
    console.print("\n[bold blue]Phase 7: PostgreSQL Setup[/bold blue]\n")

    install_postgres(ssh, config)
    configure_postgres(ssh, config)
    passwords = create_databases(ssh, config)

    if not test_postgres_connectivity(ssh, config):
        console.print("[yellow]⚠ PostgreSQL connectivity test failed - verify manually after provisioning[/yellow]")

    console.print("\n[bold green]✓ PostgreSQL setup complete[/bold green]")

    return passwords
