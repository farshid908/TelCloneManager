# TelCloneManager

Telegram automation software for managing a main Telegram account and multiple clone accounts with Telethon. The project also includes an optional web dashboard, a localhost admin API, a CLI client, media utilities, persistent loop state, and an optional aiogram-based Clone Manager.

> ⚠️ **WARNING: Misuse of this source code, including unauthorized automation, abusive behavior, or spam, may result in Telegram restricting or permanently banning the account(s) involved. Use this project responsibly and comply with Telegram's Terms of Service and applicable laws.** ⚠️

## Supported environments

The project is designed for Python 3.10 or newer and can run on:

- Ubuntu and Debian-based VPS systems
- Debian, Fedora/RHEL-compatible, and Arch-based VPS systems
- Ubuntu running inside Termux `proot-distro`
- Other Linux VPS providers with Python 3.10+ and a working package manager

The commands below assume a normal user with `sudo` access. Running as `root` also works, but a dedicated user is recommended for production.

## Requirements

### Python packages

The exact Python package versions are pinned in [`requirements.txt`](requirements.txt):

```text
telethon==1.44.0
Flask==3.0.3
PySocks==1.7.1
Pillow==12.3.0
aiogram==3.31.0
```

### System packages

The core bot needs Python, pip, and virtual-environment support. `ffmpeg` is required for video/media features, and `screen` is useful for keeping the bot running in a detached terminal session.

## Ubuntu or Debian VPS installation

```bash
sudo apt update
sudo apt install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  ca-certificates \
  git \
  ffmpeg \
  screen
```

Clone the repository and create an isolated Python environment:

```bash
git clone https://github.com/farshid908/TelCloneManager.git
cd TelCloneManager

python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
```

If the repository is already present, use `git pull` instead of cloning it again.

## Termux with Ubuntu inside proot-distro

Run these commands in the Termux host environment:

```bash
pkg update
pkg upgrade -y
pkg install -y proot-distro git
proot-distro install ubuntu
proot-distro login ubuntu
```

Then run the following commands inside the Ubuntu proot environment:

```bash
apt update
apt install -y \
  python3 \
  python3-pip \
  python3-venv \
  python3-dev \
  ca-certificates \
  git \
  ffmpeg \
  screen

git clone https://github.com/farshid908/TelCloneManager.git
cd TelCloneManager

python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
```

If a package is unavailable in the proot distribution, update the Ubuntu release first:

```bash
apt update
apt full-upgrade -y
```

## Fedora or RHEL-compatible VPS

On Fedora or newer RHEL-compatible distributions:

```bash
sudo dnf install -y \
  python3 \
  python3-pip \
  python3-devel \
  ca-certificates \
  git \
  screen
```

Install `ffmpeg` from the repositories enabled on your distribution if media and video features are needed. Then install the project:

```bash
git clone https://github.com/farshid908/TelCloneManager.git
cd TelCloneManager

python3 -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
```

## Arch Linux VPS

```bash
sudo pacman -Syu --needed \
  python \
  python-pip \
  ffmpeg \
  git \
  screen

git clone https://github.com/farshid908/TelCloneManager.git
cd TelCloneManager

python -m venv venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
```

## Environment configuration

Create a local `.env` file in the project directory. Never commit this file. If
the repository does not contain an `.env.example` file, create `.env` manually:

```env
# Telegram API credentials from https://my.telegram.org
API_ID=123456
API_HASH=replace_with_your_api_hash

# Main session and session directory
SESSIONS_DIR=./sessions
MAIN_SESSION_NAME=main_commander

# Optional bridge group. It must be a negative Telegram chat ID.
BRIDGE_GROUP=-1001234567890
BRIDGE_INVITE_LINK=

# Timing
BUTTON_CLICK_TIMEOUT=30
AUTO_DELETE_DELAY=1

# Web dashboard
WEB_PASSWORD=replace_with_a_strong_password
WEB_SECRET_KEY=replace_with_a_long_random_secret

# Local admin API used by cli_client.py
ADMIN_API_HOST=127.0.0.1
ADMIN_API_PORT=1600
ADMIN_API_TOKEN=replace_with_a_long_random_token

# Optional Clone Manager bot
CLONE_MANAGER_BOT_TOKEN=replace_with_botfather_token
CLONE_MANAGER_BOT_USERNAME=your_clone_manager_bot_username

# Optional media directory
MUSIC_DIR=./music
```

Then restrict the file permissions:

```bash
chmod 600 .env
```

At minimum, `API_ID` and `API_HASH` are required for Telegram connectivity. The Clone Manager requires `CLONE_MANAGER_BOT_TOKEN`. The CLI requires a matching `ADMIN_API_TOKEN`.

## Create Telegram sessions with `cli_client.py`

You do not need to create `.session` files manually. The recommended method is
to create every session from the interactive CLI. The CLI sends the Telegram
login code, verifies the code, asks for a 2FA password when required, and saves
the new session in the configured `sessions/` directory.

