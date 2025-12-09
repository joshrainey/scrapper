# scraper_app.py
# Run with: streamlit run scraper_app.py
# Requires: pip install streamlit requests beautifulsoup4 lxml markdownify

import streamlit as st
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import time
import hashlib
import re
import json

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
                 exclude_paths=None, single_page_mode=False):
        self.base_url = base_url.rstrip("/")
        self.single_page_mode = single_page_mode
        self.start_url = base_url if single_page_mode else self.base_url + "/"
        self.max_pages = 1 if single_page_mode else max_pages
        self.delay = delay
        self.respect_robots = respect_robots
        self.exclude_paths = exclude_paths or []
        
        self.visited = set()
        self.to_visit = [self.start_url]
        self.results = {}
        self.content_hashes = set()
        
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        
        self.robot_parser = None
        if respect_robots:
            self._load_robots()
    
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
        
        # Remove junk elements
        for selector in JUNK_SELECTORS:
            for element in soup.select(selector):
                element.decompose()
        
        for tag in soup(["script", "style", "noscript", "meta", "link", "iframe", "svg"]):
            tag.decompose()
        
        title = self._extract_title(soup, url)
        
        # Find main content area
        main_content = (
            soup.find("main") or 
            soup.find("article") or 
            soup.find(class_=lambda x: x and ("content" in str(x).lower())) or
            soup.find(id=lambda x: x and ("content" in str(x).lower() or "main" in str(x).lower())) or
            soup.body
        )
        
        if not main_content:
            main_content = soup
        
        # Convert to Markdown
        content = md(
            str(main_content),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
        )
        
        # Clean up
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        # Filter junk lines
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
    
    def crawl(self, progress_callback=None, stats_callback=None):
        pages_processed = 0
        
        while self.to_visit and len(self.visited) < self.max_pages:
            url = self.to_visit.pop(0)
            
            if url in self.visited:
                continue
            
            if self.respect_robots and not self._can_fetch(url):
                continue
            
            self.visited.add(url)
            pages_processed += 1
            
            # Update progress
            if progress_callback:
                progress_callback(min(pages_processed / self.max_pages, 1.0))
            
            if stats_callback:
                stats_callback({
                    "processed": pages_processed,
                    "saved": len(self.results),
                    "queue": len(self.to_visit)
                })
            
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue
                
            except requests.RequestException:
                continue
            
            title, content = self._extract_content(response.text, url)
            
            # Check for duplicates
            content_hash = self._content_hash(content)
            if content_hash in self.content_hashes:
                continue
            
            if len(content) >= MIN_CONTENT_LENGTH:
                self.content_hashes.add(content_hash)
                self.results[url] = {
                    "title": title,
                    "content": content
                }
            
            # Extract links (unless single page mode)
            if not self.single_page_mode:
                new_links = self._extract_links(response.text, url)
                self.to_visit.extend(link for link in new_links if link not in self.visited)
            
            time.sleep(self.delay)
        
        if progress_callback:
            progress_callback(1.0)
        
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


# ============ STREAMLIT UI ============
st.set_page_config(page_title="Web Scraper", page_icon="🕷️", layout="wide")
st.title("🕷️ Web Scraper")
st.markdown("Extract content from websites as clean Markdown.")

# Sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    
    url = st.text_input("Website URL", placeholder="https://example.com")
    
    single_page = st.checkbox("🎯 Single page only", help="Scrape only the exact URL provided")
    
    if not single_page:
        max_pages = st.slider("Max pages", 5, 200, 50)
    else:
        max_pages = 1
    
    delay = st.slider("Delay (seconds)", 0.1, 2.0, 0.3)
    respect_robots = st.checkbox("Respect robots.txt", value=True)
    
    st.markdown("---")
    st.header("🚫 Exclude Paths")
    
    if "exclude_paths_textarea" not in st.session_state:
        st.session_state.exclude_paths_textarea = ""
    
    def add_paths(new_paths):
        current = st.session_state.exclude_paths_textarea.strip()
        current_set = set(p.strip() for p in current.split("\n") if p.strip())
        new_set = set(p.strip() for p in new_paths.split("\n") if p.strip())
        combined = current_set | new_set
        st.session_state.exclude_paths_textarea = "\n".join(sorted(combined))
    
    st.markdown("**Quick add:**")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🛒 E-commerce", use_container_width=True):
            add_paths("/shop/\n/cart/\n/checkout/\n/account/\n/product/\n/products/\n/collection/\n/collections/\n/order/\n/wishlist/")
            st.rerun()
    with col2:
        if st.button("🌍 Languages", use_container_width=True):
            add_paths("/es/\n/fr/\n/de/\n/it/\n/pt/\n/ja/\n/zh/\n/ko/\n/ru/\n/ar/")
            st.rerun()
    
    col3, col4 = st.columns(2)
    with col3:
        if st.button("👤 User areas", use_container_width=True):
            add_paths("/login/\n/register/\n/account/\n/profile/\n/dashboard/\n/my-account/\n/signin/\n/signup/")
            st.rerun()
    with col4:
        if st.button("📰 Blog/News", use_container_width=True):
            add_paths("/blog/\n/news/\n/articles/\n/posts/\n/tag/\n/category/\n/author/")
            st.rerun()
    
    if st.button("🗑️ Clear all", use_container_width=True):
        st.session_state.exclude_paths_textarea = ""
        st.rerun()
    
    exclude_paths_input = st.text_area(
        "Directories to skip (one per line)",
        height=120,
        help="URLs containing these paths will be skipped",
        key="exclude_paths_textarea"
    )
    
    st.markdown("---")
    st.markdown("**Tips:**")
    st.markdown("""
    - Start small to test
    - Increase delay if blocked
    - Use exclusions to focus on content
    """)

# Main area
if url:
    button_text = "🎯 Scrape This Page" if single_page else "🚀 Start Scraping"
    
    if st.button(button_text, type="primary", use_container_width=True):
        if not url.startswith(("http://", "https://")):
            st.error("Please enter a valid URL starting with http:// or https://")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            stats_container = st.empty()
            
            def update_progress(p):
                progress_bar.progress(p)
            
            def update_stats(s):
                stats_container.markdown(
                    f"**Processed:** {s['processed']} | **Saved:** {s['saved']} | **Queue:** {s['queue']}"
                )
            
            exclude_list = [p.strip() for p in exclude_paths_input.split('\n') if p.strip()]
            
            scraper = WebScraper(
                url,
                max_pages=max_pages,
                delay=delay,
                respect_robots=respect_robots,
                exclude_paths=exclude_list,
                single_page_mode=single_page
            )
            
            status_text.text("Scraping...")
            results = scraper.crawl(
                progress_callback=update_progress,
                stats_callback=update_stats
            )
            
            st.session_state.results = results
            st.session_state.scraper = scraper
            
            status_text.text("✅ Done!")
            st.success(f"Found {len(results)} pages with unique content.")

# Results
if "results" in st.session_state and st.session_state.results:
    st.markdown("---")
    st.header(f"📄 Results: {len(st.session_state.results)} pages")
    
    # Export buttons
    col1, col2 = st.columns(2)
    
    with col1:
        markdown_content = st.session_state.scraper.to_markdown()
        st.download_button(
            label="📝 Download Markdown",
            data=markdown_content,
            file_name=f"scraped_{urlparse(url).netloc}.md",
            mime="text/markdown",
            use_container_width=True
        )
    
    with col2:
        json_content = st.session_state.scraper.to_json()
        st.download_button(
            label="📦 Download JSON",
            data=json_content,
            file_name=f"scraped_{urlparse(url).netloc}.json",
            mime="application/json",
            use_container_width=True
        )
    
    # Preview
    st.markdown("### Preview")
    
    for page_url, data in st.session_state.results.items():
        with st.expander(f"**{data['title']}** — {page_url[:60]}..."):
            st.markdown(f"**URL:** {page_url}")
            st.markdown("---")
            # Show first 2000 chars of content
            preview = data['content'][:2000]
            if len(data['content']) > 2000:
                preview += "\n\n*[Content truncated in preview...]*"
            st.markdown(preview)

# Footer
st.markdown("---")
st.caption("Built with Streamlit • Be respectful when scraping")
