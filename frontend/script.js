console.log('script.js: init');
try {
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const processBtn = document.getElementById('process-btn');
const categoryInput = document.getElementById('category');
const catBtns = document.querySelectorAll('.cat-btn');
catBtns.forEach(btn=>{btn.addEventListener('click',()=>{catBtns.forEach(b=>b.classList.remove('active'));btn.classList.add('active'); if(categoryInput){ categoryInput.value=btn.dataset.cat; }});});
const previewSection = document.getElementById('preview-section');
const originalImg = document.getElementById('original-img');
const resultImg = document.getElementById('result-img');
const downloadLink = document.getElementById('download-link');
const resetBtn = document.getElementById('reset-btn');

let currentFile = null;

// Analytics logging functions
function logUserAction(action, details = {}) {
    const logEntry = {
        timestamp: new Date().toISOString(),
        action: action,
        page: window.location.pathname,
        userAgent: navigator.userAgent,
        details: details
    };
    
    // Send to backend analytics endpoint (local dev uses 8000)
    const isLocalFrontend = (['127.0.0.1','localhost'].includes(window.location.hostname)) && window.location.port === '8080';
    const analyticsBase = window.API_BASE || (isLocalFrontend ? 'http://127.0.0.1:8000' : 'https://bgremover-backend-121350814881.us-central1.run.app');
    fetch(analyticsBase + '/api/analytics', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(logEntry)
    }).catch(() => { /* ignore analytics failures */ });
}

// Google Analytics event tracking with fallback
function trackGAEvent(eventName, eventParams) {
    // Ensure dataLayer exists (GA will read from this even if gtag isn't ready)
    window.dataLayer = window.dataLayer || [];
    
    // Build the event object in the format GA expects
    const eventData = {
        'event': eventName,
        'event_category': eventParams.event_category || 'engagement',
        'event_label': eventParams.event_label || '',
        'value': eventParams.value || 1
    };
    
    // Add any additional parameters
    if (eventParams.page_path) eventData.page_path = eventParams.page_path;
    if (eventParams.page_title) eventData.page_title = eventParams.page_title;
    
    // Always push to dataLayer - GA will process it when ready
    window.dataLayer.push(eventData);
    console.log('GA Event queued:', eventName, eventData);
    
    // Also try to use gtag if available (for immediate processing)
    if (typeof window.gtag === 'function') {
        try {
            window.gtag('event', eventName, eventParams);
            console.log('GA Event also sent via gtag:', eventName);
        } catch (e) {
            console.warn('gtag call failed, but event is in dataLayer:', e);
        }
    } else {
        // gtag not available - that's OK, dataLayer push is sufficient
        // GA will process dataLayer events when it loads
        console.log('gtag not available, event queued in dataLayer (will be processed when GA loads)');
    }
}

// Setup download link tracking once on page load
function setupDownloadTracking() {
  const downloadLink = document.getElementById('download-link');
  if (downloadLink && !downloadLink.dataset.gaTracked) {
    downloadLink.dataset.gaTracked = 'true';
    downloadLink.addEventListener('click', function() {
      const pageType = getPageType();
      const actionType = pageType === 'upscale_image' ? 'upscale' :
                        pageType === 'color_change' ? 'color_change' :
                        pageType === 'blur_background' ? 'blur_background' :
                        pageType === 'grayscale_background' ? 'grayscale_background' :
                        pageType === 'enhance_image' ? 'enhance' :
                        pageType === 'remove_text' ? 'remove_text' :
                        pageType === 'edit_text' ? 'edit_text' :
                        pageType === 'remove_gemini_watermark' ? 'remove_gemini_watermark' :
                        'remove_background';
      
      // Track in Google Analytics
      trackGAEvent('download_image', {
        'event_category': 'engagement',
        'event_label': actionType,
        'page_path': window.location.pathname,
        'page_title': document.title,
        'value': 1
      });
      
      // Also log to backend analytics
      logUserAction('download_clicked', {
        page_type: pageType,
        action_type: actionType,
        filename: downloadLink ? (downloadLink.download || 'unknown') : 'unknown'
      });
    });
  }
}

// Log page visit on load
document.addEventListener('DOMContentLoaded', function() {
    console.log('script.js: DOMContentLoaded');
    const pageType = getPageType();
    logUserAction('page_visit', {
        page_type: pageType,
        url: window.location.href,
        referrer: document.referrer || 'direct'
    });
    
    // Setup download tracking
    setupDownloadTracking();

    // Inject standard footer link pills across all pages
    try {
      const footer = document.querySelector('footer.container.footer');
      if (footer) {
        const existing = document.querySelector('nav.seo-links');
        if (!existing) {
          const nav = document.createElement('nav');
          nav.className = 'seo-links';
          nav.innerHTML = [
            { href: '/remove-background-from-image.html', label: 'Remove Background from Image' },
            { href: '/enhance-image.html', label: 'Enhance Image' },
            { href: '/upscale-image.html', label: 'AI Image Upscaler' },
            { href: '/remove-text-from-image.html', label: 'Remove Text from Image' },
            { href: '/remove-people-from-photo.html', label: 'Remove People from Photos' },
            { href: '/change-color-of-image.html', label: 'Change color of image online' },
            { href: '/change-image-background.html', label: 'Change image background' },
            { href: '/convert-image-format.html', label: 'Convert image format' },
            { href: '/blur-background.html', label: 'Blur Background' },
        { href: '/grayscale-background.html', label: 'Black & White Image Background' },
            { href: '/bulk-image-resizer.html', label: 'Bulk Image Resizer' },
            { href: '/image-quality-checker.html', label: 'Image Quality Checker' },
          ].map(i => `<a href="${i.href}">${i.label}</a>`).join('');
          // Insert right before footer to match existing structure
          footer.parentElement.insertBefore(nav, footer);
        }
      }
    } catch (e) {
      console.warn('Footer injection failed:', e);
    }

    // Ensure the footer "Image Tools" list is consistent across all pages
    try {
      const toolLinks = [
        { href: '/remove-background-from-image.html', label: 'Remove Background' },
        { href: '/change-image-background.html', label: 'Change Background' },
        { href: '/change-color-of-image.html', label: 'Change Image Colors' },
        { href: '/upscale-image.html', label: 'Upscale Image' },
        { href: '/enhance-image.html', label: 'Enhance Image' },
        { href: '/blur-background.html', label: 'Blur Background' },
        { href: '/grayscale-background.html', label: 'Black & White Image Background' },
        { href: '/convert-image-format.html', label: 'Convert Image Format' },
        { href: '/remove-people-from-photo.html', label: 'Remove People / Objects' },
        { href: '/remove-text-from-image.html', label: 'Remove Text / Watermark' },
        { href: '/bulk-image-resizer.html', label: 'Bulk Image Resizer' },
        { href: '/image-quality-checker.html', label: 'Image Quality Checker' }
      ];

      // Find any footer section with heading "Image Tools" and update the following UL
      const headings = Array.from(document.querySelectorAll('footer .footer-heading'));
      const imgToolsHeading = headings.find(h => (h.textContent || '').trim().toLowerCase() === 'image tools');
      if (imgToolsHeading) {
        const ul = imgToolsHeading.nextElementSibling;
        if (ul && ul.classList && ul.classList.contains('footer-links')) {
          ul.innerHTML = toolLinks.map(l => `<li><a href="${l.href}">${l.label}</a></li>`).join('');
        }
      }
    } catch (e) {
      console.warn('Footer tools update failed:', e);
    }
});

function getPageType() {
    const path = window.location.pathname;
    if (path === '/' || path === '/index.html') return 'remove_background';
    if (path.includes('change-image-background-to-')) return 'color_specific';
    if (path === '/change-image-background.html') return 'color_palette';
    if (path === '/change-color-of-image.html') return 'color_change';
    if (path === '/convert-image-format.html') return 'convert_format';
    if (path === '/upscale-image.html') return 'upscale_image';
    if (path === '/blur-background.html') return 'blur_background';
    if (path === '/grayscale-background.html') return 'grayscale_background';
    if (path === '/enhance-image.html') return 'enhance_image';
  if (path === '/remove-text-from-image.html') return 'remove_text';
  if (path === '/remove-people-from-photo.html') return 'remove_people';
  if (path === '/remove-gemini-watermark.html') return 'remove_gemini_watermark';
  if (path === '/convert-image-to-pdf.html') return 'image_to_pdf';
  if (path === '/convert-image-to-text.html') return 'image_to_text';
  
  return 'unknown';
}

