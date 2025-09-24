# Firefox configuration constants
FIREFOX_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-plugins",
    "--disable-default-apps",
    "--disable-hang-monitor",
    "--disable-prompt-on-repost",
    "--disable-sync",
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--search-engine=Google",
    "--search-default-engine=Google",
    "--no-first-run", 
    "--disable-background-timer-throttling",  
    "--disable-backgrounding-occluded-windows",  
    
]

FIREFOX_CONTEXT_CONFIG = {
    "viewport": {"width": 1366, "height": 768},
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
    "locale": "en-US",
    "timezone_id": "America/New_York",
    "permissions": ["geolocation"],
    "geolocation": {"longitude": -74.006, "latitude": 40.7128},
    "color_scheme": "light",
    "extra_http_headers": {
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-User": "?1",
        "Sec-Fetch-Dest": "document",
    },
}
