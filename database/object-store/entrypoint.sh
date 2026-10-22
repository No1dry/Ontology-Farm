#!/bin/sh
set -eu
access=$(cat /run/secrets/s3_access_key)
secret=$(cat /run/secrets/s3_secret_key)
admin_access=$(cat /run/secrets/s3_admin_access_key)
admin_secret=$(cat /run/secrets/s3_admin_secret_key)
case "$access$secret$admin_access$admin_secret" in *[!a-zA-Z0-9_-]*) echo 'Invalid credential file format' >&2; exit 1;; esac
umask 077
printf '{"identities":[{"name":"bootstrap","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Admin"]},{"name":"lzo","credentials":[{"accessKey":"%s","secretKey":"%s"}],"actions":["Read:lzo-artifacts","Write:lzo-artifacts","List:lzo-artifacts"]}]}' "$admin_access" "$admin_secret" "$access" "$secret" > /data/s3.json
exec weed server -dir=/data -ip=object-store -master.port=9333 -master.telemetry=false -volume.port=8080 -volume.max=8 -master.volumeSizeLimitMB=1024 -filer -filer.port=8888 -s3 -s3.port=8333 -s3.port.iceberg=0 -s3.port.lance=0 -s3.config=/data/s3.json
