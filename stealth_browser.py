import asyncio
import logging
from playwright.async_api import async_playwright
from browser_config import BROWSER_ARGS, CONTEXT_CONFIG
from stealth_scripts import STEALTH_SCRIPT, PAGE_EVENT_LISTENER_SCRIPT
from stepRecord import StepRecord

logger = logging.getLogger(__name__)

class StealthBrowser:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def launch(self):
        """Launch stealth browser (like in Node.js)"""
        self.playwright = await async_playwright().start()
        
        # Launch browser with stealth args (matching your Node.js config)
        self.browser = await self.playwright.chromium.launch(
            headless=False,
            args=BROWSER_ARGS
        )

        # Create context
        self.context = await self.browser.new_context(**CONTEXT_CONFIG)

        self.page = await self.context.new_page()
        
        # Listen to console messages from the browser
        # self.page.on("console", lambda msg: print(f"🌐 Browser console: {msg.text}"))
        
        await self.apply_stealth_techniques()
        await self.setup_dom_listeners()
        
        return self.page

    async def apply_stealth_techniques(self):
        """Apply stealth techniques to avoid detection"""
        await self.page.add_init_script(STEALTH_SCRIPT)

    async def setup_dom_listeners(self):
        """Setup DOM event listeners (like in Node.js)"""
        print("🔧 Setting up DOM listeners...")
        await self.page.expose_function("onPageEvent", self.page_event_handler)
        await self.page.add_init_script(PAGE_EVENT_LISTENER_SCRIPT)
        print("✅ DOM listeners setup complete")

    async def page_event_handler(self, event_info):
        """Log click events (like console.log in Node.js)"""
        step_record = StepRecord()
        step_record.record_step({
            'event_info': event_info
        })

    async def close(self):
        """Close browser (like in Node.js)"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
