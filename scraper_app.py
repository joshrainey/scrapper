# scraper_app.py
# Run with: streamlit run scraper_app.py
# Requires: pip install streamlit requests beautifulsoup4 lxml

import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
import time
import hashlib
from collections import OrderedDict

# ============ CONFIGURATION ============
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MIN_CONTENT_LENGTH = 150
MIN_PARAGRAPH_WORDS = 8

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
    "subscribe to", "sign up for", "enter your email"
]

SKIP_EXTENSIONS = {
    '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
    '.mp3', '.mp4', '.avi', '.mov', '.zip', '.tar', '.gz',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'
}


class WebScraper:
    def __init__(self, base_url, max_pages=100, delay=0.3, respect_robots=True, exclude_paths=None):
        self.base_url = base_url.rstrip("/")
        self.start_url = self.base_url + "/"
        self.max_pages = max_pages
        self.delay = delay
        self.respect_robots = respect_robots
        self.exclude_paths = exclude_paths or []
        
        self.visited = set()
        self.to_visit = {self.start_url}
        self.unique_content = OrderedDict()
        self.content_hashes = set()
        
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        
        self.robots_parser = None
        self.status_messages = []
    
    def _load_robots_txt(self):
        if not self.respect_robots:
            return
        self.robots_parser = RobotFileParser()
        robots_url = urljoin(self.base_url, "/robots.txt")
        try:
            self.robots_parser.set_url(robots_url)
            self.robots_parser.read()
            self.status_messages.append(f"✓ Loaded robots.txt")
        except Exception as e:
            self.status_messages.append(f"⚠ Could not load robots.txt: {e}")
            self.robots_parser = None
    
    def _can_fetch(self, url):
        if not self.robots_parser:
            return True
        return self.robots_parser.can_fetch(USER_AGENT, url)
    
    def _normalize_url(self, url):
        url, _ = urldefrag(url)
        url = url.rstrip("/")
        return url
    
    def _is_valid_url(self, url):
        parsed = urlparse(url)
        if parsed.netloc != urlparse(self.base_url).netloc:
            return False
        if parsed.query:
            return False
        path_lower = parsed.path.lower()
        if any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
            return False
        # Check excluded paths
        for exclude in self.exclude_paths:
            if exclude and exclude.lower() in path_lower:
                return False
        return True
    
    def _extract_title(self, soup, url):
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
            if "|" in title:
                title = title.split("|")[0].strip()
            if "-" in title and len(title.split("-")) > 1:
                title = title.rsplit("-", 1)[0].strip()
            if title:
                return title
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text().strip()
            if title:
                return title
        path = urlparse(url).path.strip("/")
        if path:
            return path.replace("-", " ").replace("/", " → ").title()
        return "Home"
    
    def _is_junk_text(self, text):
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in JUNK_TEXT_PATTERNS)
    
    def _extract_content(self, html, url):
        soup = BeautifulSoup(html, "lxml")
        
        for selector in JUNK_SELECTORS:
            for element in soup.select(selector):
                element.decompose()
        
        for tag in soup(["script", "style", "noscript", "meta", "link", "iframe"]):
            tag.decompose()
        
        title = self._extract_title(soup, url)
        content_blocks = []
        
        main_content = (
            soup.find("main") or 
            soup.find("article") or 
            soup.find(class_=lambda x: x and ("content" in x.lower() or "body" in x.lower())) or
            soup.find(id=lambda x: x and ("content" in x.lower() or "main" in x.lower()))
        )
        
        search_area = main_content if main_content else soup
        
        for elem in search_area.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"]):
            text = elem.get_text(separator=" ", strip=True)
            word_count = len(text.split())
            
            if word_count >= MIN_PARAGRAPH_WORDS and not self._is_junk_text(text):
                if elem.name.startswith("h"):
                    level = int(elem.name[1])
                    text = "#" * level + " " + text
                content_blocks.append(text)
        
        content = "\n\n".join(content_blocks)
        
        if len(content) < MIN_CONTENT_LENGTH:
            content = soup.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            content = "\n".join(lines)
        
        return title, content
    
    def _content_hash(self, text):
        normalized = " ".join(text.lower().split())
        return hashlib.md5(normalized.encode()).hexdigest()
    
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
    
    def crawl(self, progress_bar, status_text, stats_container):
        """Main crawl loop with Streamlit progress updates"""
        self._load_robots_txt()
        
        pages_processed = 0
        
        while self.to_visit and len(self.visited) < self.max_pages:
            url = self.to_visit.pop()
            
            if url in self.visited:
                continue
            
            if self.respect_robots and not self._can_fetch(url):
                self.status_messages.append(f"⛔ Blocked by robots.txt: {url}")
                continue
            
            self.visited.add(url)
            pages_processed += 1
            
            # Update progress
            progress = min(pages_processed / self.max_pages, 1.0)
            progress_bar.progress(progress)
            status_text.text(f"Crawling: {url[:60]}...")
            
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue
                
            except requests.RequestException as e:
                self.status_messages.append(f"❌ Failed: {url} ({str(e)[:50]})")
                continue
            
            title, content = self._extract_content(response.text, url)
            
            content_hash = self._content_hash(content)
            if content_hash in self.content_hashes:
                continue
            
            if len(content) >= MIN_CONTENT_LENGTH:
                self.content_hashes.add(content_hash)
                self.unique_content[url] = {
                    "title": title,
                    "content": content
                }
            
            new_links = self._extract_links(response.text, url)
            self.to_visit.update(new_links)
            
            # Update stats
            stats_container.markdown(f"""
            **Pages processed:** {pages_processed}  
            **Unique pages saved:** {len(self.unique_content)}  
            **URLs in queue:** {len(self.to_visit)}
            """)
            
            time.sleep(self.delay)
        
        progress_bar.progress(1.0)
        status_text.text("✅ Crawl complete!")
        
        return self.unique_content
    
    def to_markdown(self):
        """Export results as markdown string"""
        lines = [
            f"# {urlparse(self.base_url).netloc} – Scraped Content\n",
            f"*Extracted {time.strftime('%Y-%m-%d %H:%M')}*",
            f"*{len(self.unique_content)} pages with unique content*\n",
            "---\n"
        ]
        
        for url, data in self.unique_content.items():
            lines.append(f"## {data['title']}\n")
            lines.append(f"**URL:** {url}\n")
            lines.append(f"{data['content']}\n")
            lines.append("---\n")
        
        return "\n".join(lines)


