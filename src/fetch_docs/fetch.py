import asyncio
import aiohttp
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pathlib import Path
import time
from typing import Set, Optional
from dataclasses import dataclass
import readabilipy.simple_json
import markdownify
import shutil

# Connection limits for performance and server respect
MAX_CONCURRENT_CONNECTIONS = 8
MAX_CONNECTIONS_PER_HOST = 4
REQUEST_TIMEOUT = 30

@dataclass
class PageData:
    """Data extracted from a single HTML page"""
    internal_links: Set[str]
    updated_html: str


class HTMLProcessor:
    """Single-pass HTML processor for extracting assets and updating links"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self._url_cache = {}  # Cache for urljoin operations
    
    def _cached_urljoin(self, base: str, url: str) -> str:
        """Cached version of urljoin to avoid repeated operations"""
        cache_key = (base, url)
        if cache_key not in self._url_cache:
            self._url_cache[cache_key] = urljoin(base, url)
        return self._url_cache[cache_key]
    
    def parse_and_extract(self, html_content: str) -> PageData:
        """Single-pass HTML parsing that extracts all data and updates links"""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        internal_links = set()
        
        # Process all elements in a single pass
        self._process_elements(soup, internal_links)
        
        return PageData(
            internal_links=internal_links,
            updated_html=str(soup)
        )
    
    def _process_elements(self, soup, internal_links: Set[str]):
        """Process all HTML elements in a single DOM traversal"""
        
        # Process all anchor tags for internal links and update them
        for link in soup.find_all('a', href=True):
            href = link['href']
            # Check if it's an internal omarchy manual link
            if href.startswith('/') and 'the-omarchy-manual' in href.lower():
                full_url = self._cached_urljoin(self.base_url, href)
                internal_links.add(full_url)
                
                # Update the link to point to local file
                if href == '/2/the-omarchy-manual':
                    link['href'] = 'toc.md'
                elif (local_filename := convert_url_to_filename(href)):
                    link['href'] = local_filename


def convert_url_to_filename(url: str) -> Optional[str]:
    """Convert URL path to local filename format"""
    parsed = urlparse(url)
    if (path := parsed.path.strip('/')):
        return 'toc.md' if path == '2/the-omarchy-manual' else f"{Path(path).name}.md"
    return None


async def download_page_content(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Download HTML content of a page"""
    try:
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.text()
    except Exception as e:
        print(f"Error downloading page {url}: {e}")
        return None


async def download_page(session: aiohttp.ClientSession, url: str, output_dir: str) -> Optional[str]:
    """Download a single page and its assets asynchronously"""
    html_content = await download_page_content(session, url)
    if not html_content:
        return None

    try:
        # Use HTMLProcessor for single-pass extraction and updating
        processor = HTMLProcessor(url)
        page_data = processor.parse_and_extract(html_content)

        # Parse URL to get filename
        filename = convert_url_to_filename(url)
        if not filename:
            print(f"Could not determine filename for URL: {url}")
            return None
        filepath = Path(output_dir) / filename
        
        ret = readabilipy.simple_json.simple_json_from_html_string(
            page_data.updated_html, use_readability=True
        )
        if not ret["content"]:
           return "<error>Page failed to be simplified from HTML</error>"
        content = markdownify.markdownify(
            ret["content"],
            heading_style=markdownify.ATX,
        )
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"Downloaded: {url} -> {filename}")
        return content

    except Exception as e:
        print(f"Error processing page {url}: {e}")
        return None


async def find_documentation_links(session: aiohttp.ClientSession, base_url: str) -> Set[str]:
    """Find all documentation page links from the main page"""
    html_content = await download_page_content(session, base_url)
    if not html_content:
        return set()
    
    # Use HTMLProcessor to extract internal links efficiently
    processor = HTMLProcessor(base_url)  # Empty assets dict for initial discovery
    page_data = processor.parse_and_extract(html_content)
    
    return page_data.internal_links


async def download(
    base_url: str,
    docs_dir: str
) -> None:

    # Create docs directory and subdirectories
    output_path = Path(docs_dir)
 
    # Remove entire directory and recreate it
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Omarchy documentation to {docs_dir}/")
    print("This will include Markdown pages for offline viewing.")
    print("Using async downloads for better performance...")

    start_time = time.time()

    # Configure aiohttp session with connection limits
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENT_CONNECTIONS,
        limit_per_host=MAX_CONNECTIONS_PER_HOST
    )
    
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        # Find all documentation links
        print("Discovering documentation pages...")
        links = await find_documentation_links(session, base_url)
        all_pages = {base_url} | links
 
        print(f"Found {len(all_pages)} pages to download...")

        # Create semaphore to limit concurrent page downloads
        # (assets within each page are still downloaded concurrently)
        page_semaphore = asyncio.Semaphore(MAX_CONNECTIONS_PER_HOST)
 
        async def download_with_semaphore(url: str) -> Optional[str]:
            async with page_semaphore:
                return await download_page(session, url, docs_dir)

        # Download all pages concurrently
        print("Starting concurrent downloads...")
        tasks = [download_with_semaphore(url) for url in all_pages]
        results = await asyncio.gather(*tasks, return_exceptions=True)
 
        # Count successful downloads
        successful = len([result for result in results if isinstance(result, str) and result])
        failed = len(results) - successful

    end_time = time.time()
    duration = end_time - start_time

    print(f"\nDocumentation download completed in {duration:.2f} seconds!")
    print(f"Successfully downloaded: {successful} pages")
    if failed > 0:
        print(f"Failed downloads: {failed} pages")
    print(f"Files saved to: {docs_dir}/")
    print(f"Open {docs_dir}/toc.md to view offline.")
