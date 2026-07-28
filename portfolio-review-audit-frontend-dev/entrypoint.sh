#!/bin/sh
set -e

echo "[entrypoint] Starting configuration..."

# ==========================================
# PART 1: Frontend Variable Injection
# ==========================================
REACT_BUILD_DIR=/usr/share/nginx/html

cat > $REACT_BUILD_DIR/config.js << EOF
window._env_ = {
  'API_URL': '${API_URL}',
  'VITE_API_BASE_URL': '${VITE_API_BASE_URL}',
  'VITE_OKTA_ISSUER': '${VITE_OKTA_ISSUER}',
  'VITE_OKTA_CLIENT_ID': '${VITE_OKTA_CLIENT_ID}',
  'VITE_OKTA_REDIRECT_URI': '${VITE_OKTA_REDIRECT_URI}',
  'VITE_ENABLE_OKTA': '${VITE_ENABLE_OKTA:-true}',
  'VITE_ENABLE_CSRF': '${VITE_ENABLE_CSRF:-true}',
};
EOF

echo "[entrypoint] Frontend variables injected into config.js"

# ==========================================
# PART 2: Nginx CORS Variable Injection
# ==========================================
echo "set \$CORS_ORIGIN \"$CORS_ORIGIN\";" > /etc/nginx/conf.d/cors_env.txt

echo "[entrypoint] CORS variable written to Nginx config"

# ==========================================
# PART 2b: API upstream for /api/ → FastAPI proxy (see nginx.conf)
# ==========================================
# Prefer deriving from VITE_API_BASE_URL so we only need one var.
# Expect values like:
# - http://pr-audit-api-service:8000
# - https://pr-audit-api-dev.peakxvapps.net
API_PROXY_PASS="${VITE_API_BASE_URL}"
API_PROXY_PASS="$(echo "$API_PROXY_PASS" | sed -E 's#[[:space:]]+$##g; s#/*$##g; s#/api/v1$##g; s#/api$##g; s#\\.private\\.#.#g')"
if [ -z "$API_PROXY_PASS" ]; then
  # Fallback if VITE_API_BASE_URL is not provided.
  # Prefer in-cluster service DNS by default (works in Kubernetes).
  API_PROXY_PASS="http://pr-audit-api-service:8000"
fi

# Ensure scheme exists for nginx proxy_pass.
case "$API_PROXY_PASS" in
  http://*|https://*) ;;
  *) API_PROXY_PASS="http://${API_PROXY_PASS}" ;;
esac

# proxy_pass with a URI-less prefix must end in no trailing slash; location already has /api/
if [ -f /etc/nginx/conf.d/default.conf.template ]; then
  cp /etc/nginx/conf.d/default.conf.template /etc/nginx/conf.d/default.conf
  sed -i "s|__API_PROXY_PASS__|${API_PROXY_PASS}|g" /etc/nginx/conf.d/default.conf
  echo "[entrypoint] API proxy_pass set to ${API_PROXY_PASS}"
fi

# ==========================================
# PART 3: Start Nginx
# ==========================================
echo "[entrypoint] Configuration complete. Starting Nginx..."
exec nginx -g "daemon off;"
 
