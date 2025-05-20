import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Optional, cast

from browser_use.agent.service import Agent
from browser_use.browser.browser import BrowserConfig, Browser
from browser_use.browser.context import BrowserContextConfig, BrowserContext
from fake_useragent import UserAgent
from langchain_core.language_models import BaseChatModel
from playwright.async_api import BrowserContext as PlaywrightBrowserContext, Page

logger = logging.getLogger(__name__)


@dataclass
class ManagedSession:
    """
    A managed browser session containing the Playwright browser context and related components.

    This class encapsulates a browser session with its associated context and provides
    a convenient interface for creating agents that can interact with the browser.

    Attributes:
        browser_context: The Playwright browser context used for web interactions
        _factory_context: The BrowserContext that created this session
        _cdp_url: Optional Chrome DevTools Protocol URL for debugging
    """
    browser_context: PlaywrightBrowserContext
    _factory_context: BrowserContext
    _browser: Browser

    def make_agent(self, start_page: Page, llm: BaseChatModel, **kwargs) -> Agent:
        """
        Creates an Agent instance for browser automation using the provided LLM.

        This method instantiates a new Browser object with the current CDP URL
        and creates an Agent that can use both the browser and the language model
        for automated interactions.

        Args:
            start_page: the page to start on
            llm: The language model to use for agent cognition
            **kwargs: Additional keyword arguments to pass to the Agent constructor

        Returns:
            Agent: A configured agent that can interact with the browser
        """
        self._factory_context.human_current_page = start_page
        self._factory_context.agent_current_page = start_page

        agent_kws = {"browser": self._browser,
                     "browser_context": self._factory_context, "llm": llm, **kwargs}
        print(agent_kws)
        return Agent(**agent_kws)


class BrowserManager:
    """
    Manages browser instances and their lifecycle for browser automation.

    This class handles the creation, configuration, and cleanup of browser instances
    and provides utilities for creating managed browser contexts. It supports Chrome
    DevTools Protocol (CDP) for remote debugging and browser control.

    The manager ensures that browser resources are properly allocated and can be
    cleanly released when no longer needed.
    """

    def __init__(
            self,
            *,
            browser_config: BrowserConfig,
            autostart: bool = True,
    ) -> None:
        """
        Initialize a BrowserManager with the specified configuration.

        Args:
            browser_config: Configuration for browser initialization
            autostart: If True, automatically starts the browser on initialization

        Raises:
            IOError: If the specified CDP port is not available and bypass_port_check is False
        """
        self.browser_config = browser_config
        self._browser = None

        if autostart:
            self.start()

    def start(self):
        """
        Start the browser instance.

        Creates a new Browser instance using the configured browser settings
        and makes it available for creating contexts and sessions.
        """
        self._browser = Browser(config=self.browser_config)

    @asynccontextmanager
    async def managed_context(
            self,
            *,
            context_kwargs: Optional[dict] = None,
            use_tracing: bool = False,
            tracing_output_path: Optional[Path] = None,
            randomize_user_agent: bool = True,
    ) -> AsyncGenerator[ManagedSession, None]:
        """
        Create and manage a browser context as an async context manager.

        This method provides a convenient way to create a browser context with
        specific configurations and ensure it's properly cleaned up when done.
        It supports tracing for debugging and can randomize the user agent.

        Args:
            context_kwargs: Optional additional keyword arguments for context creation
            use_tracing: If True, enables Playwright tracing for debugging
            tracing_output_path: Path where tracing data will be saved if tracing is enabled
            randomize_user_agent: If True, uses a random user agent string

        Yields:
            ManagedSession: A session object containing the browser context

        Raises:
            AssertionError: If tracing is enabled without specifying an output path
        """
        assert use_tracing == bool(tracing_output_path), "You must specify an output path for tracing"
        if randomize_user_agent:
            ua = UserAgent().random
            logger.debug(f"Using User-Agent: {ua}")
            kwargs = {"user_agent": ua}
        else:
            kwargs = {}

        context_config = BrowserContextConfig(
            **kwargs,
            **(context_kwargs or {}),
        )

        # The browser-use "BrowserContext" wraps the underlying context
        ctx_wrapper: BrowserContext = await self._browser.new_context(config=context_config)

        # Will open up about:blank as a starting point, which causes problems
        await ctx_wrapper.get_session()
        ctx: PlaywrightBrowserContext = cast(PlaywrightBrowserContext, ctx_wrapper.session.context)

        if len(ctx.pages) > 1 and ctx.pages[0].url == 'about:blank':
            await ctx.pages[0].close()

        try:
            if use_tracing:
                await ctx.tracing.start()
            yield ManagedSession(
                browser_context=cast(PlaywrightBrowserContext, ctx_wrapper.session.context),
                _factory_context=ctx_wrapper,
                _browser=self._browser,
            )
        finally:
            if use_tracing:
                logger.info(f"Producing trace at {tracing_output_path!r}")
                await ctx.tracing.stop(path=tracing_output_path)

            logger.info("Closing BrowserContext…")
            await ctx_wrapper.close()

    async def shutdown(self) -> None:
        """
        Tear down Playwright and browser resources.

        This method should be called when the browser manager is no longer needed
        to ensure all browser resources are properly released and connections closed.
        """
        await self._browser.close()
