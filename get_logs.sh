#!/bin/bash
# Simple script to get bot logs
docker compose logs --tail=100 bluesky-bot 2>/dev/null || echo "Failed to get logs"