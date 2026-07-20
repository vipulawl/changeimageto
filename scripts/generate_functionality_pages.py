#!/usr/bin/env python3
"""
Generate Functionality Pages (Tool Pages) for Programmatic SEO
Creates interactive tool pages with uploader interface
"""

import csv
import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

FRONTEND_DIR = Path(__file__).parent.parent / 'frontend'

# Existing pages to avoid duplicating
# This is a hardcoded list of protected pages that should never be overwritten
EXISTING_FUNCTIONALITY_PAGES = {
    'remove-background-from-image.html',
    'change-image-background.html',
    'change-image-background-to-white.html',
    'change-image-background-to-black.html',
    'change-image-background-to-blue.html',
    'change-image-background-to-orange.html',
    'change-image-background-to-yellow.html',
    'blur-background.html',
    'upscale-image.html',
    'enhance-image.html',
    'remove-text-from-image.html',
    'remove-people-from-photo.html',
    'change-color-of-image.html',
    'convert-image-format.html',
    'bulk-image-resizer.html',
    'bulk-image-quality-checker.html',
    'real-estate-photo-enhancement.html',
    'remove-background-for-shopify.html',  # Programmatic SEO page
}

def normalize_slug(keyword: str) -> str:
    """Convert keyword to URL-friendly slug"""
    slug = keyword.lower().strip()
    slug = slug.replace(' ', '-')
    slug = slug.replace('_', '-')
    slug = ''.join(c if c.isalnum() or c == '-' else '' for c in slug)
    while '--' in slug:
        slug = slug.replace('--', '-')
    return slug.strip('-')

def get_base_template(page_type: str) -> Optional[str]:
    """Get base template for functionality page"""
    base_templates = {
        'remove_background': 'remove-background-from-image.html',
        'change_background': 'change-image-background.html',
        'change_background_color': 'change-image-background-to-white.html',
        'upscale': 'upscale-image.html',
        'enhance': 'enhance-image.html',
        'blur': 'blur-background.html',
        'remove_text': 'remove-text-from-image.html',
        'remove_people': 'remove-people-from-photo.html',
        'change_color': 'change-color-of-image.html',
        'convert_format': 'convert-image-format.html',
        'bulk_resize': 'bulk-image-resizer.html',
        'image_quality': 'bulk-image-quality-checker.html',
        'real_estate_enhance': 'real-estate-photo-enhancement.html'
    }
    
    template_file = base_templates.get(page_type)
    if not template_file:
        return None
    
    template_path = FRONTEND_DIR / template_file
    if not template_path.exists():
        return None
    
    with open(template_path, 'r', encoding='utf-8') as f:
        return f.read()

