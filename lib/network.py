"""Network configuration for vibehost-setup."""

from rich.console import Console

from lib.config import VibehostConfig
from lib.ssh import SSHConnection

console = Console()


def create_private_network(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create the private bridge network for inter-container communication."""
    console.print("[cyan]Creating private network...[/cyan]")

    network_name = "vibenet-private"
    private = config.network.private

    # Check if network exists
    result = ssh.conn.run(f"sudo incus network show {network_name}", warn=True, hide=True)

    if result.ok:
        console.print(f"[yellow]Network '{network_name}' already exists, skipping...[/yellow]")
        return

    # Create the bridge network
    ssh.sudo(f"incus network create {network_name} --type=bridge", hide=True)

    # Configure the network
    ssh.sudo(f"incus network set {network_name} ipv4.address={private.gateway}/24", hide=True)
    ssh.sudo(f"incus network set {network_name} ipv4.nat=true", hide=True)
    ssh.sudo(f"incus network set {network_name} ipv6.address=none", hide=True)

    console.print(f"[green]✓ Private network '{network_name}' created ({private.subnet})[/green]")


def create_macvlan_profiles(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create macvlan network profiles for public IP assignment.

    These profiles attach the macvlan network device to containers.
    The actual IP configuration is done via systemd-networkd in containers.py,
    since Debian 13+ images don't include cloud-init.
    """
    console.print("[cyan]Creating macvlan profiles for public IPs...[/cyan]")

    interface = config.network.interface

    # Create a macvlan network
    macvlan_name = "vibenet-public"
    result = ssh.conn.run(f"sudo incus network show {macvlan_name}", warn=True, hide=True)

    if not result.ok:
        ssh.sudo(f"incus network create {macvlan_name} --type=macvlan parent={interface}", hide=True)
        console.print(f"[dim]  Created macvlan network: {macvlan_name}[/dim]")

    # Create profile for each container with public IP
    containers = {
        "dev": config.network.ips.dev,
        "staging": config.network.ips.staging,
        "prod": config.network.ips.prod,
    }

    for container_name, ip in containers.items():
        profile_name = f"public-{container_name}"

        # Check if profile exists
        result = ssh.conn.run(f"sudo incus profile show {profile_name}", warn=True, hide=True)

        if not result.ok:
            ssh.sudo(f"incus profile create {profile_name}", hide=True)

        # Add the macvlan device to the profile
        # Note: IP configuration is done via systemd-networkd in containers.py
        ssh.sudo(
            f"incus profile device add {profile_name} eth0 nic "
            f"network={macvlan_name} name=eth0 || true",
            hide=True,
        )

        console.print(f"[dim]  Created profile: {profile_name} ({ip})[/dim]")

    console.print("[green]✓ Macvlan profiles created for public IPs[/green]")


def create_private_network_profile(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create profile for private network attachment."""
    console.print("[cyan]Creating private network profile...[/cyan]")

    private = config.network.private

    # Container private IPs
    container_ips = {
        "dev": "10.10.10.2",
        "staging": "10.10.10.3",
        "prod": "10.10.10.4",
        "postgres": private.postgres,
    }

    for container_name, ip in container_ips.items():
        profile_name = f"private-{container_name}"

        # Check if profile exists
        result = ssh.conn.run(f"sudo incus profile show {profile_name}", warn=True, hide=True)

        if not result.ok:
            ssh.sudo(f"incus profile create {profile_name}", hide=True)

        # Add the private network device
        ssh.sudo(
            f"incus profile device add {profile_name} eth1 nic "
            f"network=vibenet-private name=eth1 "
            f"ipv4.address={ip} || true",
            hide=True,
        )

        console.print(f"[dim]  Created profile: {profile_name} ({ip})[/dim]")

    console.print("[green]✓ Private network profiles created[/green]")


def verify_network(ssh: SSHConnection) -> bool:
    """Verify network configuration is correct."""
    console.print("[cyan]Verifying network configuration...[/cyan]")

    # Check private network exists (needs sudo)
    result = ssh.conn.run("sudo incus network show vibenet-private", warn=True, hide=True)
    if not result.ok:
        console.print("[red]✗ Private network not found[/red]")
        return False

    # Check macvlan network exists
    result = ssh.conn.run("sudo incus network show vibenet-public", warn=True, hide=True)
    if not result.ok:
        console.print("[red]✗ Public macvlan network not found[/red]")
        return False

    console.print("[green]✓ Network verification passed[/green]")
    return True


def configure_ufw_for_incus(ssh: SSHConnection) -> None:
    """Configure UFW to allow Incus bridge traffic.

    UFW's default 'deny routed' policy blocks container NAT traffic.
    This adds rules to allow traffic on the incusbr0 and vibenet-private bridges.
    """
    console.print("[cyan]Configuring UFW for Incus bridges...[/cyan]")

    # Allow traffic on incusbr0 (Incus default NAT bridge)
    ssh.sudo("ufw allow in on incusbr0", hide=True)
    ssh.sudo("ufw allow out on incusbr0", hide=True)
    ssh.sudo("ufw route allow in on incusbr0", hide=True)
    ssh.sudo("ufw route allow out on incusbr0", hide=True)

    # Allow traffic on vibenet-private (private network bridge)
    ssh.sudo("ufw allow in on vibenet-private", hide=True)
    ssh.sudo("ufw allow out on vibenet-private", hide=True)
    ssh.sudo("ufw route allow in on vibenet-private", hide=True)
    ssh.sudo("ufw route allow out on vibenet-private", hide=True)

    console.print("[green]✓ UFW configured for Incus bridges[/green]")


def setup_network(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Run all network setup steps."""
    console.print("\n[bold blue]Phase 5: Network Configuration[/bold blue]\n")

    create_private_network(ssh, config)
    create_macvlan_profiles(ssh, config)
    create_private_network_profile(ssh, config)
    configure_ufw_for_incus(ssh)

    if not verify_network(ssh):
        raise RuntimeError("Network verification failed")

    console.print("\n[bold green]✓ Network configuration complete[/bold green]")
