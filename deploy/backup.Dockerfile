# Z11: the image of the compose `backup` service - the Postgres Alpine image of the database's major version (so
# pg_dump / pg_restore match it) plus the openssl CLI. The base image ships only the libssl libraries, and
# scripts/backup/backup.sh and restore.sh need the openssl command to encrypt, decrypt and seal every dump.
# Built per release by deploy/atp.sh (deploy/compose.release.yml: atp-backup:<release>). CI builds it with
# PG_IMAGE=postgres:17-alpine to match its own service database and runs the backup scripts inside it.
ARG PG_IMAGE=postgres:17-alpine
FROM ${PG_IMAGE}
RUN apk add --no-cache openssl && openssl version
