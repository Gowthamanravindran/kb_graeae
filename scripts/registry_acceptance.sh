#!/bin/bash
# Real-stack acceptance in a Linux workspace: Postgres 16 binaries + moto S3 server + kb-registry over HTTP.
# Needs: /usr/lib/postgresql/16/bin, a venv with moto[server] at $A/motoenv, a complete pack in /home/claude/kbrun/home.
set -u
export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
A=/tmp/claude-0/acc; KB=/home/claude/kb-system; PG=/usr/lib/postgresql/16/bin
cd $KB
step() { echo; echo "=== $*"; }
step "postgres 16"
PGD=/tmp/kbacc-pg; rm -rf $PGD && mkdir -p $PGD && chown postgres $PGD
runuser -u postgres -- $PG/initdb -D $PGD/db -A trust -U kb -E UTF8 --no-locale >/dev/null
runuser -u postgres -- $PG/pg_ctl -D $PGD/db -o "-p 55432 -k $PGD -c listen_addresses=127.0.0.1" -l $PGD/log -w start >/dev/null && echo up
step "S3 (moto server)"
$A/motoenv/bin/moto_server -p 5555 > $A/moto.log 2>&1 &
MOTO=$!
sleep 3
$A/motoenv/bin/python -c "import boto3; boto3.client('s3', endpoint_url='http://127.0.0.1:5555', region_name='us-east-1', aws_access_key_id='x', aws_secret_access_key='x').create_bucket(Bucket='kb-packs'); print('bucket kb-packs')"
export KB_REGISTRY_DATABASE_URL=postgresql+psycopg://kb@127.0.0.1:55432/postgres KB_REGISTRY_STORAGE=s3://kb-packs KB_REGISTRY_S3_ENDPOINT=http://127.0.0.1:5555 AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=x AWS_DEFAULT_REGION=us-east-1
step "registry"
uv run kb-registry migrate 2>&1 | grep -v UV_
start_reg() { uv run kb-registry serve --port 8080 > $A/reg.log 2>&1 & REG=$!; for i in $(seq 1 30); do curl -sf localhost:8080/api/v1/auth/config >/dev/null && break; sleep 1; done; curl -s localhost:8080/api/v1/auth/config; echo; }
start_reg
TA=$(uv run kb-registry admin create-user gowthaman@local --admin 2>/dev/null | tail -1)
uv run kb-registry admin create-project sop 2>/dev/null
TV=$(uv run kb-registry admin create-user carol@local 2>/dev/null | tail -1)
uv run kb-registry admin grant carol@local sop viewer 2>/dev/null
sed 's#^langfuse:.*#registry: http://localhost:8080#' /home/claude/kbrun/sop.kb.yaml > $A/sop.kb.yaml
step "maintainer: login + push SOP pack"
export KB_HOME=/home/claude/kbrun/home
uv run kb login http://localhost:8080 --token $TA 2>&1 | grep -v UV_
( time uv run kb push $A/sop.kb.yaml --branch main ) 2>&1 | grep -v UV_ | grep -v "^$" | grep -v "user\|sys"
uv run kb push $A/sop.kb.yaml --branch main 2>&1 | grep -v UV_
QA=$(uv run kb memory list $A/sop.kb.yaml --branch main 2>/dev/null | grep "scheduling work in Tasks" | head -1 | cut -d' ' -f1)
echo "publishing $QA"
uv run kb publish $A/sop.kb.yaml --branch main $QA 2>&1 | grep -v UV_
uv run kb list $A/sop.kb.yaml 2>&1 | grep -v UV_
step "viewer (fresh machine): login, pull without kb.yaml, ask"
export KB_HOME=$A/carol
uv run kb login http://localhost:8080 --token $TV 2>&1 | grep -v UV_
( time uv run kb pull --project sop --registry http://localhost:8080 --branch main ) 2>&1 | grep -v UV_ | grep -v "^$" | grep -v "user\|sys"
uv run kb ask $A/sop.kb.yaml --branch main "How does scheduling work in Tasks?" 2>&1 | grep -v UV_ | grep -E "Reused|Saved as|UI flow|Backend flow" 
uv run kb publish $A/sop.kb.yaml --branch main $QA 2>&1 | grep -v UV_
step "restart registry; state survives"
kill $REG; sleep 2; start_reg
uv run kb pull $A/sop.kb.yaml --branch main 2>&1 | grep -v UV_
step "stop"
kill $REG $MOTO; runuser -u postgres -- $PG/pg_ctl -D $PGD/db -m fast stop >/dev/null && echo stopped
