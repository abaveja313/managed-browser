import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator, Mapping, Optional, Tuple, cast

from fake_useragent import UserAgent
from langchain_core.language_models import BaseChatModel
from playwright.async_api import BrowserContext as PlaywrightBrowserContext, Page

from browser_use.agent.service import Agent
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig

__all__ = [
    "BrowserManager",
    "ManagedSession",
    "AgentWithControlTransfer",
]

logger = logging.getLogger(__name__)


class AgentWithControlTransfer(Agent):
    """
    Agent subclass for handling control transfer.
    """

    async def run(self, *args: Any, **kwargs: Any) -> Tuple[Any, Page]:  # type: ignore[override]
        """
        Execute the agent's task and capture the final page context.

        Args:
            *args: Positional arguments forwarded to the base Agent.run().
            **kwargs: Keyword arguments forwarded to the base Agent.run().

        Returns:
            A tuple of (base_agent_output, final_page).

        Raises:
            Exception: Propagates any exception raised by the base Agent.run().
        """
        # Perform the original run logic
        base_output = await super().run(*args, **kwargs)
        # Grab the Playwright Page the agent is currently on
        final_page = self.browser_context.agent_current_page  # type: ignore[attr-defined]
        return base_output, final_page


@dataclass(frozen=True)
class ManagedSession:
    """
    Encapsulates a Playwright browser context plus factory metadata
    for creating browser-use Agents.

    Attributes:
        browser_context: The PlaywrightBrowserContext for page interactions.
        _factory_context: The internal BrowserContext wrapper instance.
        _browser: The shared Browser instance for this session.
    """
    browser_context: PlaywrightBrowserContext
    _factory_context: BrowserContext
    _browser: Browser

    def make_agent(
            self,
            start_page: Page,
            llm: BaseChatModel,
            **kwargs: Any,
    ) -> AgentWithControlTransfer:
        """
        Instantiate an AgentWithControlTransfer bound to this session.

        This sets both the human-facing and agent-facing starting pages,
        then constructs the agent with the shared Browser and LLM.

        Args:
            start_page: The initial Playwright Page for the agent.
            llm: An LLM instance for agent reasoning.
            **kwargs: Additional keyword args forwarded to the Agent constructor.

        Returns:
            AgentWithControlTransfer: Ready-to-run agent instance.
        """
        # Ensure the session points to the intended start_page
        self._factory_context.human_current_page = start_page
        self._factory_context.agent_current_page = start_page

        agent_args = {
            "browser": self._browser,
            "browser_context": self._factory_context,
            "llm": llm,
            **kwargs,
        }
        return AgentWithControlTransfer(**agent_args)


class BrowserManager:
    """
    Factory for creating and managing Browser and BrowserContext instances.

    Handles browser startup/shutdown and provides an async context manager
    to yield ManagedSession objects for scoped automation.
    """

    def __init__(
            self,
            *,
            browser_config: BrowserConfig,
            autostart: bool = True,
    ) -> None:
        """
        Initialize the BrowserManager.

        Args:
            browser_config: Settings for launching the browser.
            autostart: If True, immediately starts the Browser.

        Raises:
            RuntimeError: If browser startup fails.
        """
        self._browser_config = browser_config
        self._browser: Optional[Browser] = None

        if autostart:
            self.start()

    def start(self) -> None:
        """
        Launch the Browser with the stored configuration.

        Raises:
            RuntimeError: If the browser fails to start.
        """
        try:
            self._browser = Browser(config=self._browser_config)
            logger.info("Browser started successfully.")
        except Exception as exc:
            logger.error("Failed to start browser: %s", exc)
            raise RuntimeError("Could not start browser") from exc

    @asynccontextmanager
    async def managed_context(
            self,
            *,
            context_kwargs: Optional[Mapping[str, Any]] = None,
            use_tracing: bool = False,
            tracing_output_path: Optional[Path] = None,
            randomize_user_agent: bool = True,
    ) -> AsyncGenerator[ManagedSession, None]:
        """
        Async context manager that yields a ManagedSession.

        It creates a new BrowserContext, optionally records a trace,
        and ensures cleanup on exit.

        Args:
            context_kwargs: Overrides for BrowserContextConfig fields.
            use_tracing: Enable Playwright tracing if True.
            tracing_output_path: File path to save trace (required if use_tracing).
            randomize_user_agent: If True, injects a random UA string.

        Yields:
            ManagedSession: Encapsulated session for agent usage.

        Raises:
            AssertionError: If use_tracing is True without a path.
            RuntimeError: If the browser is not started.
        """
        # validate preconditions
        assert not (use_tracing and tracing_output_path is None), (
            "tracing_output_path must be set when use_tracing is True"
        )
        if self._browser is None:
            raise RuntimeError("BrowserManager.start() must be called before managed_context")

        ua_override: Mapping[str, Any] = {}
        if randomize_user_agent:
            ua_override = {"user_agent": UserAgent().random}
            logger.debug("Randomized UA: %s", ua_override["user_agent"])

        config = BrowserContextConfig(**ua_override, **(context_kwargs or {}))
        ctx_wrapper: BrowserContext = await self._browser.new_context(config=config)

        # prime the session (closes default about:blank)
        await ctx_wrapper.get_session()
        playwright_ctx = cast(
            PlaywrightBrowserContext,
            ctx_wrapper.session.context,
        )
        if playwright_ctx.pages and playwright_ctx.pages[0].url == "about:blank":
            await playwright_ctx.pages[0].close()

        if use_tracing:
            # TODO do we need to expose this?
            await playwright_ctx.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=False
            )

        try:
            yield ManagedSession(
                browser_context=playwright_ctx,
                _factory_context=ctx_wrapper,
                _browser=self._browser,
            )
        finally:
            # tracing stop
            if use_tracing:
                logger.info("Stopping trace to %s", tracing_output_path)
                await playwright_ctx.tracing.stop(path=tracing_output_path)

            # cleanup
            logger.info("Closing browser context")
            await ctx_wrapper.close()

    async def shutdown(self) -> None:
        """
        Gracefully close the Browser and release all resources.

        Raises:
            RuntimeError: If shutdown fails or browser was never started.
        """
        if not self._browser:
            raise RuntimeError("Browser was not started or already shut down.")
        try:
            await self._browser.close()
            logger.info("Browser shut down successfully.")
        except Exception as exc:
            logger.error("Error during browser shutdown: %s", exc)
            raise
