# Deployment — EC2 Free Tier

This guide deploys NSEMargins on an AWS EC2 t2.micro instance using Docker.

---

## Prerequisites

- AWS account with free tier available
- Docker Desktop installed locally (for building the image)
- An EC2 key pair for SSH access

---

## Step 1 — Launch an EC2 Instance

1. Open the **EC2 Console** → Launch Instance.
2. **Name:** `nsemargins`
3. **AMI:** Ubuntu Server 24.04 LTS (free tier eligible)
4. **Instance type:** `t2.micro` (free tier)
5. **Key pair:** select an existing one or create new — save the `.pem` file
6. **Network settings:**
   - Allow SSH (port 22) from your IP only
   - Allow HTTP (port 80) from anywhere — **0.0.0.0/0**
   - (Optional) Allow HTTPS (port 443) from anywhere if you add SSL later
7. **Storage:** 20 GB gp3 (free tier allows up to 30 GB)
8. Launch the instance and note the **Public IPv4 address**.

---

## Step 2 — Install Docker on the Instance

SSH into the instance:

```bash
ssh -i your-key.pem ubuntu@<EC2_PUBLIC_IP>
```

Install Docker:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) \
  signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

# Allow running docker without sudo
sudo usermod -aG docker ubuntu
newgrp docker

# Verify
docker --version
```

---

## Step 3 — Copy the App to EC2

From your local machine, sync the project (excluding venv and data):

```bash
rsync -avz --exclude='venv/' --exclude='data/' --exclude='.git/' \
  -e "ssh -i your-key.pem" \
  "path/to/NSE margin calculator/" \
  ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/nsemargins/
```

Or clone from GitHub if the repo is pushed there:

```bash
# On EC2
git clone https://github.com/yourname/nsemargins.git
cd nsemargins
```

---

## Step 4 — Configure Environment Variables

On EC2, inside the project directory:

```bash
cd /home/ubuntu/nsemargins

# Generate a secure secret key
python3 -c "import secrets; print(secrets.token_hex(32))"

# Create your .env file
cat > .env << 'EOF'
SECRET_KEY=<paste-generated-key-here>
EOF

chmod 600 .env
```

---

## Step 5 — Build and Start

```bash
cd /home/ubuntu/nsemargins

# Build the image (takes ~2 minutes on first run)
docker compose build

# Start in the background
docker compose up -d

# Check it's running
docker compose ps
docker compose logs -f
```

The app is now available at **http://\<EC2_PUBLIC_IP\>**

On first start it will automatically download today's NSE data (bhavcopy + SPAN XML). Watch the logs to confirm:

```bash
docker compose logs -f
# Look for: "Bhavcopy: inserted 45066 contracts"
# And:      "SPAN XML: wrote 45066 RiskArray rows"
```

---

## Step 6 — Verify

```bash
curl http://<EC2_PUBLIC_IP>/api/span-status
```

Expected response:
```json
{
  "data_mode": "span_file",
  "instrument_count": 45066,
  "risk_array_count": 45066,
  "status": "success"
}
```

---

## Updating the App

```bash
cd /home/ubuntu/nsemargins

# Pull latest code (if using git)
git pull

# Rebuild and restart (data volume is preserved)
docker compose build
docker compose up -d
```

---

## Useful Commands

```bash
# View live logs
docker compose logs -f

# Restart without rebuilding
docker compose restart

# Stop
docker compose down

# Stop and delete data volume (destructive — deletes DB)
docker compose down -v

# Open a shell inside the running container
docker compose exec app bash

# Manually trigger SPAN refresh
curl -X POST http://localhost/api/span/refresh
```

---

## Data Persistence

Market data (SQLite DB + cached NSE zips) is stored in a Docker named volume called `nse_data`. It survives container restarts and image rebuilds. Only `docker compose down -v` deletes it.

To back up the database:

```bash
docker compose exec app sqlite3 /app/data/nse_margin.db ".backup '/app/data/backup.db'"
docker cp $(docker compose ps -q app):/app/data/backup.db ./nse_margin_backup.db
```

---

## Notes on Free Tier Limits

- t2.micro: 1 vCPU, 1 GB RAM — sufficient for the single-worker gunicorn setup
- 750 hours/month of EC2 compute (enough for one instance running 24/7)
- 30 GB EBS storage free; the full DB + image uses well under 2 GB
- The SPAN XML download (~10 MB/day) is negligible against the 15 GB outbound data free tier

---

## Next Steps (Optional)

- **Custom domain + HTTPS** — point a domain at the EC2 IP and add nginx + Let's Encrypt (Certbot) in front of gunicorn
- **Elastic IP** — assign a static IP so the address doesn't change on stop/start
- **GitHub Actions** — automate build and deploy on push
