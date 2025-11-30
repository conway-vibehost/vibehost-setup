"""Container creation and setup for vibehost-setup."""

import time

from rich.console import Console

from lib.config import VibehostConfig
from lib.ssh import SSHConnection, ContainerExec

console = Console()


def launch_container(
    ssh: SSHConnection,
    name: str,
    image: str,
    profiles: list[str],
) -> None:
    """Launch a single container with specified profiles."""
    console.print(f"[cyan]Launching container '{name}'...[/cyan]")

    # Check if container already exists
    result = ssh.conn.sudo(f"incus info {name}", warn=True, hide=True)
    if result.ok:
        console.print(f"[yellow]Container '{name}' already exists, skipping...[/yellow]")
        return

    # Build profile flags
    profile_flags = " ".join(f"-p {p}" for p in profiles)

    # Launch container
    ssh.sudo(f"incus launch images:{image} {name} {profile_flags}", hide=True)

    console.print(f"[green]✓ Container '{name}' launched[/green]")


def wait_for_container(ssh: SSHConnection, name: str, timeout: int = 120) -> bool:
    """Wait for a container to be ready (cloud-init complete)."""
    console.print(f"[dim]Waiting for {name} to be ready...[/dim]")

    start = time.time()
    while time.time() - start < timeout:
        # Check if container is running
        result = ssh.conn.sudo(f"incus info {name} | grep 'Status: RUNNING'", warn=True, hide=True)
        if not result.ok:
            time.sleep(2)
            continue

        # Check if cloud-init is done
        result = ssh.conn.sudo(
            f"incus exec {name} -- test -f /var/lib/cloud/instance/boot-finished",
            warn=True,
            hide=True,
        )
        if result.ok:
            console.print(f"[green]✓ {name} is ready[/green]")
            return True

        # Also check if cloud-init exists (some images don't have it)
        result = ssh.conn.sudo(
            f"incus exec {name} -- which cloud-init",
            warn=True,
            hide=True,
        )
        if not result.ok:
            # No cloud-init, just check if we can run commands
            result = ssh.conn.sudo(f"incus exec {name} -- echo ready", warn=True, hide=True)
            if result.ok:
                console.print(f"[green]✓ {name} is ready (no cloud-init)[/green]")
                return True

        time.sleep(2)

    console.print(f"[red]✗ Timeout waiting for {name}[/red]")
    return False


def configure_public_network(
    ssh: SSHConnection,
    container: str,
    ip: str,
    gateway: str,
    cidr: str,
) -> None:
    """Configure eth0 with a static public IP via systemd-networkd.

    Debian 13+ images don't include cloud-init, so we configure the network
    directly using systemd-networkd instead of relying on cloud-init profiles.
    """
    console.print(f"[dim]Configuring public network in {container} ({ip})...[/dim]")

    exec_cmd = ContainerExec(ssh, container)

    eth0_network = f"""[Match]
Name=eth0

[Network]
Address={ip}/{cidr}
Gateway={gateway}
DNS=1.1.1.1
DNS=8.8.8.8
"""
    exec_cmd.write_file("/etc/systemd/network/eth0.network", eth0_network)


def configure_private_network(ssh: SSHConnection, container: str) -> None:
    """Configure eth1 for DHCP to get private network IP.

    The default Debian container image only has eth0 configured.
    We need to add systemd-networkd config for eth1 to get DHCP from vibenet-private.
    """
    console.print(f"[dim]Configuring private network in {container}...[/dim]")

    exec_cmd = ContainerExec(ssh, container)

    eth1_network = """[Match]
Name=eth1

[Network]
DHCP=yes

[DHCPv4]
UseDomains=true
UseMTU=true

[DHCP]
ClientIdentifier=mac
"""
    exec_cmd.write_file("/etc/systemd/network/eth1.network", eth1_network)


def setup_container_ssh(ssh: SSHConnection, config: VibehostConfig, container: str) -> None:
    """Set up SSH access in a container."""
    console.print(f"[dim]Setting up SSH in {container}...[/dim]")

    exec_cmd = ContainerExec(ssh, container)

    # Install SSH server
    exec_cmd.run("apt-get update && apt-get install -y openssh-server", hide=True)

    # Set up root SSH access with customer's key
    exec_cmd.run("mkdir -p /root/.ssh")
    exec_cmd.run("chmod 700 /root/.ssh")
    exec_cmd.write_file("/root/.ssh/authorized_keys", config.admin.ssh_public_key + "\n", mode="600")

    # Configure SSH to allow root login with key
    exec_cmd.run("sed -i 's/#PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config")
    exec_cmd.run("sed -i 's/PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config")

    # Start SSH service
    exec_cmd.run("systemctl enable ssh")
    exec_cmd.run("systemctl start ssh")

    console.print(f"[green]✓ SSH configured in {container}[/green]")


