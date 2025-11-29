"""Host hardening functions for vibehost-setup."""

from rich.console import Console

from lib.config import VibehostConfig
from lib.ssh import SSHConnection

console = Console()


def enable_contrib_repo(ssh: SSHConnection) -> None:
    """Enable contrib repo (needed for ZFS)."""
    console.print("[cyan]Enabling contrib repository...[/cyan]")

    # Check if contrib is already enabled
    result = ssh.run("grep -q 'contrib' /etc/apt/sources.list", warn=True, hide=True)
    if result:
        console.print("[dim]contrib repo already enabled[/dim]")
        return

    # Add contrib to sources.list
    ssh.sudo(
        "sed -i 's/main non-free-firmware/main contrib non-free-firmware/g' /etc/apt/sources.list",
        hide=True,
    )
    # Also handle case where it's just 'main'
    ssh.sudo(
        "sed -i 's/^\\(deb.*\\) main$/\\1 main contrib/' /etc/apt/sources.list",
        hide=True,
    )

    console.print("[green]✓ contrib repository enabled[/green]")


def update_system(ssh: SSHConnection) -> None:
    """Update system packages."""
    console.print("[cyan]Updating system packages...[/cyan]")
    ssh.sudo("apt-get update", hide=True)
    ssh.sudo("DEBIAN_FRONTEND=noninteractive apt-get upgrade -y", hide=True)
    console.print("[green]✓ System packages updated[/green]")


