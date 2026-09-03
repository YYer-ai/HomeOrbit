import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "docker-compose.yml"
BUILD_SCRIPT = ROOT / "data/scripts/build_valhalla.sh"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
SCRIPT_VERSION = "2"
COMMANDS = [
    "valhalla_build_config",
    "valhalla_build_admins",
    "valhalla_build_timezones",
    "valhalla_build_tiles",
    "valhalla_build_extract",
]


def _compose_config() -> dict:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(COMPOSE_FILE),
            "--profile",
            "build",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _mount(service: dict, target: str) -> dict:
    return next(volume for volume in service["volumes"] if volume["target"] == target)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _msys_path(path: Path) -> str:
    resolved = path.resolve()
    return f"/{resolved.drive[0].lower()}{resolved.as_posix()[2:]}"


def _write_valid_tar(path: Path) -> None:
    payload = path.parent / "tile.txt"
    payload.write_text("tile", encoding="utf-8")
    with tarfile.open(path, "w") as archive:
        archive.add(payload, arcname="tile.txt")
    payload.unlink()


def _write_marker(raw: Path, output: Path) -> None:
    marker = output / ".build-complete"
    marker.write_text(
        "\n".join(
            [
                f"script_version={SCRIPT_VERSION}",
                f"pbf_sha256={_sha256(raw / 'california-latest.osm.pbf')}",
                f"tiles_sha256={_sha256(output / 'tiles.tar')}",
                f"config_sha256={_sha256(output / 'valhalla.json')}",
                f"admin_sha256={_sha256(output / 'admin.sqlite')}",
                f"timezone_sha256={_sha256(output / 'tz_world.sqlite')}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _make_stubs(stub_dir: Path) -> tuple[Path, Path]:
    command_log = stub_dir / "commands.log"
    move_log = stub_dir / "move.log"
    stub = """#!/bin/bash
name="$(basename "$0")"
printf '%s\\n' "$name" >> "$STUB_LOG"
if [[ "$name" == "${FAIL_COMMAND:-}" ]]; then
  exit 42
fi
case "$name" in
  valhalla_build_config)
    printf '%s\\n' '{}'
    ;;
  valhalla_build_admins)
    printf '%s' 'admin' > "$VALHALLA_OUTPUT_DIR/admin.sqlite"
    ;;
  valhalla_build_timezones)
    printf '%s' 'timezone'
    ;;
  valhalla_build_tiles)
    mkdir -p "$VALHALLA_OUTPUT_DIR/tiles"
    printf '%s' 'tile' > "$VALHALLA_OUTPUT_DIR/tiles/tile.txt"
    ;;
  valhalla_build_extract)
    if [[ "${STUB_CORRUPT_TAR:-}" == "1" ]]; then
      printf '%s' 'corrupt tar' > "$VALHALLA_OUTPUT_DIR/tiles.tar"
    else
      tar -cf "$VALHALLA_OUTPUT_DIR/tiles.tar" -C "$VALHALLA_OUTPUT_DIR/tiles" .
    fi
    ;;
esac
"""
    for command in COMMANDS:
        path = stub_dir / command
        path.write_text(stub, encoding="utf-8", newline="\n")
        path.chmod(0o755)
    bash_env = stub_dir / "bash_env"
    bash_env.write_text(
        "mv() {\n  printf '%s|%s\\n' \"$1\" \"$2\" >> \"$MV_LOG\"\n  /usr/bin/mv \"$@\"\n}\n",
        encoding="utf-8",
        newline="\n",
    )
    return command_log, move_log


def _run_build(
    tmp_path: Path, *, fail_command: str = "", corrupt_tar: bool = False
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    assert GIT_BASH.is_file(), "Git Bash is required for isolated script tests"
    raw = tmp_path / "raw"
    output = tmp_path / "output"
    stubs = tmp_path / "stubs"
    raw.mkdir(exist_ok=True)
    output.mkdir(exist_ok=True)
    stubs.mkdir(exist_ok=True)
    command_log, move_log = _make_stubs(stubs)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{_msys_path(stubs)}:/usr/bin:/bin",
            "VALHALLA_RAW_DIR": _msys_path(raw),
            "VALHALLA_OUTPUT_DIR": _msys_path(output),
            "STUB_LOG": _msys_path(command_log),
            "MV_LOG": _msys_path(move_log),
            "BASH_ENV": _msys_path(stubs / "bash_env"),
            "FAIL_COMMAND": fail_command,
            "STUB_CORRUPT_TAR": "1" if corrupt_tar else "0",
        }
    )
    result = subprocess.run(
        [str(GIT_BASH), BUILD_SCRIPT.as_posix()],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return result, raw, output, command_log


def test_build_and_runtime_services_have_isolated_mounts_and_ports() -> None:
    config = _compose_config()
    build = config["services"]["valhalla-build"]
    runtime = config["services"]["valhalla"]

    assert build["profiles"] == ["build"]
    assert "profiles" not in runtime
    assert build["image"] == runtime["image"] == "ghcr.io/valhalla/valhalla:latest"

    for service in (build, runtime):
        raw = _mount(service, "/data/raw")
        script = _mount(service, "/opt/homeorbit/build_valhalla.sh")
        output = _mount(service, "/data/valhalla")
        assert raw["type"] == script["type"] == output["type"] == "bind"
        assert raw["read_only"] is True
        assert script["read_only"] is True
        assert output.get("read_only", False) is False
        assert Path(raw["source"]) == ROOT / "data/raw"
        assert Path(script["source"]) == ROOT / "data/scripts/build_valhalla.sh"
        assert Path(output["source"]) == ROOT / "data/valhalla"

    assert "ports" not in build
    assert runtime["ports"] == [
        {
            "mode": "ingress",
            "protocol": "tcp",
            "published": "8002",
            "target": 8002,
        }
    ]


def test_runtime_only_serves_prebuilt_tiles() -> None:
    runtime = _compose_config()["services"]["valhalla"]

    assert runtime["command"] == [
        "valhalla_service",
        "/data/valhalla/valhalla.json",
        "1",
    ]
    assert not runtime.get("depends_on")
    command = " ".join(runtime["command"]).lower()
    assert "build" not in command
    assert "download" not in command


def test_postgis_contract_is_unchanged() -> None:
    config = _compose_config()
    postgis = config["services"]["postgis"]

    assert postgis["image"] == "postgis/postgis:17-3.5"
    assert postgis["container_name"] == "homeorbit-postgis"
    assert postgis["environment"] == {
        "POSTGRES_DB": "homeorbit",
        "POSTGRES_PASSWORD": "homeorbit_dev",
        "POSTGRES_USER": "homeorbit",
    }
    assert postgis["ports"] == [
        {"mode": "ingress", "protocol": "tcp", "published": "5432", "target": 5432}
    ]
    assert postgis["volumes"] == [
        {
            "source": "postgis_data",
            "target": "/var/lib/postgresql/data",
            "type": "volume",
            "volume": {},
        }
    ]
    assert postgis["healthcheck"] == {
        "test": ["CMD-SHELL", "pg_isready -U homeorbit -d homeorbit"],
        "interval": "5s",
        "timeout": "5s",
        "retries": 10,
        "start_period": "10s",
    }
    assert config["volumes"]["postgis_data"]["name"] == "homeorbit_postgis_data"


def test_build_fails_before_commands_when_pbf_is_missing(tmp_path: Path) -> None:
    result, _, _, command_log = _run_build(tmp_path)

    assert result.returncode != 0
    assert "missing required PBF" in result.stderr
    assert not command_log.exists()


@pytest.mark.parametrize(
    "partial",
    [
        "valhalla.json",
        "tiles.tar",
        "admin.sqlite",
        "tz_world.sqlite",
        "tiles",
        ".build-complete",
        ".build-complete.tmp.interrupted",
    ],
)
def test_any_partial_artifact_without_valid_completion_fails_closed(tmp_path: Path, partial: str) -> None:
    result, raw, output, command_log = _run_build(tmp_path)
    (raw / "california-latest.osm.pbf").write_bytes(b"pbf")
    target = output / partial
    if partial == "tiles":
        target.mkdir()
    elif partial == "tiles.tar":
        _write_valid_tar(target)
    else:
        target.write_bytes(b"partial")

    result, _, _, command_log = _run_build(tmp_path)

    assert result.returncode != 0
    assert "incomplete or invalid" in result.stderr
    assert target.exists()
    assert not command_log.exists()


def test_complete_marker_requires_a_readable_tar(tmp_path: Path) -> None:
    _, raw, output, _ = _run_build(tmp_path)
    (raw / "california-latest.osm.pbf").write_bytes(b"pbf")
    (output / "valhalla.json").write_bytes(b"config")
    (output / "tiles.tar").write_bytes(b"not a tar")
    (output / "admin.sqlite").write_bytes(b"admin")
    (output / "tz_world.sqlite").write_bytes(b"timezone")
    _write_marker(raw, output)

    result, _, _, command_log = _run_build(tmp_path)

    assert result.returncode != 0
    assert "incomplete or invalid" in result.stderr
    assert not command_log.exists()


def test_complete_marker_is_idempotent_and_rejects_input_drift(tmp_path: Path) -> None:
    _, raw, output, _ = _run_build(tmp_path)
    pbf = raw / "california-latest.osm.pbf"
    pbf.write_bytes(b"pbf")
    (output / "valhalla.json").write_bytes(b"config")
    _write_valid_tar(output / "tiles.tar")
    (output / "admin.sqlite").write_bytes(b"admin")
    (output / "tz_world.sqlite").write_bytes(b"timezone")
    _write_marker(raw, output)

    complete, _, _, command_log = _run_build(tmp_path)
    assert complete.returncode == 0
    assert "already exist" in complete.stdout
    assert not command_log.exists()

    pbf.write_bytes(b"changed-pbf")
    drifted, _, _, command_log = _run_build(tmp_path)
    assert drifted.returncode != 0
    assert "incomplete or invalid" in drifted.stderr
    assert not command_log.exists()


@pytest.mark.parametrize("failed_index", range(len(COMMANDS)))
def test_command_failure_stops_the_build_without_marker(tmp_path: Path, failed_index: int) -> None:
    _, raw, output, _ = _run_build(tmp_path)
    (raw / "california-latest.osm.pbf").write_bytes(b"pbf")

    result, _, _, command_log = _run_build(tmp_path, fail_command=COMMANDS[failed_index])

    assert result.returncode == 42
    assert command_log.read_text(encoding="utf-8").splitlines() == COMMANDS[: failed_index + 1]
    assert not (output / ".build-complete").exists()
    assert not list(output.glob(".build-complete.tmp.*"))


def test_success_runs_five_commands_in_order_and_atomically_marks_complete(tmp_path: Path) -> None:
    _, raw, output, _ = _run_build(tmp_path)
    (raw / "california-latest.osm.pbf").write_bytes(b"pbf")

    result, _, _, command_log = _run_build(tmp_path)

    assert result.returncode == 0, result.stderr
    assert command_log.read_text(encoding="utf-8").splitlines() == COMMANDS
    assert not list(output.glob(".build-complete.tmp.*"))
    marker = output / ".build-complete"
    assert marker.is_file()
    marker_text = marker.read_text(encoding="utf-8")
    assert f"script_version={SCRIPT_VERSION}" in marker_text
    assert f"pbf_sha256={_sha256(raw / 'california-latest.osm.pbf')}" in marker_text
    assert f"tiles_sha256={_sha256(output / 'tiles.tar')}" in marker_text
    move_log = (tmp_path / "stubs/move.log").read_text(encoding="utf-8").strip()
    source, destination = move_log.split("|")
    assert source.rsplit("/", 1)[0] == _msys_path(output)
    assert destination == _msys_path(marker)
    assert Path(source).name.startswith(".build-complete.tmp.")


def test_successful_commands_with_corrupt_tar_do_not_create_marker(tmp_path: Path) -> None:
    _, raw, output, _ = _run_build(tmp_path)
    (raw / "california-latest.osm.pbf").write_bytes(b"pbf")

    result, _, _, command_log = _run_build(tmp_path, corrupt_tar=True)

    assert result.returncode != 0
    assert command_log.read_text(encoding="utf-8").splitlines() == COMMANDS
    assert not (output / ".build-complete").exists()
    assert not list(output.glob(".build-complete.tmp.*"))


def test_build_never_invokes_rm(tmp_path: Path) -> None:
    _, raw, _, _ = _run_build(tmp_path)
    (raw / "california-latest.osm.pbf").write_bytes(b"pbf")
    rm_log = tmp_path / "stubs/rm.log"
    rm_stub = tmp_path / "stubs/rm"
    rm_stub.write_text(
        f"#!/bin/bash\nprintf '%s\\n' \"$*\" >> '{rm_log.as_posix()}'\nexit 99\n",
        encoding="utf-8",
        newline="\n",
    )
    rm_stub.chmod(0o755)

    result, _, _, _ = _run_build(tmp_path)

    assert result.returncode == 0
    assert not rm_log.exists()
