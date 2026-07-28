#!/bin/bash

# Exit on error
set -e

# Configuration
DOCKER_IMAGE_NAME="peakxv-pr-tool"
DOCKER_REGISTRY="ashishtailoredai"  # Replace with your Docker registry
DOCKER_TAG="latest"

# Create a builder instance if it doesn't exist
docker buildx create --use --name multiarch-builder --driver docker-container || true

# Build and push Docker image for AMD64 architecture directly
echo "Building and pushing Docker image for AMD64 architecture..."
docker buildx build --platform linux/amd64 --push -t $DOCKER_REGISTRY/$DOCKER_IMAGE_NAME:$DOCKER_TAG .

echo "Docker image pushed to registry: $DOCKER_REGISTRY/$DOCKER_IMAGE_NAME:$DOCKER_TAG"
echo "Run the following command on your EC2 instance:"
echo "docker pull $DOCKER_REGISTRY/$DOCKER_IMAGE_NAME:$DOCKER_TAG && docker-compose -f docker-compose.prod.yaml up -d" 