def generate_seo_content(keyword: str, page_type: str, variables: Dict) -> str:
    """Generate SEO content section for functionality page"""
    
    use_case = variables.get('use_case', '')
    industry = variables.get('industry', '')
    platform = variables.get('platform', '')
    color = variables.get('color', '')
    color_hex = variables.get('color_hex', '')
    format_type = variables.get('format', '')
    
    # Map page types to action descriptions
    action_descriptions = {
        'remove_background': 'remove backgrounds',
        'change_background': 'change backgrounds',
        'change_background_color': 'change background colors',
        'upscale': 'upscale images',
        'enhance': 'enhance images',
        'blur': 'blur backgrounds',
        'remove_text': 'remove text from images',
        'remove_people': 'remove people from photos',
        'change_color': 'change image colors',
        'convert_format': 'convert image formats',
        'bulk_resize': 'resize images in bulk',
        'image_quality': 'check image quality',
        'real_estate_enhance': 'enhance real estate photos'
    }
    
    action_desc = action_descriptions.get(page_type, 'edit images')
    
    # Build use case description
    use_case_desc = ""
    if platform:
        use_case_desc = f"for {platform} listings"
    elif industry:
        use_case_desc = f"for {industry}"
    elif use_case:
        use_case_desc = f"for {use_case}"
    
    content = f"""
      <section class="seo">
        <h2>How to {keyword.title()}</h2>
        <p>Our free online tool makes it easy to {keyword}{use_case_desc}. Simply upload your image above and click the process button. No signup required, no watermarks, completely free.</p>
        
        <h3>Why {keyword.title()}?</h3>
        <ul>
"""
    
    if platform:
        content += f"""
          <li><strong>{platform} Listings</strong>: Professional product photos with clean backgrounds are essential for {platform} success. Our tool helps you create consistent, high-quality images that stand out in search results and increase conversion rates.</li>
          <li><strong>Fast Processing</strong>: Process images in seconds, not minutes. Perfect for bulk editing {platform} product photos.</li>
          <li><strong>No Software Required</strong>: Works directly in your browser - no downloads, no installations, no subscriptions.</li>
"""
    elif industry:
        content += f"""
          <li><strong>{industry.title()} Industry</strong>: Professional image editing is crucial in {industry}. Our tool helps you create polished, professional images that represent your brand well.</li>
          <li><strong>Cost-Effective</strong>: Save money on expensive software or professional services. Our free tool provides professional-quality results.</li>
          <li><strong>Easy to Use</strong>: No technical skills required. Upload, process, download - it's that simple.</li>
"""
    else:
        content += f"""
          <li><strong>Professional Results</strong>: Get high-quality, professional image editing results in seconds.</li>
          <li><strong>Free Forever</strong>: No hidden costs, no subscriptions, no watermarks.</li>
          <li><strong>Works Everywhere</strong>: Compatible with all devices and browsers.</li>
"""
    
    content += """
        </ul>
        
        <h3>Step-by-Step Guide</h3>
        <ol>
          <li><strong>Upload Your Image</strong>: Click the upload area above or drag and drop your image file.</li>
          <li><strong>Select Photo Type</strong>: Choose "Product/Object" or "Portrait/People" for best results.</li>
          <li><strong>Process</strong>: Click the process button and wait a few seconds.</li>
          <li><strong>Download</strong>: Preview your result and download the processed image.</li>
        </ol>
        
        <h3>Tips for Best Results</h3>
        <ul>
          <li>Use high-resolution images (minimum 800x800 pixels)</li>
          <li>Ensure good contrast between subject and background</li>
          <li>For product photos, use consistent lighting</li>
          <li>Save as PNG format to preserve transparency</li>
        </ul>
        
        <h3>Frequently Asked Questions</h3>
        <div itemscope itemtype="https://schema.org/FAQPage">
          <div itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
            <h4 itemprop="name">Is this tool free to use?</h4>
            <div itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">
              <p itemprop="text">Yes, our tool is completely free with no signup required and no watermarks.</p>
            </div>
          </div>
          <div itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
            <h4 itemprop="name">What image formats are supported?</h4>
            <div itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">
              <p itemprop="text">We support JPG, PNG, and WebP formats for input. Output is always PNG format.</p>
            </div>
          </div>
          <div itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
            <h4 itemprop="name">How long does processing take?</h4>
            <div itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">
              <p itemprop="text">Most images are processed in 2-5 seconds, depending on image size.</p>
            </div>
          </div>
        </div>
        
        <h3>Related Tools</h3>
        <ul>
          <li><a href="/remove-background-from-image.html">Remove Background from Image</a></li>
          <li><a href="/change-image-background.html">Change Image Background</a></li>
          <li><a href="/upscale-image.html">Upscale Image</a></li>
          <li><a href="/enhance-image.html">Enhance Image</a></li>
          <li><a href="/blur-background.html">Blur Background</a></li>
          <li><a href="/remove-text-from-image.html">Remove Text from Image</a></li>
          <li><a href="/remove-people-from-photo.html">Remove People from Photo</a></li>
          <li><a href="/change-color-of-image.html">Change Color of Image</a></li>
          <li><a href="/convert-image-format.html">Convert Image Format</a></li>
          <li><a href="/bulk-image-resizer.html">Bulk Image Resizer</a></li>
        </ul>
      </section>
"""
    
    return content

