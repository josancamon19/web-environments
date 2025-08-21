# JavaScript stealth and DOM listener scripts

STEALTH_SCRIPT = """
() => {
    // Remove webdriver property
    Object.defineProperty(navigator, 'webdriver', {
        get: () => false,
    });
    
    // Mock plugins
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
    
    // Mock languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
    
    // Override the `plugins` property to use a custom getter.
    Object.defineProperty(navigator, 'plugins', {
        get: function() {
            return [1, 2, 3, 4, 5];
        },
    });
    
    // Pass the Webdriver test
    Object.defineProperty(navigator, 'webdriver', {
        get: () => false,
    });
    
    // Pass the Chrome test
    window.chrome = {
        runtime: {},
    };
    
    // Pass the Permissions test
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
    );
}
"""

PAGE_EVENT_LISTENER_SCRIPT = """
console.log('🎯 Page event listener script loaded');

 function sendEventPage(type, payload) {
    try {
      console.log('[RECORDER] Sending event:', type, payload);
      window.onPageEvent({
        event_type: type,
        event_data: payload,
        dom_snapshot: document.documentElement.outerHTML,
        metadata: JSON.stringify({
            timestamp: Date.now(),
            page_url: window.location.href,
            page_title: document.title,
        })
      });
    } catch (e) {
      console.error('[RECORDER] Failed to send event:', e);
    }
  }


function setupPageEventListener() {
    document.addEventListener('click', (event) => {
        console.log('👆 Click event triggered');
        const element = event.target;
        const info = {
            tag: element.tagName,
            id: element.id,
            className: element.className,
            text: element.innerText?.substring(0, 50) || '',
            x: event.clientX,
            y: event.clientY
        };
        console.log('📤 Sending click info:', info);
        sendEventPage('click', info);
    });
}

// Setup immediately if DOM is already loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupPageEventListener);
} else {
    setupPageEventListener();
}
"""
