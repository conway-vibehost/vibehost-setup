"""Handoff document generation for vibehost-setup."""

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from lib.config import VibehostConfig

console = Console()


def generate_handoff(
    config: VibehostConfig,
    db_passwords: dict[str, str],
    storagebox_pubkey: str | None = None,
    output_dir: str = ".",
) -> Path:
    """Generate the customer handoff document.

    Args:
        config: The vibehost configuration
        db_passwords: Dict mapping database names to passwords
        storagebox_pubkey: Public key for Storage Box (if generated)
        output_dir: Directory to write the handoff document

    Returns:
        Path to the generated document
    """
    console.print("\n[bold blue]Phase 11: Handoff Document Generation[/bold blue]\n")
    console.print("[cyan]Generating customer handoff document...[/cyan]")

    # Load template
    template_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("handoff.md.j2")

    # Prepare template context
    context = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "host_ip": config.network.ips.host,
        "admin_user": config.admin.username,
        "dev_ip": config.network.ips.dev,
        "staging_ip": config.network.ips.staging,
        "prod_ip": config.network.ips.prod,
        "spare_ip": config.network.ips.spare,
        "postgres_private_ip": config.network.private.postgres,
        "private_subnet": config.network.private.subnet,
        "databases": [],
        "python_version": config.dev_setup.python.version,
        "python_packages": config.dev_setup.python.global_packages,
        "node_version": config.dev_setup.node.version,
        "node_packages": config.dev_setup.node.global_packages,
        "extras": config.dev_setup.extras,
        "snapshot_schedule": config.backups.snapshots.schedule,
        "snapshot_retention": config.backups.snapshots.retention_days,
        "offsite_enabled": config.backups.offsite.enabled,
        "offsite_schedule": config.backups.offsite.schedule if config.backups.offsite.enabled else None,
        "offsite_retention": config.backups.offsite.retention_weeks if config.backups.offsite.enabled else None,
        "storagebox_pubkey": storagebox_pubkey,
        "firewall_rules": config.common_setup.firewall.allow,
    }

    # Add database info with passwords
    for db in config.postgres.databases:
        context["databases"].append({
            "name": db.name,
            "user": db.user,
            "password": db_passwords.get(db.name, db.actual_password),
        })

    # Render template
    content = template.render(**context)

    # Write to file
    hostname = config.network.ips.host.replace(".", "-")
    date_str = datetime.now().strftime("%Y%m%d")
    output_path = Path(output_dir) / f"handoff-{hostname}-{date_str}.md"

    output_path.write_text(content)

    console.print(f"[green]✓ Handoff document saved to: {output_path}[/green]")
    console.print("\n[yellow]⚠ This document contains secrets - store securely![/yellow]")

    return output_path
