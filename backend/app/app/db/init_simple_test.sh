#!/usr/bin/env bash

# Minimal scenario for testing the huwaei QoS backend (Slice Manager):
# 1 gNB, 1 Cell and 1 UE matching the slice association in docs/slice-manager-curl.md

URL=http://localhost
PORT=8888
FIRST_SUPERUSER="admin@my-email.com"
FIRST_SUPERUSER_PASSWORD="pass"

set -a # automatically export all variables
source .env
set +a

# help
for arg in "$@"; do
  if [ "$arg" == "--help" ]; then
    echo "Usage: cmd [-h URL] [-p PORT] [-u FIRST_SUPERUSER] [-s FIRST_SUPERUSER_PASSWORD] [--help]"
    exit 0
  fi
done

# get opts
while getopts ":h:p:u:s:" opt; do
  case ${opt} in
    h )
      URL=$OPTARG
      ;;
    p )
      PORT=$OPTARG
      ;;
    u )
      FIRST_SUPERUSER=$OPTARG
      ;;
    # Secret
    s )
      FIRST_SUPERUSER_PASSWORD=$OPTARG
      ;;
    \? )
      echo "Invalid option: -$OPTARG" >&2
      echo "Usage: cmd [-h URL] [-p PORT] [-u FIRST_SUPERUSER] [-s FIRST_SUPERUSER_PASSWORD] [--help]"
      exit 1
      ;;
  esac
done

printf '\n==================================================\n'
printf 'Create Token for login'
printf '\n==================================================\n'


TOKEN=$(curl -X 'POST' \
  "${URL}:${PORT}/api/v1/login/access-token" \
  -H 'accept: application/json' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "username=${FIRST_SUPERUSER}" \
  --data-urlencode "password=${FIRST_SUPERUSER_PASSWORD}" \
  -d "grant_type=&scope=&client_id=&client_secret=" \
  | jq -r '.access_token')

printf $TOKEN


printf '\n==================================================\n'
printf 'Initiallizing gNBs for admin...'
printf '\n==================================================\n'

curl -X 'POST' \
  "${URL}:${PORT}/api/v1/gNBs" \
  -H 'accept: application/json' \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
  "gNB_id": "AAAAA1",
  "name": "gNB1",
  "description": "This is a base station",
  "location": "unknown"
}'


printf '\n==================================================\n'
printf 'Initiallizing Cells for admin...'
printf '\n==================================================\n'

curl -X 'POST' \
  "${URL}:${PORT}/api/v1/Cells" \
  -H 'accept: application/json' \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
  "cell_id": "AAAAA1001",
  "name": "cell1",
  "description": "Administration Building",
  "gNB_id": 1,
  "latitude" : 37.999239,
  "longitude" : 23.819069,
  "radius" : 100
}'


printf '\n==================================================\n'
printf 'Initiallizing UEs for admin...'
printf '\n==================================================\n'

# supi, ip_address_v4 and dnn match the Slice Manager UE slice association
curl -X 'POST' \
  "${URL}:${PORT}/api/v1/UEs" \
  -H 'accept: application/json' \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{
  "supi": "999080100001134",
  "name": "UE1",
  "description": "Slice Manager test UE",
  "gNB_id": 1,
  "Cell_id": 1,
  "ip_address_v4": "10.16.9.5",
  "ip_address_v6": "0:0:0:0:0:0:0:1",
  "mac_address": "22-00-00-00-00-01",
  "msisdn": "9990801000011",
  "dnn": "embb",
  "mcc": 999,
  "mnc": 8,
  "external_identifier": "10001@domain.com",
  "speed": "LOW"
}'

printf '\n==================================================\n\n'
