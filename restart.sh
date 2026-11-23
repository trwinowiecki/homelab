#!/usr/bin/bash
set -e

FOLDER_FOR_YAMLS=/mnt/config # <-- Folder where the yaml and .env files are located

obsv_mark() {
  local stage="$1"
  mkdir -p /arrstack/obsv 2>/dev/null || true
  printf '{"_time":"%s","script":"%s","stage":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(basename "$0")" \
    "$stage" >> /arrstack/obsv/deploy-markers.log 2>/dev/null || true
}

# Check if .env exists
cd $FOLDER_FOR_YAMLS

ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
  echo "❌ Error: .env file not found in $(pwd)"
  echo "Please update the FOLDER_FOR_YAMLS=/docker location inside the restart.sh script"
  exit 1
fi

# Read values from .env and clean them
FOLDER_FOR_MEDIA=$(grep -E '^FOLDER_FOR_MEDIA=' "$ENV_FILE" | cut -d '=' -f2- | xargs | tr -d '\r')
FOLDER_FOR_DOWNLOADS=$(grep -E '^FOLDER_FOR_DOWNLOADS=' "$ENV_FILE" | cut -d '=' -f2- | xargs | tr -d '\r')
FOLDER_FOR_DATA=$(grep -E '^FOLDER_FOR_DATA=' "$ENV_FILE" | cut -d '=' -f2- | xargs | tr -d '\r')
PUID=$(grep -E '^PUID=' "$ENV_FILE" | cut -d '=' -f2- | xargs | tr -d '\r')
PGID=$(grep -E '^PGID=' "$ENV_FILE" | cut -d '=' -f2- | xargs | tr -d '\r')

echo && echo "✅ Found the following variables / values in your $ENV_FILE file:"
echo "   - FOLDER_FOR_MEDIA=$FOLDER_FOR_MEDIA"
echo "   - FOLDER_FOR_DATA=$FOLDER_FOR_DATA"
echo "   - PUID=$PUID"
echo "   - PGID=$PGID"

# Validate required vars
MISSING_VARS=()
[ -z "$FOLDER_FOR_MEDIA" ] && MISSING_VARS+=("FOLDER_FOR_MEDIA")
[ -z "$FOLDER_FOR_DATA" ] && MISSING_VARS+=("FOLDER_FOR_DATA")
[ -z "$PUID" ] && MISSING_VARS+=("PUID")
[ -z "$PGID" ] && MISSING_VARS+=("PGID")

