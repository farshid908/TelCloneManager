#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY="${UPDATER_REPOSITORY:-https://github.com/farshid908/TelCloneManager}"
BRANCH="${UPDATER_BRANCH:-main}"
MAIN_SCREEN="${MAIN_SCREEN_NAME:-telegram}"
PYTHON_BIN="${PYTHON_BIN:-}"
START_COMMAND="${MAIN_START_COMMAND:-}"
BACKUP_ROOT="${ROOT_DIR}/.update_backups"
PROGRESS_FILE="${ROOT_DIR}/.update_progress.log"
DEPENDENCY_LOG="${ROOT_DIR}/dependency_update_error.txt"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d -t telclonemanager-update.XXXXXX)"
ARCHIVE="${WORK_DIR}/source.zip"
EXTRACT_DIR="${WORK_DIR}/extract"
BACKUP_DIR="${BACKUP_ROOT}/${STAMP}"
INSTALLED_FILES=()
CHANGED_FILES=()

cleanup() { rm -rf -- "${WORK_DIR}"; }
trap cleanup EXIT
die() { progress "ERROR: $*"; printf 'ERROR: %s\n' "$*" >&2; exit 1; }
progress() { printf '%s\n' "$*" | tee -a "${PROGRESS_FILE}"; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

detect_python() {
    local candidate version pid exe base
    if [[ -n "${PYTHON_BIN}" ]]; then
        if [[ -x "${PYTHON_BIN}" ]]; then
            version="$(${PYTHON_BIN} --version 2>&1 || true)"
            [[ "${version}" == Python\ * ]] && return
        elif candidate="$(command -v "${PYTHON_BIN}" 2>/dev/null || true)" && [[ -n "${candidate}" ]]; then
            version="$(${candidate} --version 2>&1 || true)"
            if [[ "${version}" == Python\ * ]]; then
                PYTHON_BIN="${candidate}"
                return
            fi
        fi
        PYTHON_BIN=""
    fi
    while read -r pid; do
        [[ -n "${pid}" && -e "/proc/${pid}/exe" ]] || continue
        exe="$(readlink -f "/proc/${pid}/exe" 2>/dev/null || true)"
        base="$(basename "${exe}")"
        [[ "${base}" == python || "${base}" == python[0-9]* ]] || continue
        version="$(${exe} --version 2>&1 || true)"
        if [[ "${version}" == Python\ * ]]; then
            PYTHON_BIN="${exe}"
            return
        fi
    done < <(pgrep -f '[p]ython.*main\.py' || true)

    for candidate in /opt/python3.14/bin/python3.14 /usr/local/bin/python3 /usr/bin/python3 /usr/bin/python; do
        [[ -x "${candidate}" ]] || continue
        version="$(${candidate} --version 2>&1 || true)"
        if [[ "${version}" == Python\ * ]]; then
            PYTHON_BIN="${candidate}"
            return
        fi
    done
    die "Could not find a usable Python interpreter for Main"
}

archive_key() {
    local relative="$1"
    if [[ "${relative}" == "requirements.txt" ]]; then
        printf 'requirements'
    else
        printf '%s' "${relative%.py}" | sed 's#/#__#g'
    fi
}

expected_hash() {
    local key="$1" file="$2"
    awk -F= -v wanted="${key}" '$1 == wanted {print $2; found=1; exit} END {if (!found) exit 1}' "${file}"
}

local_hash() {
    local file="$1"
    if [[ "${file}" == "requirements.txt" ]]; then
        sed -n 's/^# TCM_REQUIREMENTS_HASH=\([0-9]\{10\}\)$/\1/p' "${ROOT_DIR}/${file}" | head -n 1
    else
        sed -n 's/^__TCM_FILE_HASH__ = "\([0-9]\{10\}\)"$/\1/p' "${ROOT_DIR}/${file}" | head -n 1
    fi
}

restore_backup() {
    [[ -d "${BACKUP_DIR}" ]] || return 0
    for relative in "${INSTALLED_FILES[@]}"; do
        local destination="${ROOT_DIR}/${relative}"
        local saved="${BACKUP_DIR}/${relative}"
        if [[ -f "${saved}" ]]; then
            mkdir -p -- "$(dirname -- "${destination}")"
            cp -p -- "${saved}" "${destination}"
        else
            rm -f -- "${destination}"
        fi
    done
}

restart_main() {
    local python_for_main="${PYTHON_BIN}"
    local command="${START_COMMAND:-${python_for_main} -u main.py >> runtime.stdout.log 2>> runtime.stderr.log}"
    screen -S "${MAIN_SCREEN}" -X quit >/dev/null 2>&1 || true
    sleep 2
    screen -dmS "${MAIN_SCREEN}" bash -lc "cd $(printf '%q' "${ROOT_DIR}") && exec ${command}"
    for _ in $(seq 1 15); do
        if screen -list | grep -Eq "[.]${MAIN_SCREEN}[[:space:]]" \
            && ps -eo comm=,args= | awk '$1 ~ /^python/ && $0 ~ /main\.py/ {found=1} END {exit !found}'; then
            sleep 2
            ps -eo comm=,args= | awk '$1 ~ /^python/ && $0 ~ /main\.py/ {found=1} END {exit !found}'
            return $?
        fi
        sleep 2
    done
    return 1
}

require_command curl
require_command unzip
require_command find
require_command screen
require_command awk
require_command cmp
detect_python
: > "${PROGRESS_FILE}"
progress "Using Python interpreter: ${PYTHON_BIN}"
"${PYTHON_BIN}" --version | tee -a "${PROGRESS_FILE}"

progress "Downloading ${REPOSITORY} (${BRANCH})..."
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
[[ -f "${SOURCE_ROOT}/hashforupdate" ]] || die "hashforupdate is missing from the update archive"

progress "Checking Python syntax..."
while IFS= read -r -d '' source_file; do
    relative="${source_file#"${SOURCE_ROOT}/"}"
    if ! syntax_error="$(${PYTHON_BIN} -m py_compile "${source_file}" 2>&1)"; then
        progress "ERROR: Syntax check failed: ${relative}"
        progress "${syntax_error}"
        die "Syntax check failed: ${relative}"
    fi
done < <(find "${SOURCE_ROOT}" -type f -name '*.py' -not -path '*/.*' -print0)

while IFS= read -r -d '' source_file; do
    relative="${source_file#"${SOURCE_ROOT}/"}"
    key="$(archive_key "${relative}")"
    expected="$(expected_hash "${key}" "${SOURCE_ROOT}/hashforupdate" || true)"
    [[ -n "${expected}" ]] || die "No hash entry for ${relative} (${key})"
    if [[ ! -f "${ROOT_DIR}/${relative}" ]] || [[ "$(local_hash "${relative}")" != "${expected}" ]]; then
        CHANGED_FILES+=("${relative}")
    fi
done < <(find "${SOURCE_ROOT}" -type f \( -name '*.py' -o -name 'requirements.txt' \) -not -path '*/.*' -print0)

if ! cmp -s -- "${SOURCE_ROOT}/hashforupdate" "${ROOT_DIR}/hashforupdate" 2>/dev/null; then
    CHANGED_FILES+=("hashforupdate")
fi

if [[ "${#CHANGED_FILES[@]}" -eq 0 ]]; then
    progress "Already up to date. No source hashes changed."
    exit 0
fi

printf '%s\n' "${CHANGED_FILES[@]}" | sort -u > "${WORK_DIR}/changed.list"
mapfile -t CHANGED_FILES < "${WORK_DIR}/changed.list"
progress "Changed files: ${#CHANGED_FILES[@]}"

REQUIREMENTS_CHANGED=0
for relative in "${CHANGED_FILES[@]}"; do
    [[ "${relative}" == "requirements.txt" ]] && REQUIREMENTS_CHANGED=1
done

if [[ "${REQUIREMENTS_CHANGED}" -eq 1 ]]; then
    progress "requirements.txt changed; creating an isolated virtual environment..."
    VENV_NEW="${WORK_DIR}/venv"
    : > "${DEPENDENCY_LOG}"
    if ! "${PYTHON_BIN}" -m venv "${VENV_NEW}" >>"${DEPENDENCY_LOG}" 2>&1; then
        progress "DEPENDENCY_UPDATE_REQUIRED=1"
        progress "Python venv creation failed; the new source was not started."
        exit 42
    fi
    if ! "${VENV_NEW}/bin/python" -m pip install --disable-pip-version-check -r "${SOURCE_ROOT}/requirements.txt" >>"${DEPENDENCY_LOG}" 2>&1; then
        progress "DEPENDENCY_UPDATE_REQUIRED=1"
        progress "Dependency installation failed; the new source was not started."
        exit 42
    fi
    progress "All requirements installed successfully in the isolated venv."
fi

mkdir -p -- "${BACKUP_DIR}"
for relative in "${CHANGED_FILES[@]}"; do
    [[ "${relative}" == "hashforupdate" ]] && source_file="${SOURCE_ROOT}/hashforupdate" || source_file="${SOURCE_ROOT}/${relative}"
    INSTALLED_FILES+=("${relative}")
    destination="${ROOT_DIR}/${relative}"
    if [[ -f "${destination}" ]]; then
        mkdir -p -- "${BACKUP_DIR}/$(dirname -- "${relative}")"
        cp -p -- "${destination}" "${BACKUP_DIR}/${relative}"
    fi
done

if [[ "${REQUIREMENTS_CHANGED}" -eq 1 ]]; then
    if [[ -d "${ROOT_DIR}/venv" ]]; then
        mkdir -p -- "${BACKUP_DIR}/venv-backup"
        mv -- "${ROOT_DIR}/venv" "${BACKUP_DIR}/venv-backup/venv"
    fi
    mv -- "${VENV_NEW}" "${ROOT_DIR}/venv"
fi

progress "Installing changed source files..."
for relative in "${CHANGED_FILES[@]}"; do
    source_file="${SOURCE_ROOT}/${relative}"
    destination="${ROOT_DIR}/${relative}"
    mkdir -p -- "$(dirname -- "${destination}")"
    cp -p -- "${source_file}" "${destination}"
done

progress "Restarting screen ${MAIN_SCREEN}..."
if ! restart_main; then
    progress "Main screen failed to start; restoring backup..."
    restore_backup
    restart_main || true
    die "Update rolled back. Backup: ${BACKUP_DIR}"
fi
progress "Update completed successfully. Backup: ${BACKUP_DIR}"
