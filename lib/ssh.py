"""SSH connection handling for vibehost-setup."""

from pathlib import Path

from fabric import Connection
from invoke import Responder
from paramiko import RSAKey, Ed25519Key
from rich.console import Console

from lib.config import ServerConfig, VibehostConfig

console = Console()


class SSHConnection:
    """Manages SSH connection to the target server."""

    def __init__(self, config: VibehostConfig):
        self.config = config
        self.server = config.server
        self._connection: Connection | None = None
        self._admin_connection: Connection | None = None

    def _get_connect_kwargs(self) -> dict:
        """Get connection kwargs based on auth method."""
        if self.server.auth_method == "password":
            return {"password": self.server.ssh_password}
        else:
            key_path = Path(self.server.ssh_key_path).expanduser()
            return {"key_filename": str(key_path)}

    @property
    def conn(self) -> Connection:
        """Get or create the root/initial connection."""
        if self._connection is None:
            self._connection = Connection(
                host=self.server.host,
                user=self.server.ssh_user,
                port=self.server.ssh_port,
                connect_kwargs=self._get_connect_kwargs(),
            )
        return self._connection

    @property
    def admin_conn(self) -> Connection:
        """Get connection as the admin user (after hardening)."""
        if self._admin_connection is None:
            # Admin always uses SSH key
            key_path = Path("~/.ssh/id_ed25519").expanduser()
            self._admin_connection = Connection(
                host=self.server.host,
                user=self.config.admin.username,
                port=self.server.ssh_port,
                connect_kwargs={"key_filename": str(key_path)},
            )
        return self._admin_connection

    def test_connection(self) -> bool:
        """Test if we can connect to the server."""
        try:
            result = self.conn.run("echo 'connection test'", hide=True)
            return result.ok
        except Exception as e:
            console.print(f"[red]Connection failed: {e}[/red]")
            return False

    def run(self, command: str, hide: bool = False, warn: bool = False, **kwargs) -> str:
        """Run a command on the server and return stdout."""
        result = self.conn.run(command, hide=hide, warn=warn, **kwargs)
        return result.stdout.strip()

    def sudo(self, command: str, hide: bool = False, warn: bool = False, **kwargs) -> str:
        """Run a command with sudo on the server."""
        # If we're root, just run directly
        if self.server.ssh_user == "root":
            return self.run(command, hide=hide, warn=warn, **kwargs)

        result = self.conn.sudo(command, hide=hide, warn=warn, **kwargs)
        return result.stdout.strip()

    def put(self, local: str | Path, remote: str) -> None:
        """Upload a file to the server."""
        self.conn.put(str(local), remote)

    def get(self, remote: str, local: str | Path) -> None:
        """Download a file from the server."""
        self.conn.get(remote, str(local))

    def file_exists(self, path: str) -> bool:
        """Check if a file exists on the remote server."""
        result = self.conn.run(f"test -f {path}", warn=True, hide=True)
        return result.ok

    def dir_exists(self, path: str) -> bool:
        """Check if a directory exists on the remote server."""
        result = self.conn.run(f"test -d {path}", warn=True, hide=True)
        return result.ok

    def write_file(self, path: str, content: str, mode: str = "644") -> None:
        """Write content to a file on the remote server."""
        # Use tee with heredoc - tee runs under sudo so redirect works
        self.sudo(f"tee {path} > /dev/null << 'VIBEHOST_EOF'\n{content}\nVIBEHOST_EOF")
        self.sudo(f"chmod {mode} {path}")

    def append_file(self, path: str, content: str) -> None:
        """Append content to a file on the remote server."""
        # Use tee -a for append
        self.sudo(f"tee -a {path} > /dev/null << 'VIBEHOST_EOF'\n{content}\nVIBEHOST_EOF")

    def get_os_info(self) -> dict:
        """Get information about the remote OS."""
        info = {}

        # Get OS release info
        result = self.run("cat /etc/os-release", hide=True)
        for line in result.split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                info[key] = value.strip('"')

        # Get kernel version
        info["kernel"] = self.run("uname -r", hide=True)

        return info

    def verify_debian_13(self) -> bool:
        """Verify the server is running Debian 13."""
        info = self.get_os_info()
        is_debian = info.get("ID") == "debian"
        is_13 = info.get("VERSION_ID", "").startswith("13")
        return is_debian and is_13

    def get_resources(self) -> dict:
        """Get available system resources."""
        resources = {}

        # Memory (in GB)
        mem_kb = int(self.run("grep MemTotal /proc/meminfo | awk '{print $2}'", hide=True))
        resources["memory_gb"] = round(mem_kb / 1024 / 1024, 1)

        # CPU cores
        resources["cpu_cores"] = int(self.run("nproc", hide=True))

        # Disk space (root partition, in GB)
        disk_output = self.run("df -BG / | tail -1 | awk '{print $4}'", hide=True)
        resources["disk_free_gb"] = int(disk_output.rstrip("G"))

        return resources

    def close(self) -> None:
        """Close all connections."""
        if self._connection:
            self._connection.close()
        if self._admin_connection:
            self._admin_connection.close()


class ContainerExec:
    """Execute commands inside incus containers."""

    def __init__(self, ssh: SSHConnection, container: str):
        self.ssh = ssh
        self.container = container

    def run(self, command: str, hide: bool = False, warn: bool = False) -> str:
        """Run a command inside the container."""
        # Escape single quotes in command
        escaped = command.replace("'", "'\"'\"'")
        full_cmd = f"incus exec {self.container} -- bash -c '{escaped}'"
        return self.ssh.sudo(full_cmd, hide=hide, warn=warn)

    def file_exists(self, path: str) -> bool:
        """Check if a file exists in the container."""
        result = self.ssh.conn.sudo(
            f"incus exec {self.container} -- test -f {path}",
            warn=True,
            hide=True,
        )
        return result.ok

    def write_file(self, path: str, content: str, mode: str = "644") -> None:
        """Write content to a file inside the container."""
        self.run(f"cat > {path} << 'VIBEHOST_EOF'\n{content}\nVIBEHOST_EOF")
        self.run(f"chmod {mode} {path}")

    def append_file(self, path: str, content: str) -> None:
        """Append content to a file inside the container."""
        self.run(f"cat >> {path} << 'VIBEHOST_EOF'\n{content}\nVIBEHOST_EOF")

    def push_file(self, local_content: str, remote_path: str) -> None:
        """Push content to a file in the container via incus file push."""
        # Write to temp file on host, then push to container
        import tempfile
        import os

        # Create temp file locally, upload to host, then push to container
        self.ssh.run(f"cat > /tmp/vibehost_push << 'VIBEHOST_EOF'\n{local_content}\nVIBEHOST_EOF", hide=True)
        self.ssh.sudo(f"incus file push /tmp/vibehost_push {self.container}{remote_path}", hide=True)
        self.ssh.run("rm /tmp/vibehost_push", hide=True)