def apply_common_setup(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Apply common setup to specified containers."""
    console.print("[cyan]Applying common setup to containers...[/cyan]")

    common = config.common_setup

    for container in common.containers:
        console.print(f"\n[dim]Setting up {container}...[/dim]")
        exec_cmd = ContainerExec(ssh, container)

        # Install common packages
        if common.packages:
            pkg_list = " ".join(common.packages)
            exec_cmd.run(f"apt-get update && apt-get install -y {pkg_list}", hide=True)

        # Configure firewall
        exec_cmd.run("apt-get install -y ufw", hide=True)
        exec_cmd.run("ufw --force reset", hide=True)
        exec_cmd.run("ufw default deny incoming", hide=True)
        exec_cmd.run("ufw default allow outgoing", hide=True)

        for rule in common.firewall.allow:
            exec_cmd.run(f"ufw allow {rule}", hide=True)

        exec_cmd.run("ufw --force enable", hide=True)

        console.print(f"[green]✓ Common setup applied to {container}[/green]")


def create_containers(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create all containers."""
    console.print("\n[bold blue]Phase 6: Container Creation[/bold blue]\n")

    containers_config = config.containers

    # Define container configurations
    # docker-ready profile enables nested containers for Docker support on ZFS
    containers = [
        {
            "name": "dev",
            "image": containers_config.get_image("dev"),
            "profiles": ["default", "dev-pool", "docker-ready", "public-dev", "private-dev"],
        },
        {
            "name": "staging",
            "image": containers_config.get_image("staging"),
            "profiles": ["default", "staging-pool", "docker-ready", "public-staging", "private-staging"],
        },
        {
            "name": "prod",
            "image": containers_config.get_image("prod"),
            "profiles": ["default", "prod-pool", "docker-ready", "public-prod", "private-prod"],
        },
        {
            "name": "postgres",
            "image": containers_config.get_image("postgres"),
            "profiles": ["default", "db-pool", "private-postgres"],
        },
    ]

    # Launch all containers
    for c in containers:
        launch_container(ssh, c["name"], c["image"], c["profiles"])

    # Wait for all containers to be ready
    console.print("\n[cyan]Waiting for containers to be ready...[/cyan]")
    for c in containers:
        if not wait_for_container(ssh, c["name"]):
            raise RuntimeError(f"Container {c['name']} failed to start")

    # Configure public network (eth0) with static IPs for public containers
    # Debian 13+ doesn't have cloud-init, so we configure via systemd-networkd
    console.print("\n[cyan]Configuring public network in containers...[/cyan]")

    # Get network config for CIDR calculation
    netmask = config.network.netmask
    if netmask.startswith("255"):
        mask_map = {
            "255.255.255.0": "24",
            "255.255.255.128": "25",
            "255.255.255.192": "26",
            "255.255.0.0": "16",
        }
        cidr = mask_map.get(netmask, "24")
    else:
        cidr = netmask.lstrip("/")

    public_containers = {
        "dev": config.network.ips.dev,
        "staging": config.network.ips.staging,
        "prod": config.network.ips.prod,
    }

    for name, ip in public_containers.items():
        configure_public_network(ssh, name, ip, config.network.gateway, cidr)

    # Configure private network (eth1) in all containers for DHCP
    console.print("\n[cyan]Configuring private network in containers...[/cyan]")
    for c in containers:
        configure_private_network(ssh, c["name"])

    # Restart networkd in all containers to apply both eth0 and eth1 configs
    console.print("\n[cyan]Applying network configuration...[/cyan]")
    for c in containers:
        exec_cmd = ContainerExec(ssh, c["name"])
        exec_cmd.run("systemctl restart systemd-networkd", hide=True)

    # Set up SSH in public containers
    console.print("\n[cyan]Setting up SSH access in containers...[/cyan]")
    for c in containers:
        if c["name"] != "postgres":  # postgres doesn't need SSH
            setup_container_ssh(ssh, config, c["name"])

    console.print("\n[bold green]✓ Container creation complete[/bold green]")
