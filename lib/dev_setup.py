"""Dev container setup for vibehost-setup."""

from rich.console import Console

from lib.config import VibehostConfig
from lib.ssh import SSHConnection, ContainerExec

console = Console()


def install_system_packages(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Install system packages in the dev container."""
    console.print("[cyan]Installing system packages...[/cyan]")

    exec_cmd = ContainerExec(ssh, "dev")
    packages = config.dev_setup.packages

    if packages:
        pkg_list = " ".join(packages)
        exec_cmd.run(f"apt-get update && apt-get install -y {pkg_list}", hide=True)
        console.print(f"[green]✓ Installed {len(packages)} system packages[/green]")
    else:
        console.print("[dim]No system packages specified[/dim]")


def install_python(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Install Python environment in the dev container."""
    console.print("[cyan]Installing Python environment...[/cyan]")

    exec_cmd = ContainerExec(ssh, "dev")
    python_config = config.dev_setup.python
    version = python_config.version

    # Install Python and pip (Debian 13 uses python3 as the main package)
    exec_cmd.run("apt-get install -y python3 python3-venv python3-pip", hide=True)

    # Make python point to python3
    exec_cmd.run("update-alternatives --install /usr/bin/python python /usr/bin/python3 1 || true", hide=True)

    # Install uv for fast package management
    exec_cmd.run("curl -LsSf https://astral.sh/uv/install.sh | sh", hide=True)
    exec_cmd.run("echo 'export PATH=\"/root/.local/bin:$PATH\"' >> /root/.bashrc", hide=True)

    # Install global packages with uv (break system packages for Debian 13's PEP 668)
    if python_config.global_packages:
        pkg_list = " ".join(python_config.global_packages)
        exec_cmd.run(f"/root/.local/bin/uv pip install --system --break-system-packages {pkg_list}", hide=True)
        console.print(f"[green]✓ Python {version} with {len(python_config.global_packages)} packages installed[/green]")
    else:
        console.print(f"[green]✓ Python {version} installed[/green]")


def install_node(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Install Node.js environment in the dev container."""
    console.print("[cyan]Installing Node.js environment...[/cyan]")

    exec_cmd = ContainerExec(ssh, "dev")
    node_config = config.dev_setup.node
    version = node_config.version

    # Install Node.js via NodeSource
    exec_cmd.run(f"curl -fsSL https://deb.nodesource.com/setup_{version}.x | bash -", hide=True)
    exec_cmd.run("apt-get install -y nodejs", hide=True)

    # Install global npm packages
    if node_config.global_packages:
        pkg_list = " ".join(node_config.global_packages)
        exec_cmd.run(f"npm install -g {pkg_list}", hide=True)
        console.print(f"[green]✓ Node.js {version} with {len(node_config.global_packages)} packages installed[/green]")
    else:
        console.print(f"[green]✓ Node.js {version} installed[/green]")


def install_extras(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Install additional tools in the dev container."""
    console.print("[cyan]Installing additional tools...[/cyan]")

    exec_cmd = ContainerExec(ssh, "dev")
    extras = config.dev_setup.extras

    if extras.claude_code:
        console.print("[dim]Installing Claude Code CLI...[/dim]")
        exec_cmd.run("npm install -g @anthropic-ai/claude-code", hide=True)
        console.print("[green]✓ Claude Code CLI installed[/green]")

    if extras.docker:
        console.print("[dim]Installing Docker...[/dim]")
        # Install Docker using official script
        exec_cmd.run("curl -fsSL https://get.docker.com | sh", hide=True)
        exec_cmd.run("systemctl enable docker", hide=True)
        exec_cmd.run("systemctl start docker", hide=True)
        console.print("[green]✓ Docker installed[/green]")

    if extras.certbot:
        console.print("[dim]Installing Certbot...[/dim]")
        exec_cmd.run("apt-get install -y certbot", hide=True)
        console.print("[green]✓ Certbot installed[/green]")


def configure_shell(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Configure shell environment in the dev container."""
    console.print("[cyan]Configuring shell environment...[/cyan]")

    exec_cmd = ContainerExec(ssh, "dev")

    # Add useful aliases and settings to .bashrc
    bashrc_additions = """
# vibehost dev environment

# Aliases
alias ll='ls -la'
alias la='ls -A'
alias l='ls -CF'
alias ..='cd ..'
alias ...='cd ../..'

# Git aliases
alias gs='git status'
alias ga='git add'
alias gc='git commit'
alias gp='git push'
alias gl='git log --oneline -10'
alias gd='git diff'

# Python aliases
alias py='python'
alias pip='uv pip'
alias venv='python -m venv'

# Docker aliases
alias d='docker'
alias dc='docker compose'
alias dps='docker ps'

# Useful environment
export EDITOR=vim
export VISUAL=vim
export HISTSIZE=10000
export HISTFILESIZE=20000

# direnv hook
eval "$(direnv hook bash)"

# Path additions
export PATH="/root/.local/bin:$PATH"

# Prompt with git branch
parse_git_branch() {
    git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \\(.*\\)/ (\\1)/'
}
export PS1='\\[\\033[01;32m\\]\\u@dev\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[33m\\]$(parse_git_branch)\\[\\033[00m\\]\\$ '
"""

    exec_cmd.append_file("/root/.bashrc", bashrc_additions)

    # Configure git
    exec_cmd.run('git config --global init.defaultBranch main', hide=True)
    exec_cmd.run('git config --global pull.rebase false', hide=True)

    console.print("[green]✓ Shell environment configured[/green]")


def setup_dev_container(ssh: SSHConnection, config: VibehostConfig) -> None:
    """Run all dev container setup steps."""
    console.print("\n[bold blue]Phase 8: Dev Container Setup[/bold blue]\n")

    install_system_packages(ssh, config)
    install_python(ssh, config)
    install_node(ssh, config)
    install_extras(ssh, config)
    configure_shell(ssh, config)

    console.print("\n[bold green]✓ Dev container setup complete[/bold green]")