function updateCtaText(){
  if(!processBtn) return;
  const isColorPage = !!document.body.getAttribute('data-target-color') || !!document.getElementById('color-palette');
  const isColorChangePage = window.location.pathname === '/change-color-of-image.html';
  const isUpscalePage = window.location.pathname === '/upscale-image.html';
  const isBlurPage = window.location.pathname === '/blur-background.html';
  const isGrayscalePage = window.location.pathname === '/grayscale-background.html';
  const isEnhancePage = window.location.pathname === '/enhance-image.html';
  const isRemoveTextPage = window.location.pathname === '/remove-text-from-image.html';
  const isRemovePeoplePage = window.location.pathname === '/remove-people-from-photo.html';
  const isChristmasBgPage = window.location.pathname === '/add-christmas-background.html';
  const isEditTextPage = false;
  const isRemoveGeminiWatermarkPage = window.location.pathname === '/remove-gemini-watermark.html';
  const isDenoisePage = false;
  
  if (isEditTextPage) {
    return;
  }
  
  // Don't update button text on convert-image-to-pdf, convert-image-to-text, metadata pages, or remove-gemini-watermark pages - they have their own inline scripts
  if (window.location.pathname === '/convert-image-to-pdf.html' || 
      window.location.pathname === '/convert-image-to-text.html' ||
      window.location.pathname === '/view-metadata.html' ||
      window.location.pathname === '/edit-metadata.html' ||
      window.location.pathname === '/remove-metadata.html' ||
      isRemoveGeminiWatermarkPage) {
    return;
  }
  
  if (isColorChangePage) {
    processBtn.textContent = 'Change Image Color';
    processBtn.setAttribute('aria-label', 'Change Image Color');
  } else if (isUpscalePage) {
    processBtn.textContent = 'Upscale Image';
    processBtn.setAttribute('aria-label', 'Upscale Image');
  } else if (isBlurPage) {
    processBtn.textContent = 'Blur Background';
    processBtn.setAttribute('aria-label', 'Blur Background');
  } else if (isEnhancePage) {
    processBtn.textContent = 'Enhance Image';
    processBtn.setAttribute('aria-label', 'Enhance Image');
  } else if (isRemoveTextPage) {
    processBtn.textContent = 'Remove Text';
    processBtn.setAttribute('aria-label', 'Remove Text');
  } else if (isRemovePeoplePage) {
    processBtn.textContent = 'Remove Painted Areas';
    processBtn.setAttribute('aria-label', 'Remove Painted Areas');
  } else if (isChristmasBgPage) {
    processBtn.textContent = 'Generate Christmas Photo';
    processBtn.setAttribute('aria-label', 'Generate Christmas Photo');
  } else if (isColorPage) {
    processBtn.textContent = 'Change image background';
    processBtn.setAttribute('aria-label', 'Change image background');
  } else {
    processBtn.textContent = 'Remove Background';
    processBtn.setAttribute('aria-label', 'Remove Background');
  }
}
function updatePromptText(){
  const prompt = document.getElementById('process-prompt');
  if(!prompt) return;
  const isColorPage = !!document.body.getAttribute('data-target-color') || !!document.getElementById('color-palette');
  const isColorChangePage = window.location.pathname === '/change-color-of-image.html';
  const isUpscalePage = window.location.pathname === '/upscale-image.html';
  const isBlurPage = window.location.pathname === '/blur-background.html';
  const isGrayscalePage = window.location.pathname === '/grayscale-background.html';
  const isEnhancePage = window.location.pathname === '/enhance-image.html';
  const isRemoveTextPage = window.location.pathname === '/remove-text-from-image.html';
  const isRemovePeoplePage = window.location.pathname === '/remove-people-from-photo.html';
  const isChristmasBgPage = window.location.pathname === '/add-christmas-background.html';
  const isRemoveGeminiWatermarkPage = window.location.pathname === '/remove-gemini-watermark.html';
  const isDenoisePage = false;
  
  // Don't update prompt text on pages with inline scripts
  if (window.location.pathname === '/convert-image-to-pdf.html' || 
      window.location.pathname === '/convert-image-to-text.html' ||
      window.location.pathname === '/view-metadata.html' ||
      window.location.pathname === '/edit-metadata.html' ||
      window.location.pathname === '/remove-metadata.html' ||
      isRemoveGeminiWatermarkPage) {
    return;
  }
  
  if (isColorChangePage) {
    prompt.textContent = 'Press "Change Image Color" to process.';
  } else if (isUpscalePage) {
    prompt.textContent = 'Press "Upscale Image" to process.';
  } else if (isBlurPage) {
    prompt.textContent = 'Press "Blur Background" to process.';
  } else if (isEnhancePage) {
    prompt.textContent = 'Press "Enhance Image" to process.';
  } else if (isRemoveTextPage) {
    prompt.textContent = 'Press "Remove Text" to process.';
  } else if (isRemovePeoplePage) {
    prompt.textContent = 'Paint over people to remove, then press "Remove Painted Areas".';
  } else if (isChristmasBgPage) {
    prompt.textContent = 'Press "Generate Christmas Photo" to process.';
  } else if (isColorPage) {
    prompt.textContent = 'Press "Change image background" to process.';
  } else {
    prompt.textContent = 'Press "Remove Background" to process.';
  }
}
updateCtaText();

// Color change page specific functionality
if (window.location.pathname === '/change-color-of-image.html') {
    // Wait for DOM to be ready
    document.addEventListener('DOMContentLoaded', function() {
        // Initialize sliders
        const hueSlider = document.getElementById('hue-slider');
        const saturationSlider = document.getElementById('saturation-slider');
        const brightnessSlider = document.getElementById('brightness-slider');
        const contrastSlider = document.getElementById('contrast-slider');
        
        const hueValue = document.getElementById('hue-value');
        const saturationValue = document.getElementById('saturation-value');
        const brightnessValue = document.getElementById('brightness-value');
        const contrastValue = document.getElementById('contrast-value');
        
        console.log('Sliders found:', { hueSlider, saturationSlider, brightnessSlider, contrastSlider });
        console.log('Value elements found:', { hueValue, saturationValue, brightnessValue, contrastValue });
        
        // Update slider values
        if (hueSlider && hueValue) {
            hueSlider.addEventListener('input', () => {
                hueValue.textContent = hueSlider.value + '°';
                console.log('Hue changed to:', hueSlider.value);
            });
        }
        
        if (saturationSlider && saturationValue) {
            saturationSlider.addEventListener('input', () => {
                saturationValue.textContent = saturationSlider.value + '%';
                console.log('Saturation changed to:', saturationSlider.value);
            });
        }
        
        if (brightnessSlider && brightnessValue) {
            brightnessSlider.addEventListener('input', () => {
                brightnessValue.textContent = brightnessSlider.value + '%';
                console.log('Brightness changed to:', brightnessSlider.value);
            });
        }
        
        if (contrastSlider && contrastValue) {
            contrastSlider.addEventListener('input', () => {
                contrastValue.textContent = contrastSlider.value + '%';
                console.log('Contrast changed to:', contrastSlider.value);
            });
        }
    
        // Preset buttons functionality
        const presetButtons = document.querySelectorAll('.preset-btn');
        presetButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                const preset = btn.dataset.preset;
                applyPreset(preset);
                
                // Update active state
                presetButtons.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            });
        });
        
        function applyPreset(preset) {
            switch(preset) {
                case 'vibrant':
                    hueSlider.value = 0;
                    saturationSlider.value = 150;
                    brightnessSlider.value = 110;
                    contrastSlider.value = 120;
                    break;
                case 'muted':
                    hueSlider.value = 0;
                    saturationSlider.value = 50;
                    brightnessSlider.value = 90;
                    contrastSlider.value = 80;
                    break;
                case 'warm':
                    hueSlider.value = 30;
                    saturationSlider.value = 120;
                    brightnessSlider.value = 110;
                    contrastSlider.value = 110;
                    break;
                case 'cool':
                    hueSlider.value = -30;
                    saturationSlider.value = 120;
                    brightnessSlider.value = 110;
                    contrastSlider.value = 110;
                    break;
                case 'grayscale':
                    hueSlider.value = 0;
                    saturationSlider.value = 0;
                    brightnessSlider.value = 100;
                    contrastSlider.value = 120;
                    break;
                case 'reset':
                    hueSlider.value = 0;
                    saturationSlider.value = 100;
                    brightnessSlider.value = 100;
                    contrastSlider.value = 100;
                    break;
            }
            
            // Update display values
            hueValue.textContent = hueSlider.value + '°';
            saturationValue.textContent = saturationSlider.value + '%';
            brightnessValue.textContent = brightnessSlider.value + '%';
            contrastValue.textContent = contrastSlider.value + '%';
        }
    });
}

