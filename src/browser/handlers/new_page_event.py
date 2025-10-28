import logging
from browser.recorder import Recorder

logger = logging.getLogger(__name__)


class PlaywrightPageEvent:
    def __init__(self):
        self._page_event_handlers = {}
        self.recorder = Recorder()

    async def attach(self, page):
        #  Guarantee that all resources of the page have been loaded
        await page.wait_for_load_state("load")
        # Treat the most recently seen page as the active page for screenshots/DOM
        # logger.info(f"[ATTACH_PAGE] Attaching listeners to page: {page.url}")
        try:
            # No need to re-inject scripts here since context.add_init_script handles it
            # Just ensure the page-specific binding is there (defensive)
            # The context-level expose_binding should already work, but add page-level too
            # logger.info("[ATTACH_PAGE] Page attached with context-level scripts already active")
            # Bind page at definition time to avoid late-binding issues
            async def on_domcontentloaded(p=page):
                # logger.info(f"[PAGE_EVENT] DOM content loaded for {p.url}")
                await self.recorder.record_step(
                    {
                        "event_info": {
                            "event_type": "domcontentloaded",
                            "event_context": "state:page",
                            "event_data": {"url": p.url},
                        },
                        "prefix_action": "state:page",
                        "source_page": p,
                    }
                )

            async def on_load(p=page):
                # logger.info(f"[PAGE_EVENT] Page loaded: {p.url}")
                # Scripts are already injected by context.add_init_script

                # Record page load as a high-level event
                await self.recorder.record_step(
                    {
                        "event_info": {
                            "event_type": "loaded",
                            "event_context": "state:page",
                            "event_data": {"url": p.url},
                        },
                        "prefix_action": "state:page",
                        "source_page": p,
                    }
                )

            async def on_framenavigated(frame, p=page):
                if frame == p.main_frame:  # Only track main frame navigation
                    # logger.info(f"[PAGE_EVENT] Main frame navigated to {frame.url}")
                    # Scripts are already injected by context.add_init_script
                    await self.recorder.record_step(
                        {
                            "event_info": {
                                "event_type": "navigated",
                                "event_context": "state:browser",
                                "event_data": {"url": frame.url},
                            },
                            "prefix_action": "state:browser",
                            "source_page": p,
                        },
                        omit_screenshot=True,
                    )

            async def on_close(p=page):
                # logger.info(f"[PAGE_EVENT] Tab/page closed: {p.url}")
                await self.recorder.record_step(
                    {
                        "event_info": {
                            "event_type": "tab_closed",
                            "event_context": "state:browser",
                            "event_data": {"url": p.url, "final_url": p.url},
                        },
                        "prefix_action": "state:browser",
                        "source_page": p,
                    },
                    omit_screenshot=True,
                )

            handlers = [
                ("domcontentloaded", on_domcontentloaded),
                ("load", on_load),
                ("framenavigated", on_framenavigated),
                ("close", on_close),
            ]
            self._page_event_handlers[page] = handlers
            for event_name, handler in handlers:
                page.on(event_name, handler)

        except Exception as e:
            logger.error(f"[ATTACH_PAGE] Error attaching page listeners: {e}")

    def _detach_page_listeners(self, page):
        try:
            handlers = self._page_event_handlers.pop(page, [])
            for event_name, handler in handlers:
                try:
                    if hasattr(page, "off"):
                        page.off(event_name, handler)
                    else:
                        page.remove_listener(event_name, handler)
                except Exception as e:
                    logger.error(f"[DETACH_PAGE] Error detaching page listeners: {e}")
            # logger.info(f"[DETACH_PAGE] Page listeners detached")
        except Exception as e:
            logger.error(f"[DETACH_PAGE] Error detaching page listeners: {e}")

    def detach_all_page_listeners(self):
        try:
            for page in list(self._page_event_handlers.keys()):
                self._detach_page_listeners(page)
        except Exception as e:
            logger.error(f"[DETACH_ALL_PAGE] Error detaching all page listeners: {e}")
