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

 function sendEventPage(type, context, payload) {
    try {
      window.onPageEvent({
        event_type: type,
        event_context: context,
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

    let scrollArmed = true;
    window.addEventListener('scroll', (event) => {
        if (!scrollArmed) return;
        scrollArmed = false;

        const info = {
            x: window.scrollX,
            y: window.scrollY
        };
        sendEventPage('scroll', 'action:user', info);

        setTimeout(() => { scrollArmed = true; }, 500);
    }, { capture: true, passive: true });

    // Click event
    document.addEventListener('click', (e) => {
        const element = e.target;
        const info = {
            tag: element.tagName,
            id: element.id,
            className: element.className,
            text: (element.innerText || '').substring(0, 50),
            x: e.clientX,
            y: e.clientY
        };
        sendEventPage('click', 'action:user', info);
    }, { capture: true });

    // Mousedown event
    document.addEventListener('mousedown', (e) => {
        const element = e.target;
        const info = {
            tag: element.tagName,
            id: element.id,
            className: element.className,
            x: e.clientX,
            y: e.clientY,
            button: e.button
        };
        sendEventPage('mousedown', 'action:user', info);
    }, { capture: true });

    // Mouseup event
    document.addEventListener('mouseup', (e) => {
        const element = e.target;
        const info = {
            tag: element.tagName,
            id: element.id,
            className: element.className,
            x: e.clientX,
            y: e.clientY,
            button: e.button
        };
        sendEventPage('mouseup', 'action:user', info);
    }, { capture: true });

    // Pointerdown event
    document.addEventListener('pointerdown', (e) => {
        const element = e.target;
        const info = {
            tag: element.tagName,
            id: element.id,
            className: element.className,
            x: e.clientX,
            y: e.clientY,
            button: e.button,
            pointerType: e.pointerType
        };
        sendEventPage('pointerdown', 'action:user', info);
    }, { capture: true });

    // Pointerup event
    document.addEventListener('pointerup', (e) => {
        const element = e.target;
        const info = {
            tag: element.tagName,
            id: element.id,
            className: element.className,
            x: e.clientX,
            y: e.clientY,
            button: e.button,
            pointerType: e.pointerType
        };
        sendEventPage('pointerup', 'action:user', info);
    }, { capture: true });

    // Contextmenu event
    document.addEventListener('contextmenu', (e) => {
        const element = e.target;
        const info = {
            tag: element.tagName,
            id: element.id,
            className: element.className,
            x: e.clientX,
            y: e.clientY
        };
        sendEventPage('contextmenu', 'action:user', info);
    }, { capture: true });

    // Input event
    document.addEventListener('input', (e) => {
        try {
            const element = e.target;
            const info = {
                tag: element.tagName,
                id: element.id,
                className: element.className,
                value: element.value || ''
            };
            sendEventPage('input', 'action:user', info);
        } catch (_) {}
    }, { capture: true });

    // Keydown event
    document.addEventListener('keydown', (e) => {
        const info = {
            key: e.key,
            code: e.code,
            keyCode: e.keyCode,
            ctrlKey: e.ctrlKey,
            metaKey: e.metaKey,
            altKey: e.altKey,
            shiftKey: e.shiftKey
        };
        sendEventPage('keydown', 'action:user', info);
    }, { capture: true });

    document.addEventListener('DOMContentLoaded', (event) => {
        const info = {
            message: 'DOM fully loaded and parsed',
            url: window.location.href, // Get the current URL
            title: document.title, // Get the title of the page
            timestamp: Date.now() // Capture the timestamp for when the event occurred
        };
        sendEventPage('domcontentloaded', 'state:page', info);
    });

    window.addEventListener('load', (event) => {
    const info = {
        message: 'Page fully loaded',
        url: window.location.href, // Get the current URL
        title: document.title, // Get the title of the page
        timestamp: Date.now() // Capture the timestamp for when the event occurred
    };
    console.log('📤 Sending load info:', info);
    sendEventPage('load', 'state:page', info);
});
}

// Setup immediately if DOM is already loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupPageEventListener);
} else {
    setupPageEventListener();
}
"""
