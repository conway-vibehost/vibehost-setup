# vibehost-setup

A one-shot provisioning script for dedicated servers. Reads a config file, bootstraps Debian 13 into a fully-configured incus environment with dev/staging/prod/postgres containers, flexible resource pools, private networking, backups, and security hardening.

## Overview

### What This Does

Takes a fresh Debian 13 dedicated server and transforms it into a ready-to-use vibehost environment:

1. Hardens the host (SSH keys, sudoer, no root login, firewall, crowdsec)
2. Installs and configures incus with resource pool profiles
3. Creates 4 containers: dev, staging, prod, postgres
4. Configures networking (public IPs + private inter-container network)
5. Sets up postgres container (internal-only, accessible from other containers)
6. Installs dev tooling in the dev container
7. Configures automated backups (local snapshots + Hetzner Storage Box)
8. Outputs a customer handoff document with all credentials and IPs

### Design Philosophy

- **One-shot execution**: Run once, get a working server
- **Config-driven**: All customer-specific details in a single YAML file
- **Idempotent where possible**: Re-running shouldn't break things
- **Fail loudly**: Stop and report clearly if something goes wrong
- **Good defaults**: Sensible choices that work for 90% of vibecoders

## Config File Format

```yaml
# vibehost-config.yaml

# Server access (script needs to get root somehow)
server:
  host: "203.0.113.1"                    # IP or hostname
  auth_method: "password"                 # "password" | "ssh_key"
  ssh_user: "root"                        # Initial user (often root for fresh server)
  ssh_password: "provider-given-pw"       # If auth_method is password
  ssh_key_path: "~/.ssh/id_ed25519"       # If auth_method is ssh_key
  ssh_port: 22

# New admin user (created by script, replaces root login)
admin:
  username: "vibehost"
  ssh_public_key: "ssh-ed25519 AAAA... customer@laptop"

# Network configuration
network:
  interface: "eno1"                       # Host's physical interface
  gateway: "203.0.113.1"                  # Network gateway
  netmask: "255.255.255.0"                # Or CIDR /24
  
  # Public IPs to assign
  ips:
    host: "203.0.113.1"
    dev: "203.0.113.2"
    staging: "203.0.113.3"
    prod: "203.0.113.4"
    spare: "203.0.113.5"
  
  # Private network for inter-container communication
  private:
    subnet: "10.10.10.0/24"
    gateway: "10.10.10.1"
    postgres: "10.10.10.5"

# Resource pool configuration (soft limits, can burst)
resources:
  dev:
    memory: "32GB"
    cpu_allowance: "50%"
    cpu_priority: 10
  staging:
    memory: "8GB"
    cpu_allowance: "12%"
    cpu_priority: 5
  prod:
    memory: "16GB"
    cpu_allowance: "25%"
    cpu_priority: 7
  postgres:
    memory: "16GB"
    cpu_allowance: "25%"
    cpu_priority: 6

# Postgres configuration
postgres:
  version: "16"
  databases:
    - name: "app_dev"
      user: "app"
      password: "generate"               # "generate" = script creates random pw
    - name: "app_staging"
      user: "app"
      password: "generate"
    - name: "app_prod"
      user: "app"
      password: "generate"

# Backup configuration
backups:
  snapshots:
    enabled: true
    retention_days: 7
    schedule: "0 2 * * *"                # 2 AM daily
  
  offsite:
    enabled: true
    provider: "hetzner"                  # Currently only hetzner supported
    storagebox_host: "uXXXXXX.your-storagebox.de"
    storagebox_user: "uXXXXXX"
    ssh_key_path: "/root/.ssh/storagebox_key"   # Script will generate if missing
    retention_weeks: 4
    schedule: "0 3 * * 0"                # 3 AM Sundays

# Dev container packages and tools
dev_setup:
  # System packages
  packages:
    - build-essential
    - git
    - curl
    - wget
    - htop
    - ncdu
    - tree
    - tmux
    - vim
    - jq
    - unzip
    - direnv
    - ufw

  # Python setup
  python:
    version: "3.12"
    global_packages:
      - jupyter
      - pandas
      - numpy
      - requests
      - httpx
      - polars
      - duckdb
      - sqlalchemy
      - psycopg2-binary
      - python-dotenv
      - ruff
      - ipython

  # Node.js setup
  node:
    version: "20"                        # LTS
    global_packages:
      - pnpm
      - tsx
      - nodemon
      - wrangler

  # Additional tools
  extras:
    - claude_code: true                  # Install Claude Code CLI
    - docker: true                       # Docker for experimentation
    - certbot: true                      # SSL automation

# Container OS (default: debian/13, can override per-container)
containers:
  default_image: "debian/13"
  overrides:
    # dev: "ubuntu/24.04"      # Uncomment to use Ubuntu for dev
    # staging: "ubuntu/24.04"
    # prod: "ubuntu/24.04"
    # postgres: "debian/13"    # Postgres stays Debian regardless

# Containers to apply common setup (firewall, basic packages)
common_setup:
  containers:
    - dev
    - staging
    - prod
  packages:
    - ufw
    - curl
    - wget
    - git
    - vim
  firewall:
    allow:
      - 22/tcp
      - 80/tcp
      - 443/tcp
```

