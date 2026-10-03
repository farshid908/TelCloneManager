#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="${UPDATER_REPOSITORY:-https://github.com/farshid908/TelCloneManager}"
BRANCH="${UPDATER_BRANCH:-main}"
MAIN_SCREEN="${MAIN_SCREEN_NAME:-telegram}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
START_COMMAND="${MAIN_START_COMMAND:-python3 -u main.py >> runtime.stdout.log 2>> runtime.stderr.log}"
BACKUP_ROOT="${ROOT_DIR}/.update_backups"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d -t telclonemanager-update.XXXXXX)"
ARCHIVE="${WORK_DIR}/source.zip"
EXTRACT_DIR="${WORK_DIR}/extract"
BACKUP_DIR="${BACKUP_ROOT}/${STAMP}"
INSTALLED_FILES=()
CHANGED_FILES=()

cleanup() { rm -rf -- "${WORK_DIR}"; }
trap cleanup EXIT
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

restore_backup() {
    [[ -d "${BACKUP_DIR}" ]] || return 0
    for relative in "${INSTALLED_FILES[@]}"; do
        destination="${ROOT_DIR}/${relative}"
        saved="${BACKUP_DIR}/${relative}"
        if [[ -f "${saved}" ]]; then
            mkdir -p -- "$(dirname -- "${destination}")"
            cp -p -- "${saved}" "${destination}"
        else
            rm -f -- "${destination}"
        fi
    done
}

restart_main() {
    screen -S "${MAIN_SCREEN}" -X quit >/dev/null 2>&1 || true
    sleep 2
    screen -dmS "${MAIN_SCREEN}" bash -lc "cd $(printf '%q' "${ROOT_DIR}") && exec ${START_COMMAND}"
    sleep 5
    screen -list | grep -Eq "[.]${MAIN_SCREEN}[[:space:]]" || return 1
}

require_command curl
require_command unzip
require_command find
require_command screen
require_command "${PYTHON_BIN}"

printf 'Downloading %s (%s)...\n' "${REPOSITORY}" "${BRANCH}"
mkdir -p -- "${EXTRACT_DIR}"
curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 \
    "${REPOSITORY}/archive/refs/heads/${BRANCH}.zip" --output "${ARCHIVE}"
unzip -q -- "${ARCHIVE}" -d "${EXTRACT_DIR}"
SOURCE_ROOT=""
for candidate in "${EXTRACT_DIR}"/*; do
    [[ -d "${candidate}" ]] || continue
    SOURCE_ROOT="${candidate}"
    break
done
[[ -n "${SOURCE_ROOT}" ]] || die "Could not identify the archive root"

printf 'Checking Python syntax...\n'
while IFS= read -r -d '' source_file; do
    relative="${source_file#"${SOURCE_ROOT}/"}"
    "${PYTHON_BIN}" -m py_compile "${source_file}" >/dev/null || die "Syntax check failed: ${relative}"
done < <(find "${SOURCE_ROOT}" -type f -name '*.py' -not -path '*/.*' -print0)

while IFS= read -r -d '' source_file; do
    relative="${source_file#"${SOURCE_ROOT}/"}"
    destination="${ROOT_DIR}/${relative}"
    if [[ ! -f "${destination}" ]] || ! cmp -s -- "${source_file}" "${destination}"; then
        CHANGED_FILES+=("${relative}")
    fi
done < <(find "${SOURCE_ROOT}" -type f \( -name '*.py' -o -name 'requirements.txt' \) -not -path '*/.*' -print0)
if [[ "${#CHANGED_FILES[@]}" -eq 0 ]]; then
    printf 'Already up to date. No source files changed.\n'
    exit 0
fi
printf 'Changed files: %s\n' "${#CHANGED_FILES[@]}"

mkdir -p -- "${BACKUP_DIR}"
for relative in "${CHANGED_FILES[@]}"; do
    INSTALLED_FILES+=("${relative}")
    destination="${ROOT_DIR}/${relative}"
    if [[ -f "${destination}" ]]; then
        mkdir -p -- "${BACKUP_DIR}/$(dirname -- "${relative}")"
        cp -p -- "${destination}" "${BACKUP_DIR}/${relative}"
    fi
done

printf 'Installing source files...\n'
for relative in "${CHANGED_FILES[@]}"; do
    source_file="${SOURCE_ROOT}/${relative}"
    destination="${ROOT_DIR}/${relative}"
    mkdir -p -- "$(dirname -- "${destination}")"
    cp -p -- "${source_file}" "${destination}"
done

printf 'Restarting screen %s...\n' "${MAIN_SCREEN}"
if ! restart_main; then
    printf 'Main screen failed to start. Restoring backup...\n' >&2
    restore_backup
    restart_main || true
    die "Update rolled back. Backup: ${BACKUP_DIR}"
fi
printf 'Update completed successfully. Backup: %s\n' "${BACKUP_DIR}"
