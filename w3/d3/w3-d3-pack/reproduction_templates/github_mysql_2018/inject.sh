#!/usr/bin/env bash
# Disconnect orchestrator from primary's network for 43 seconds (original partition length)
echo "[$(date -u +%H:%M:%S)] partitioning orchestrator from east network..."
docker network disconnect "$(basename "$PWD")"_east "$(basename "$PWD")-orchestrator-1" 2>/dev/null || \
  docker network disconnect github_mysql_2018_east github_mysql_2018-orchestrator-1
sleep 43
echo "[$(date -u +%H:%M:%S)] reconnecting..."
docker network connect "$(basename "$PWD")"_east "$(basename "$PWD")-orchestrator-1" 2>/dev/null || \
  docker network connect github_mysql_2018_east github_mysql_2018-orchestrator-1
echo "[$(date -u +%H:%M:%S)] reconnected — observe orchestrator logs for promotion decisions"
