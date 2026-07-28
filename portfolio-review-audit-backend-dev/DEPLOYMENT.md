# PeakXV PR Tool Deployment Guide

This guide explains how to deploy the PeakXV PR Tool to an EC2 instance using Docker and HTTPS.

## Prerequisites

1. An EC2 instance running Ubuntu/Amazon Linux
2. Docker registry access (Docker Hub, ECR, etc.)
3. Open ports 80 and 443 in your EC2 security group

## Step 1: Build and Push Docker Image

On your local machine:

1. Update the Docker registry in `deploy-ec2.sh` if needed (current value: `ashishtailoredai`):
   ```sh
   DOCKER_REGISTRY="your-registry"  # Replace with your Docker registry
   ```

2. Make the scripts executable:
   ```sh
   chmod +x *.sh
   ```

3. Build and push Docker image:
   ```sh
   ./deploy-ec2.sh
   ```

## Step 2: Set Up EC2 Instance

1. Copy necessary files to EC2:
   ```sh
   # Create a directory to hold deployment files
   mkdir -p ec2-deploy
   cp docker-compose.prod.yaml setup-ec2.sh generate-ssl.sh nginx/nginx.conf ec2-deploy/
   
   # Transfer to EC2 (replace with your key path and EC2 IP)
   scp -i /path/to/key.pem -r ec2-deploy/* ubuntu@your-ec2-ip:~/
   ```

2. SSH into your EC2 instance:
   ```sh
   ssh -i /path/to/key.pem ubuntu@your-ec2-ip
   ```

3. Make scripts executable and run setup:
   ```sh
   chmod +x *.sh
   ./setup-ec2.sh
   ```

## Step 3: Deploy Application

1. Pull the Docker image (replace with your registry details):
   ```sh
   docker pull ashishtailoredai/peakxv-pr-tool:latest
   ```

2. Create a `.env` file with your database credentials if needed:
   ```sh
   cat > .env << EOF
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=yourpassword
   POSTGRES_HOST=your-db-host
   POSTGRES_PORT=5432
   POSTGRES_DB=peakxv_pr_tool
   EOF
   ```

3. Start the application:
   ```sh
   docker-compose -f docker-compose.prod.yaml up -d
   ```

4. Verify the application is running:
   ```sh
   docker-compose -f docker-compose.prod.yaml ps
   ```

## Step 4: Access the Application

Access your application at:
```
https://your-ec2-public-ip
```

Note: You will see a browser security warning because the SSL certificate is self-signed. You can click "Advanced" and proceed to the site.

## Troubleshooting

1. Check container logs:
   ```sh
   docker-compose -f docker-compose.prod.yaml logs
   ```

2. Check nginx logs:
   ```sh
   docker-compose -f docker-compose.prod.yaml logs nginx
   ```

3. If the database connection fails, verify your environment variables:
   ```sh
   docker-compose -f docker-compose.prod.yaml config
   ```

4. Regenerate SSL certificate:
   ```sh
   ./generate-ssl.sh $(curl -s http://checkip.amazonaws.com)
   ```

5. Restart services:
   ```sh
   docker-compose -f docker-compose.prod.yaml restart
   ``` 


# Build and push new image locally
./deploy-ec2.sh

# On EC2, pull and restart
sudo docker pull ashishtailoredai/peakxv-pr-tool:latest
sudo docker-compose -f docker-compose.prod.yaml up -d