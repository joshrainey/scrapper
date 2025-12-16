#!/bin/bash
# VPS Setup Script for Web Scraper
# Run with: sudo bash setup.sh

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   Web Scraper VPS Setup Script${NC}"
echo -e "${GREEN}========================================${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (sudo bash setup.sh)${NC}"
    exit 1
fi

# Configuration
APP_DIR="/opt/scraper"
APP_USER="scraper"
PYTHON_VERSION="python3"

echo -e "\n${YELLOW}[1/7] Updating system packages...${NC}"
apt update && apt upgrade -y

echo -e "\n${YELLOW}[2/7] Installing dependencies...${NC}"
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx

echo -e "\n${YELLOW}[3/7] Creating application user...${NC}"
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -s /bin/false $APP_USER
    echo "User '$APP_USER' created"
else
    echo "User '$APP_USER' already exists"
fi

echo -e "\n${YELLOW}[4/7] Setting up application directory...${NC}"
mkdir -p $APP_DIR
cp scraper_app.py $APP_DIR/
cp requirements.txt $APP_DIR/

echo -e "\n${YELLOW}[5/7] Creating Python virtual environment...${NC}"
cd $APP_DIR
$PYTHON_VERSION -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo -e "\n${YELLOW}[6/7] Setting permissions...${NC}"
chown -R $APP_USER:$APP_USER $APP_DIR

echo -e "\n${YELLOW}[7/7] Installing systemd service...${NC}"
cp "$(dirname "$0")/scraper.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable scraper
systemctl start scraper

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}   Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Service Status:"
systemctl status scraper --no-pager
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "1. Configure nginx: sudo cp nginx-scraper.conf /etc/nginx/sites-available/scraper"
echo "2. Enable site: sudo ln -s /etc/nginx/sites-available/scraper /etc/nginx/sites-enabled/"
echo "3. Edit /etc/nginx/sites-available/scraper and set your domain"
echo "4. Test nginx: sudo nginx -t"
echo "5. Reload nginx: sudo systemctl reload nginx"
echo "6. Setup SSL: sudo certbot --nginx -d yourdomain.com"
echo ""
echo -e "${GREEN}Access the app at: http://YOUR_SERVER_IP:8501${NC}"
echo ""
echo "Useful commands:"
echo "  - View logs: sudo journalctl -u scraper -f"
echo "  - Restart: sudo systemctl restart scraper"
echo "  - Stop: sudo systemctl stop scraper"