function enableProcess(enabled){
  if(processBtn) processBtn.disabled = !enabled;
}

function setOriginalPreview(file){
  if (!originalImg) return; // Skip if element doesn't exist (e.g., on convert-image-to-pdf page)
  const reader = new FileReader();
  reader.onload = () => {
    originalImg.src = reader.result;
    originalImg.style.display = 'block';
    if (previewSection) {
      previewSection.hidden = false;
    }
    
    // Hide upload prompt and show image
    const uploadPrompt = document.getElementById('upload-prompt');
    if (uploadPrompt) uploadPrompt.style.display = 'none';
    
    // Show the image columns
    const imageColumns = document.querySelectorAll('.image-column');
    imageColumns.forEach(col => col.style.display = 'block');
    
    if (resultImg) resultImg.src = '';
    if (downloadLink) downloadLink.removeAttribute('href');
    const prompt = document.getElementById('process-prompt');
    if(prompt) prompt.style.display = 'block';
    const resultWrap = document.getElementById('result-wrapper');
    if(resultWrap) resultWrap.hidden = true;
    
    // Log file upload
    logUserAction('file_uploaded', {
      filename: file.name,
      file_size: file.size,
      file_type: file.type,
      page_type: getPageType()
    });
  };
  reader.readAsDataURL(file);
}

if (dropzone) {
  dropzone.addEventListener('click', () => { 
    const fi = document.getElementById('file-input');
    console.log('dropzone click', !!fi);
    if (fi) {
      if (typeof fi.showPicker === 'function') {
        try { fi.showPicker(); return; } catch(_) {}
      }
      fi.click();
    }
  });
  dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag');
    if(e.dataTransfer.files && e.dataTransfer.files[0]){
      console.log('drop event got file');
      currentFile = e.dataTransfer.files[0];
      setOriginalPreview(currentFile);
      enableProcess(true);
    }
  });
}

// Fallback: delegate click anywhere inside the uploader to open the file chooser
document.addEventListener('click', (e) => {
  const dz = e.target.closest && e.target.closest('#dropzone');
  if (dz && fileInput) {
    e.preventDefault();
    fileInput.click();
  }
});

if (fileInput) {
  fileInput.addEventListener('change', (e) => {
    console.log('fileInput change fired');
    const file = e.target.files[0];
    if(file){
      currentFile = file;
      setOriginalPreview(currentFile);
      enableProcess(true);
    }
  });
}

// Defensive: also listen for change events bubbled from any #file-input that might be re-rendered
document.addEventListener('change', (e) => {
  const target = e.target;
  if (target && target.id === 'file-input' && target.files && target.files[0]) {
    console.log('file-input delegated change');
    currentFile = target.files[0];
    setOriginalPreview(currentFile);
    enableProcess(true);
  }
});


