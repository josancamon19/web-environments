import logging
from src.steps.step_record import StepRecord

logger = logging.getLogger(__name__)


class NewPageEvent:
    def __init__(self):
        self._page_event_handlers = {}
        self.step_record = StepRecord()

    async def attach_page(self, page):
        await page.wait_for_load_state(
            "load"
        )  # Esto garantiza que todos los recursos de la página se han cargado
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
                await self.step_record.record_step(
                    {
                        "event_info": {
                            "event_type": "domcontentloaded",
                            "event_context": "state:page",
                            "event_data": {"url": p.url},
                        },
                        "prefix_action": f"state:page",
                    }
                )

            async def on_load(p=page):
                # logger.info(f"[PAGE_EVENT] Page loaded: {p.url}")
                # Scripts are already injected by context.add_init_script

                # Record page load as a high-level event
                await self.step_record.record_step(
                    {
                        "event_info": {
                            "event_type": "loaded",
                            "event_context": "state:page",
                            "event_data": {"url": p.url},
                        },
                        "prefix_action": f"state:page",
                    }
                )

            async def on_framenavigated(frame, p=page):
                if frame == p.main_frame:  # Only track main frame navigation
                    # logger.info(f"[PAGE_EVENT] Main frame navigated to {frame.url}")
                    # Scripts are already injected by context.add_init_script
                    await self.step_record.record_step(
                        {
                            "event_info": {
                                "event_type": "navigated",
                                "event_context": "state:browser",
                                "event_data": {"url": frame.url},
                            },
                            "prefix_action": f"state:browser",
                        },
                        omit_screenshot=True,
                    )

            handlers = [
                ("domcontentloaded", on_domcontentloaded),
                ("load", on_load),
                ("framenavigated", on_framenavigated),
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
                    page.off(event_name, handler)
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
