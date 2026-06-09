#!/usr/bin/env python3
"""
Generate sitemap.xml automatically by scanning frontend directory for HTML files.

This script scans the frontend directory for HTML files and generates a sitemap.xml
with appropriate priorities and change frequencies based on file patterns.

Usage:
  python scripts/generate_sitemap.py

The script will:
1. Scan frontend/ for all .html files
2. Assign priorities and change frequencies based on file patterns
3. Generate frontend/sitemap.xml with proper XML structure
4. Preserve the base URL and XML structure
"""

import os
import re
from pathlib import Path
from typing import List, Tuple

# Base URL for the site
BASE_URL = "https://www.changeimageto.com"

# Priority and change frequency rules
PRIORITY_RULES = [
    # High priority main pages
    (r"^index\.html?$", 1.0, "daily"),
    (r"^remove-background-from-image\.html$", 0.9, "daily"),
    (r"^enhance-image\.html$", 0.9, "weekly"),
    (r"^upscale-image\.html$", 0.9, "weekly"),
    (r"^remove-text-from-image\.html$", 0.9, "weekly"),
    (r"^remove-people-from-photo\.html$", 0.9, "weekly"),
    (r"^blur-background\.html$", 0.9, "weekly"),
    (r"^bulk-image-resizer\.html$", 0.9, "weekly"),
    (r"^image-quality-checker\.html$", 0.9, "weekly"),
    
    # Medium priority utility pages
    (r"^change-image-background\.html$", 0.9, "daily"),
    (r"^change-color-of-image\.html$", 0.9, "daily"),
    (r"^convert-image-format\.html$", 0.9, "weekly"),
    
    # SEO-focused pages (nano-banana and gemini)
    (r"^nano-banana-change-image\.html$", 0.85, "weekly"),
    (r"^nano-banana-change-image-.*\.html$", 0.85, "weekly"),
    (r"^gemini-change-image-.*\.html$", 0.85, "weekly"),
    (r"^how-to-change-the-aspect-ratio-of-an-image-in-gemini\.html$", 0.85, "weekly"),
    
    # Color-specific pages (lower priority)
    (r"change-image-background-to-.*\.html$", 0.8, "weekly"),
    
    # Blog pages
    (r"^blog/.*\.html$", 0.7, "monthly"),
    
    # Default for any other HTML files
    (r".*\.html$", 0.5, "monthly"),
]

def get_priority_and_frequency(filename: str) -> Tuple[float, str]:
    """Get priority and change frequency for a given filename."""
    for pattern, priority, freq in PRIORITY_RULES:
        if re.match(pattern, filename):
            return priority, freq
    # Fallback
    return 0.5, "monthly"

SKIP_FILES = {
    "blog-admin.html",
    "feedback-popup.html",
}

def should_skip_file(rel_path_str: str) -> bool:
    """Return True for internal, test, or non-public pages."""
    basename = Path(rel_path_str).name
    if basename.startswith("test-"):
        return True
    if basename in SKIP_FILES:
        return True
    if any(skip in rel_path_str for skip in ["node_modules", ".git", "__pycache__"]):
        return True
    return False

def scan_html_files(frontend_dir: Path) -> List[Tuple[str, float, str]]:
    """Scan frontend directory for HTML files and return their sitemap info."""
    html_files = []
    
    for html_file in frontend_dir.rglob("*.html"):
        # Get relative path from frontend directory
        rel_path = html_file.relative_to(frontend_dir)
        rel_path_str = str(rel_path)
        
        if should_skip_file(rel_path_str):
            continue
            
        priority, freq = get_priority_and_frequency(rel_path_str)
        html_files.append((rel_path_str, priority, freq))
    
    # Sort by priority (highest first), then by filename
    html_files.sort(key=lambda x: (-x[1], x[0]))
    return html_files

def generate_sitemap_xml(html_files: List[Tuple[str, float, str]]) -> str:
    """Generate the XML content for the sitemap."""
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]
    
    for filename, priority, changefreq in html_files:
        # Convert file path to URL path
        url_path = filename.replace('\\', '/')  # Handle Windows paths
        
        # Skip index.html from URLs (it's the root)
        if url_path == 'index.html':
            url = BASE_URL + "/"
        else:
            url = BASE_URL + "/" + url_path
            
        xml_lines.extend([
            '        <url>',
            f'            <loc>{url}</loc>',
            f'            <changefreq>{changefreq}</changefreq>',
            f'            <priority>{priority}</priority>',
            '        </url>'
        ])
    
    xml_lines.append('</urlset>')
    return '\n'.join(xml_lines)

def main():
    """Main function to generate sitemap."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    frontend_dir = project_root / "frontend"
    sitemap_path = frontend_dir / "sitemap.xml"
    
    if not frontend_dir.exists():
        print(f"Error: Frontend directory not found: {frontend_dir}")
        return 1
    
    print(f"Scanning {frontend_dir} for HTML files...")
    html_files = scan_html_files(frontend_dir)
    
    if not html_files:
        print("No HTML files found!")
        return 1
    
    print(f"Found {len(html_files)} HTML files:")
    for filename, priority, freq in html_files:
        print(f"  {filename} (priority: {priority}, freq: {freq})")
    
    print(f"\nGenerating sitemap: {sitemap_path}")
    xml_content = generate_sitemap_xml(html_files)
    
    # Write the sitemap
    with open(sitemap_path, 'w', encoding='utf-8') as f:
        f.write(xml_content)
    
    print("✅ Sitemap generated successfully!")
    print(f"📄 {len(html_files)} URLs included")
    print(f"🔗 Base URL: {BASE_URL}")
    
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
