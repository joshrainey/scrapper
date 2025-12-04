# scraper_app.py
# Run with: streamlit run scraper_app.py
# Requires: pip install streamlit requests beautifulsoup4 lxml markdownify

import streamlit as st
import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser
import time
import hashlib
import re
from collections import OrderedDict

# ============ CONFIGURATION ============
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
MIN_CONTENT_LENGTH = 150
MIN_PARAGRAPH_WORDS = 8

# Layout detection patterns
LAYOUT_PATTERNS = {
    "hero": {
        "classes": ["hero", "banner", "jumbotron", "masthead", "splash", "cover", "intro-section"],
        "ids": ["hero", "banner", "masthead", "intro"],
    },
    "testimonial": {
        "classes": ["testimonial", "quote", "review", "customer-quote", "client-quote", "blockquote"],
        "tags": ["blockquote"],
    },
    "faq": {
        "classes": ["faq", "accordion", "collapsible", "questions", "q-and-a"],
        "tags": ["details"],
    },
    "cta": {
        "classes": ["cta", "call-to-action", "action-box", "signup", "get-started"],
    },
    "gallery": {
        "classes": ["gallery", "image-grid", "photo-grid", "lightbox", "carousel", "slider"],
    },
    "features": {
        "classes": ["features", "benefits", "services", "offerings", "highlights"],
    },
    "pricing": {
        "classes": ["pricing", "plans", "packages", "tiers"],
    },
    "team": {
        "classes": ["team", "staff", "people", "about-us", "our-team"],
    },
    "contact": {
        "classes": ["contact", "get-in-touch", "reach-us"],
    },
}

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
    def __init__(self, base_url, max_pages=100, delay=0.3, respect_robots=True, exclude_paths=None, single_page_mode=False):
        self.base_url = base_url.rstrip("/")
        self.single_page_mode = single_page_mode
        
        # In single page mode, use the exact URL provided
        if single_page_mode:
            self.start_url = base_url
        else:
            self.start_url = self.base_url + "/"
        
        self.max_pages = 1 if single_page_mode else max_pages
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
    
    def _detect_section_type(self, element, is_first=False):
        """Detect the type of section based on classes, IDs, structure, and position"""
        classes = " ".join(element.get("class", [])).lower()
        elem_id = (element.get("id") or "").lower()
        tag_name = element.name
        text_content = element.get_text(strip=True)
        text_lower = text_content.lower()
        
        # Check against known patterns first (class/id based detection)
        for layout_type, patterns in LAYOUT_PATTERNS.items():
            if "classes" in patterns:
                for cls in patterns["classes"]:
                    if cls in classes:
                        return layout_type
            if "ids" in patterns:
                for id_pattern in patterns["ids"]:
                    if id_pattern in elem_id:
                        return layout_type
            if "tags" in patterns:
                if tag_name in patterns["tags"]:
                    return layout_type
        
        # === POSITION HEURISTICS ===
        
        # First section with heading/image is likely hero
        if is_first:
            has_h1 = element.find("h1") is not None
            has_large_image = element.find("img") is not None
            has_bg_style = "background" in (element.get("style") or "").lower()
            if has_h1 or has_large_image or has_bg_style:
                return "hero"
        
        # === CONTENT HEURISTICS ===
        
        # Testimonial: quotes, blockquotes, or attribution patterns
        has_blockquote = element.find("blockquote") is not None
        has_quotes = any(q in text_content for q in ['"', '"', '"', '«', '»', "''"])
        has_citation = element.find("cite") is not None
        # Attribution patterns: "Name, City" or "- Name" or "— Name, Title"
        attribution_pattern = re.search(r'[—–-]\s*[A-Z][a-z]+(\s+[A-Z]\.?)?(\s+[A-Z][a-z]+)?,?\s*([\w\s]+,\s*[A-Z]{2})?', text_content)
        
        if has_blockquote or has_citation or (has_quotes and attribution_pattern):
            return "testimonial"
        
        # FAQ: multiple questions
        questions = text_content.count("?")
        has_details = element.find("details") is not None
        if questions >= 3 or has_details:
            return "faq"
        
        # Gallery: multiple images (3+)
        images = element.find_all("img")
        if len(images) >= 3:
            return "gallery"
        
        # Pricing: dollar signs or price patterns
        price_pattern = re.search(r'\$\d+|\d+\.\d{2}|/month|/year|per month|per year', text_lower)
        if price_pattern and ("plan" in text_lower or "price" in text_lower or "tier" in text_lower):
            return "pricing"
        
        # Features/benefits: list with short items, often with icons
        list_items = element.find_all("li")
        if len(list_items) >= 3:
            avg_item_length = sum(len(li.get_text()) for li in list_items) / len(list_items)
            if avg_item_length < 100:  # Short list items suggest features
                return "features"
        
        # Contact: contact-related keywords
        contact_keywords = ["email", "phone", "address", "contact us", "get in touch", "reach out"]
        if any(kw in text_lower for kw in contact_keywords):
            if element.find("a", href=lambda h: h and ("mailto:" in h or "tel:" in h)):
                return "contact"
        
        # CTA: short section with action words
        word_count = len(text_content.split())
        cta_phrases = ["book now", "get started", "sign up", "contact us", "learn more", 
                       "buy now", "subscribe", "join", "start free", "try free", "call us"]
        has_cta_text = any(cta in text_lower for cta in cta_phrases)
        if word_count < 75 and has_cta_text:
            return "cta"
        
        return None
    
    def _detect_column_layout(self, element):
        """Detect if element has a multi-column layout"""
        classes = " ".join(element.get("class", [])).lower()
        style = (element.get("style") or "").lower()
        
        # Bootstrap/common grid patterns
        col_patterns = {
            "two_column": ["col-6", "col-md-6", "col-lg-6", "two-col", "half", "split"],
            "three_column": ["col-4", "col-md-4", "col-lg-4", "three-col", "thirds"],
            "four_column": ["col-3", "col-md-3", "col-lg-3", "four-col", "quarters"],
        }
        
        for layout, patterns in col_patterns.items():
            for pattern in patterns:
                if pattern in classes:
                    return layout
        
        # Check for flexbox/grid in style
        if "display: flex" in style or "display: grid" in style:
            # Count direct children to estimate columns
            children = [c for c in element.children if hasattr(c, 'name') and c.name]
            if len(children) == 2:
                return "two_column"
            elif len(children) == 3:
                return "three_column"
            elif len(children) >= 4:
                return "four_column"
        
        return "single_column"
    
    def _has_image(self, element):
        """Check if element contains an image"""
        return bool(element.find(["img", "picture", "figure", "svg"]))
    
    def _extract_section_data(self, element, url, is_first=False):
        """Extract structured data from a section"""
        section_type = self._detect_section_type(element, is_first=is_first) or "content"
        column_layout = self._detect_column_layout(element)
        has_image = self._has_image(element)
        
        # Determine overall layout type
        if section_type == "hero":
            layout = "hero"
        elif section_type in ["testimonial", "faq", "cta", "gallery", "features", "pricing", "team", "contact"]:
            layout = section_type
        elif column_layout != "single_column":
            if has_image:
                layout = f"{column_layout}_text_image"
            else:
                layout = f"{column_layout}_text"
        else:
            layout = "single_column"
        
        # Extract headings
        h1 = element.find("h1")
        h2 = element.find("h2")
        h3 = element.find("h3")
        
        heading = h1.get_text(strip=True) if h1 else None
        subheading = (h2 or h3).get_text(strip=True) if (h2 or h3) else None
        
        # Extract images
        images = []
        for img in element.find_all("img", src=True):
            src = img.get("src", "")
            if src and not src.startswith("data:"):
                if not src.startswith("http"):
                    src = urljoin(url, src)
                images.append({
                    "src": src,
                    "alt": img.get("alt", ""),
                })
        
        # Get content as markdown
        content_md = md(
            str(element),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
        )
        content_md = re.sub(r'\n{3,}', '\n\n', content_md).strip()
        
        return {
            "layout": layout,
            "section_type": section_type,
            "heading": heading,
            "subheading": subheading,
            "has_image": has_image,
            "images": images[:5],  # Limit to 5 images
            "content": content_md,
        }
    
    def _extract_content(self, html, url):
        soup = BeautifulSoup(html, "lxml")
        
        # Remove junk elements first
        for selector in JUNK_SELECTORS:
            for element in soup.select(selector):
                element.decompose()
        
        for tag in soup(["script", "style", "noscript", "meta", "link", "iframe", "svg", "path"]):
            tag.decompose()
        
        title = self._extract_title(soup, url)
        
        # Find main content area
        main_content = (
            soup.find("main") or 
            soup.find("article") or 
            soup.find(class_=lambda x: x and ("content" in x.lower() or "body" in x.lower())) or
            soup.find(id=lambda x: x and ("content" in x.lower() or "main" in x.lower())) or
            soup.body
        )
        
        if not main_content:
            main_content = soup
        
        # Extract sections by finding heading-based boundaries
        sections = self._extract_sections_by_headings(main_content, url)
        
        # Also create a combined markdown version
        content = md(
            str(main_content),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
        )
        
        # Clean up the markdown
        content = re.sub(r'\n{3,}', '\n\n', content)
        lines = [line.rstrip() for line in content.split('\n')]
        content = '\n'.join(lines)
        
        # Filter out junk lines
        filtered_lines = []
        for line in content.split('\n'):
            line_lower = line.lower().strip()
            if any(pattern in line_lower for pattern in JUNK_TEXT_PATTERNS):
                continue
            if line.strip() and not line.startswith('#') and len(line.strip()) < 15:
                if not line.strip().startswith('-') and not re.match(r'^\d+\.', line.strip()):
                    continue
            filtered_lines.append(line)
        
        content = '\n'.join(filtered_lines).strip()
        
        # Fix encoding issues
        content = self._fix_encoding(content)
        
        if len(content) < MIN_CONTENT_LENGTH:
            content = soup.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            content = "\n".join(lines)
        
        return title, content, sections
    
    def _extract_sections_by_headings(self, main_content, url):
        """Extract sections by splitting content at H1 and H2 headings"""
        sections = []
        seen_headings = set()  # Track headings to avoid duplicates
        
        # Find ALL H1 and H2 headings anywhere in the content (not just direct children)
        major_headings = main_content.find_all(['h1', 'h2'], recursive=True)
        
        if not major_headings:
            section_data = self._extract_section_data(main_content, url, is_first=True)
            if section_data["content"]:
                sections.append(section_data)
            return sections
        
        for i, heading in enumerate(major_headings):
            heading_text = heading.get_text(strip=True)
            
            # Skip empty, very short, or junk headings
            if not heading_text or len(heading_text) < 3:
                continue
            if self._is_junk_text(heading_text):
                continue
            # Skip button-like headings
            if heading_text.startswith('[') and heading_text.endswith(']'):
                continue
            # Skip duplicates
            heading_key = heading_text.lower().strip()[:50]
            if heading_key in seen_headings:
                continue
            seen_headings.add(heading_key)
            
            # Find next heading to know where this section ends
            next_heading = major_headings[i + 1] if i + 1 < len(major_headings) else None
            
            # IMPROVED: Build section content by collecting siblings until next heading
            section_parts = [heading]
            
            # Method 1: Try to get following siblings of the heading
            current = heading.find_next_sibling()
            found_content = False
            
            while current:
                # Stop if we hit the next major heading
                if current.name in ['h1', 'h2']:
                    break
                if next_heading and (current == next_heading or (hasattr(current, 'find') and current.find(['h1', 'h2']))):
                    break
                
                section_parts.append(current)
                found_content = True
                current = current.find_next_sibling()
                
                if len(section_parts) > 20:
                    break
            
            # Method 2: If no siblings found, try immediate parent's content
            if not found_content:
                parent = heading.parent
                if parent and parent.name not in ['body', 'html', '[document]']:
                    # Only use parent if it's small enough (not the whole page)
                    parent_text = parent.get_text(strip=True)
                    if len(parent_text) < 2000:  # Limit to avoid grabbing whole page
                        section_parts = [parent]
            
            # Build section HTML from parts
            section_html = ''.join(str(p) for p in section_parts)
            
            # Safety check: don't use if it's too large (likely grabbed whole page)
            if len(section_html) > 15000:
                # Fall back to just the heading
                section_html = str(heading)
            
            # Parse the section
            section_soup = BeautifulSoup(section_html, "lxml")
            section_text = section_soup.get_text(strip=True)
            
            # Skip if too short or just the heading
            if len(section_text) < 30:
                continue
            if len(section_text) < len(heading_text) + 20:
                continue
            
            # Detect section type using only the local content
            is_first = (len(sections) == 0)
            section_type = self._detect_section_type_from_content(
                heading_text, 
                section_text,
                section_soup,
                is_first
            )
            
            # Get column layout
            column_layout = self._detect_column_layout(section_soup)
            has_image = self._has_image(section_soup)
            
            # Determine final layout
            if section_type:
                layout = section_type
            elif column_layout != "single_column":
                layout = f"{column_layout}_text_image" if has_image else f"{column_layout}_text"
            else:
                layout = "single_column"
            
            # Get subheading (first H3 in this section)
            subheading = None
            h3 = section_soup.find('h3')
            if h3:
                sub_text = h3.get_text(strip=True)
                if sub_text and not sub_text.startswith('[') and sub_text != heading_text:
                    subheading = sub_text
            
            # Extract images
            images = []
            for img in section_soup.find_all("img", src=True)[:5]:
                src = img.get("src", "")
                if src and not src.startswith("data:"):
                    if not src.startswith("http"):
                        src = urljoin(url, src)
                    images.append({"src": src, "alt": img.get("alt", "")})
            
            # Get markdown content and fix encoding
            content_md = md(
                str(section_soup),
                heading_style="ATX",
                bullets="-",
                strong_em_symbol="*",
            )
            content_md = re.sub(r'\n{3,}', '\n\n', content_md).strip()
            content_md = self._fix_encoding(content_md)
            
            # Skip very short content
            if len(content_md) < 30:
                continue
            
            sections.append({
                "layout": layout,
                "section_type": section_type or "content",
                "heading": heading_text,
                "subheading": subheading,
                "has_image": has_image,
                "images": images,
                "content": content_md,
            })
        
        return sections
    
    def _fix_encoding(self, text):
        """Fix common encoding issues"""
        replacements = {
            'â€"': '—',  # em dash
            'â€"': '–',  # en dash
            'â€™': "'",  # right single quote
            'â€œ': '"',  # left double quote
            'â€': '"',   # right double quote
            'â€˜': "'",  # left single quote
            'Â ': ' ',   # non-breaking space artifact
            'Â': '',     # lone Â
            '\xa0': ' ', # actual non-breaking space
        }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        return text
    
    def _detect_section_type_from_content(self, heading_text, text_content, soup, is_first=False):
        """Detect section type based on heading and ONLY immediate section content"""
        heading_lower = heading_text.lower()
        
        # Limit text analysis to first 500 chars to avoid detecting content from later sections
        text_local = text_content[:500] if len(text_content) > 500 else text_content
        text_lower = text_local.lower()
        word_count = len(text_content.split())
        
        # Hero: first section
        if is_first:
            return "hero"
        
        # Skip detection for button-like headings
        if heading_text.startswith('[') or heading_text.startswith('BOOK'):
            return "cta"
        
        # Testimonial: heading is a quote or "Title" (common placeholder)
        if heading_text.startswith('"') or heading_text.startswith('"') or heading_text == '"Title"':
            return "testimonial"
        
        # Overview - check heading first (before other checks)
        if "overview" in heading_lower:
            return "overview"
        
        # FAQ: heading explicitly says FAQ/questions
        if any(kw in heading_lower for kw in ["faq", "frequently asked", "questions", "q&a"]):
            return "faq"
        
        # Testimonial: Must have quotes AND attribution in the LOCAL content only
        has_quotes = text_local.count('"') >= 2 or ('"' in text_local and '"' in text_local)
        # Attribution must be in this section
        attribution_pattern = re.search(r'[—–-]\s*(The\s+)?[A-Z][a-z]+\s+[A-Z]|[A-Z][a-z]+\s+Family', text_local)
        
        if has_quotes and attribution_pattern and heading_lower not in ["overview", "the most thrilling"]:
            return "testimonial"
        
        # Features/Benefits - check heading
        if any(kw in heading_lower for kw in ["why choose", "why paddle", "why ", "features", "benefits"]):
            return "features"
        
        # CTA - short with action words
        cta_in_heading = any(cta in heading_lower for cta in ["book", "contact", "ready to", "let's", "call us", "get started"])
        if cta_in_heading:
            return "cta"
        if word_count < 100 and any(cta in text_lower for cta in ["book trip", "contact us", "call us at"]):
            return "cta"
        
        # Pricing - dollar amounts
        if any(kw in heading_lower for kw in ["pricing", "price", "rates"]):
            return "pricing"
        if re.search(r'\$\d+.*per person|starting at \$\d+', text_lower):
            return "pricing"
        
        # Options/Adventures (multi-card sections)
        if any(kw in heading_lower for kw in ["choose your", "our trips", "adventures", "options", "services"]):
            return "options"
        
        # What to Expect / Process
        if any(kw in heading_lower for kw in ["what to expect", "how it works", "the process", "your adventure"]):
            return "process"
        
        # Preparing / Checklist - check heading
        if any(kw in heading_lower for kw in ["preparing", "what to bring", "what you need", "everything you need", "packing", "gear up"]):
            return "checklist"
        
        # Gear/Equipment
        if any(kw in heading_lower for kw in ["gear", "equipment", "what we provide"]):
            return "checklist"
        
        return None
    
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
        
        if self.single_page_mode:
            status_text.text(f"🎯 Single page mode: {self.start_url}")
        
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
                
                # Force UTF-8 encoding to fix character issues
                response.encoding = 'utf-8'
                
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue
                
            except requests.RequestException as e:
                self.status_messages.append(f"❌ Failed: {url} ({str(e)[:50]})")
                continue
            
            title, content, sections = self._extract_content(response.text, url)
            
            content_hash = self._content_hash(content)
            if content_hash in self.content_hashes:
                continue
            
            if len(content) >= MIN_CONTENT_LENGTH:
                self.content_hashes.add(content_hash)
                self.unique_content[url] = {
                    "title": title,
                    "content": content,
                    "sections": sections,
                }
            
            new_links = self._extract_links(response.text, url)
            if not self.single_page_mode:
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
        """Export results as markdown string with layout annotations"""
        lines = [
            f"# {urlparse(self.base_url).netloc} – Scraped Content\n",
            f"*Extracted {time.strftime('%Y-%m-%d %H:%M')}*",
            f"*{len(self.unique_content)} pages with unique content*\n",
            "---\n"
        ]
        
        for url, data in self.unique_content.items():
            lines.append(f"## {data['title']}\n")
            lines.append(f"**URL:** {url}\n")
            
            # Add layout summary
            if data.get("sections"):
                layouts = [s["layout"] for s in data["sections"] if s.get("layout")]
                if layouts:
                    lines.append(f"**Layouts:** {', '.join(layouts)}\n")
            
            lines.append(f"\n{data['content']}\n")
            lines.append("---\n")
        
        result = "\n".join(lines)
        return self._fix_encoding(result)
    
    def to_json(self):
        """Export results as structured JSON with layout data"""
        import json
        
        output = {
            "source": self.base_url,
            "extracted_at": time.strftime('%Y-%m-%d %H:%M'),
            "page_count": len(self.unique_content),
            "pages": []
        }
        
        for url, data in self.unique_content.items():
            # Fix encoding in sections
            fixed_sections = []
            for section in data.get("sections", []):
                fixed_section = section.copy()
                if "content" in fixed_section:
                    fixed_section["content"] = self._fix_encoding(fixed_section["content"])
                if "heading" in fixed_section:
                    fixed_section["heading"] = self._fix_encoding(fixed_section["heading"])
                if "subheading" in fixed_section and fixed_section["subheading"]:
                    fixed_section["subheading"] = self._fix_encoding(fixed_section["subheading"])
                fixed_sections.append(fixed_section)
            
            page_data = {
                "url": url,
                "title": self._fix_encoding(data["title"]),
                "sections": fixed_sections,
                "content_markdown": self._fix_encoding(data["content"]),
            }
            output["pages"].append(page_data)
        
        return json.dumps(output, indent=2, ensure_ascii=False)
    
    def to_structured_markdown(self):
        """Export with detailed layout annotations per section"""
        lines = [
            f"# {urlparse(self.base_url).netloc} – Structured Content\n",
            f"*Extracted {time.strftime('%Y-%m-%d %H:%M')}*",
            f"*{len(self.unique_content)} pages*\n",
            "---\n"
        ]
        
        for url, data in self.unique_content.items():
            lines.append(f"## PAGE: {data['title']}\n")
            lines.append(f"**URL:** {url}\n")
            
            if data.get("sections"):
                lines.append(f"\n**Page Structure:** {len(data['sections'])} sections detected\n")
                
                for i, section in enumerate(data["sections"], 1):
                    layout = section.get('layout', 'content')
                    section_type = section.get('section_type', 'content')
                    
                    lines.append(f"\n---\n")
                    lines.append(f"### Section {i}: `{layout}`")
                    if section_type and section_type != layout:
                        lines.append(f" (type: `{section_type}`)")
                    lines.append("\n")
                    
                    if section.get("heading"):
                        lines.append(f"**Heading:** {section['heading']}")
                    if section.get("subheading"):
                        lines.append(f"**Subheading:** {section['subheading']}")
                    if section.get("has_image"):
                        lines.append(f"**Has Image:** Yes")
                        if section.get("images"):
                            for img in section["images"][:3]:
                                alt = img.get('alt', 'no alt')[:50]
                                lines.append(f"  - ![{alt}]({img['src'][:80]}...)")
                    
                    lines.append(f"\n**Content:**\n")
                    # Truncate very long sections
                    content = section.get("content", "")[:2000]
                    if len(section.get("content", "")) > 2000:
                        content += "\n\n*[Content truncated...]*"
                    lines.append(content)
                    lines.append("")
            else:
                lines.append(f"\n{data['content']}\n")
            
            lines.append("\n---\n")
        
        result = "\n".join(lines)
        return self._fix_encoding(result)


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
        help="Enter a full URL. Use 'Single page only' to scrape just this page."
    )
    
    single_page_mode = st.checkbox(
        "🎯 Single page only",
        value=False,
        help="Scrape only the URL entered, don't follow links"
    )
    
    if not single_page_mode:
        max_pages = st.slider(
            "Maximum pages",
            min_value=1,
            max_value=500,
            value=50,
            step=1,
            help="Limit how many pages to crawl"
        )
    else:
        max_pages = 1
    
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
        st.session_state.exclude_paths_textarea = ""
    
    def add_paths(new_paths):
        """Append new paths to existing, avoiding duplicates"""
        current = st.session_state.exclude_paths_textarea.strip()
        current_set = set(p.strip() for p in current.split("\n") if p.strip())
        new_set = set(p.strip() for p in new_paths.split("\n") if p.strip())
        combined = current_set | new_set  # Union of both sets
        st.session_state.exclude_paths_textarea = "\n".join(sorted(combined))
    
    # Common exclusion presets - BEFORE the text area
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
    
    # Clear button
    if st.button("🗑️ Clear all", use_container_width=True):
        st.session_state.exclude_paths_textarea = ""
        st.rerun()
    
    # Text area - uses key only, no value parameter
    exclude_paths_input = st.text_area(
        "Directories to skip (one per line)",
        height=150,
        help="URLs containing these paths will be skipped. Click buttons above to add common exclusions.",
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
    button_label = "🎯 Scrape This Page" if single_page_mode else "🚀 Start Scraping"
    start_button = st.button(button_label, type="primary", use_container_width=True)

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
                exclude_paths=exclude_paths,
                single_page_mode=single_page_mode
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
    
    # Export options
    st.subheader("Export")
    export_col1, export_col2, export_col3 = st.columns(3)
    
    with export_col1:
        markdown_content = st.session_state.scraper.to_markdown()
        st.download_button(
            label="📝 Markdown",
            data=markdown_content,
            file_name=f"scraped_{urlparse(url).netloc}.md",
            mime="text/markdown",
            use_container_width=True
        )
    
    with export_col2:
        structured_md = st.session_state.scraper.to_structured_markdown()
        st.download_button(
            label="📐 With Layouts",
            data=structured_md,
            file_name=f"scraped_{urlparse(url).netloc}_structured.md",
            mime="text/markdown",
            use_container_width=True
        )
    
    with export_col3:
        json_content = st.session_state.scraper.to_json()
        st.download_button(
            label="🔧 JSON",
            data=json_content,
            file_name=f"scraped_{urlparse(url).netloc}.json",
            mime="application/json",
            use_container_width=True
        )
    
    # Preview results
    st.markdown("### Preview")
    
    for i, (page_url, data) in enumerate(st.session_state.results.items()):
        # Show layout summary in expander title
        layouts = []
        if data.get("sections"):
            layouts = list(set(s["layout"] for s in data["sections"] if s.get("layout")))
        layout_str = f" [{', '.join(layouts[:3])}]" if layouts else ""
        
        with st.expander(f"**{data['title']}**{layout_str}"):
            st.markdown(f"**URL:** {page_url}")
            
            # Show sections breakdown
            if data.get("sections"):
                st.markdown("**Page Structure:**")
                for j, section in enumerate(data["sections"], 1):
                    layout = section.get("layout", "unknown")
                    heading = section.get("heading", "")[:50] or "(no heading)"
                    has_img = "🖼️" if section.get("has_image") else ""
                    st.markdown(f"- Section {j}: `{layout}` – {heading} {has_img}")
            
            st.markdown("---")
            st.markdown("**Content Preview:**")
            st.text(data['content'][:1000] + ("..." if len(data['content']) > 1000 else ""))

# Footer
st.markdown("---")
st.markdown("*Built with Streamlit • Be respectful when scraping*")
