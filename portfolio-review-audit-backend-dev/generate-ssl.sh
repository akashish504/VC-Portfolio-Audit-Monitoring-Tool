#!/bin/bash

# Create directories for certificates
mkdir -p certbot/conf
mkdir -p certbot/data

# Generate self-signed SSL certificate
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certbot/conf/privkey.pem \
  -out certbot/conf/fullchain.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=IP:$1"

echo "Self-signed SSL certificate has been generated with IP: $1"
echo "Certificate location: certbot/conf/"

chmod -R 755 certbot/conf 