def create_admin_user(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Create the admin user with sudo privileges."""
    username = config.admin.username
    pubkey = config.admin.ssh_public_key

    console.print(f"[cyan]Creating admin user '{username}'...[/cyan]")

    # Check if user already exists
    result = ssh.conn.run(f"id {username}", warn=True, hide=True)
    if result.ok:
        console.print(f"[yellow]User '{username}' already exists, updating...[/yellow]")
    else:
        # Create user with home directory
        ssh.sudo(f"useradd -m -s /bin/bash {username}")

    # Add to sudo group
    ssh.sudo(f"usermod -aG sudo {username}")

    # Set up passwordless sudo
    ssh.write_file(
        f"/etc/sudoers.d/{username}",
        f"{username} ALL=(ALL) NOPASSWD:ALL\n",
        mode="440",
    )

    # Set up SSH directory and authorized_keys
    ssh.sudo(f"mkdir -p /home/{username}/.ssh")
    ssh.sudo(f"chmod 700 /home/{username}/.ssh")
    ssh.write_file(f"/home/{username}/.ssh/authorized_keys", f"{pubkey}\n", mode="600")
    ssh.sudo(f"chown -R {username}:{username} /home/{username}/.ssh")

    console.print(f"[green]✓ Admin user '{username}' created with SSH key[/green]")


def verify_ssh_key_access(ssh: SSHConnection, config: VibehostConfig) -> bool:
    """Verify the admin user can authenticate with SSH key before disabling password auth.

    This is a critical safety check for servers without console access.
    """
    console.print("[dim]Verifying SSH key access for admin user...[/dim]")

    # Check that the admin user exists and has the SSH key
    admin = config.admin.username
    result = ssh.conn.sudo(f"test -f /home/{admin}/.ssh/authorized_keys", warn=True, hide=True)
    if not result.ok:
        console.print(f"[red]Admin user {admin} has no authorized_keys file![/red]")
        return False

    # Check the key is actually in the file
    result = ssh.conn.sudo(
        f"grep -q '{config.admin.ssh_public_key[:50]}' /home/{admin}/.ssh/authorized_keys",
        warn=True,
        hide=True,
    )
    if not result.ok:
        console.print(f"[red]SSH key not found in {admin}'s authorized_keys![/red]")
        return False

    console.print(f"[green]  SSH key verified for {admin}[/green]")
    return True


def harden_ssh(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Harden SSH configuration."""
    console.print("[cyan]Hardening SSH configuration...[/cyan]")

    # Safety check: verify admin can log in with key before disabling password auth
    if not verify_ssh_key_access(ssh, config):
        raise RuntimeError(
            "Cannot verify SSH key access for admin user. "
            "Refusing to disable password auth to prevent lockout."
        )

    sshd_config = """
# vibehost hardened SSH config
Port 22
HostKey /etc/ssh/ssh_host_ed25519_key
HostKey /etc/ssh/ssh_host_rsa_key

# Authentication
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
PermitEmptyPasswords no
KbdInteractiveAuthentication no
UsePAM yes

# Security
X11Forwarding no
AllowTcpForwarding yes
MaxAuthTries 3
MaxSessions 10
ClientAliveInterval 300
ClientAliveCountMax 2

# Allowed users (admin + original ssh user if different)
AllowUsers {allowed_users}
""".format(
        allowed_users=f"{config.admin.username} {config.server.ssh_user}".strip()
        if config.server.ssh_user != config.admin.username
        else config.admin.username
    )

    # Backup original config
    ssh.sudo("cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup")

    # Write new config
    ssh.write_file("/etc/ssh/sshd_config", sshd_config)

    # Test config before restarting (needs sudo to read config)
    result = ssh.conn.run("sudo sshd -t", warn=True, hide=True)
    if not result.ok:
        console.print(f"[red]SSH config test failed: {result.stderr}[/red]")
        ssh.sudo("mv /etc/ssh/sshd_config.backup /etc/ssh/sshd_config")
        raise RuntimeError("SSH configuration test failed")

    # Restart SSH service
    ssh.sudo("systemctl restart sshd")

    console.print("[green]✓ SSH hardened (root login disabled, key-only auth)[/green]")


def setup_firewall(ssh: SSHConnection) -> None:
    """Set up UFW firewall on the host."""
    console.print("[cyan]Setting up UFW firewall...[/cyan]")

    # Install ufw if not present
    ssh.sudo("apt-get install -y ufw", hide=True)

    # Reset and configure
    ssh.sudo("ufw --force reset", hide=True)
    ssh.sudo("ufw default deny incoming", hide=True)
    ssh.sudo("ufw default allow outgoing", hide=True)

    # Allow SSH
    ssh.sudo("ufw allow 22/tcp", hide=True)

    # Enable firewall
    ssh.sudo("ufw --force enable", hide=True)

    console.print("[green]✓ UFW firewall configured (SSH only)[/green]")


def install_crowdsec(ssh: SSHConnection) -> None:
    """Install and configure CrowdSec for intrusion prevention."""
    console.print("[cyan]Installing CrowdSec...[/cyan]")

    # Ensure curl is installed (needed for repo setup)
    ssh.sudo("apt-get install -y curl", hide=True)

    # Add CrowdSec repository - need to run the whole pipeline as root
    ssh.sudo(
        "bash -c 'curl -s https://install.crowdsec.net | bash'",
        hide=True,
    )

    # Update package lists after adding repo
    ssh.sudo("apt-get update", hide=True)

    # Install crowdsec and firewall bouncer
    # Use -o options to handle config file conflicts non-interactively
    ssh.sudo(
        "DEBIAN_FRONTEND=noninteractive apt-get install -y "
        "-o Dpkg::Options::='--force-confnew' "
        "crowdsec crowdsec-firewall-bouncer-iptables",
        hide=True,
    )

    # Enable and start services
    ssh.sudo("systemctl enable crowdsec", hide=True)
    ssh.sudo("systemctl start crowdsec", hide=True)
    ssh.sudo("systemctl enable crowdsec-firewall-bouncer", hide=True)
    ssh.sudo("systemctl start crowdsec-firewall-bouncer", hide=True)

    console.print("[green]✓ CrowdSec installed and running[/green]")


def setup_unattended_upgrades(ssh: SSHConnection) -> None:
    """Set up automatic security updates."""
    console.print("[cyan]Setting up unattended security upgrades...[/cyan]")

    ssh.sudo("apt-get install -y unattended-upgrades apt-listchanges", hide=True)

    # Configure for security updates only
    config = """
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
Unattended-Upgrade::Origins-Pattern {
    "origin=Debian,codename=${distro_codename},label=Debian-Security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
"""

    ssh.write_file("/etc/apt/apt.conf.d/20auto-upgrades", config)

    # Enable the service
    ssh.sudo("systemctl enable unattended-upgrades", hide=True)
    ssh.sudo("systemctl start unattended-upgrades", hide=True)

    console.print("[green]✓ Unattended security upgrades configured[/green]")


def configure_kernel_params(ssh: SSHConnection) -> None:
    """Configure kernel parameters for production Incus.

    See: https://linuxcontainers.org/incus/docs/main/reference/server_settings/
    """
    console.print("[cyan]Configuring kernel parameters for Incus...[/cyan]")

    sysctl_config = """# vibehost: Kernel parameters for Incus production
# See: https://linuxcontainers.org/incus/docs/main/reference/server_settings/

# Async I/O - increase for database workloads (MySQL, etc)
fs.aio-max-nr = 524288

# inotify limits - needed for many containers with file watchers
fs.inotify.max_queued_events = 1048576
fs.inotify.max_user_instances = 1048576
fs.inotify.max_user_watches = 1048576

# Restrict container access to kernel ring buffer
kernel.dmesg_restrict = 1

# Key ring limits - should exceed number of containers
kernel.keys.maxbytes = 2000000
kernel.keys.maxkeys = 2000

# eBPF JIT limit
net.core.bpf_jit_limit = 1000000000

# ARP table size - prevents neighbor table overflow with many containers
net.ipv4.neigh.default.gc_thresh3 = 8192
net.ipv6.neigh.default.gc_thresh3 = 8192

# Memory map areas - needed for many applications
vm.max_map_count = 262144
"""

    ssh.write_file("/etc/sysctl.d/99-vibehost-incus.conf", sysctl_config)

    # Apply immediately
    ssh.sudo("sysctl --system", hide=True)

    console.print("[green]✓ Kernel parameters configured for Incus production[/green]")


def harden_host(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Run all host hardening steps."""
    console.print("\n[bold blue]Phase 2: Host Hardening[/bold blue]\n")

    enable_contrib_repo(ssh)
    update_system(ssh)
    create_admin_user(ssh, config)
    setup_firewall(ssh)
    install_crowdsec(ssh)
    setup_unattended_upgrades(ssh)
    configure_kernel_params(ssh)

    # Harden SSH last since it will lock out root
    console.print("\n[yellow]⚠ About to harden SSH - root login will be disabled[/yellow]")
    harden_ssh(ssh, config)

    console.print("\n[bold green]✓ Host hardening complete[/bold green]")