const form = document.getElementById('upload-form');
// If on a color page, rename CTA
(function(){ const color = document.body.getAttribute('data-target-color'); if(color){ updateCtaText(); processBtn.setAttribute('aria-label','Change image background'); } })();
if (form) form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if(!currentFile) return;

  // Get page type and action details for tracking
  const pageType = getPageType();
  const targetColor = document.body.getAttribute('data-target-color') || document.getElementById('bg-color')?.value;
  
  // Determine action type for GA tracking
  let actionType = 'remove_background'; // default
  let actionLabel = 'remove_background';
  
  if (pageType === 'color_change') {
    actionType = 'change_image_color';
    actionLabel = 'change_image_color';
  } else if (pageType === 'upscale_image') {
    actionType = 'upscale_image';
    actionLabel = 'upscale';
  } else if (pageType === 'blur_background') {
    actionType = 'blur_background';
    actionLabel = 'blur_background';
  } else if (pageType === 'enhance_image') {
    actionType = 'enhance_image';
    actionLabel = 'enhance';
  } else if (pageType === 'remove_text') {
    actionType = 'remove_text';
    actionLabel = 'remove_text';
  } else if (pageType === 'remove_people') {
    actionType = 'remove_people';
    actionLabel = 'remove_people';
  } else if (pageType === 'remove_gemini_watermark') {
    actionType = 'remove_gemini_watermark';
    actionLabel = 'remove_gemini_watermark';
  } else if (targetColor) {
    actionType = 'change_background';
    actionLabel = 'change_background';
  }
  
  // Track CTA click in Google Analytics
  trackGAEvent('process_image_cta', {
    'event_category': 'engagement',
    'event_label': actionLabel,
    'page_path': window.location.pathname,
    'page_title': document.title,
    'value': 1
  });
  
  enableProcess(false);
  processBtn.textContent = 'Processing…';

  // Log processing start
  let logDetails = {
    page_type: pageType,
    filename: currentFile.name,
    file_size: currentFile.size
  };
  
  if (pageType === 'color_change') {
    logDetails.action_type = 'change_image_color';
    logDetails.color_type = 'hue';
    logDetails.hue_shift = document.getElementById('hue-slider')?.value || 0;
    logDetails.saturation = document.getElementById('saturation-slider')?.value || 100;
    logDetails.brightness = document.getElementById('brightness-slider')?.value || 100;
    logDetails.contrast = document.getElementById('contrast-slider')?.value || 100;
  } else if (pageType === 'upscale_image') {
    logDetails.action_type = 'upscale_image';
    logDetails.scale_factor = document.getElementById('scale-factor')?.value || '2';
    logDetails.method = document.getElementById('upscale-method')?.value || 'lanczos';
  } else if (pageType === 'blur_background') {
    logDetails.action_type = 'blur_background';
    logDetails.blur_radius = document.getElementById('blur-radius')?.value || '12';
  } else if (pageType === 'enhance_image') {
    logDetails.action_type = 'enhance_image';
    logDetails.preset = document.getElementById('enhance-preset')?.value || 'balanced';
  } else if (pageType === 'remove_text') {
    logDetails.action_type = 'remove_text';
  
  } else {
    logDetails.category = categoryInput?.value || 'unknown';
    logDetails.target_color = targetColor;
    logDetails.action_type = targetColor ? 'change_background' : 'remove_background';
  }
  
  logUserAction('processing_started', logDetails);

  try{
    const body = new FormData();
    body.append('file', currentFile);
    
    const isLocalFrontend = (['127.0.0.1','localhost'].includes(window.location.hostname)) && window.location.port === '8080';
    const apiBase = window.API_BASE || (isLocalFrontend ? 'http://127.0.0.1:8000' : 'https://bgremover-backend-121350814881.us-central1.run.app');
    let endpoint = '/api/remove-bg';
    
    console.log('API Base:', apiBase);
    console.log('Current location:', window.location.hostname, window.location.port);
    
    // Check if this is a color change page
    if (window.location.pathname === '/change-color-of-image.html') {
      endpoint = '/api/change-color';
      // Always use 'hue' as color_type since we're doing comprehensive color adjustment
      const hueSlider = document.getElementById('hue-slider');
      const saturationSlider = document.getElementById('saturation-slider');
      const brightnessSlider = document.getElementById('brightness-slider');
      const contrastSlider = document.getElementById('contrast-slider');
      
      console.log('Slider elements found:', {
        hueSlider: !!hueSlider,
        saturationSlider: !!saturationSlider,
        brightnessSlider: !!brightnessSlider,
        contrastSlider: !!contrastSlider
      });
      
      const hueShift = hueSlider?.value || 0;
      const saturation = saturationSlider?.value || 100;
      const brightness = brightnessSlider?.value || 100;
      const contrast = contrastSlider?.value || 100;
      
      console.log('Color change values:', { hueShift, saturation, brightness, contrast });
      
      body.append('color_type', 'hue');
      body.append('hue_shift', hueShift);
      body.append('saturation', saturation);
      body.append('brightness', brightness);
      body.append('contrast', contrast);
      
      console.log('FormData contents:');
      for (let [key, value] of body.entries()) {
        console.log(key, ':', value);
      }
    } else if (window.location.pathname === '/upscale-image.html') {
      // Upscaling page
      endpoint = '/api/upscale-image';
      const scaleFactor = document.getElementById('scale-factor')?.value || '2';
      const method = document.getElementById('upscale-method')?.value || 'lanczos';
      
      console.log('Upscaling values:', { scaleFactor, method });
      
      body.append('scale_factor', scaleFactor);
      body.append('method', method);
      
      console.log('FormData contents:');
      for (let [key, value] of body.entries()) {
        console.log(key, ':', value);
      }
    } else if (window.location.pathname === '/blur-background.html') {
      endpoint = '/api/blur-background';
      const radius = document.getElementById('blur-radius')?.value || '12';
      body.append('blur_radius', radius);
    } else if (window.location.pathname === '/grayscale-background.html') {
      endpoint = '/api/grayscale-background';
      body.append('category', categoryInput?.value || 'portrait');
    } else if (window.location.pathname === '/enhance-image.html') {
      endpoint = '/api/enhance-image';
      const preset = document.getElementById('enhance-preset')?.value || 'balanced';
      // Map preset to parameters server expects
      if (preset === 'soft') { body.append('sharpen', '1.0'); body.append('contrast', '105'); body.append('brightness', '100'); }
      if (preset === 'balanced') { body.append('sharpen', '1.3'); body.append('contrast', '110'); body.append('brightness', '102'); }
      if (preset === 'strong') { body.append('sharpen', '1.6'); body.append('contrast', '120'); body.append('brightness', '105'); }
      // Add de-blur and denoise parameters
      body.append('deblur', document.getElementById('deblur')?.checked !== false ? 'true' : 'false');
      body.append('denoise', document.getElementById('denoise')?.checked !== false ? 'true' : 'false');
      body.append('deblur_strength', document.getElementById('deblur-strength')?.value || '1.5');
      body.append('denoise_strength', document.getElementById('denoise-strength')?.value || '1.0');
    } else if (window.location.pathname === '/remove-text-from-image.html') {
      endpoint = '/api/remove-text';
    } else if (window.location.pathname === '/remove-gemini-watermark.html') {
      endpoint = '/api/remove-gemini-watermark';
    
    } else {
      // Background removal or background change
      body.append('category', categoryInput?.value || 'product');
      
      // Check for background image (Christmas backgrounds or custom)
      if (window.customBackgroundFile) {
        body.append('background_image', window.customBackgroundFile);
        // Add position and scale parameters if available (for Christmas background page)
        const foregroundX = document.getElementById('foreground-x');
        const foregroundY = document.getElementById('foreground-y');
        const foregroundScale = document.getElementById('foreground-scale');
        const backgroundBlur = document.getElementById('background-blur');
        if (foregroundX && foregroundX.value) body.append('foreground_x', foregroundX.value);
        if (foregroundY && foregroundY.value) body.append('foreground_y', foregroundY.value);
        if (foregroundScale && foregroundScale.value) body.append('foreground_scale', foregroundScale.value);
        if (backgroundBlur && backgroundBlur.value) body.append('background_blur', backgroundBlur.value);
      } else {
        // Check for solid color background
      var hidden = document.getElementById('bg-color');
      if(hidden) body.append('bg_color', hidden.value);
      // If a swatch is active, prefer that color
      var activeSwatch = document.querySelector('#color-palette .swatch.active');
      if(activeSwatch){ body.append('bg_color', activeSwatch.getAttribute('data-color')); }
      var pageColor = document.body.getAttribute('data-target-color');
      if(pageColor) body.append('bg_color', pageColor);
      }
    }

        console.log('Making API call to:', apiBase + endpoint);
        const res = await fetch(apiBase + endpoint, { method: 'POST', body });
        console.log('API response status:', res.status);
        console.log('API response headers:', res.headers);
        if(!res.ok){
          const err = await res.text();
          console.error('API error:', err);
          throw new Error(err || 'Failed to process image');
        }
        const blob = await res.blob();
        console.log('Response blob size:', blob.size, 'bytes');
        const objectUrl = URL.createObjectURL(blob);
        if (resultImg) resultImg.src = objectUrl;
        if (downloadLink) {
        downloadLink.href = objectUrl;
        // Set a sensible filename based on page type
        if (pageType === 'upscale_image') {
          downloadLink.download = `upscaled-${Date.now()}.png`;
        } else if (pageType === 'color_change') {
          downloadLink.download = `color-changed-${Date.now()}.png`;
        } else if (pageType === 'blur_background') {
          downloadLink.download = `blur-background-${Date.now()}.png`;
          } else if (pageType === 'grayscale_background') {
            downloadLink.download = `grayscale-background-${Date.now()}.png`;
        } else if (pageType === 'enhance_image') {
          downloadLink.download = `enhanced-${Date.now()}.png`;
        } else if (pageType === 'remove_text') {
          downloadLink.download = `text-removed-${Date.now()}.png`;
          } else if (pageType === 'edit_text') {
            downloadLink.download = `text-edited-${Date.now()}.png`;
        } else {
          downloadLink.download = `bg-removed-${Date.now()}.png`;
          }
        }
        
        // Ensure download tracking is set up (in case element was reset)
        setupDownloadTracking();
        try {
          // Convert blob to DataURL so it persists across navigation (Blob URLs are per-document)
          const fr = new FileReader();
          fr.onload = () => {
            try {
              sessionStorage.setItem('preloadImageDataURL', fr.result);
              sessionStorage.setItem('preloadImageName', downloadLink ? (downloadLink.download || `image-${Date.now()}.png`) : `image-${Date.now()}.png`);
              sessionStorage.removeItem('preloadImageURL');
            } catch(_) {}
          };
          fr.readAsDataURL(blob);
          var toConv = document.getElementById('to-converter');
          if (toConv) toConv.style.display = 'inline-block';
        } catch(_){}
    
    // Log download link creation
    logUserAction('download_link_created', {
      page_type: pageType,
      category: categoryInput?.value || 'unknown',
      target_color: targetColor,
      action_type: targetColor ? 'change_background' : 'remove_background',
      filename: downloadLink ? downloadLink.download : 'unknown'
    });
    
    const prompt = document.getElementById('process-prompt');
    if(prompt) {
      prompt.hidden = true;
      prompt.style.display = 'none';
    }
    
    // Log successful processing
    let successLogDetails = {
      page_type: pageType,
      output_size: blob.size,
      processing_successful: true
    };
    
    if (pageType === 'color_change') {
      successLogDetails.action_type = 'change_image_color';
      successLogDetails.color_type = 'hue';
      successLogDetails.hue_shift = document.getElementById('hue-slider')?.value || 0;
      successLogDetails.saturation = document.getElementById('saturation-slider')?.value || 100;
      successLogDetails.brightness = document.getElementById('brightness-slider')?.value || 100;
      successLogDetails.contrast = document.getElementById('contrast-slider')?.value || 100;
    } else if (pageType === 'upscale_image') {
      successLogDetails.action_type = 'upscale_image';
      successLogDetails.scale_factor = document.getElementById('scale-factor')?.value || '2';
      successLogDetails.method = document.getElementById('upscale-method')?.value || 'lanczos';
    } else if (pageType === 'blur_background') {
      successLogDetails.action_type = 'blur_background';
      successLogDetails.blur_radius = document.getElementById('blur-radius')?.value || '12';
    } else if (pageType === 'enhance_image') {
      successLogDetails.action_type = 'enhance_image';
      successLogDetails.preset = document.getElementById('enhance-preset')?.value || 'balanced';
    } else if (pageType === 'remove_text') {
      successLogDetails.action_type = 'remove_text';
    
    } else {
      successLogDetails.category = categoryInput?.value || 'unknown';
      successLogDetails.target_color = targetColor;
      successLogDetails.action_type = targetColor ? 'change_background' : 'remove_background';
    }
    
    logUserAction('processing_completed', successLogDetails);
    
    // Show feedback popup after successful processing (with delay for better UX)
    setTimeout(() => {
      if (typeof window.showFeedbackPopup === 'function') {
        window.showFeedbackPopup();
      }
    }, 5000); // 5 second delay to let user see the result first
    
    // make checkerboard solid and edge-to-edge on landing page
    (function(){
      var palette = document.getElementById('color-palette');
      var pageColor = document.body.getAttribute('data-target-color');
      var checker = document.querySelector('.checkerboard');
      
      if(checker){
        var hex = '#ffffff'; // default
        
        if(palette) {
          // General background page with color palette
          var hidden = document.getElementById('bg-color');
          hex = hidden ? hidden.value : '#ffffff';
        } else if(pageColor) {
          // Color-specific page
          hex = pageColor;
        }
        
        checker.style.background = hex;
        checker.style.padding = '0px';
        checker.style.borderRadius = '12px';
        
        // Add processed class for CSS specificity
        checker.classList.add('processed');
        
        // Force the styles to be applied
        checker.style.setProperty('padding', '0px', 'important');
        checker.style.setProperty('background', hex, 'important');
      }
    })();
    const resultWrap = document.getElementById('result-wrapper');
    if(resultWrap) resultWrap.hidden = false;

    // Inject or show an "Edit Result (Remove more)" button to allow iterative processing
    // Skip on remove-text page (it has its own inline magic eraser UI).
    try {
      const isRemoveTextPage = window.location.pathname === '/remove-text-from-image.html';
      if (!isRemoveTextPage) {
        var actions = document.querySelector('.actions');
        if (actions) {
          var editBtn = document.getElementById('edit-result-btn');
          if (!editBtn) {
            editBtn = document.createElement('button');
            editBtn.id = 'edit-result-btn';
            editBtn.type = 'button';
            editBtn.className = 'btn secondary';
            editBtn.textContent = 'Edit result (remove more)';
            actions.insertBefore(editBtn, document.getElementById('reset-btn'));
          }
          editBtn.style.display = 'inline-block';

          // Handler: use the processed image as new input and reset UI for another pass
          editBtn.onclick = async function(){
            try {
              if (!downloadLink) return;
              var href = downloadLink.href;
              if (!href) return;
              const res = await fetch(href);
              const b = await res.blob();
              const name = (downloadLink.download || 'image.png');
              const f = new File([b], name, { type: b.type || 'image/png' });
              currentFile = f;
              setOriginalPreview(currentFile);

              // Hide result and prompt to process again
              if (resultWrap) resultWrap.hidden = true;
              var promptEl = document.getElementById('process-prompt');
              if (promptEl) { promptEl.hidden = false; promptEl.style.display = 'block'; }
              enableProcess(true);
              updateCtaText();
              updatePromptText();
            } catch (e) {
              console.warn('Failed to reload result as input', e);
            }
          };
        }
      }
    } catch(_){}
  }catch(err){
    // Log processing error
    let errorLogDetails = {
      page_type: pageType,
      error_message: err.message || err,
      processing_successful: false
    };
    
    if (pageType === 'color_change') {
      errorLogDetails.action_type = 'change_image_color';
      errorLogDetails.color_type = 'hue';
      errorLogDetails.hue_shift = document.getElementById('hue-slider')?.value || 0;
      errorLogDetails.saturation = document.getElementById('saturation-slider')?.value || 100;
      errorLogDetails.brightness = document.getElementById('brightness-slider')?.value || 100;
      errorLogDetails.contrast = document.getElementById('contrast-slider')?.value || 100;
    } else if (pageType === 'upscale_image') {
      errorLogDetails.action_type = 'upscale_image';
      errorLogDetails.scale_factor = document.getElementById('scale-factor')?.value || '2';
      errorLogDetails.method = document.getElementById('upscale-method')?.value || 'lanczos';
    } else if (pageType === 'blur_background') {
      errorLogDetails.action_type = 'blur_background';
      errorLogDetails.blur_radius = document.getElementById('blur-radius')?.value || '12';
    } else if (pageType === 'enhance_image') {
      errorLogDetails.action_type = 'enhance_image';
      errorLogDetails.preset = document.getElementById('enhance-preset')?.value || 'balanced';
    } else if (pageType === 'remove_text') {
      errorLogDetails.action_type = 'remove_text';
    
    } else {
      errorLogDetails.category = categoryInput?.value || 'unknown';
      errorLogDetails.target_color = targetColor;
      errorLogDetails.action_type = targetColor ? 'change_background' : 'remove_background';
    }
    
    logUserAction('processing_error', errorLogDetails);
    
    alert('Error: ' + (err.message || err));
  }finally{
    updateCtaText();
    enableProcess(true);
  }
});

