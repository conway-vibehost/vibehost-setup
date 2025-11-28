"""Configuration parsing and validation for vibehost-setup."""

import secrets
import string
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


def generate_password(length: int = 32) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class ServerConfig(BaseModel):
    """Server access configuration."""

    host: str
    auth_method: Literal["password", "ssh_key"]
    ssh_user: str = "root"
    ssh_password: str | None = None
    ssh_key_path: str | None = None
    ssh_port: int = 22

    @model_validator(mode="after")
    def validate_auth(self):
        if self.auth_method == "password" and not self.ssh_password:
            raise ValueError("ssh_password required when auth_method is 'password'")
        if self.auth_method == "ssh_key" and not self.ssh_key_path:
            raise ValueError("ssh_key_path required when auth_method is 'ssh_key'")
        return self


class AdminConfig(BaseModel):
    """New admin user configuration."""

    username: str
    ssh_public_key: str


class NetworkIPs(BaseModel):
    """Public IP assignments."""

    host: str
    dev: str
    staging: str
    prod: str
    spare: str | None = None


class PrivateNetwork(BaseModel):
    """Private network configuration."""

    subnet: str = "10.10.10.0/24"
    gateway: str = "10.10.10.1"
    postgres: str = "10.10.10.5"


class NetworkConfig(BaseModel):
    """Network configuration."""

    interface: str
    gateway: str
    netmask: str
    ips: NetworkIPs
    private: PrivateNetwork = Field(default_factory=PrivateNetwork)


class ResourcePool(BaseModel):
    """Resource allocation for a container."""

    memory: str
    cpu_allowance: str
    cpu_priority: int = 5


class ResourcesConfig(BaseModel):
    """Resource pool configuration for all containers."""

    dev: ResourcePool
    staging: ResourcePool
    prod: ResourcePool
    postgres: ResourcePool


class DatabaseConfig(BaseModel):
    """Database configuration."""

    name: str
    user: str
    password: str = "generate"
    _generated_password: str | None = None

    @property
    def actual_password(self) -> str:
        """Get the actual password, generating if needed."""
        if self.password == "generate":
            if self._generated_password is None:
                object.__setattr__(self, "_generated_password", generate_password())
            return self._generated_password
        return self.password


class PostgresConfig(BaseModel):
    """PostgreSQL configuration."""

    version: str = "16"
    databases: list[DatabaseConfig]


class SnapshotConfig(BaseModel):
    """Local snapshot backup configuration."""

    enabled: bool = True
    retention_days: int = 7
    schedule: str = "0 2 * * *"


class OffsiteConfig(BaseModel):
    """Offsite backup configuration."""

    enabled: bool = True
    provider: Literal["hetzner"] = "hetzner"
    storagebox_host: str | None = None
    storagebox_user: str | None = None
    ssh_key_path: str = "/root/.ssh/storagebox_key"
    retention_weeks: int = 4
    schedule: str = "0 3 * * 0"

    @model_validator(mode="after")
    def validate_offsite(self):
        if self.enabled:
            if not self.storagebox_host:
                raise ValueError("storagebox_host required when offsite backups enabled")
            if not self.storagebox_user:
                raise ValueError("storagebox_user required when offsite backups enabled")
        return self


class BackupsConfig(BaseModel):
    """Backup configuration."""

    snapshots: SnapshotConfig = Field(default_factory=SnapshotConfig)
    offsite: OffsiteConfig = Field(default_factory=lambda: OffsiteConfig(enabled=False))


class PythonSetup(BaseModel):
    """Python environment setup."""

    version: str = "3.12"
    global_packages: list[str] = Field(default_factory=list)


class NodeSetup(BaseModel):
    """Node.js environment setup."""

    version: str = "20"
    global_packages: list[str] = Field(default_factory=list)


class ExtrasSetup(BaseModel):
    """Additional tools to install."""

    claude_code: bool = True
    docker: bool = True
    certbot: bool = True


class DevSetupConfig(BaseModel):
    """Dev container setup configuration."""

    packages: list[str] = Field(default_factory=list)
    python: PythonSetup = Field(default_factory=PythonSetup)
    node: NodeSetup = Field(default_factory=NodeSetup)
    extras: ExtrasSetup = Field(default_factory=ExtrasSetup)

    @field_validator("extras", mode="before")
    @classmethod
    def parse_extras(cls, v):
        """Handle list-of-dicts format from YAML."""
        if isinstance(v, list):
            result = {}
            for item in v:
                if isinstance(item, dict):
                    result.update(item)
            return result
        return v


class ContainersConfig(BaseModel):
    """Container image configuration."""

    default_image: str = "debian/13"
    overrides: dict[str, str] = Field(default_factory=dict)

    def get_image(self, container: str) -> str:
        """Get the image for a specific container."""
        return self.overrides.get(container, self.default_image)


class FirewallConfig(BaseModel):
    """Firewall configuration."""

    allow: list[str] = Field(default_factory=lambda: ["22/tcp", "80/tcp", "443/tcp"])


class CommonSetupConfig(BaseModel):
    """Common setup applied to multiple containers."""

    containers: list[str] = Field(default_factory=lambda: ["dev", "staging", "prod"])
    packages: list[str] = Field(default_factory=list)
    firewall: FirewallConfig = Field(default_factory=FirewallConfig)


class StorageConfig(BaseModel):
    """Storage configuration for Incus.

    By default, uses a 100GiB ZFS loopback file.
    If device is specified, uses that block device directly for ZFS.
    """

    driver: Literal["zfs"] = "zfs"
    device: str | None = None  # e.g., "/dev/sda" for dedicated disk
    size: str = "100GiB"  # Only used for loopback


class VibehostConfig(BaseModel):
    """Main configuration for vibehost-setup."""

    server: ServerConfig
    admin: AdminConfig
    network: NetworkConfig
    resources: ResourcesConfig
    postgres: PostgresConfig
    backups: BackupsConfig = Field(default_factory=BackupsConfig)
    dev_setup: DevSetupConfig = Field(default_factory=DevSetupConfig)
    containers: ContainersConfig = Field(default_factory=ContainersConfig)
    common_setup: CommonSetupConfig = Field(default_factory=CommonSetupConfig)
    storage: StorageConfig | None = None  # Optional, uses defaults if not specified


def load_config(path: str | Path) -> VibehostConfig:
    """Load and validate configuration from a YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    return VibehostConfig(**raw)


def validate_config(config: VibehostConfig) -> list[str]:
    """Perform additional validation checks on the config.

    Returns a list of warnings (empty if all good).
    """
    warnings = []

    # Check that all IPs look valid (basic check)
    ips = [
        config.network.ips.host,
        config.network.ips.dev,
        config.network.ips.staging,
        config.network.ips.prod,
    ]
    for ip in ips:
        parts = ip.split(".")
        if len(parts) != 4:
            warnings.append(f"Invalid IP format: {ip}")

    # Check resource allocations don't exceed 100% CPU
    total_cpu = 0
    for pool in [
        config.resources.dev,
        config.resources.staging,
        config.resources.prod,
        config.resources.postgres,
    ]:
        cpu_str = pool.cpu_allowance.rstrip("%")
        try:
            total_cpu += int(cpu_str)
        except ValueError:
            warnings.append(f"Invalid CPU allowance format: {pool.cpu_allowance}")

    if total_cpu > 100:
        warnings.append(
            f"Total CPU allowance ({total_cpu}%) exceeds 100% - containers may compete for resources"
        )

    return warnings
