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
if (!window.__RECORDER_EVENT_LISTENER_LOADED__) {
  window.__RECORDER_EVENT_LISTENER_LOADED__ = true;

  console.log('🎯 Page event listener script loaded');

  // Queue events until binding becomes available; also bridge iframe -> top frame via postMessage
  window.__RECORDER_QUEUE__ = window.__RECORDER_QUEUE__ || [];

  function enqueueEvent(eventPayload) {
    try {
      window.__RECORDER_QUEUE__.push(eventPayload);
    } catch (_) {}
  }

  function flushQueuedEvents() {
    try {
      if (typeof window.onPageEvent === 'function' && Array.isArray(window.__RECORDER_QUEUE__) && window.__RECORDER_QUEUE__.length) {
        const batch = window.__RECORDER_QUEUE__.splice(0, window.__RECORDER_QUEUE__.length);
        for (const evt of batch) {
          try { window.onPageEvent(evt); } catch (_) { /* stop flushing on hard failure */ break; }
        }
      }
    } catch (_) {}
  }

  // Periodically try to flush queue in case binding is registered later
  try { setInterval(flushQueuedEvents, 500); } catch (_) {}

  // Listen for child iframes posting events to us
  try {
    window.addEventListener('message', (e) => {
      try {
        const data = e && e.data;
        if (data && data.__RECORDER_EVENT__) {
          const eventInfo = data.__RECORDER_EVENT__;
          if (typeof window.onPageEvent === 'function') {
            window.onPageEvent(eventInfo);
          } else {
            enqueueEvent(eventInfo);
          }
        }
      } catch (_) {}
    }, { capture: true });
  } catch (_) {}

  function sendEventPage(type, context, payload) {
    try {
      const eventObj = {
        event_type: type,
        event_context: context,
        event_data: payload,
        dom_snapshot: '',
        metadata: {
            timestamp: Date.now(),
            page_url: window.location.href,
            page_title: document.title,
        }
      };
      if (typeof window.onPageEvent === 'function') {
        window.onPageEvent(eventObj);
        if (type === 'scroll') {
          console.log('[RECORDER] ✅ Scroll event sent via onPageEvent');
        }
      } else {
        // If we're inside an iframe or binding not yet ready, try parent first, else queue
        if (type === 'scroll') {
          console.log('[RECORDER] ⚠️ onPageEvent not available, queuing scroll event');
        }
        try {
          if (window.parent && window.parent !== window) {
            window.parent.postMessage({ __RECORDER_EVENT__: eventObj }, '*');
          } else {
            enqueueEvent(eventObj);
          }
        } catch (_) {
          enqueueEvent(eventObj);
        }
      }
    } catch (e) {
      console.error('[RECORDER] Failed to send event:', e);
    }
  }


  const isFiniteNumber = (value) => typeof value === 'number' && Number.isFinite(value);

  const toCoordinatePair = (x, y) => {
    const pair = {
      x: isFiniteNumber(x) ? x : null,
      y: isFiniteNumber(y) ? y : null,
    };
    return pair.x === null && pair.y === null ? null : pair;
  };

  function captureCoordinates(event) {
    if (!event) {
      return null;
    }

    const coordinateSpaces = {
      client: toCoordinatePair(event.clientX, event.clientY),
      page: toCoordinatePair(event.pageX, event.pageY),
      screen: toCoordinatePair(event.screenX, event.screenY),
      offset: toCoordinatePair(event.offsetX, event.offsetY),
    };

    const docEl = document && document.documentElement ? document.documentElement : null;
    const viewportWidth = typeof window !== 'undefined' && isFiniteNumber(window.innerWidth)
      ? window.innerWidth
      : docEl && isFiniteNumber(docEl.clientWidth)
        ? docEl.clientWidth
        : null;
    const viewportHeight = typeof window !== 'undefined' && isFiniteNumber(window.innerHeight)
      ? window.innerHeight
      : docEl && isFiniteNumber(docEl.clientHeight)
        ? docEl.clientHeight
        : null;

    const result = {};
    for (const [space, pair] of Object.entries(coordinateSpaces)) {
      if (pair) {
        result[space] = pair;
      }
    }

    if (isFiniteNumber(viewportWidth) || isFiniteNumber(viewportHeight)) {
      result.viewport = {
        width: isFiniteNumber(viewportWidth) ? viewportWidth : null,
        height: isFiniteNumber(viewportHeight) ? viewportHeight : null,
      };
    }

    const clientPair = coordinateSpaces.client;
    if (
      clientPair &&
      clientPair.x !== null &&
      clientPair.y !== null &&
      isFiniteNumber(viewportWidth) &&
      isFiniteNumber(viewportHeight) &&
      viewportWidth !== 0 &&
      viewportHeight !== 0
    ) {
      result.relative = {
        x: clientPair.x / viewportWidth,
        y: clientPair.y / viewportHeight,
      };
    }

    if (isFiniteNumber(event.timeStamp)) {
      result.eventTimestamp = event.timeStamp;
    }

    return Object.keys(result).length ? result : null;
  }

  function withPointerInfo(element, event, extra, options) {
    const rect = element && element.getBoundingClientRect ? element.getBoundingClientRect() : null;

    const payload = {
      tag: element ? element.tagName : null,
      id: element ? element.id : null,
      className: element ? element.className : null,
      coordinates: captureCoordinates(event),
      elementRect: rect
        ? {
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
            bottom: rect.bottom,
            right: rect.right,
            x: rect.x,
            y: rect.y
          }
        : null
    };

    if (options && options.includeText) {
      const textValue = element && typeof element.innerText === 'string' ? element.innerText.substring(0, 50) : '';
      payload.text = textValue;
    }

    if (options && options.includeHref) {
      const hrefValue = element && 'href' in element ? element.href || null : null;
      payload.href = hrefValue;
    }

    return Object.assign(payload, extra || {});
  }


  function setupPageEventListener() {

    let scrollArmed = true;
    
    // Listen for scroll on both window and document to catch all cases
    const handleScroll = (event) => {
        if (!scrollArmed) return;
        scrollArmed = false;

        const info = {
            x: window.scrollX || window.pageXOffset || document.documentElement.scrollLeft || 0,
            y: window.scrollY || window.pageYOffset || document.documentElement.scrollTop || 0
        };
        console.log('📜 Scroll detected:', info);
        sendEventPage('scroll', 'action:user', info);


        // don't over capture scroll events (was in 500 ms, which was missing plenty, but 100, also captured too many)
        setTimeout(() => { scrollArmed = true; }, 250);
    };
    
    // Attach to both window and document to ensure we catch scroll events
    window.addEventListener('scroll', handleScroll, { capture: true, passive: true });
    document.addEventListener('scroll', handleScroll, { capture: true, passive: true });

    // Click event
    document.addEventListener('click', (e) => {
        const element = e.target;
        const info = withPointerInfo(element, e, {
            x: e.clientX,
            y: e.clientY
        }, { includeText: true });
        sendEventPage('click', 'action:user', info);
    }, { capture: true });

    // Mousedown event
    document.addEventListener('mousedown', (e) => {
        const element = e.target;
        const info = withPointerInfo(element, e, {
            button: e.button,
            x: e.clientX,
            y: e.clientY
        });
        sendEventPage('mousedown', 'action:user', info);
    }, { capture: true });

    // Mouseup event
    document.addEventListener('mouseup', (e) => {
        const element = e.target;
        const info = withPointerInfo(element, e, {
            button: e.button,
            x: e.clientX,
            y: e.clientY
        });
        sendEventPage('mouseup', 'action:user', info);
    }, { capture: true });

    // Pointerdown event
    document.addEventListener('pointerdown', (e) => {
        const element = e.target;
        const info = withPointerInfo(element, e, {
            button: e.button,
            pointerType: e.pointerType,
            x: e.clientX,
            y: e.clientY
        });
        sendEventPage('pointerdown', 'action:user', info);
    }, { capture: true });

    // Pointerup event
    document.addEventListener('pointerup', (e) => {
        const element = e.target;
        const info = withPointerInfo(element, e, {
            button: e.button,
            pointerType: e.pointerType,
            x: e.clientX,
            y: e.clientY
        });
        sendEventPage('pointerup', 'action:user', info);
    }, { capture: true });

    // Contextmenu event
    document.addEventListener('contextmenu', (e) => {
        const element = e.target;
        const info = withPointerInfo(element, e, {
            x: e.clientX,
            y: e.clientY
        });
        sendEventPage('contextmenu', 'action:user', info);
    }, { capture: true });

    // Hover event (mouseover with throttling)
    let hoverTimeout = null;
    let lastHoveredElement = null;
    document.addEventListener('mouseover', (e) => {
        const element = e.target;
        
        // Only track if hovering over a different element
        if (element === lastHoveredElement) return;
        lastHoveredElement = element;
        
        // Clear previous timeout
        if (hoverTimeout) {
            clearTimeout(hoverTimeout);
        }
        
        // Throttle hover events to reduce frequency
        hoverTimeout = setTimeout(() => {
            const info = withPointerInfo(element, e, {
                x: e.clientX,
                y: e.clientY
            }, { includeText: true, includeHref: true });
            sendEventPage('hover', 'action:user', info);
        }, 300); // Send after 300ms of stable hover
    }, { capture: true });

    // Input event with throttling
    let inputTimeout = null;
    document.addEventListener('input', (e) => {
        try {
            const element = e.target;
            
            // Clear previous timeout
            if (inputTimeout) {
                clearTimeout(inputTimeout);
            }
            
            // Throttle input events to reduce frequency
            inputTimeout = setTimeout(() => {
                const info = {
                    tag: element.tagName,
                    id: element.id,
                    className: element.className,
                    value: element.value || ''
                };
                sendEventPage('input', 'action:user', info);
            }, 100); // Send after 100ms of no input
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


    document.addEventListener('submit', (e) => {
        const form = e.target;
        const info = {
            tag: form.tagName,
            id: form.id,
            className: form.className,
            action: form.action,
            method: form.method
        };
        sendEventPage('submit', 'action:user', info);
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

    // Back/Forward navigation detection using popstate
    window.addEventListener('popstate', (event) => {
        const info = {
            url: window.location.href,
            title: document.title,
            state: event.state,
            timestamp: Date.now(),
            message: 'Browser back/forward navigation'
        };
        sendEventPage('back', 'action:user', info);
    }, { capture: true });
    
    // Tab visibility change detection (tab switching)
    document.addEventListener('visibilitychange', () => {
        const info = {
            hidden: document.hidden,
            visibilityState: document.visibilityState,
            url: window.location.href,
            title: document.title,
            timestamp: Date.now(),
            message: document.hidden ? 'Tab switched away' : 'Tab became active'
        };
        sendEventPage('tab_visibility_changed', 'state:browser', info);
    });

    // Also detect history navigation using performance navigation API
    if (window.performance && window.performance.navigation) {
        // Check if page was loaded via back/forward on initial load
        if (window.performance.navigation.type === 2) {
            const info = {
                url: window.location.href,
                title: document.title,
                timestamp: Date.now(),
                message: 'Page loaded via back/forward button',
                navigationType: 'back_forward'
            };
            sendEventPage('back', 'action:user', info);
        }
    }
  }

  // Setup immediately if DOM is already loaded
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupPageEventListener);
  } else {
    setupPageEventListener();
  }

  // Attempt immediate flush in case binding exists already
  try { flushQueuedEvents(); } catch (_) {}
}
"""