// ------------------------------
// Convert format page integration
// ------------------------------
// Handle all convert format pages (convert-image-format.html, convert-png-to-svg.html, etc.)
const convertFormatPages = [
  '/convert-image-format.html',
  '/convert-png-to-svg.html',
  '/convert-jpg-to-svg.html',
  '/convert-webp-to-svg.html',
  '/convert-image-to-svg.html'
];

if (convertFormatPages.includes(window.location.pathname)) {
  document.addEventListener('DOMContentLoaded', function(){
    const dz = document.getElementById('convert-dropzone');
    const input = document.getElementById('convert-file');
    const btn = document.getElementById('convert-btn');
    const orig = document.getElementById('convert-original');
    const resImg = document.getElementById('convert-result');
    const resWrap = document.getElementById('convert-result-wrapper');
    const prompt = document.getElementById('convert-prompt');
    const preview = document.getElementById('convert-preview');
    const downloadA = document.getElementById('convert-download');
    const resetC = document.getElementById('convert-reset');
    const target = document.getElementById('target-format');
    const keepT = document.getElementById('keep-transparent');
    const quality = document.getElementById('quality'); // May be null for SVG pages
    const openA = document.getElementById('convert-open');

    let file = null;
    function setPreview(f){
      const r = new FileReader();
      r.onload = ()=>{ orig.src = r.result; preview.hidden = false; btn.disabled = false; };
      r.readAsDataURL(f);
    }
    dz.addEventListener('click', ()=> input.click());
    dz.addEventListener('dragover', e=>{e.preventDefault(); dz.classList.add('drag');});
    dz.addEventListener('dragleave', ()=> dz.classList.remove('drag'));
    dz.addEventListener('drop', e=>{e.preventDefault(); dz.classList.remove('drag'); if(e.dataTransfer.files[0]){ file = e.dataTransfer.files[0]; setPreview(file);} });
    input.addEventListener('change', e=>{ if(e.target.files[0]){ file = e.target.files[0]; setPreview(file);} });

    document.getElementById('convert-form').addEventListener('submit', async function(e){
      e.preventDefault(); if(!file) return;
      
      // Track CTA click in Google Analytics for convert format
      trackGAEvent('process_image_cta', {
        'event_category': 'engagement',
        'event_label': 'convert_format',
        'page_path': window.location.pathname,
        'page_title': document.title,
        'value': 1
      });
      
      btn.disabled = true; btn.textContent = 'Converting…';
      const body = new FormData();
      body.append('file', file);
      body.append('target_format', target.value);
      body.append('transparent', keepT ? (keepT.checked ? 'true' : 'false') : 'false');
      // Quality is optional (not needed for SVG)
      if (quality) {
        body.append('quality', quality.value);
      }
      const apiBase = window.API_BASE || (window.location.hostname === '127.0.0.1' && window.location.port === '8080' ? 'http://127.0.0.1:8000' : 'https://bgremover-backend-121350814881.us-central1.run.app');
      try {
        console.log('Sending conversion request:', { targetFormat: target.value, fileName: file.name, fileSize: file.size });
        const res = await fetch(apiBase + '/api/convert-format', { method: 'POST', body });
        if(!res.ok){ 
          const errorText = await res.text();
          console.error('Conversion error:', errorText);
          throw new Error(errorText); 
        }
        const blob = await res.blob();
        // For formats browsers can't preview (ICO, PPM, PGM, TIFF), skip inline preview and just enable download
        const ct = res.headers.get('content-type') || '';
        const nonPreview = ['image/x-icon','image/tiff','image/x-portable-pixmap','image/x-portable-graymap'];
        const url = URL.createObjectURL(blob);
        // Handle file extension properly for all formats including SVG
        let ext = target.value;
        if (ext === 'jpg') ext = 'jpg';
        else if (ext === 'svg') ext = 'svg';
        downloadA.href = url;
        downloadA.download = `converted-${Date.now()}.${ext}`;
        
        // Log for debugging
        console.log('Conversion response:', { 
          contentType: ct, 
          targetFormat: target.value, 
          blobSize: blob.size,
          blobType: blob.type,
          expectedSVG: target.value === 'svg',
          isSVGContentType: ct === 'image/svg+xml'
        });
        
        // For SVG, verify the blob content
        if (target.value === 'svg') {
          blob.text().then(text => {
            console.log('SVG content preview (first 200 chars):', text.substring(0, 200));
            console.log('Is valid SVG?', text.trim().startsWith('<?xml') || text.trim().startsWith('<svg'));
          }).catch(err => console.error('Error reading SVG blob:', err));
        }
        
        // Add Google Analytics tracking for convert format download button clicks
        if (downloadA && !downloadA.dataset.gaTracked) {
          downloadA.dataset.gaTracked = 'true';
          downloadA.addEventListener('click', function() {
            // Track in Google Analytics
            trackGAEvent('download_image', {
              'event_category': 'engagement',
              'event_label': 'convert_format',
              'page_path': window.location.pathname,
              'page_title': document.title,
              'value': 1
            });
            
            // Also log to backend analytics
            logUserAction('download_clicked', {
              page_type: 'convert_format',
              action_type: 'convert_format',
              target_format: target.value,
              filename: downloadA.download || 'unknown'
            });
          });
        }
        resWrap.hidden = false;
        prompt.style.display = 'none';
        // SVG can be previewed in img tags
        if (!nonPreview.includes(ct) || ct === 'image/svg+xml') {
          resImg.style.display = 'block';
          // Force reload for SVG to avoid caching issues
          if (target.value === 'svg') {
            resImg.src = '';
            setTimeout(() => { resImg.src = url; }, 10);
          } else {
            resImg.src = url;
          }
        } else {
          // Hide unsupported preview types to avoid broken tiny icon
          resImg.removeAttribute('src');
          resImg.style.display = 'none';
          // Optionally show a text note
          if (!document.getElementById('convert-note')) {
            const note = document.createElement('p');
            note.id = 'convert-note';
            note.className = 'prompt';
            note.textContent = 'Preview not supported in browser for this format. Use Download to view the file.';
            resWrap.parentElement.insertBefore(note, resWrap.nextSibling);
          }
        }
        if (openA){ openA.href = url; openA.target = '_blank'; openA.style.display = 'inline-block'; }
        logUserAction('convert_completed', { target_format: target.value, transparent: keepT ? keepT.checked : false, size: blob.size });
        
        // Show feedback popup after successful conversion
        setTimeout(() => {
          if (typeof window.showFeedbackPopup === 'function') {
            window.showFeedbackPopup();
          }
        }, 5000); // 5 second delay to let user see the result first
      } catch (err) {
        alert('Error: ' + (err.message || err));
        logUserAction('convert_error', { message: err.message || String(err) });
      } finally {
        btn.disabled = false;
        // Restore button text based on page
        if (window.location.pathname.includes('svg')) {
          btn.textContent = 'Convert PNG to SVG';
        } else {
          btn.textContent = 'Convert Image';
        }
      }
    });

    resetC.addEventListener('click', function(){
      file = null; input.value = ''; orig.src = ''; resImg.src = ''; preview.hidden = true; resWrap.hidden = true; prompt.style.display = 'block'; btn.disabled = true;
      if (openA){ openA.removeAttribute('href'); openA.style.display = 'none'; }
    });

    // Preset buttons for GIMP/Inkscape
    const convertPresets = document.querySelectorAll('[data-convert-preset]');
    convertPresets.forEach(btn => {
      btn.addEventListener('click', function(){
        const preset = btn.getAttribute('data-convert-preset');
        if (preset === 'gimp') {
          target.value = 'png';
          keepT.checked = true;
          quality.value = '90';
        } else if (preset === 'inkscape-png') {
          target.value = 'png';
          keepT.checked = true;
          quality.value = '90';
        } else if (preset === 'inkscape-webp') {
          target.value = 'webp';
          keepT.checked = true;
          quality.value = '90';
        }
        logUserAction('convert_preset_selected', { preset, target_format: target.value, transparent: keepT.checked, quality: quality.value });
      });
    });

    // Auto preload from sessionStorage
    try {
      const preloadDataURL = sessionStorage.getItem('preloadImageDataURL');
      const preloadName = sessionStorage.getItem('preloadImageName') || 'image.png';
      if (preloadDataURL) {
        fetch(preloadDataURL).then(r=>r.blob()).then(b=>{
          const f = new File([b], preloadName, { type: b.type || 'image/png' });
          file = f; setPreview(file);
        }).catch(()=>{});
      }
    } catch(_){ }
  });
}