def customize_functionality_page(template: str, keyword: str, page_type: str, variables: Dict) -> str:
    """Customize functionality page template with keyword data"""
    
    slug = normalize_slug(keyword)
    title = keyword.title()
    
    # Generate meta description
    use_case = variables.get('use_case', '')
    platform = variables.get('platform', '')
    meta_desc = f"Free tool to {keyword.lower()}. Fast, no signup, no watermark. Perfect {use_case or platform or 'for your images'}."
    if len(meta_desc) > 160:
        meta_desc = f"Free tool to {keyword.lower()}. Fast, no signup required."
    
    # Update title tag
    template = re.sub(
        r'<title>.*?</title>',
        f'<title>{title} | Free Online Tool</title>',
        template,
        flags=re.DOTALL
    )
    
    # Update meta description
    template = re.sub(
        r'<meta name="description" content=".*?"',
        f'<meta name="description" content="{meta_desc}"',
        template
    )
    
    # Update canonical URL
    template = re.sub(
        r'<link rel="canonical" href="[^"]*"',
        f'<link rel="canonical" href="https://www.changeimageto.com/{slug}.html"',
        template
    )
    
    # Update H1
    template = re.sub(
        r'<h1[^>]*>.*?</h1>',
        f'<h1 style="margin:0">{title} (Free)</h1>',
        template,
        flags=re.DOTALL
    )
    
    # Update header description
    template = re.sub(
        r'<p>Upload.*?</p>',
        f'<p>Upload a photo and {keyword.lower()}. Free, no login, no watermark.</p>',
        template,
        flags=re.DOTALL
    )
    
    # Add or update SEO content section
    seo_content = generate_seo_content(keyword, page_type, variables)
    
    # Check if SEO section exists, if not add before footer
    if '<section class="seo">' not in template:
        # Find footer and insert before it
        template = template.replace('</main>', seo_content + '\n    </main>')
    else:
        # Replace existing SEO section
        template = re.sub(
            r'<section class="seo">.*?</section>',
            seo_content.strip(),
            template,
            flags=re.DOTALL
        )
    
    # Update data-target-color if it's a color-specific page
    if variables.get('color_hex'):
        template = re.sub(
            r'data-target-color="[^"]*"',
            f'data-target-color="{variables["color_hex"]}"',
            template
        )
    
    return template

def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate functionality pages for programmatic SEO')
    parser.add_argument('--csv', default='programmatic_seo_keywords.csv',
                       help='CSV file with keywords')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be created without actually creating')
    parser.add_argument('--limit', type=int, default=None,
                       help='Limit number of pages to generate')
    
    args = parser.parse_args()
    
    csv_path = Path(__file__).parent / args.csv
    if not csv_path.exists():
        print(f"❌ CSV file not found: {csv_path}")
        return
    
    # Read keywords
    keywords = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('page_type') == 'functionality':
                # Parse variables
                variables = {}
                if row.get('variables'):
                    try:
                        variables = json.loads(row['variables'])
                    except json.JSONDecodeError:
                        pass
                
                keywords.append({
                    'keyword': row['keyword'],
                    'page_type': row.get('page_type_category', 'remove_background'),
                    'category': row['category'],
                    'variables': variables
                })
    
    if args.limit:
        keywords = keywords[:args.limit]
    
    print(f"📝 Generating {len(keywords)} functionality pages...")
    print()
    
    created = 0
    skipped = 0
    
    for kw_data in keywords:
        slug = normalize_slug(kw_data['keyword'])
        filename = f"{slug}.html"
        file_path = FRONTEND_DIR / filename
        
        # Check if already exists (hardcoded list)
        if filename in EXISTING_FUNCTIONALITY_PAGES:
            print(f"⚠️  Skipping (in protected list): {filename}")
            skipped += 1
            continue
        
        # Check if file actually exists in filesystem
        if file_path.exists():
            print(f"⚠️  Skipping (file already exists): {filename}")
            skipped += 1
            continue
        
        if args.dry_run:
            print(f"Would create: {filename}")
            print(f"  Keyword: {kw_data['keyword']}")
            print(f"  Page Type: {kw_data['page_type']}")
            print()
            continue
        
        # Get base template
        base_template = get_base_template(kw_data['page_type'])
        if not base_template:
            print(f"❌ No template found for: {kw_data['page_type']}")
            skipped += 1
            continue
        
        # Customize template
        customized = customize_functionality_page(
            base_template,
            kw_data['keyword'],
            kw_data['page_type'],
            kw_data['variables']
        )
        
        # Write file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(customized)
        
        print(f"✅ Created: {filename}")
        created += 1
    
    print()
    print(f"✅ Created: {created}")
    print(f"⚠️  Skipped: {skipped}")
    print()
    print("Next steps:")
    print("1. Review generated pages in frontend/ directory")
    print("2. Test functionality on each page")
    print("3. Update sitemap.xml")
    print("4. Submit to Google Search Console")

if __name__ == '__main__':
    main()