## Script Behavior

### Execution Flow

```
vibehost-setup ./vibehost-config.yaml [--dry-run] [--verbose] [--skip-backups]
```

**Phase 1: Connection & Validation**
1. Parse and validate config file
2. Test SSH connection to server
3. Verify Debian 13 is running
4. Check available resources (RAM, CPU, disk)
5. Validate IP addresses are routable

**Phase 2: Host Hardening**
1. Update system packages
2. Create admin user with sudo privileges
3. Add customer's SSH public key
4. Disable root SSH login
5. Disable password authentication
6. Change SSH port (optional, if specified)
7. Install and configure ufw (allow SSH only on host)
8. Install and configure crowdsec
9. Set up unattended security updates

**Phase 3: Incus Installation**
1. Install incus from official repos
2. Initialize incus with sensible defaults
3. Create storage pool on fastest available disk

**Phase 4: Resource Pool Profiles**
1. Create dev-pool profile with soft memory limits and CPU allowance
2. Create staging-pool profile
3. Create prod-pool profile
4. Create db-pool profile
5. Verify profiles are correctly configured

**Phase 5: Network Configuration**
1. Create private bridge network (vibenet-private)
2. Create macvlan network profiles for each public IP
3. Verify network connectivity

**Phase 6: Container Creation**
1. Resolve container images (use override if specified, otherwise default_image)
2. Launch dev container with dev-pool + public IP + private network
3. Launch staging container with staging-pool + public IP + private network
4. Launch prod container with prod-pool + public IP + private network
5. Launch postgres container with db-pool + private network only
6. Wait for containers to be ready

**Phase 7: Postgres Setup**
1. Install PostgreSQL in postgres container
2. Configure to listen on private network only
3. Create databases and users per config
4. Set up pg_hba.conf to allow connections from container subnet
5. Test connectivity from dev container

**Phase 8: Dev Container Setup**
1. Install system packages
2. Install Python + pyenv or system python
3. Install global Python packages
4. Install Node.js via nvm or nodesource
5. Install global npm packages
6. Install Claude Code CLI (if enabled)
7. Install Docker (if enabled)
8. Configure direnv
9. Set up .bashrc with sensible defaults

**Phase 9: Common Container Setup**
1. For each container in common_setup.containers:
   - Install common packages
   - Configure ufw with specified rules
   - Set up basic SSH access

**Phase 10: Backup Configuration**
1. Generate SSH key for Hetzner Storage Box (if needed)
2. Create backup scripts on host
3. Set up cron jobs for snapshots
4. Set up cron jobs for offsite backups
5. Test backup connectivity
6. Run initial snapshot

**Phase 11: Handoff Document Generation**
1. Generate customer-facing markdown document with:
   - All IP addresses and their purposes
   - SSH connection strings for each container
   - Database connection strings
   - Generated passwords
   - Backup schedule and restore instructions
   - Quick reference commands
2. Save to ./handoff-[hostname]-[date].md

### Error Handling

- **Fail fast**: Stop execution on any error
- **Clear messages**: Report exactly what failed and why
- **Rollback hints**: Suggest manual cleanup steps if needed
- **Log everything**: Write detailed log to ./vibehost-setup-[timestamp].log

### Dry Run Mode

With `--dry-run`:
- Parse and validate config
- Test SSH connection
- Print planned actions without executing
- Useful for verifying config before real run

## Output: Customer Handoff Document

