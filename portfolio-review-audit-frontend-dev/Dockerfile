# -------- STEP 1: Build React frontend (as 'builder') --------
FROM node:20-alpine AS builder
WORKDIR /app

# Copy package files and install
# Docker caches this layer unless package.json/lock changes
COPY package*.json ./
RUN npm ci

# Copy the rest of the source code and build
COPY . .
RUN npm run build

# -------- STEP 2: Setup Production Server (Nginx) --------
FROM nginx:stable-alpine

# Install Doppler CLI
RUN wget -q -t3 'https://packages.doppler.com/public/cli/rsa.8004D9FF50437357.key' -O /etc/apk/keys/cli@doppler-8004D9FF50437357.rsa.pub && \
    echo 'https://packages.doppler.com/public/cli/alpine/any-version/main' | tee -a /etc/apk/repositories && \
    apk add --no-cache doppler

# Copy the built React app from the 'builder' stage
# Vite build output is 'dist/'
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy supporting config files
COPY nginx.conf /etc/nginx/conf.d/default.conf.template
COPY entrypoint.sh /entrypoint.sh

# Ensure Nginx user can read the static files
RUN chmod -R 755 /usr/share/nginx/html

# Ensure the entrypoint script is executable
RUN chmod +x /entrypoint.sh

# Expose Nginx default port
EXPOSE 80

# Doppler injects secrets at runtime before starting the app
ENTRYPOINT ["doppler", "run", "--fallback=doppler.encrypted.internal.json", "--"]

CMD ["/entrypoint.sh"]