if (resetBtn) resetBtn.addEventListener('click', () => {
  // Log reset action
  logUserAction('reset_action', {
    page_type: getPageType()
  });
  
  currentFile = null;
  if (fileInput) fileInput.value = '';
  if (originalImg) {
  originalImg.src = '';
  originalImg.style.display = 'none';
  }
  if (resultImg) resultImg.src = '';
  
  // Show upload prompt
  const uploadPrompt = document.getElementById('upload-prompt');
  if (uploadPrompt) uploadPrompt.style.display = 'block';
  
  // Hide image columns initially
  const imageColumns = document.querySelectorAll('.image-column');
  imageColumns.forEach(col => col.style.display = 'none');
  
  const prompt = document.getElementById('process-prompt');
  if(prompt) prompt.style.display = 'none';
  const resultWrap = document.getElementById('result-wrapper');
  if(resultWrap) resultWrap.hidden = true;
  
  enableProcess(false);
  updateCtaText();
  updatePromptText();
});

} catch (e) {
  console.error('script.js init error:', e);
}

// ============================================
// Feedback Popup System
// ============================================
(function() {
  'use strict';
  
  // Check if feedback popup already exists
  if (document.getElementById('feedback-popup')) {
    return;
  }
  
  // Create popup element
  const popup = document.createElement('div');
  popup.id = 'feedback-popup';
  popup.className = 'feedback-popup';
  popup.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:10000;display:none;align-items:center;justify-content:center;';
  popup.innerHTML = `
    <div class="feedback-popup-overlay" style="position:absolute;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);backdrop-filter:blur(4px);"></div>
    <div class="feedback-popup-content" style="position:relative;background:var(--card,#12171d);border:1px solid var(--border,#1e2630);border-radius:16px;padding:32px;max-width:500px;width:90%;max-height:90vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,0.3);z-index:1;">
      <button class="feedback-popup-close" id="feedback-close-btn" aria-label="Close feedback popup" style="position:absolute;top:16px;right:16px;background:transparent;border:none;color:var(--muted,#9aa7b2);font-size:28px;line-height:1;cursor:pointer;padding:4px 8px;border-radius:4px;transition:all 0.2s ease;">&times;</button>
      <h3 class="feedback-popup-title" style="font-size:24px;font-weight:700;color:var(--fg,#eaf0f6);margin:0 0 8px;">How was your experience?</h3>
      <p class="feedback-popup-subtitle" style="font-size:14px;color:var(--muted,#9aa7b2);margin:0 0 24px;">We'd love to hear your feedback!</p>
      
      <div class="feedback-rating" style="margin:24px 0;text-align:center;">
        <div class="feedback-stars" style="display:flex;justify-content:center;gap:8px;margin-bottom:12px;">
          <button class="feedback-star" data-rating="1" aria-label="1 star" style="background:transparent;border:none;font-size:40px;cursor:pointer;padding:4px;transition:all 0.2s ease;filter:grayscale(100%) opacity(0.5);">⭐</button>
          <button class="feedback-star" data-rating="2" aria-label="2 stars" style="background:transparent;border:none;font-size:40px;cursor:pointer;padding:4px;transition:all 0.2s ease;filter:grayscale(100%) opacity(0.5);">⭐</button>
          <button class="feedback-star" data-rating="3" aria-label="3 stars" style="background:transparent;border:none;font-size:40px;cursor:pointer;padding:4px;transition:all 0.2s ease;filter:grayscale(100%) opacity(0.5);">⭐</button>
          <button class="feedback-star" data-rating="4" aria-label="4 stars" style="background:transparent;border:none;font-size:40px;cursor:pointer;padding:4px;transition:all 0.2s ease;filter:grayscale(100%) opacity(0.5);">⭐</button>
          <button class="feedback-star" data-rating="5" aria-label="5 stars" style="background:transparent;border:none;font-size:40px;cursor:pointer;padding:4px;transition:all 0.2s ease;filter:grayscale(100%) opacity(0.5);">⭐</button>
        </div>
        <p class="feedback-rating-text" id="feedback-rating-text" style="font-size:14px;color:var(--muted,#9aa7b2);margin:0;">Tap a star to rate</p>
      </div>
      
      <div class="feedback-comment-section" style="margin:24px 0;">
        <label for="feedback-comment" class="feedback-comment-label" style="display:block;font-size:14px;font-weight:600;color:var(--fg,#eaf0f6);margin-bottom:8px;">Your feedback (optional)</label>
        <textarea id="feedback-comment" class="feedback-comment-input" placeholder="Tell us what you think..." rows="4" maxlength="1000" style="width:100%;padding:12px;background:var(--bg,#0b0f13);border:1px solid var(--border,#1e2630);border-radius:8px;color:var(--fg,#eaf0f6);font-family:inherit;font-size:14px;resize:vertical;box-sizing:border-box;"></textarea>
        <div class="feedback-char-count" style="font-size:12px;color:var(--muted,#9aa7b2);text-align:right;margin-top:4px;"><span id="feedback-char-count">0</span>/1000</div>
      </div>
      
      <div class="feedback-actions" style="display:flex;gap:12px;margin-top:24px;">
        <button class="feedback-btn feedback-btn-submit" id="feedback-submit-btn" disabled style="flex:1;padding:12px 24px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;transition:all 0.2s ease;background:var(--accent,#6aa7ff);color:#001633;opacity:0.5;">Submit Feedback</button>
        <button class="feedback-btn feedback-btn-skip" id="feedback-skip-btn" style="flex:1;padding:12px 24px;border:1px solid var(--border,#1e2630);border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;transition:all 0.2s ease;background:transparent;color:var(--muted,#9aa7b2);">Skip</button>
      </div>
    </div>
  `;
  document.body.appendChild(popup);
  
  let selectedRating = 0;
  let hasShownFeedback = false;
  
  // Check if user has already submitted feedback in this session
  const feedbackShown = sessionStorage.getItem('feedbackShown');
  if (feedbackShown === 'true') {
    hasShownFeedback = true;
  }
  
  function logImpression(action) {
    const pageType = window.location.pathname;
    const operation = getOperationType();
    
    const impressionData = {
      page: pageType,
      operation: operation,
      action: action, // 'shown', 'submitted', 'skipped', 'closed'
      userAgent: navigator.userAgent,
      timestamp: new Date().toISOString()
    };
    
    // Send to backend
    const isLocalFrontend = (['127.0.0.1','localhost'].includes(window.location.hostname)) && window.location.port === '8080';
    const apiBase = window.API_BASE || (isLocalFrontend ? 'http://127.0.0.1:8000' : 'https://bgremover-backend-121350814881.us-central1.run.app');
    
    fetch(apiBase + '/api/feedback/impression', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(impressionData)
    }).catch(err => {
      console.warn('Failed to log feedback impression:', err);
    });
  }
  
  function showFeedbackPopup() {
    // Don't show if already shown in this session
    if (hasShownFeedback) {
      return;
    }
    
    const popupEl = document.getElementById('feedback-popup');
    if (!popupEl) return;
    
    // Log impression when modal is shown
    logImpression('shown');
    
    // Reset state
    selectedRating = 0;
    const commentEl = document.getElementById('feedback-comment');
    if (commentEl) {
      commentEl.value = '';
    }
    const charCountEl = document.getElementById('feedback-char-count');
    if (charCountEl) charCountEl.textContent = '0';
    const ratingTextEl = document.getElementById('feedback-rating-text');
    if (ratingTextEl) ratingTextEl.textContent = 'Tap a star to rate';
    const submitBtn = document.getElementById('feedback-submit-btn');
    if (submitBtn) submitBtn.disabled = true;
    
    // Reset stars
    document.querySelectorAll('.feedback-star').forEach(star => {
      star.classList.remove('active');
      star.style.filter = 'grayscale(100%) opacity(0.5)';
    });
    
    popupEl.style.display = 'flex';
    document.body.style.overflow = 'hidden';
  }
  
  function hideFeedbackPopup(action = 'closed') {
    const popupEl = document.getElementById('feedback-popup');
    if (popupEl) {
      // Log impression if closing without submitting
      if (action === 'closed' && !hasShownFeedback) {
        logImpression('closed');
      }
      popupEl.style.display = 'none';
      document.body.style.overflow = '';
    }
  }
  
  function getOperationType() {
    const path = window.location.pathname;
    if (path === '/' || path === '/index.html' || path.includes('remove-background')) return 'remove_background';
    if (path.includes('change-image-background')) return 'change_background';
    if (path === '/change-color-of-image.html') return 'change_color';
    if (path === '/upscale-image.html') return 'upscale';
    if (path === '/blur-background.html') return 'blur_background';
    if (path === '/grayscale-background.html') return 'grayscale_background';
    if (path === '/enhance-image.html') return 'enhance';
  if (path === '/remove-text-from-image.html') return 'remove_text';
  if (path === '/remove-people-from-photo.html') return 'remove_people';
  if (path === '/remove-gemini-watermark.html') return 'remove_gemini_watermark';
  if (path === '/convert-image-format.html') return 'convert_format';
  return 'unknown';
  }
  
  function submitFeedback() {
    if (selectedRating === 0) return;
    
    const commentEl = document.getElementById('feedback-comment');
    const comment = commentEl ? commentEl.value.trim() : '';
    const pageType = window.location.pathname;
    const operation = getOperationType();
    
    const feedbackData = {
      rating: selectedRating,
      comment: comment,
      page: pageType,
      operation: operation,
      timestamp: new Date().toISOString(),
      userAgent: navigator.userAgent
    };
    
    // Send to backend
    const isLocalFrontend = (['127.0.0.1','localhost'].includes(window.location.hostname)) && window.location.port === '8080';
    const apiBase = window.API_BASE || (isLocalFrontend ? 'http://127.0.0.1:8000' : 'https://bgremover-backend-121350814881.us-central1.run.app');
    
    fetch(apiBase + '/api/feedback', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(feedbackData)
    }).catch(err => {
      console.warn('Failed to submit feedback:', err);
    });
    
    // Log impression as submitted
    logImpression('submitted');
    
    // Mark as shown
    hasShownFeedback = true;
    sessionStorage.setItem('feedbackShown', 'true');
    
    hideFeedbackPopup('submitted');
    
    // Show thank you message briefly
    const thankYou = document.createElement('div');
    thankYou.style.cssText = 'position:fixed;top:20px;right:20px;background:var(--accent,#6aa7ff);color:#001633;padding:16px 24px;border-radius:8px;z-index:10001;font-weight:600;box-shadow:0 4px 12px rgba(0,0,0,0.2);';
    thankYou.textContent = 'Thank you for your feedback! 🙏';
    document.body.appendChild(thankYou);
    
    setTimeout(() => {
      thankYou.style.opacity = '0';
      thankYou.style.transition = 'opacity 0.3s';
      setTimeout(() => thankYou.remove(), 300);
    }, 2000);
  }
  
  // Initialize event listeners when DOM is ready
  function initFeedbackPopup() {
    const popupEl = document.getElementById('feedback-popup');
    if (!popupEl) return;
    
    // Close button
    const closeBtn = document.getElementById('feedback-close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', function() {
        logImpression('closed');
        hideFeedbackPopup('closed');
      });
      closeBtn.addEventListener('mouseenter', function() {
        this.style.color = 'var(--fg, #eaf0f6)';
        this.style.background = 'var(--border, #1e2630)';
      });
      closeBtn.addEventListener('mouseleave', function() {
        this.style.color = 'var(--muted, #9aa7b2)';
        this.style.background = 'transparent';
      });
    }
    
    // Overlay click
    const overlay = popupEl.querySelector('.feedback-popup-overlay');
    if (overlay) {
      overlay.addEventListener('click', function() {
        logImpression('closed');
        hideFeedbackPopup('closed');
      });
    }
    
    // Star ratings
    document.querySelectorAll('.feedback-star').forEach(star => {
      star.addEventListener('click', function() {
        selectedRating = parseInt(this.dataset.rating);
        
        // Update star display
        document.querySelectorAll('.feedback-star').forEach((s, index) => {
          if (index < selectedRating) {
            s.classList.add('active');
            s.style.filter = 'grayscale(0%) opacity(1)';
          } else {
            s.classList.remove('active');
            s.style.filter = 'grayscale(100%) opacity(0.5)';
          }
        });
        
        // Update rating text
        const ratingTexts = {
          1: 'Poor',
          2: 'Fair',
          3: 'Good',
          4: 'Very Good',
          5: 'Excellent'
        };
        const ratingTextEl = document.getElementById('feedback-rating-text');
        if (ratingTextEl) {
          ratingTextEl.textContent = ratingTexts[selectedRating] || 'Tap a star to rate';
        }
        
        // Enable submit button
        const submitBtn = document.getElementById('feedback-submit-btn');
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.style.opacity = '1';
          submitBtn.style.cursor = 'pointer';
        }
      });
      
      star.addEventListener('mouseenter', function() {
        if (!this.classList.contains('active')) {
          this.style.filter = 'grayscale(0%) opacity(1)';
          this.style.transform = 'scale(1.1)';
        }
      });
      
      star.addEventListener('mouseleave', function() {
        if (!this.classList.contains('active')) {
          this.style.filter = 'grayscale(100%) opacity(0.5)';
          this.style.transform = 'scale(1)';
        }
      });
    });
    
    // Comment character count
    const commentEl = document.getElementById('feedback-comment');
    if (commentEl) {
      commentEl.addEventListener('input', function() {
        const count = this.value.length;
        const charCountEl = document.getElementById('feedback-char-count');
        if (charCountEl) charCountEl.textContent = count;
      });
      
      commentEl.addEventListener('focus', function() {
        this.style.borderColor = 'var(--accent, #6aa7ff)';
        this.style.boxShadow = '0 0 0 3px rgba(106, 167, 255, 0.1)';
      });
      
      commentEl.addEventListener('blur', function() {
        this.style.borderColor = 'var(--border, #1e2630)';
        this.style.boxShadow = 'none';
      });
    }
    
    // Submit button
    const submitBtn = document.getElementById('feedback-submit-btn');
    if (submitBtn) {
      submitBtn.addEventListener('click', submitFeedback);
      submitBtn.addEventListener('mouseenter', function() {
        if (!this.disabled) {
          this.style.background = '#4f46e5';
          this.style.transform = 'translateY(-1px)';
        }
      });
      submitBtn.addEventListener('mouseleave', function() {
        if (!this.disabled) {
          this.style.background = 'var(--accent, #6aa7ff)';
          this.style.transform = 'translateY(0)';
        }
      });
    }
    
    // Skip button
    const skipBtn = document.getElementById('feedback-skip-btn');
    if (skipBtn) {
      skipBtn.addEventListener('click', function() {
        // Log impression as skipped
        logImpression('skipped');
        hasShownFeedback = true;
        sessionStorage.setItem('feedbackShown', 'true');
        hideFeedbackPopup('skipped');
      });
      skipBtn.addEventListener('mouseenter', function() {
        this.style.background = 'var(--border, #1e2630)';
        this.style.color = 'var(--fg, #eaf0f6)';
      });
      skipBtn.addEventListener('mouseleave', function() {
        this.style.background = 'transparent';
        this.style.color = 'var(--muted, #9aa7b2)';
      });
    }
    
    // ESC key to close
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && popupEl.style.display !== 'none') {
        logImpression('closed');
        hideFeedbackPopup('closed');
      }
    });
  }
  
  // Wait for DOM to be ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFeedbackPopup);
  } else {
    initFeedbackPopup();
  }
  
  // Export function to show popup
  window.showFeedbackPopup = showFeedbackPopup;
})();


