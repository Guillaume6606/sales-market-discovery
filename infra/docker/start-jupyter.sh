#!/bin/bash
set -e

echo "Starting Jupyter Lab..."

# Create notebooks directory if it doesn't exist
mkdir -p /app/notebooks

# Start Jupyter Lab
exec jupyter lab \
    --ip=0.0.0.0 \
    --port=8888 \
    --no-browser \
    --allow-root \
    --NotebookApp.token='' \
    --NotebookApp.password='' \
    --NotebookApp.allow_origin='*' \
    --NotebookApp.base_url='/' \
    --notebook-dir=/app/notebooks