# ============ STREAMLIT UI ============
st.set_page_config(
    page_title="Web Scraper",
    page_icon="🕷️",
    layout="wide"
)

st.title("🕷️ Web Scraper")
st.markdown("Extract unique text content from any website.")

# Sidebar settings
with st.sidebar:
    st.header("⚙️ Settings")
    
    url = st.text_input(
        "Website URL",
        value="https://example.com",
        help="Enter the full URL including https://"
    )
    
    max_pages = st.slider(
        "Maximum pages",
        min_value=5,
        max_value=500,
        value=50,
        step=5,
        help="Limit how many pages to crawl"
    )
    
    delay = st.slider(
        "Request delay (seconds)",
        min_value=0.1,
        max_value=2.0,
        value=0.3,
        step=0.1,
        help="Time between requests (be polite!)"
    )
    
    respect_robots = st.checkbox(
        "Respect robots.txt",
        value=True,
        help="Follow the site's crawling rules"
    )
    
    st.markdown("---")
    st.header("🚫 Exclude Paths")
    
    # Initialize session state for the text area widget
    if "exclude_paths_textarea" not in st.session_state:
        st.session_state.exclude_paths_textarea = "/shop/\n/cart/\n/checkout/\n/account/"
    
    # Common exclusion presets - BEFORE the text area
    st.markdown("**Quick add:**")
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("🛒 E-commerce", use_container_width=True):
            st.session_state.exclude_paths_textarea = "/shop/\n/cart/\n/checkout/\n/account/\n/product/\n/products/\n/collection/\n/collections/\n/order/\n/wishlist/"
            st.rerun()
    with col2:
        if st.button("🌍 Languages", use_container_width=True):
            st.session_state.exclude_paths_textarea = "/es/\n/fr/\n/de/\n/it/\n/pt/\n/ja/\n/zh/\n/ko/\n/ru/\n/ar/"
            st.rerun()
    
    col3, col4 = st.columns(2)
    with col3:
        if st.button("👤 User areas", use_container_width=True):
            st.session_state.exclude_paths_textarea = "/login/\n/register/\n/account/\n/profile/\n/dashboard/\n/my-account/\n/signin/\n/signup/"
            st.rerun()
    with col4:
        if st.button("📰 Blog/News", use_container_width=True):
            st.session_state.exclude_paths_textarea = "/blog/\n/news/\n/articles/\n/posts/\n/tag/\n/category/\n/author/"
            st.rerun()
    
    # Text area - uses key only, no value parameter
    exclude_paths_input = st.text_area(
        "Directories to skip (one per line)",
        height=120,
        help="URLs containing these paths will be skipped",
        key="exclude_paths_textarea"
    )
    
    st.markdown("---")
    st.markdown("**Tips:**")
    st.markdown("""
    - Start with a smaller max pages to test
    - Increase delay if you get blocked
    - Use path exclusions to focus on content
    """)

