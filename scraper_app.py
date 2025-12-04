"""
Simple Web Scraper - Streamlit App
Extracts text content from websites
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import hashlib
import time
import re

# Configuration
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MIN_CONTENT_LENGTH = 100
REQUEST_DELAY = 0.3

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
    def __init__(self, base_url, max_pages=100, delay=0.3, respect_robots=True, exclude_paths=None, single_page_mode=False):
        self.base_url = base_url.rstrip("/")
        self.single_page_mode = single_page_mode
        
        if single_page_mode:
            self.start_url = base_url
        else:
            self.start_url = self.base_url + "/"
        
        self.max_pages = 1 if single_page_mode else max_pages
        self.delay = delay
        self.respect_robots = respect_robots
        self.exclude_paths = exclude_paths or []
        
        self.visited = set()
        self.to_visit = [self.start_url]
        self.unique_content = {}
        self.content_hashes = set()
        
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        
        self.robot_parser = None
        if respect_robots:
            self._load_robots_txt()
    
    def _load_robots_txt(self):
        try:
            robots_url = f"{urlparse(self.base_url).scheme}://{urlparse(self.base_url).netloc}/robots.txt"
            self.robot_parser = RobotFileParser()
            self.robot_parser.set_url(robots_url)
            self.robot_parser.read()
        except:
            self.robot_parser = None
    
    def _can_fetch(self, url):
        if not self.respect_robots or not self.robot_parser:
            return True
        try:
            return self.robot_parser.can_fetch(USER_AGENT, url)
        except:
            return True
    
    def _is_excluded(self, url):
        path = urlparse(url).path
        for exclude in self.exclude_paths:
            if exclude and exclude in path:
                return True
        return False
    
    def _normalize_url(self, url):
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return normalized.rstrip("/")
    
    def _is_valid_url(self, url):
        parsed = urlparse(url)
        base_parsed = urlparse(self.base_url)
        
        if parsed.netloc != base_parsed.netloc:
            return False
        
        path_lower = parsed.path.lower()
        for ext in SKIP_EXTENSIONS:
            if path_lower.endswith(ext):
                return False
        
        return True
    
    def _is_junk_text(self, text):
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in JUNK_TEXT_PATTERNS)
    
    def _extract_content(self, html, url):
        soup = BeautifulSoup(html, "lxml")
        
        # Remove unwanted elements
        for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside', 'noscript', 'iframe']):
            tag.decompose()
        
        # Get title
        title = ""
        if soup.title:
            title = soup.title.get_text(strip=True)
        if not title:
            h1 = soup.find('h1')
            if h1:
                title = h1.get_text(strip=True)
        
        # Find main content area
        main_content = None
        for selector in ['main', 'article', '[role="main"]', '.content', '#content', '.main', '#main']:
            main_content = soup.select_one(selector)
            if main_content:
                break
        
        if not main_content:
            main_content = soup.body if soup.body else soup
        
        # Extract text content
        content_parts = []
        
        for element in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'td', 'th', 'blockquote']):
            text = element.get_text(strip=True)
            
            if not text or len(text) < 3:
                continue
            if self._is_junk_text(text):
                continue
            
            # Format based on tag
            tag = element.name
            if tag == 'h1':
                content_parts.append(f"\n# {text}\n")
            elif tag == 'h2':
                content_parts.append(f"\n## {text}\n")
            elif tag == 'h3':
                content_parts.append(f"\n### {text}\n")
            elif tag in ['h4', 'h5', 'h6']:
                content_parts.append(f"\n#### {text}\n")
            elif tag == 'li':
                content_parts.append(f"- {text}")
            elif tag == 'blockquote':
                content_parts.append(f"> {text}")
            else:
                content_parts.append(text)
        
        content = '\n'.join(content_parts).strip()
        content = self._fix_encoding(content)
        
        # Clean up multiple newlines
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        if len(content) < MIN_CONTENT_LENGTH:
            return title, None
        
        return title, content
    
    def _fix_encoding(self, text):
        """Fix common encoding issues"""
        if not text:
            return text
        
        try:
            fixed = text.encode('latin-1').decode('utf-8')
            return fixed
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
        
        replacements = {
            'â€"': '—',
            'â€"': '–',
            'â€™': "'",
            'â€œ': '"',
            'â€': '"',
            'â€˜': "'",
            'Â ': ' ',
            'Â': '',
            '\xa0': ' ',
        }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        
        return text
    
    def _extract_links(self, html, current_url):
        if self.single_page_mode:
            return []
        
        soup = BeautifulSoup(html, "lxml")
        links = []
        
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            full_url = urljoin(current_url, href)
            normalized = self._normalize_url(full_url)
            
            if self._is_valid_url(normalized) and normalized not in self.visited:
                if not self._is_excluded(normalized):
                    links.append(normalized)
        
        return list(set(links))
    
    def crawl(self, progress_callback=None, stats_callback=None):
        pages_processed = 0
        
        while self.to_visit and pages_processed < self.max_pages:
            url = self.to_visit.pop(0)
            
            if url in self.visited:
                continue
            
            if self._is_excluded(url):
                continue
            
            if not self._can_fetch(url):
                continue
            
            self.visited.add(url)
            
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                
                html_content = response.content.decode('utf-8', errors='replace')
                
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue
                
            except requests.RequestException as e:
                continue
            
            title, content = self._extract_content(html_content, url)
            
            if content:
                content_hash = hashlib.md5(content.encode()).hexdigest()
                
                if content_hash not in self.content_hashes:
                    self.content_hashes.add(content_hash)
                    self.unique_content[url] = {
                        "title": self._fix_encoding(title) or urlparse(url).path,
                        "content": content,
                    }
            
            new_links = self._extract_links(html_content, url)
            for link in new_links:
                if link not in self.visited and link not in self.to_visit:
                    self.to_visit.append(link)
            
            pages_processed += 1
            
            if progress_callback:
                progress = min(pages_processed / self.max_pages, 1.0)
                progress_callback(progress)
            
            if stats_callback:
                stats_callback({
                    "processed": pages_processed,
                    "saved": len(self.unique_content),
                    "queue": len(self.to_visit)
                })
            
            time.sleep(self.delay)
        
        return self.unique_content
    
    def to_markdown(self):
        """Export results as markdown"""
        lines = [
            f"# {urlparse(self.base_url).netloc} – Scraped Content\n",
            f"*Extracted {time.strftime('%Y-%m-%d %H:%M')}*",
            f"*{len(self.unique_content)} pages*\n",
            "---\n"
        ]
        
        for url, data in self.unique_content.items():
            lines.append(f"## {self._fix_encoding(data['title'])}\n")
            lines.append(f"**URL:** {url}\n")
            lines.append(f"\n{data['content']}\n")
            lines.append("---\n")
        
        return "\n".join(lines)


# ============== STREAMLIT UI ==============

st.set_page_config(page_title="Web Scraper", page_icon="🕷️", layout="wide")

st.title("🕷️ Web Scraper")
st.markdown("Extract text content from websites")

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Settings")
    
    url = st.text_input("Website URL", placeholder="https://example.com")
    
    single_page = st.checkbox("🎯 Single page only", value=False, 
                              help="Only scrape the exact URL provided")
    
    if not single_page:
        max_pages = st.slider("Max pages", 1, 200, 50)
    else:
        max_pages = 1
    
    delay = st.slider("Request delay (seconds)", 0.1, 2.0, 0.3)
    respect_robots = st.checkbox("Respect robots.txt", value=True)
    
    # Path exclusions
    st.markdown("---")
    st.subheader("🚫 Exclude Paths")
    
    # Quick-add buttons
    st.markdown("**Quick add:**")
    
    if "exclude_paths" not in st.session_state:
        st.session_state.exclude_paths = ""
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🛒 E-commerce", use_container_width=True):
            current = set(st.session_state.exclude_paths.split('\n')) if st.session_state.exclude_paths else set()
            current.update(['/shop/', '/cart/', '/checkout/', '/product/', '/store/'])
            current.discard('')
            st.session_state.exclude_paths = '\n'.join(sorted(current))
            st.rerun()
        
        if st.button("👤 User areas", use_container_width=True):
            current = set(st.session_state.exclude_paths.split('\n')) if st.session_state.exclude_paths else set()
            current.update(['/account/', '/login/', '/register/', '/profile/', '/my-'])
            current.discard('')
            st.session_state.exclude_paths = '\n'.join(sorted(current))
            st.rerun()
    
    with col2:
        if st.button("🌍 Languages", use_container_width=True):
            current = set(st.session_state.exclude_paths.split('\n')) if st.session_state.exclude_paths else set()
            current.update(['/es/', '/fr/', '/de/', '/it/', '/pt/', '/ja/', '/zh/', '/ko/'])
            current.discard('')
            st.session_state.exclude_paths = '\n'.join(sorted(current))
            st.rerun()
        
        if st.button("📰 Blog/News", use_container_width=True):
            current = set(st.session_state.exclude_paths.split('\n')) if st.session_state.exclude_paths else set()
            current.update(['/blog/', '/news/', '/press/', '/media/', '/archive/'])
            current.discard('')
            st.session_state.exclude_paths = '\n'.join(sorted(current))
            st.rerun()
    
    if st.button("🗑️ Clear all", use_container_width=True):
        st.session_state.exclude_paths = ""
        st.rerun()
    
    exclude_text = st.text_area(
        "Paths to exclude (one per line)",
        value=st.session_state.exclude_paths,
        height=100,
        placeholder="/shop/\n/cart/\n/es/",
        key="exclude_input"
    )
    
    if exclude_text != st.session_state.exclude_paths:
        st.session_state.exclude_paths = exclude_text

# Main area
if "results" not in st.session_state:
    st.session_state.results = None
if "scraper" not in st.session_state:
    st.session_state.scraper = None

# Start button
button_label = "🎯 Scrape This Page" if single_page else "🚀 Start Scraping"

if st.button(button_label, type="primary", use_container_width=True):
    if not url:
        st.error("Please enter a URL")
    else:
        # Parse exclusions
        exclude_paths = [p.strip() for p in st.session_state.exclude_paths.split('\n') if p.strip()]
        
        # Create scraper
        scraper = WebScraper(
            base_url=url,
            max_pages=max_pages,
            delay=delay,
            respect_robots=respect_robots,
            exclude_paths=exclude_paths,
            single_page_mode=single_page
        )
        
        # Progress display
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        def update_progress(p):
            progress_bar.progress(p)
        
        def update_stats(stats):
            status_text.text(f"Processed: {stats['processed']} | Saved: {stats['saved']} | Queue: {stats['queue']}")
        
        # Run scraper
        with st.spinner("Scraping..."):
            results = scraper.crawl(
                progress_callback=update_progress,
                stats_callback=update_stats
            )
        
        st.session_state.results = results
        st.session_state.scraper = scraper
        
        progress_bar.progress(1.0)
        st.success(f"✅ Done! Found {len(results)} pages with unique content")

# Display results
if st.session_state.results and st.session_state.scraper:
    st.markdown("---")
    st.subheader("📥 Download")
    
    markdown_content = st.session_state.scraper.to_markdown()
    st.download_button(
        label="📝 Download Markdown",
        data=markdown_content,
        file_name=f"scraped_{urlparse(url).netloc}.md",
        mime="text/markdown",
        use_container_width=True
    )
    
    # Preview
    st.markdown("### Preview")
    
    for page_url, data in st.session_state.results.items():
        with st.expander(f"📄 {data['title'][:60]}"):
            st.markdown(f"**URL:** {page_url}")
            preview = data['content'][:1000]
            if len(data['content']) > 1000:
                preview += "\n\n*[Content truncated...]*"
            st.markdown(preview)
