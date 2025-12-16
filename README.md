# Web Scraper

A Streamlit web application for extracting website content as clean Markdown and JSON.

## Features

- Crawl websites respectfully (honors robots.txt)
- Extract main content, removing navigation and junk elements
- Convert HTML to clean Markdown format
- Single-page or multi-page scraping (up to 200 pages)
- Path exclusion filters
- Export to Markdown or JSON

## VPS Deployment

### Option 1: Docker (Recommended)

The easiest way to deploy on a VPS:

```bash
# Clone the repository
git clone <repository-url>
cd scrapper

# Build and run with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f
```

The app will be available at `http://YOUR_SERVER_IP:8501`

### Option 2: Manual Setup with Systemd

For a traditional deployment:

```bash
# Clone the repository
git clone <repository-url>
cd scrapper

# Run the setup script (as root)
sudo bash setup.sh
```

The setup script will:
1. Install Python and dependencies
2. Create a dedicated user
3. Set up a virtual environment
4. Configure systemd service
5. Start the application

### Nginx Reverse Proxy (Optional)

To serve the app through nginx with SSL:

```bash
# Copy nginx config
sudo cp nginx-scraper.conf /etc/nginx/sites-available/scraper

# Edit and set your domain
sudo nano /etc/nginx/sites-available/scraper

# Enable the site
sudo ln -s /etc/nginx/sites-available/scraper /etc/nginx/sites-enabled/

# Test and reload nginx
sudo nginx -t
sudo systemctl reload nginx

# Setup SSL with Let's Encrypt
sudo certbot --nginx -d your-domain.com
```

## Service Management

```bash
# View status
sudo systemctl status scraper

# View logs
sudo journalctl -u scraper -f

# Restart
sudo systemctl restart scraper

# Stop
sudo systemctl stop scraper
```

## Docker Commands

```bash
# Build image
docker-compose build

# Start container
docker-compose up -d

# Stop container
docker-compose down

# View logs
docker-compose logs -f

# Rebuild and restart
docker-compose up -d --build
```

## Requirements

- Python 3.8+
- 1GB RAM minimum (2GB recommended)
- Network connectivity for scraping

## Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the app
streamlit run scraper_app.py
```

## License

MIT
