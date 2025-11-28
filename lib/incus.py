"""Incus installation and setup for vibehost-setup."""

from rich.console import Console

from lib.config import VibehostConfig
from lib.ssh import SSHConnection

console = Console()


def install_incus(ssh: SSHConnection) -> None:
    """Install incus and ZFS from official Debian repos."""
    console.print("[cyan]Installing Incus and ZFS...[/cyan]")

    # Install ZFS with DKMS (required for storage backend)
    # Need kernel headers for DKMS to build the module
    ssh.sudo(
        "apt-get install -y linux-headers-$(uname -r) zfsutils-linux zfs-dkms",
        hide=True,
    )
    console.print("[dim]  ZFS packages installed[/dim]")

    # Ensure DKMS module is built and loaded
    ssh.sudo("dkms autoinstall", hide=True)
    ssh.sudo("modprobe zfs", hide=True)
    console.print("[dim]  ZFS kernel module loaded[/dim]")

    # Incus is in Debian 13 repos
    ssh.sudo("apt-get install -y incus incus-client", hide=True)

    console.print("[green]✓ Incus and ZFS installed[/green]")


def initialize_incus(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Initialize incus with sensible defaults."""
    console.print("[cyan]Initializing Incus...[/cyan]")

    # Check if already initialized (needs sudo)
    result = ssh.conn.run("sudo incus storage list", warn=True, hide=True)
    if result.ok and "default" in result.stdout:
        console.print("[yellow]Incus already initialized, skipping...[/yellow]")
        return

    # Determine storage configuration
    storage_device = config.storage.device if config.storage else None
    storage_size = config.storage.size if config.storage else "100GiB"

    if storage_device:
        # Use dedicated block device for ZFS
        console.print(f"[dim]Using dedicated device: {storage_device}[/dim]")
        storage_config = f'source: {storage_device}'
        storage_msg = f"ZFS on {storage_device}"
    else:
        # Use loopback file (default)
        storage_config = f'size: {storage_size}'
        storage_msg = f"ZFS loopback ({storage_size})"

    # Create preseed config for non-interactive init
    preseed = f"""
config: {{}}
networks:
- config:
    ipv4.address: auto
    ipv6.address: none
  description: ""
  name: incusbr0
  type: bridge
storage_pools:
- config:
    {storage_config}
  description: ""
  name: default
  driver: zfs
profiles:
- config: {{}}
  description: Default profile
  devices:
    root:
      path: /
      pool: default
      type: disk
    eth0:
      name: eth0
      network: incusbr0
      type: nic
  name: default
cluster: null
"""

    ssh.write_file("/tmp/incus-preseed.yaml", preseed)
    ssh.sudo("incus admin init --preseed < /tmp/incus-preseed.yaml", hide=True)
    ssh.sudo("rm /tmp/incus-preseed.yaml", hide=True)

    console.print(f"[green]✓ Incus initialized with {storage_msg}[/green]")


def create_resource_profiles(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create resource pool profiles for containers."""
    console.print("[cyan]Creating resource profiles...[/cyan]")

    profiles = {
        "dev-pool": config.resources.dev,
        "staging-pool": config.resources.staging,
        "prod-pool": config.resources.prod,
        "db-pool": config.resources.postgres,
    }

    for profile_name, resources in profiles.items():
        # Check if profile exists (needs sudo)
        result = ssh.conn.run(f"sudo incus profile show {profile_name}", warn=True, hide=True)

        if not result.ok:
            # Create new profile
            ssh.sudo(f"incus profile create {profile_name}", hide=True)

        # Set resource limits
        # Memory limit (soft limit that can burst)
        ssh.sudo(f"incus profile set {profile_name} limits.memory={resources.memory}", hide=True)

        # CPU allowance (percentage of total CPU time)
        ssh.sudo(f"incus profile set {profile_name} limits.cpu.allowance={resources.cpu_allowance}", hide=True)

        # CPU priority (0-10, higher is more priority)
        ssh.sudo(f"incus profile set {profile_name} limits.cpu.priority={resources.cpu_priority}", hide=True)

        console.print(f"  [dim]Created profile: {profile_name}[/dim]")

    console.print("[green]✓ Resource profiles created[/green]")


def create_docker_profile(ssh: SSHConnection) -> None:
    """Create a profile with Docker-compatible security settings.

    These settings allow Docker to run properly inside containers on ZFS.
    See: https://discuss.linuxcontainers.org/t/it-appears-docker-now-works-fine-on-incus-containers-with-zfs-storage/23332
    """
    console.print("[cyan]Creating Docker-compatible profile...[/cyan]")

    profile_name = "docker-ready"

    # Check if profile exists (needs sudo)
    result = ssh.conn.run(f"sudo incus profile show {profile_name}", warn=True, hide=True)

    if not result.ok:
        ssh.sudo(f"incus profile create {profile_name}", hide=True)

    # Set security options for Docker compatibility
    # security.nesting: allows nested containers (required for Docker)
    ssh.sudo(f"incus profile set {profile_name} security.nesting=true", hide=True)

    # security.syscalls.intercept.mknod: allows creating device nodes
    ssh.sudo(f"incus profile set {profile_name} security.syscalls.intercept.mknod=true", hide=True)

    # security.syscalls.intercept.setxattr: allows setting extended attributes (for overlay2)
    ssh.sudo(f"incus profile set {profile_name} security.syscalls.intercept.setxattr=true", hide=True)

    console.print("[green]✓ Docker-compatible profile created[/green]")


def verify_incus(ssh: SSHConnection) -> bool:
    """Verify incus is properly set up."""
    console.print("[cyan]Verifying Incus installation...[/cyan]")

    # Check storage pool (needs sudo since user isn't in incus group)
    result = ssh.conn.run("sudo incus storage list -f csv", warn=True, hide=True)
    if not result.ok or "default" not in result.stdout:
        console.print("[red]✗ Storage pool not found[/red]")
        return False

    # Check profiles
    result = ssh.conn.run("sudo incus profile list -f csv", warn=True, hide=True)
    if not result.ok:
        console.print("[red]✗ Could not list profiles[/red]")
        return False

    required_profiles = ["dev-pool", "staging-pool", "prod-pool", "db-pool", "docker-ready"]
    for profile in required_profiles:
        if profile not in result.stdout:
            console.print(f"[red]✗ Profile '{profile}' not found[/red]")
            return False

    console.print("[green]✓ Incus verification passed[/green]")
    return True


def setup_incus(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Run all incus setup steps."""
    console.print("\n[bold blue]Phase 3: Incus Installation[/bold blue]\n")

    install_incus(ssh)
    initialize_incus(ssh, config)

    console.print("\n[bold blue]Phase 4: Resource Pool Profiles[/bold blue]\n")

    create_resource_profiles(ssh, config)
    create_docker_profile(ssh)

    if not verify_incus(ssh):
        raise RuntimeError("Incus verification failed")

    console.print("\n[bold green]✓ Incus setup complete[/bold green]")
