#!/usr/bin/env python3
# CLI Web Scraper
# Usage: python scraper_cli.py https://example.com -o output.md

import argparse
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import time
import hashlib
import re
import json
import sys

# Configuration
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MIN_CONTENT_LENGTH = 150

JUNK_SELECTORS = [
    "nav", "header", "footer", ".navbar", ".menu", ".sidebar",
    ".footer", ".copyright", ".social-links", ".cookie", ".popup",
    "form", "input", "button", "select", "textarea",
    ".btn", ".button", '[role="navigation"]', ".skip-link",
    ".site-header", ".site-footer", ".main-navigation",
    ".breadcrumb", ".pagination", ".comments", ".related-posts",
    "[aria-hidden='true']", ".screen-reader-text", ".sr-only"
]

JUNK_TEXT_PATTERNS = [
    "privacy policy", "terms of service", "cookie policy",
    "accept cookies", "all rights reserved", "©",
    "follow us on", "share on facebook", "tweet this",
    "subscribe to", "sign up for", "enter your email",
    "page updated", "report abuse"
]

SKIP_EXTENSIONS = {
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
    '.mp3', '.mp4', '.avi', '.mov', '.zip', '.tar', '.gz',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'
}


class WebScraper:
    def __init__(self, base_url, max_pages=100, delay=0.3, respect_robots=True,
                 exclude_paths=None, single_page_mode=False, verbose=True):
        self.base_url = base_url.rstrip("/")
        self.single_page_mode = single_page_mode
        self.start_url = base_url if single_page_mode else self.base_url + "/"
        self.max_pages = 1 if single_page_mode else max_pages
        self.delay = delay
        self.respect_robots = respect_robots
        self.exclude_paths = exclude_paths or []
        self.verbose = verbose

        self.visited = set()
        self.to_visit = [self.start_url]
        self.results = {}
        self.content_hashes = set()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

        self.robot_parser = None
        if respect_robots:
            self._load_robots()

    def _log(self, msg):
        if self.verbose:
            print(msg, file=sys.stderr)

    def _load_robots(self):
        try:
            robots_url = f"{self.base_url}/robots.txt"
            self.robot_parser = RobotFileParser()
            self.robot_parser.set_url(robots_url)
            self.robot_parser.read()
        except:
            self.robot_parser = None

    def _can_fetch(self, url):
        if not self.robot_parser:
            return True
        try:
            return self.robot_parser.can_fetch("*", url)
        except:
            return True

    def _is_valid_url(self, url):
        parsed = urlparse(url)
        base_parsed = urlparse(self.base_url)

        if parsed.netloc != base_parsed.netloc:
            return False

        path_lower = parsed.path.lower()
        for ext in SKIP_EXTENSIONS:
            if path_lower.endswith(ext):
                return False

        for exclude in self.exclude_paths:
            if exclude in parsed.path:
                return False

        return True

    def _normalize_url(self, url):
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return normalized.rstrip("/")

    def _is_junk_text(self, text):
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in JUNK_TEXT_PATTERNS)

    def _extract_title(self, soup, url):
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            if "|" in title:
                title = title.split("|")[0].strip()
            if " - " in title:
                title = title.split(" - ")[0].strip()
            if title:
                return title

        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
            if title:
                return title

        path = urlparse(url).path.strip("/")
        if path:
            return path.replace("-", " ").replace("/", " → ").title()

        return "Home"

    def _extract_content(self, html, url):
        soup = BeautifulSoup(html, "lxml")

        for selector in JUNK_SELECTORS:
            for element in soup.select(selector):
                element.decompose()

        for tag in soup(["script", "style", "noscript", "meta", "link", "iframe", "svg"]):
            tag.decompose()

        title = self._extract_title(soup, url)

        main_content = (
            soup.find("main") or
            soup.find("article") or
            soup.find(class_=lambda x: x and ("content" in str(x).lower())) or
            soup.find(id=lambda x: x and ("content" in str(x).lower() or "main" in str(x).lower())) or
            soup.body
        )

        if not main_content:
            main_content = soup

        content = md(
            str(main_content),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
        )

        content = re.sub(r'\n{3,}', '\n\n', content)

        lines = []
        for line in content.split('\n'):
            line = line.rstrip()
            if self._is_junk_text(line):
                continue
            lines.append(line)

        content = '\n'.join(lines).strip()

        return title, content

    def _extract_links(self, html, current_url):
        soup = BeautifulSoup(html, "lxml")
        links = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue

            full_url = urljoin(current_url, href)
            normalized = self._normalize_url(full_url)

            if self._is_valid_url(normalized) and normalized not in self.visited:
                links.add(normalized)

        return links

    def _content_hash(self, text):
        normalized = " ".join(text.lower().split())
        return hashlib.md5(normalized.encode()).hexdigest()

    def crawl(self):
        pages_processed = 0

        while self.to_visit and len(self.visited) < self.max_pages:
            url = self.to_visit.pop(0)

            if url in self.visited:
                continue

            if self.respect_robots and not self._can_fetch(url):
                self._log(f"Blocked by robots.txt: {url}")
                continue

            self.visited.add(url)
            pages_processed += 1

            self._log(f"[{pages_processed}/{self.max_pages}] Scraping: {url}")

            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()

                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue

            except requests.RequestException as e:
                self._log(f"Error fetching {url}: {e}")
                continue

            title, content = self._extract_content(response.text, url)

            content_hash = self._content_hash(content)
            if content_hash in self.content_hashes:
                self._log(f"Duplicate content, skipping: {url}")
                continue

            if len(content) >= MIN_CONTENT_LENGTH:
                self.content_hashes.add(content_hash)
                self.results[url] = {
                    "title": title,
                    "content": content
                }
                self._log(f"Saved: {title}")

            if not self.single_page_mode:
                new_links = self._extract_links(response.text, url)
                self.to_visit.extend(link for link in new_links if link not in self.visited)

            time.sleep(self.delay)

        return self.results

    def to_markdown(self):
        lines = [
            f"# {urlparse(self.base_url).netloc} – Scraped Content\n",
            f"*Extracted {time.strftime('%Y-%m-%d %H:%M')}*",
            f"*{len(self.results)} pages with unique content*\n",
            "---\n"
        ]

        for url, data in self.results.items():
            lines.append(f"## {data['title']}\n")
            lines.append(f"**URL:** {url}\n")
            lines.append(f"{data['content']}\n")
            lines.append("\n---\n")

        return "\n".join(lines)

    def to_json(self):
        export_data = {
            "source": self.base_url,
            "extracted": time.strftime('%Y-%m-%d %H:%M'),
            "page_count": len(self.results),
            "pages": []
        }

        for url, data in self.results.items():
            export_data["pages"].append({
                "url": url,
                "title": data["title"],
                "content": data["content"]
            })

        return json.dumps(export_data, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape websites and convert to Markdown or JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scraper_cli.py https://example.com
  python scraper_cli.py https://example.com -o output.md
  python scraper_cli.py https://example.com --json -o output.json
  python scraper_cli.py https://example.com/page --single
  python scraper_cli.py https://example.com -m 100 -d 0.5
  python scraper_cli.py https://example.com --exclude /blog/ /shop/
        """
    )

    parser.add_argument("url", help="URL to scrape")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of Markdown")
    parser.add_argument("-s", "--single", action="store_true", help="Single page mode (scrape only the provided URL)")
    parser.add_argument("-m", "--max-pages", type=int, default=50, help="Maximum pages to scrape (default: 50)")
    parser.add_argument("-d", "--delay", type=float, default=0.3, help="Delay between requests in seconds (default: 0.3)")
    parser.add_argument("--no-robots", action="store_true", help="Ignore robots.txt")
    parser.add_argument("--exclude", nargs="+", default=[], help="Paths to exclude (e.g., /blog/ /shop/)")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")

    args = parser.parse_args()

    if not args.url.startswith(("http://", "https://")):
        print("Error: URL must start with http:// or https://", file=sys.stderr)
        sys.exit(1)

    scraper = WebScraper(
        args.url,
        max_pages=args.max_pages,
        delay=args.delay,
        respect_robots=not args.no_robots,
        exclude_paths=args.exclude,
        single_page_mode=args.single,
        verbose=not args.quiet
    )

    if not args.quiet:
        print(f"Starting scrape of {args.url}", file=sys.stderr)
        print(f"Max pages: {args.max_pages}, Delay: {args.delay}s", file=sys.stderr)
        print("", file=sys.stderr)

    results = scraper.crawl()

    if not args.quiet:
        print("", file=sys.stderr)
        print(f"Done! Scraped {len(results)} pages with unique content.", file=sys.stderr)

    if args.json:
        output = scraper.to_json()
    else:
        output = scraper.to_markdown()

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        if not args.quiet:
            print(f"Saved to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
