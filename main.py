#!/usr/bin/env python3
"""vibehost-setup: One-shot provisioning for dedicated servers."""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from lib.config import load_config, validate_config, VibehostConfig
from lib.ssh import SSHConnection
from lib.host import harden_host
from lib.incus import setup_incus
from lib.network import setup_network
from lib.containers import create_containers, apply_common_setup
from lib.postgres import setup_postgres
from lib.dev_setup import setup_dev_container
from lib.backups import setup_backups
from lib.handoff import generate_handoff

console = Console()


def print_banner():
    """Print the vibehost-setup banner."""
    banner = """
    ▄   ▄█ ▄▄▄▄▀ █▄▄▄▄ ██   ▄  █ ████▄ ▄▄▄▄▄      ▄▄▄▄▄   ▄███▄      ▄▄▄▄▀ ▄   ▄▄▄▄▄
     █  ██ ▀▀▀ █    █  ▄▀ █  █ █  █ █   █ █     ▀▀▀▀▄   █▀   ▀  ▀▀▀ █   █  █     ▀▀▀▀▄
█     █ ██     █    █▀▀▌  ██▀▀█ █   █ ▄  ▀▀▀█       ▄▀▀▀▀▄   ██▄▄       █  █ █       ▄▀▀▀▀▄
 █    █ ▐█    █     █  █  █   █ ▀████ ▀▄▄▄▄▀    ▀▀▀▀▀▄   █▄   ▄▀     █    █ █    ▀▀▀▀▀▄
  █  █   ▐   ▀        █      █                           ▀███▀     ▀    █  █
   █▐                ▀      ▀                                           ▀   ▀
"""
    console.print(Panel.fit(
        "[bold cyan]vibehost-setup[/bold cyan]\n"
        "[dim]One-shot provisioning for dedicated servers[/dim]",
        border_style="blue",
    ))


def phase1_validate(config_path: str, dry_run: bool = False) -> tuple[VibehostConfig, SSHConnection]:
    """Phase 1: Connection & Validation."""
    console.print("\n[bold blue]Phase 1: Connection & Validation[/bold blue]\n")

    # Load config
    console.print(f"[cyan]Loading config from {config_path}...[/cyan]")
    try:
        config = load_config(config_path)
        console.print("[green]✓ Config loaded and validated[/green]")
    except Exception as e:
        console.print(f"[red]✗ Config error: {e}[/red]")
        sys.exit(1)

    # Run additional validation
    warnings = validate_config(config)
    for warning in warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    # Test SSH connection
    console.print(f"[cyan]Connecting to {config.server.host}...[/cyan]")
    ssh = SSHConnection(config)

    if not ssh.test_connection():
        console.print("[red]✗ Could not connect to server[/red]")
        sys.exit(1)
    console.print("[green]✓ SSH connection established[/green]")

    # Verify Debian 12+
    console.print("[cyan]Verifying operating system...[/cyan]")
    if not ssh.verify_debian(min_version=12):
        os_info = ssh.get_os_info()
        console.print(f"[red]✗ Expected Debian 12+, found: {os_info.get('PRETTY_NAME', 'unknown')}[/red]")
        sys.exit(1)
    debian_version = ssh.get_debian_version()
    console.print(f"[green]✓ Debian {debian_version} confirmed[/green]")

    # Check resources
    console.print("[cyan]Checking available resources...[/cyan]")
    resources = ssh.get_resources()
    console.print(f"  [dim]Memory: {resources['memory_gb']} GB[/dim]")
    console.print(f"  [dim]CPU cores: {resources['cpu_cores']}[/dim]")
    console.print(f"  [dim]Disk free: {resources['disk_free_gb']} GB[/dim]")

    if resources["memory_gb"] < 8:
        console.print("[yellow]⚠ Less than 8GB RAM - performance may be limited[/yellow]")
    if resources["disk_free_gb"] < 50:
        console.print("[yellow]⚠ Less than 50GB disk space[/yellow]")

    console.print("[green]✓ Resource check complete[/green]")

    if dry_run:
        console.print("\n[bold yellow]DRY RUN: Validation complete. Exiting without making changes.[/bold yellow]")
        ssh.close()
        sys.exit(0)

    return config, ssh


def run_provisioning(config: VibehostConfig, ssh: SSHConnection, skip_backups: bool = False) -> dict:
    """Run all provisioning phases."""
    results = {
        "db_passwords": {},
        "storagebox_pubkey": None,
    }

    try:
        # Phase 2: Host Hardening
        harden_host(ssh, config)

        # Phase 3 & 4: Incus Installation & Resource Profiles
        setup_incus(ssh, config)

        # Phase 5: Network Configuration
        setup_network(ssh, config)

        # Phase 6: Container Creation
        create_containers(ssh, config)

        # Phase 7: PostgreSQL Setup
        results["db_passwords"] = setup_postgres(ssh, config)

        # Phase 8: Dev Container Setup
        setup_dev_container(ssh, config)

        # Phase 9: Common Container Setup
        console.print("\n[bold blue]Phase 9: Common Container Setup[/bold blue]\n")
        apply_common_setup(ssh, config)

        # Phase 10: Backup Configuration
        if not skip_backups:
            results["storagebox_pubkey"] = setup_backups(ssh, config)
        else:
            console.print("\n[yellow]Skipping backup configuration (--skip-backups)[/yellow]")

    except Exception as e:
        console.print(f"\n[bold red]Error during provisioning: {e}[/bold red]")
        console.print("[yellow]Some steps may have completed. Check server state.[/yellow]")
        raise

    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="One-shot provisioning for dedicated servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "config",
        help="Path to the YAML configuration file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and test connection without making changes",
    )
    parser.add_argument(
        "--skip-backups",
        action="store_true",
        help="Skip backup configuration",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default=".",
        help="Directory for output files (default: current directory)",
    )

    args = parser.parse_args()

    # Print banner
    print_banner()

    # Start timing
    start_time = datetime.now()

    # Phase 1: Validate
    config, ssh = phase1_validate(args.config, args.dry_run)

    try:
        # Run all phases
        results = run_provisioning(config, ssh, args.skip_backups)

        # Phase 11: Generate handoff document
        handoff_path = generate_handoff(
            config,
            results["db_passwords"],
            results["storagebox_pubkey"],
            args.output_dir,
        )

        # Summary
        elapsed = datetime.now() - start_time
        console.print("\n" + "=" * 60)
        console.print(Panel.fit(
            f"[bold green]Provisioning Complete![/bold green]\n\n"
            f"Time elapsed: {elapsed.total_seconds():.0f} seconds\n"
            f"Handoff document: {handoff_path}\n\n"
            f"[cyan]Quick connect:[/cyan]\n"
            f"  ssh {config.admin.username}@{config.network.ips.host}  (host)\n"
            f"  ssh root@{config.network.ips.dev}  (dev)\n",
            border_style="green",
        ))

    except Exception as e:
        console.print(f"\n[bold red]Provisioning failed: {e}[/bold red]")
        sys.exit(1)

    finally:
        ssh.close()


if __name__ == "__main__":
    main()
