#!/bin/bash
set -euo pipefail

script_version="2"
raw_dir="${VALHALLA_RAW_DIR:-/data/raw}"
output_dir="${VALHALLA_OUTPUT_DIR:-/data/valhalla}"
pbf="${VALHALLA_PBF:-$raw_dir/california-latest.osm.pbf}"
config="$output_dir/valhalla.json"
extract="$output_dir/tiles.tar"
admin="$output_dir/admin.sqlite"
timezone="$output_dir/tz_world.sqlite"
tiles="$output_dir/tiles"
marker="$output_dir/.build-complete"

if [[ ! -f "$pbf" ]]; then
  echo "ERROR: missing required PBF: $pbf" >&2
  exit 1
fi

validate_complete() {
  [[ -f "$marker" ]] || return 1
  [[ -s "$config" && -s "$extract" && -s "$admin" && -s "$timezone" ]] || return 1
  tar -tf "$extract" >/dev/null || return 1

  local pbf_sha tiles_sha config_sha admin_sha timezone_sha
  pbf_sha="$(sha256sum "$pbf" | awk '{print $1}')"
  tiles_sha="$(sha256sum "$extract" | awk '{print $1}')"
  config_sha="$(sha256sum "$config" | awk '{print $1}')"
  admin_sha="$(sha256sum "$admin" | awk '{print $1}')"
  timezone_sha="$(sha256sum "$timezone" | awk '{print $1}')"

  grep -Fqx "script_version=$script_version" "$marker" &&
    grep -Fqx "pbf_sha256=$pbf_sha" "$marker" &&
    grep -Fqx "tiles_sha256=$tiles_sha" "$marker" &&
    grep -Fqx "config_sha256=$config_sha" "$marker" &&
    grep -Fqx "admin_sha256=$admin_sha" "$marker" &&
    grep -Fqx "timezone_sha256=$timezone_sha" "$marker"
}

if validate_complete; then
  echo "Valhalla build artifacts already exist; nothing to do."
  exit 0
fi

if [[ -e "$marker" || -e "$config" || -e "$extract" || -e "$admin" || -e "$timezone" || -e "$tiles" ]] ||
  compgen -G "$marker.tmp.*" >/dev/null; then
  echo "ERROR: incomplete or invalid Valhalla artifacts detected; inspect and resolve manually without automatic deletion." >&2
  exit 1
fi

mkdir -p "$tiles"

valhalla_build_config \
  --mjolnir-tile-dir "$tiles" \
  --mjolnir-tile-extract "$extract" \
  --mjolnir-timezone "$timezone" \
  --mjolnir-admin "$admin" \
  > "$config"
valhalla_build_admins -c "$config" "$pbf"
valhalla_build_timezones > "$timezone"
valhalla_build_tiles -c "$config" "$pbf"
valhalla_build_extract -c "$config" -v

if [[ ! -s "$config" || ! -s "$extract" || ! -s "$admin" || ! -s "$timezone" ]] ||
  ! tar -tf "$extract" >/dev/null; then
  echo "ERROR: Valhalla build commands completed but output validation failed; inspect manually without automatic deletion." >&2
  exit 1
fi

pbf_sha="$(sha256sum "$pbf" | awk '{print $1}')"
tiles_sha="$(sha256sum "$extract" | awk '{print $1}')"
config_sha="$(sha256sum "$config" | awk '{print $1}')"
admin_sha="$(sha256sum "$admin" | awk '{print $1}')"
timezone_sha="$(sha256sum "$timezone" | awk '{print $1}')"
marker_tmp="$marker.tmp.$$"
printf '%s\n' \
  "script_version=$script_version" \
  "pbf_sha256=$pbf_sha" \
  "tiles_sha256=$tiles_sha" \
  "config_sha256=$config_sha" \
  "admin_sha256=$admin_sha" \
  "timezone_sha256=$timezone_sha" \
  > "$marker_tmp"
mv "$marker_tmp" "$marker"