// Color-page composition: if body has data-target-color, render solid background
(function(){
  var color = document.body.getAttribute("data-target-color");
  if(!color) return;
  async function toColorBackground(url){
    return new Promise(function(resolve, reject){
      var img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = function(){
        try{
          var canvas = document.createElement("canvas");
          canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
          var ctx = canvas.getContext("2d");
          ctx.fillStyle = color; ctx.fillRect(0,0,canvas.width,canvas.height);
          ctx.drawImage(img,0,0);
          canvas.toBlob(function(b){ if(b) resolve(b); else reject(new Error("blob failed")); }, "image/png");
        }catch(e){ reject(e); }
      };
      img.onerror = reject;
      img.src = url;
    });
  }
  form.addEventListener('submit', function(){
    setTimeout(async function(){
      if(!downloadLink || !downloadLink.href) return;
      var backendColored = !!document.body.getAttribute('data-target-color');
      if(backendColored){ return; }
      try{
        var blob = await toColorBackground(downloadLink.href);
        var coloredUrl = URL.createObjectURL(blob);
        if (resultImg) resultImg.src = coloredUrl;
        if (downloadLink) {
        downloadLink.href = coloredUrl;
        var hex = color.replace('#','');
        downloadLink.download = 'bg-' + hex + '-' + Date.now() + '.png';
        }
      }catch(err){ console.warn('Color composite failed', err); }
    }, 0);
  });
})();