### Step 1: Start the application

Open the first terminal and start the application from the project directory:

```bash
cd /path/to/TelCloneManager
./venv/bin/python -u main.py
```

Keep this terminal running. The CLI connects to the local admin API started by
`main.py`. If the CLI says that the connection failed, return to this terminal
and check that `main.py` is still running.

### Step 2: Open the CLI in a second terminal

Open another terminal, enter the same project directory, and run:

```bash
cd /path/to/TelCloneManager
./venv/bin/python cli_client.py
```

You will see a menu similar to this:

```text
1. Status
2. Logs
3. Live logs
4. Reload bot
5. Packages
6. List sessions
7. Create session
8. Convert session
9. Activate main
10. Repair sessions
11. Set bridge link
12. Install missing packages
0. Exit
```

Choose `7` (`Create session`).

### Step 3: Create the first Main session

When the CLI asks:

```text
Type #:
```

enter:

```text
1
```

Then enter the phone number in international format, including the `+` sign:

```text
+1234567890
```

Telegram will send a login code to the Telegram app or another active session.
Enter that code when the CLI asks for it.

If the account has two-step verification enabled, the CLI will ask for the
existing 2FA password. Enter it when prompted. The CLI can also offer to set a
new 2FA password; answer `y` only if you intentionally want to enable 2FA on
this account.

When the operation succeeds, the CLI creates the Main session and asks whether
to reload the bot. Answer `Y` to reload, or `n` and reload later from the menu.

### Step 4: Create Clone sessions

Run `Create session` again by choosing `7`. This time enter:

```text
2
```

Enter the phone number for the clone account and complete the Telegram code and
2FA prompts. Repeat this process for every clone account you want to add.

The application will save sessions similar to:

```text
sessions/main_commander.session
sessions/Clone1.session
sessions/Clone2.session
```

The actual names are assigned by the session manager. Do not rename these files
manually. Session files are private authentication credentials and must never be
uploaded to GitHub.

### Step 5: Check the sessions

In the CLI menu, choose `6` (`List sessions`). Confirm that the Main session and
the expected Clone sessions are listed. If more than one Main session exists,
choose `9` (`Activate main`) and select the Main session that should be active.

After creating or activating sessions, reload the bot from the CLI when it asks
you to do so. Do not run two copies of the bot at the same time.

## Start the bot

Run the main application from the project directory:

```bash
cd /path/to/TelCloneManager
./venv/bin/python -u main.py
```

The application starts the Telethon automation, the optional Clone Manager, the web dashboard, and the localhost admin API according to the configured environment.

## Run inside screen

```bash
screen -S telegram
cd /path/to/TelCloneManager
./venv/bin/python -u main.py 2>&1 | tee -a startup-current.log
```

Detach without stopping the bot by pressing `Ctrl+A`, then `D`. Reattach later with:

```bash
screen -r telegram
```

## Optional systemd service

For a VPS, create a service file such as `/etc/systemd/system/telegram-bot.service`:

```ini
[Unit]
Description=Telegram automation bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=telegram
WorkingDirectory=/opt/TelCloneManager
ExecStart=/opt/TelCloneManager/venv/bin/python -u /opt/TelCloneManager/main.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

Create the service user and enable the service:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin telegram
sudo chown -R telegram:telegram /opt/TelCloneManager
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot.service
sudo systemctl status telegram-bot.service --no-pager
```

Do not run both a systemd instance and a separate screen instance for the same bot. That can create duplicate Telegram connections and duplicate jobs.

## CLI client and dashboard

With the bot and admin API running, use:

```bash
./venv/bin/python cli_client.py
```

To open the live terminal dashboard with logs and the `F8  open menu` shortcut:

```bash
./venv/bin/python cli_client.py dashboard
```

The dashboard is client-only and does not start the bot. On Windows, a curses-compatible terminal package may be required; Linux and macOS normally provide terminal support through the operating system.

## Network and proxy notes

Telethon and the bot require access to Telegram. `PySocks` is included for SOCKS support, but the application does not automatically assume a proxy address. If the VPS network blocks Telegram, configure a supported proxy in the client connection code or provide network-level routing before starting the bot.

For pip installation through a temporary HTTP or SOCKS-compatible wrapper, configure the package manager according to the proxy software available on the host. Do not commit proxy credentials to the repository.

## Basic checks

Check Python syntax without starting the bot:

```bash
./venv/bin/python -m py_compile \
  main.py \
  bot_runner.py \
  cli_client.py
```

Check the installed package versions:

```bash
./venv/bin/python -m pip show telethon Flask PySocks Pillow aiogram
```

Do not use `pip` against the system Python on modern Ubuntu installations. Use the project virtual environment to avoid PEP 668 and system-package conflicts.

## Bug Reports and Ideas

If you find a bug or have a new idea for an improvement, please contact [@GodOfArak](https://t.me/GodOfArak) on Telegram.