if [ ${#MISSING_VARS[@]} -ne 0 ]; then
  echo "❌ Error: The following required variables are missing or empty in $ENV_FILE:"
  for var in "${MISSING_VARS[@]}"; do
    echo "   - $var"
  done
  exit 1
fi

echo
echo Creating folders and setting permissions...
echo

cd $FOLDER_FOR_YAMLS
sudo -E mkdir -p $FOLDER_FOR_DATA/{audiobookshelf/{config,metadata},bazarr,bindery,configarr,gluetun,homepage,jellyseerr,lidarr,mealie,mealie-og-sync,mixarr,portainer,prowlarr,qbittorrent,radarr,recyclarr,sabnzbd,sonarr,tdarr/{server,configs,logs},tdarr-node}
sudo -E mkdir -p $FOLDER_FOR_MEDIA/media/{anime,audio,books,comics,movies,music,photos,tv,xxx}
sudo -E mkdir -p $FOLDER_FOR_DOWNLOADS/usenet/{complete,incomplete}
sudo -E mkdir -p $FOLDER_FOR_DOWNLOADS/torrents/{complete,incomplete}
sudo -E mkdir -p $FOLDER_FOR_DOWNLOADS/torrents/complete/{tv-sonarr,radarr,lidarr,audio}
sudo -E mkdir -p $FOLDER_FOR_DOWNLOADS/usenet/complete/{tv,movies,music,audio}
sudo -E mkdir -p $FOLDER_FOR_MEDIA/watch
sudo -E mkdir -p $FOLDER_FOR_MEDIA/filebot/{input,output}
sudo -E chmod -R 2775 $FOLDER_FOR_MEDIA $FOLDER_FOR_DATA        # $FOLDER_FOR_YAMLS     # <-- Enable if you need to set permissions on YAML files / folder
sudo -E chown -R $PUID:$PGID $FOLDER_FOR_MEDIA $FOLDER_FOR_DATA # $FOLDER_FOR_YAMLS     # <-- Enable if you need to set permissions on YAML files / folder

# This checks for missing variables and invalid docker compose configuration
echo
echo Validating Docker Compose configuration...
echo
if ! docker compose config >/dev/null; then
  echo
  echo Docker Compose configuration is invalid or missing required variables...
  echo
  exit 1
fi

obsv_mark start

# Download all Docker images - will also pull newer / updated images if they exist
echo
echo Pulling new / updated Docker images...
echo
docker compose pull

echo
echo Removing all non-persistent Docker containers, volumes, and networks...
echo
if [ -n "$containers" ]; then
  sudo docker stop $containers # Stop all active Docker containers
  sudo docker rm $containers   # Remove all active Docker containers
fi

sudo docker container prune -f # Force-remove all Docker containers
#sudo docker image      prune -a -f       # Force-remove all Docker images                   <-- THIS WILL FORCE ALL DOCKER IMAGES TO BE DOWNLOADED AGAIN
sudo docker volume prune -f  # Force-remove all non-persistent Docker volumes
sudo docker network prune -f # Force-remove all Docker networks

echo
echo Moving configuration files into application folders...
echo
sudo chmod 600 .env 2>/dev/null || true
sudo chmod 664 *yaml 2>/dev/null || sudo chmod 664 docker-compose.yaml
sudo chown $PUID:$PGID .env *yaml *sh 2>/dev/null || sudo chown $PUID:$PGID .env docker-compose.yaml restart.sh

ARRSTACK_SRC="$FOLDER_FOR_YAMLS/arrstack"
if [ -d "$ARRSTACK_SRC" ]; then
  sudo mkdir -p "$FOLDER_FOR_DATA"/{audiobookshelf/{config,metadata},bindery,configarr,homepage,mealie,mealie-og-sync,mixarr,recyclarr}
  sudo cp -f "$ARRSTACK_SRC/recyclarr.yml" "$FOLDER_FOR_DATA/recyclarr/recyclarr.yml"
  sudo cp -f "$ARRSTACK_SRC/mealie-og-sync.yaml" "$FOLDER_FOR_DATA/mealie-og-sync/mealie-og-sync.yaml"
  sudo cp -n "$ARRSTACK_SRC/mealie-og-sync/credentials.env.example" "$FOLDER_FOR_DATA/mealie-og-sync/credentials.env" 2>/dev/null || true
  sudo cp -f "$ARRSTACK_SRC/configarr/config.yml" "$FOLDER_FOR_DATA/configarr/config.yml"
  sudo cp -f "$ARRSTACK_SRC/homepage/"*.yaml "$FOLDER_FOR_DATA/homepage/"
  echo "   Declarative arrstack configs copied from $ARRSTACK_SRC"
fi

if [ -d "$FOLDER_FOR_DATA/jellyseerr" ]; then
  sudo chown -R 1000:1000 "$FOLDER_FOR_DATA/jellyseerr"
  echo "   Seerr config dir ownership set to 1000:1000 (jellyseerr volume)"
fi

for pair in \
  "headplane-config.yaml:$FOLDER_FOR_DATA/headplane/config.yaml" \
  "headscale-config.yaml:$FOLDER_FOR_DATA/headscale/config.yaml" \
  "traefik-static.yaml:$FOLDER_FOR_DATA/traefik/traefik.yaml" \
  "traefik-dynamic.yaml:$FOLDER_FOR_DATA/traefik/dynamic.yaml" \
  "traefik-internal.yaml:$FOLDER_FOR_DATA/traefik/internal.yaml" \
  "crowdsec-acquis.yaml:$FOLDER_FOR_DATA/crowdsec/acquis.yaml"; do
  src="${pair%%:*}"
  dst="${pair#*:}"
  if [ -f "$src" ]; then
    sudo cp "$src" "$dst" && echo "   File $src copied to $dst"
  fi
done

# Subroutine below will check if Docker successully started all containers, before pruning un-used images from Docker
echo
echo Recreating all Docker containers, volumes, and networks...
echo
if ! docker compose up -d; then
  echo Command "'"docker compose up -d"'" failed to start containers... exiting!
  exit 1
fi

EXPECTED_SERVICES=$(docker compose config --services)
FAILED=0

for SERVICE in $EXPECTED_SERVICES; do
  STATUS=$(docker inspect --format='{{.State.Running}}' "$(docker compose ps -q $SERVICE)")
  if [[ "$STATUS" != "true" ]]; then
    echo
    echo "Docker container $SERVICE is not running..."
    echo
    FAILED=1
  fi
done

if [[ $FAILED -eq 0 ]]; then
  echo
  echo "All Docker containers are running... Removing all outdated Docker images that are not being used..."
  echo
  sudo docker image prune -a -f
  obsv_mark finish
else
  echo
  echo "One or more Docker services failed to start, unused Docker images will not be deleted..."
  echo
  exit 1
fi