// If on a color page, make the preview background solid and edge-to-edge
// This will be applied after processing is complete


// On color pages, align prompt text with CTA label
(function(){
  var pageColor = document.body.getAttribute('data-target-color');
  if(!pageColor) return;
  var prompt = document.getElementById('process-prompt');
  updatePromptText();
})();


// Landing page color selection behavior
document.addEventListener('DOMContentLoaded', function(){
  var palette = document.getElementById('color-palette');
  var hidden = document.getElementById('bg-color');
  if(!palette || !hidden){ console.log('palette or hidden not found'); return; }
  // Set CTA and prompt
  updateCtaText(); if(processBtn) processBtn.setAttribute('aria-label','Change image background');
  updatePromptText();
  function setColor(hex){ hidden.value = hex; }
  // Fresh, simple event delegation for palette clicks
  palette.addEventListener('click', function(e){
    var btn = e.target.closest && e.target.closest('.swatch');
    if(!btn || !palette.contains(btn)) return;
    e.preventDefault();
    palette.querySelectorAll('.swatch').forEach(function(b){ b.classList.remove('active'); });
    btn.classList.add('active');
    setColor(btn.getAttribute('data-color'));
    var custom = document.getElementById('custom-color');
    if(custom) custom.value = hidden.value;
    console.log('bg_color set', hidden.value);
  });
  // Initialize first swatch as active if none selected
  var first = palette.querySelector('.swatch');
  if(first && !palette.querySelector('.swatch.active')){
    first.classList.add('active');
    setColor(first.getAttribute('data-color'));
  }
  var custom = document.getElementById('custom-color');
  if(custom){ custom.addEventListener('input', function(){ setColor(custom.value); }); }
});

// Extra defensive: global delegate to ensure swatch selection always works, even if previous binding missed
document.addEventListener('click', function(e){
  var palette = document.getElementById('color-palette');
  var hidden = document.getElementById('bg-color');
  if(!palette || !hidden) return;
  var sw = e.target.closest && e.target.closest('.swatch');
  if(sw && palette.contains(sw)){
    e.preventDefault();
    palette.querySelectorAll('.swatch').forEach(function(b){ b.classList.remove('active'); b.style.outline='none'; });
    sw.classList.add('active');
    sw.style.outline = '2px solid var(--accent)';
    hidden.value = sw.getAttribute('data-color');
    var custom = document.getElementById('custom-color');
    if(custom) custom.value = hidden.value;
    console.log('global delegate set bg_color', hidden.value);
  }
});