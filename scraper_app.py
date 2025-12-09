"""
Web Scraper with Layout Detection - Streamlit App
Extracts content and detects page structure/layouts
"""

import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import hashlib
import time
import re
from markdownify import markdownify as md

# Configuration
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
MIN_CONTENT_LENGTH = 150
MIN_PARAGRAPH_WORDS = 8

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
    
    def _is_excluded(self, url):
        path = urlparse(url).path
        for exclude in self.exclude_paths:
            if exclude in path:
                return True
        return False
    
    def _is_valid_url(self, url):
        parsed = urlparse(url)
        base_parsed = urlparse(self.base_url)
        
        if parsed.netloc != base_parsed.netloc:
            return False
        
        path_lower = parsed.path.lower()
        for ext in SKIP_EXTENSIONS:
            if path_lower.endswith(ext):
                return False
        
        if self._is_excluded(url):
            return False
        
        return True
    
    def _normalize_url(self, url):
        parsed = urlparse(url)
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return normalized.rstrip("/")
    
    def _is_junk_text(self, text):
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in JUNK_TEXT_PATTERNS)
    
    def _fix_encoding(self, text):
        """Fix common encoding issues (mojibake from UTF-8 decoded as Latin-1)"""
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
            'â€¢': '•',
        }
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        
        return text
    
    def _detect_section_type_from_content(self, heading_text, text_content, soup, is_first=False):
        """Detect section type based on heading and content"""
        heading_lower = heading_text.lower()
        text_local = text_content[:500] if len(text_content) > 500 else text_content
        text_lower = text_local.lower()
        word_count = len(text_content.split())
        
        if is_first:
            return "hero"
        
        if heading_text.startswith('[') or heading_text.startswith('BOOK'):
            return "cta"
        
        if heading_text.startswith('"') or heading_text.startswith('"') or heading_text == '"Title"':
            return "testimonial"
        
        if "overview" in heading_lower:
            return "overview"
        
        if any(kw in heading_lower for kw in ["faq", "frequently asked", "questions", "q&a"]):
            return "faq"
        
        has_quotes = text_local.count('"') >= 2 or ('"' in text_local and '"' in text_local)
        attribution_pattern = re.search(r'[—–-]\s*(The\s+)?[A-Z][a-z]+\s+[A-Z]|[A-Z][a-z]+\s+Family', text_local)
        if has_quotes and attribution_pattern and heading_lower not in ["overview", "the most thrilling"]:
            return "testimonial"
        
        if any(kw in heading_lower for kw in ["why choose", "why paddle", "why ", "features", "benefits"]):
            return "features"
        
        cta_in_heading = any(cta in heading_lower for cta in ["book", "contact", "ready to", "let's", "call us", "get started"])
        if cta_in_heading:
            return "cta"
        if word_count < 100 and any(cta in text_lower for cta in ["book trip", "contact us", "call us at"]):
            return "cta"
        
        if any(kw in heading_lower for kw in ["pricing", "price", "rates"]):
            return "pricing"
        if re.search(r'\$\d+.*per person|starting at \$\d+', text_lower):
            return "pricing"
        
        if any(kw in heading_lower for kw in ["choose your", "our trips", "adventures", "options", "services"]):
            return "options"
        
        if any(kw in heading_lower for kw in ["what to expect", "how it works", "the process", "your adventure"]):
            return "process"
        
        if any(kw in heading_lower for kw in ["preparing", "what to bring", "what you need", "everything you need", "packing", "gear up"]):
            return "checklist"
        
        if any(kw in heading_lower for kw in ["gear", "equipment", "what we provide"]):
            return "checklist"
        
        return None
    
    def _detect_column_layout(self, soup):
        """Detect multi-column layouts"""
        classes = " ".join(soup.get("class", [])) if soup.name else ""
        
        col_patterns = {
            "four_column": ["col-3", "col-md-3", "col-lg-3", "w-1/4"],
            "three_column": ["col-4", "col-md-4", "col-lg-4", "w-1/3"],
            "two_column": ["col-6", "col-md-6", "col-lg-6", "w-1/2"],
        }
        
        for layout, patterns in col_patterns.items():
            for pattern in patterns:
                if pattern in classes:
                    return layout
        
        return "single_column"
    
    def _has_image(self, soup):
        """Check if section has images"""
        return bool(soup.find("img"))
    
    def _extract_sections_by_headings(self, main_content, url):
        """Extract sections by splitting content at H1 and H2 headings"""
        sections = []
        seen_headings = set()
        
        major_headings = main_content.find_all(['h1', 'h2'], recursive=True)
        
        if not major_headings:
            return sections
        
        for i, heading in enumerate(major_headings):
            heading_text = heading.get_text(strip=True)
            
            if not heading_text or len(heading_text) < 2:
                continue
            if self._is_junk_text(heading_text):
                continue
            if heading_text.startswith('[') and heading_text.endswith(']') and len(heading_text) < 30:
                continue
            
            heading_key = heading_text.lower().strip()[:50]
            if heading_key in seen_headings:
                continue
            seen_headings.add(heading_key)
            
            next_heading = major_headings[i + 1] if i + 1 < len(major_headings) else None
            
            section_parts = [heading]
            current_elem = heading
            
            while True:
                next_elem = current_elem.find_next()
                if next_elem is None:
                    break
                
                if next_elem in major_headings and next_elem != heading:
                    break
                
                if len(section_parts) > 25:
                    break
                
                if hasattr(next_elem, 'name') and next_elem.name:
                    if next_elem.name in ['script', 'style', 'meta', 'link']:
                        current_elem = next_elem
                        continue
                    if next_heading and next_elem.find(['h1', 'h2']):
                        break
                    if next_elem.name in ['p', 'div', 'ul', 'ol', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'table', 'img', 'figure']:
                        section_parts.append(next_elem)
                
                current_elem = next_elem
            
            section_html = ''.join(str(p) for p in section_parts)
            
            if len(section_html) > 10000:
                section_html = str(heading)
            
            section_soup = BeautifulSoup(section_html, "lxml")
            section_text = section_soup.get_text(strip=True)
            
            if len(section_text) < 5:
                continue
            
            is_first = (len(sections) == 0)
            section_type = self._detect_section_type_from_content(
                heading_text, 
                section_text,
                section_soup,
                is_first
            )
            
            column_layout = self._detect_column_layout(section_soup)
            has_image = self._has_image(section_soup)
            
            if section_type:
                layout = section_type
            elif column_layout != "single_column":
                layout = f"{column_layout}_text_image" if has_image else f"{column_layout}_text"
            else:
                layout = "single_column"
            
            subheading = None
            h3 = section_soup.find('h3')
            if h3:
                sub_text = h3.get_text(strip=True)
                if sub_text and not sub_text.startswith('[') and sub_text != heading_text:
                    subheading = sub_text
            
            images = []
            for img in section_soup.find_all("img", src=True)[:5]:
                src = img.get("src", "")
                if src and not src.startswith("data:"):
                    if not src.startswith("http"):
                        src = urljoin(url, src)
                    images.append({"src": src, "alt": img.get("alt", "")})
            
            content_md = md(
                str(section_soup),
                heading_style="ATX",
                bullets="-",
                strong_em_symbol="*",
            )
            content_md = re.sub(r'\n{3,}', '\n\n', content_md).strip()
            content_md = self._fix_encoding(content_md)
            
            sections.append({
                "layout": layout,
                "section_type": section_type or "content",
                "heading": self._fix_encoding(heading_text),
                "subheading": self._fix_encoding(subheading) if subheading else None,
                "has_image": has_image,
                "images": images,
                "content": content_md,
            })
        
        return sections
    
    def _extract_content(self, html, url):
        """Extract main content and sections from HTML"""
        soup = BeautifulSoup(html, "lxml")
        
        title = ""
        if soup.title:
            title = soup.title.string or ""
        
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()
        
        main_content = soup.find("main") or soup.find("article") or soup.find("body") or soup
        
        sections = self._extract_sections_by_headings(main_content, url)
        
        content_md = md(
            str(main_content),
            heading_style="ATX",
            bullets="-",
            strong_em_symbol="*",
        )
        
        lines = content_md.split('\n')
        filtered_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                filtered_lines.append("")
                continue
            if self._is_junk_text(line):
                continue
            filtered_lines.append(line)
        
        content = '\n'.join(filtered_lines).strip()
        content = self._fix_encoding(content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        
        if len(content) < MIN_CONTENT_LENGTH:
            return title, "", sections
        
        return title, content, sections
    
    def _extract_links(self, html, current_url):
        """Extract valid links from HTML"""
        soup = BeautifulSoup(html, "lxml")
        links = []
        
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(current_url, href)
            normalized = self._normalize_url(full_url)
            
            if self._is_valid_url(normalized):
                links.append(normalized)
        
        return links
    
    def crawl(self, progress_callback=None, stats_callback=None):
        """Main crawl loop"""
        pages_processed = 0
        
        while self.to_visit and pages_processed < self.max_pages:
            url = self.to_visit.pop(0)
            
            if url in self.visited:
                continue
            
            if self.respect_robots and not self._can_fetch(url):
                continue
            
            self.visited.add(url)
            time.sleep(self.delay)
            
            try:
                response = self.session.get(url, timeout=15)
                response.raise_for_status()
                
                html_content = response.content.decode('utf-8', errors='replace')
                
                content_type = response.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    continue
                
            except requests.RequestException as e:
                continue
            
            title, content, sections = self._extract_content(html_content, url)
            
            if content:
                content_hash = hashlib.md5(content.encode()).hexdigest()
                
                if content_hash not in self.content_hashes:
                    self.content_hashes.add(content_hash)
                    self.unique_content[url] = {
                        "title": self._fix_encoding(title) or urlparse(url).path,
                        "content": content,
                        "sections": sections,
                    }
            
            if not self.single_page_mode:
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
            
            if data.get("sections"):
                layouts = [s["layout"] for s in data["sections"] if s.get("layout")]
                if layouts:
                    lines.append(f"**Layouts:** {', '.join(layouts)}\n")
            
            lines.append(f"\n{data['content']}\n")
            lines.append("---\n")
        
        return self._fix_encoding("\n".join(lines))
    
    def to_json(self):
        """Export results as structured JSON"""
        import json
        
        output = {
            "source": self.base_url,
            "extracted_at": time.strftime('%Y-%m-%d %H:%M'),
            "page_count": len(self.unique_content),
            "pages": []
        }
        
        for url, data in self.unique_content.items():
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
    
    def to_structure_only(self):
        """Export layout structure only - for matching with manually copied content"""
        lines = [
            f"# {urlparse(self.base_url).netloc} – Page Structure\n",
            f"*Extracted {time.strftime('%Y-%m-%d %H:%M')}*\n",
            "---\n",
            "## Layout Types Reference\n",
            "| Layout | Description |",
            "|--------|-------------|",
            "| `hero` | Large header section with background image |",
            "| `single_column` | One column of content |",
            "| `two_column` | Two columns side by side |",
            "| `three_column` | Three columns (features/benefits) |",
            "| `four_column` | Four columns (icons/stats) |",
            "| `gallery` | Image gallery grid |",
            "| `testimonial` | Customer quote with photo |",
            "| `faq` | Accordion-style Q&A section |",
            "| `cta` | Call-to-action with button |",
            "| `pricing` | Pricing card/table |",
            "| `features` | Features/benefits list |",
            "| `checklist` | Checklist/what to bring |",
            "| `process` | Step-by-step process |",
            "| `options` | Multiple choice cards |",
            "| `overview` | Overview/intro section |",
            "\n---\n"
        ]
        
        for url, data in self.unique_content.items():
            lines.append(f"## PAGE: {self._fix_encoding(data['title'])}\n")
            lines.append(f"**URL:** {url}\n")
            
            sections = data.get("sections", [])
            if sections:
                lines.append(f"\n**Total Sections:** {len(sections)}\n")
                lines.append("\n### Section Structure\n")
                
                for idx, section in enumerate(sections, 1):
                    layout = section.get("layout", "unknown")
                    heading = self._fix_encoding(section.get("heading", "")) or "(no heading)"
                    subheading = self._fix_encoding(section.get("subheading", "")) if section.get("subheading") else None
                    has_image = section.get("has_image", False)
                    content = section.get("content", "")
                    
                    lines.append(f"\n---\n")
                    lines.append(f"### Section {idx}: `{layout}`\n")
                    
                    if layout == "hero":
                        lines.append("**Template:** Hero Section\n")
                        lines.append(f"- **H1:** {heading}")
                        if subheading:
                            lines.append(f"- **Tagline:** {subheading}")
                        lines.append("- **Background Image:** [1600×900 - 16:9]")
                        lines.append("- **CTA Button:** [ ]")
                    
                    elif layout == "testimonial":
                        lines.append("**Template:** Testimonial\n")
                        lines.append(f"- **Quote:** {heading}")
                        if subheading:
                            lines.append(f"- **Full Quote:** {subheading[:100]}...")
                        lines.append("- **Author Name:** [ ]")
                        lines.append("- **Author Title:** [ ]")
                        lines.append("- **Photo:** [200×200 - square]")
                    
                    elif layout == "faq":
                        lines.append("**Template:** FAQ Accordion\n")
                        lines.append(f"- **Section Heading:** {heading}")
                        questions = re.findall(r'\?', content)
                        q_count = len(questions) if questions else 3
                        lines.append(f"- **Q&A Pairs:** ~{q_count} questions detected")
                        for q in range(min(q_count, 6)):
                            lines.append(f"  - Q{q+1}: [ ]")
                            lines.append(f"  - A{q+1}: [ ]")
                    
                    elif layout == "cta":
                        lines.append("**Template:** Call to Action\n")
                        lines.append(f"- **Heading:** {heading}")
                        if subheading:
                            lines.append(f"- **Subheading:** {subheading[:80]}...")
                        lines.append("- **Button 1:** [ ]")
                        lines.append("- **Button 2:** [ ] (optional)")
                    
                    elif layout == "pricing":
                        lines.append("**Template:** Pricing Card\n")
                        lines.append(f"- **Plan Name:** {heading}")
                        if subheading:
                            lines.append(f"- **Subtitle:** {subheading}")
                        lines.append("- **Price:** $[ ] per [ ]")
                        lines.append("- **Description:** [ ]")
                        lines.append("- **CTA Button:** [ ]")
                    
                    elif layout == "features":
                        lines.append("**Template:** Features/Benefits\n")
                        lines.append(f"- **Section Heading:** {heading}")
                        if subheading:
                            lines.append(f"- **Subheading:** {subheading}")
                        bullets = content.count("- ")
                        feature_count = bullets if bullets > 0 else 4
                        lines.append(f"- **Features:** ~{feature_count} items")
                        for f in range(min(feature_count, 6)):
                            lines.append(f"  - Feature {f+1}: [ ]")
                    
                    elif layout == "checklist":
                        lines.append("**Template:** Checklist\n")
                        lines.append(f"- **Section Heading:** {heading}")
                        if subheading:
                            lines.append(f"- **Subheading:** {subheading}")
                        bullets = content.count("- ")
                        lines.append(f"- **List Items:** ~{bullets} items")
                    
                    elif layout == "process":
                        lines.append("**Template:** Process/Steps\n")
                        lines.append(f"- **Section Heading:** {heading}")
                        if subheading:
                            lines.append(f"- **Subheading:** {subheading}")
                        lines.append("- **Steps:**")
                        lines.append("  - Step 1: [ ]")
                        lines.append("  - Step 2: [ ]")
                        lines.append("  - Step 3: [ ]")
                    
                    elif layout == "options":
                        lines.append("**Template:** Options/Cards\n")
                        lines.append(f"- **Section Heading:** {heading}")
                        lines.append("- **Option Cards:** (see pricing sections below)")
                    
                    elif layout == "overview":
                        lines.append("**Template:** Overview/Intro\n")
                        lines.append(f"- **H2:** {heading}")
                        if subheading:
                            lines.append(f"- **H3:** {subheading}")
                        lines.append("- **Body Text:** [ ]")
                        lines.append("- **Bullet Points:** [ ] (if any)")
                        if has_image:
                            lines.append("- **Image:** [1000px wide]")
                    
                    else:
                        lines.append("**Template:** Single Column\n")
                        lines.append(f"- **H2:** {heading}")
                        if subheading:
                            lines.append(f"- **H3:** {subheading}")
                        lines.append("- **Body Text:** [ ]")
                        if has_image:
                            lines.append("- **Image:** [1200px wide]")
                    
                    lines.append("")
            
            lines.append("\n---\n")
        
        return "\n".join(lines)
    
    def to_structured_markdown(self):
        """Export with detailed layout annotations per section"""
        lines = [
            f"# {urlparse(self.base_url).netloc} – Structured Content\n",
            f"*Extracted {time.strftime('%Y-%m-%d %H:%M')}*",
            f"*{len(self.unique_content)} pages*\n",
            "---\n"
        ]
        
        for url, data in self.unique_content.items():
            lines.append(f"## PAGE: {self._fix_encoding(data['title'])}\n")
            lines.append(f"**URL:** {url}\n")
            
            if data.get("sections"):
                lines.append(f"\n**Page Structure:** {len(data['sections'])} sections detected\n")
                
                for i, section in enumerate(data["sections"], 1):
                    layout = section.get('layout', 'content')
                    section_type = section.get('section_type', 'content')
                    
                    lines.append(f"\n---\n")
                    lines.append(f"### Section {i}: `{layout}`\n")
                    
                    if section_type != layout:
                        lines.append(f" (type: `{section_type}`)\n")
                    
                    if section.get("heading"):
                        lines.append(f"\n**Heading:** {self._fix_encoding(section['heading'])}")
                    if section.get("subheading"):
                        lines.append(f"\n**Subheading:** {self._fix_encoding(section['subheading'])}")
                    if section.get("has_image"):
                        lines.append(f"\n**Has Image:** Yes")
                        if section.get("images"):
                            for img in section["images"][:3]:
                                lines.append(f"\n  - {img.get('alt', 'Image')}: {img.get('src', '')[:80]}...")
                    
                    content = self._fix_encoding(section.get("content", ""))[:2000]
                    if len(section.get("content", "")) > 2000:
                        content += "\n\n*[Content truncated...]*"
                    
                    lines.append(f"\n\n**Content:**\n\n{content}\n")
            
            lines.append("\n---\n")
        
        return self._fix_encoding("\n".join(lines))


# Streamlit UI
st.set_page_config(page_title="Web Scraper", page_icon="🕷️", layout="wide")
st.title("🕷️ Web Scraper with Layout Detection")

# Sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    
    url = st.text_input("Website URL", placeholder="https://example.com")
    
    single_page = st.checkbox("🎯 Single page only", help="Scrape only the exact URL provided")
    
    if not single_page:
        max_pages = st.slider("Max pages", 1, 200, 50)
    else:
        max_pages = 1
    
    delay = st.slider("Delay (seconds)", 0.1, 2.0, 0.3)
    respect_robots = st.checkbox("Respect robots.txt", value=True)
    
    st.header("🚫 Exclude Paths")
    
    if "exclusions" not in st.session_state:
        st.session_state.exclusions = ""
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🛒 E-commerce"):
            current = set(st.session_state.exclusions.split('\n')) if st.session_state.exclusions else set()
            current.update(["/shop/", "/cart/", "/checkout/", "/account/", "/my-account/"])
            current.discard("")
            st.session_state.exclusions = '\n'.join(sorted(current))
            st.rerun()
        if st.button("👤 User areas"):
            current = set(st.session_state.exclusions.split('\n')) if st.session_state.exclusions else set()
            current.update(["/login/", "/register/", "/profile/", "/dashboard/", "/admin/"])
            current.discard("")
            st.session_state.exclusions = '\n'.join(sorted(current))
            st.rerun()
    with col2:
        if st.button("🌍 Languages"):
            current = set(st.session_state.exclusions.split('\n')) if st.session_state.exclusions else set()
            current.update(["/es/", "/fr/", "/de/", "/it/", "/pt/", "/ja/", "/zh/"])
            current.discard("")
            st.session_state.exclusions = '\n'.join(sorted(current))
            st.rerun()
        if st.button("📰 Blog/News"):
            current = set(st.session_state.exclusions.split('\n')) if st.session_state.exclusions else set()
            current.update(["/blog/", "/news/", "/press/", "/media/"])
            current.discard("")
            st.session_state.exclusions = '\n'.join(sorted(current))
            st.rerun()
    
    if st.button("🗑️ Clear all"):
        st.session_state.exclusions = ""
        st.rerun()
    
    exclusions = st.text_area(
        "Custom exclusions (one per line)",
        value=st.session_state.exclusions,
        height=100,
        key="exclusion_input"
    )
    st.session_state.exclusions = exclusions
    
    exclude_list = [p.strip() for p in exclusions.split('\n') if p.strip()]

# Main area
if url:
    button_text = "🎯 Scrape This Page" if single_page else "🚀 Start Scraping"
    
    if st.button(button_text, type="primary", use_container_width=True):
        progress_bar = st.progress(0)
        stats_container = st.empty()
        
        def update_progress(p):
            progress_bar.progress(p)
        
        def update_stats(s):
            stats_container.markdown(
                f"**Processed:** {s['processed']} | **Saved:** {s['saved']} | **Queue:** {s['queue']}"
            )
        
        scraper = WebScraper(
            url,
            max_pages=max_pages,
            delay=delay,
            respect_robots=respect_robots,
            exclude_paths=exclude_list,
            single_page_mode=single_page
        )
        
        results = scraper.crawl(
            progress_callback=update_progress,
            stats_callback=update_stats
        )
        
        st.session_state.results = results
        st.session_state.scraper = scraper
        
        st.success(f"✅ Done! Found {len(results)} pages with unique content.")

# Results
if "results" in st.session_state and st.session_state.results:
    st.markdown("---")
    st.markdown("### 📥 Export")
    
    export_col1, export_col2, export_col3, export_col4 = st.columns(4)
    
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
        structure_only = st.session_state.scraper.to_structure_only()
        st.download_button(
            label="🏗️ Structure Only",
            data=structure_only,
            file_name=f"scraped_{urlparse(url).netloc}_structure.md",
            mime="text/markdown",
            use_container_width=True
        )
    
    with export_col4:
        json_content = st.session_state.scraper.to_json()
        st.download_button(
            label="🔧 JSON",
            data=json_content,
            file_name=f"scraped_{urlparse(url).netloc}.json",
            mime="application/json",
            use_container_width=True
        )
    
    # Preview
    st.markdown("### Preview")
    
    for page_url, data in st.session_state.results.items():
        layouts = []
        if data.get("sections"):
            layouts = list(set(s["layout"] for s in data["sections"] if s.get("layout")))
        layout_str = f" [{', '.join(layouts[:4])}]" if layouts else ""
        
        with st.expander(f"📄 {data['title'][:60]}{layout_str}"):
            st.markdown(f"**URL:** {page_url}")
            
            if data.get("sections"):
                st.markdown(f"**Sections:** {len(data['sections'])}")
                for i, section in enumerate(data["sections"][:10], 1):
                    layout = section.get("layout", "unknown")
                    heading = section.get("heading", "")[:50] or "(no heading)"
                    has_img = "🖼️" if section.get("has_image") else ""
                    st.markdown(f"- {i}. `{layout}` - {heading} {has_img}")
            
            preview = data['content'][:1000]
            if len(data['content']) > 1000:
                preview += "\n\n*[Content truncated...]*"
            st.markdown(preview)
else:
    st.info("Enter a URL and click 'Start Scraping' to begin.")