```markdown
# Your vibehost Server

Provisioned: 2025-01-15

## Quick Connect

**Host (management only)**
ssh vibehost@203.0.113.1

**Dev container**
ssh root@203.0.113.2

**Staging container**
ssh root@203.0.113.3

**Prod container**
ssh root@203.0.113.4

## Your Containers

| Name     | Public IP     | Private IP  | Purpose              |
|----------|---------------|-------------|----------------------|
| dev      | 203.0.113.2   | 10.10.10.2  | Development          |
| staging  | 203.0.113.3   | 10.10.10.3  | Pre-production       |
| prod     | 203.0.113.4   | 10.10.10.4  | Live projects        |
| postgres | (none)        | 10.10.10.5  | Database             |
| spare    | 203.0.113.5   | —           | Future use           |

## Database Connections

From any container, connect to postgres:

**Dev database**
Host: 10.10.10.5
Port: 5432
Database: app_dev
User: app
Password: [generated-password-here]

Connection string:
postgresql://app:[password]@10.10.10.5:5432/app_dev

**Staging database**
...

**Prod database**
...

## Dev Environment

Your dev container has these pre-installed:

**Python 3.12** with: jupyter, pandas, numpy, requests, polars, duckdb

**Node.js 20** with: pnpm, tsx, nodemon

**Tools**: Claude Code, Docker, direnv, tmux, htop

**Start Jupyter**:
jupyter notebook --ip=0.0.0.0 --port=8888

Then visit: http://203.0.113.2:8888

## Backups

**Automatic snapshots**: Daily at 2 AM, kept for 7 days
**Offsite backups**: Weekly on Sundays, kept for 4 weeks

**List snapshots**:
incus snapshot list dev

**Restore a snapshot**:
incus snapshot restore dev daily-20250115

**Database dumps**: /var/lib/incus/backups/

## Common Tasks

**SSH into a container from host**:
incus exec dev -- bash

**Check container resource usage**:
incus info dev

**Restart a container**:
incus restart prod

**View container logs**:
incus console staging --show-log

## Firewall

Each container allows: SSH (22), HTTP (80), HTTPS (443)

**Open additional port (e.g., for dev server)**:
# Inside the container
ufw allow 3000/tcp

**Check firewall status**:
ufw status

## Need Help?

- Password issues: Email me, I can reset via host access
- Container won't start: Check `incus info [container]`
- Database connection issues: Verify you're using 10.10.10.5

For anything else: [your email]
```

## Implementation Notes

### Language & Dependencies

**Recommend: Python 3.11+**
- fabric or paramiko for SSH operations
- pyyaml for config parsing
- jinja2 for handoff document templating
- rich for nice terminal output (optional)

**Alternative: Bash**
- Simpler but harder to handle errors gracefully
- Config parsing is uglier
- Would work if you prefer it

### File Structure

```
vibehost-setup/
├── vibehost-setup           # Main executable
├── README.md
├── example-config.yaml      # Example config for customers
├── lib/
│   ├── __init__.py
│   ├── config.py            # Config parsing & validation
│   ├── ssh.py               # SSH connection handling
│   ├── host.py              # Host hardening functions
│   ├── incus.py             # Incus setup functions
│   ├── network.py           # Network configuration
│   ├── containers.py        # Container creation & setup
│   ├── postgres.py          # Postgres setup
│   ├── dev_setup.py         # Dev container tooling
│   ├── backups.py           # Backup configuration
│   └── handoff.py           # Handoff document generation
├── templates/
│   └── handoff.md.j2        # Jinja2 template for handoff doc
└── tests/
    └── ...                  # Config validation tests at minimum
```

### Security Considerations

- Never log passwords (even to file)
- Generate strong random passwords (secrets module)
- Validate all IP addresses before use
- Don't store customer SSH keys in script repo
- Handoff document contains secrets - warn about this

### Testing Strategy

1. **Config validation tests**: Ensure bad configs fail clearly
2. **Dry run against real server**: Verify connection and planning logic
3. **Full run against test server**: Hetzner auction server works great for this
4. **Idempotency test**: Run twice, verify no errors or changes

## Future Enhancements (Out of Scope for V1)

- [ ] Multiple customer configs in one run
- [ ] Web UI for config generation
- [ ] Terraform provider (way overkill)
- [ ] Support for other backup providers (B2, S3)
- [ ] Custom container images instead of ubuntu base
- [ ] Monitoring setup (prometheus/grafana)
- [ ] Automatic SSL cert provisioning

## Success Criteria

The script is done when:

1. Fresh Debian 13 server → fully configured in under 15 minutes
2. Customer can SSH into dev container and start coding immediately
3. Postgres is accessible from all containers, not from internet
4. Backups are running without intervention
5. Handoff document has everything customer needs
6. Running twice doesn't break anything