# Main area
col1, col2 = st.columns([2, 1])

with col1:
    start_button = st.button("🚀 Start Scraping", type="primary", use_container_width=True)

# Initialize session state
if "results" not in st.session_state:
    st.session_state.results = None
if "scraper" not in st.session_state:
    st.session_state.scraper = None

if start_button:
    if not url or not url.startswith(("http://", "https://")):
        st.error("Please enter a valid URL starting with http:// or https://")
    else:
        st.session_state.results = None
        
        # Progress UI
        progress_bar = st.progress(0)
        status_text = st.empty()
        stats_container = st.empty()
        
        status_text.text("Initializing...")
        
        try:
            # Parse excluded paths from text area
            exclude_paths = [p.strip() for p in exclude_paths_input.strip().split("\n") if p.strip()]
            
            scraper = WebScraper(
                base_url=url,
                max_pages=max_pages,
                delay=delay,
                respect_robots=respect_robots,
                exclude_paths=exclude_paths
            )
            
            results = scraper.crawl(progress_bar, status_text, stats_container)
            
            st.session_state.results = results
            st.session_state.scraper = scraper
            
            # Show any status messages
            if scraper.status_messages:
                with st.expander("📋 Crawl Log"):
                    for msg in scraper.status_messages:
                        st.text(msg)
                        
        except Exception as e:
            st.error(f"Error during crawl: {str(e)}")

# Display results
if st.session_state.results:
    st.markdown("---")
    st.header(f"📄 Results: {len(st.session_state.results)} pages")
    
    # Download button
    markdown_content = st.session_state.scraper.to_markdown()
    st.download_button(
        label="⬇️ Download as Markdown",
        data=markdown_content,
        file_name=f"scraped_{urlparse(url).netloc}.md",
        mime="text/markdown",
        use_container_width=True
    )
    
    # Preview results
    st.markdown("### Preview")
    
    for i, (page_url, data) in enumerate(st.session_state.results.items()):
        with st.expander(f"**{data['title']}** - {page_url[:50]}..."):
            st.markdown(f"**URL:** {page_url}")
            st.markdown("**Content:**")
            st.text(data['content'][:1000] + ("..." if len(data['content']) > 1000 else ""))

# Footer
st.markdown("---")
st.markdown("*Built with Streamlit • Be respectful when scraping*")
