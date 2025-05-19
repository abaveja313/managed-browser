# managed-browser

A simple wrapper to integrate language-model agents into your existing Playwright (Python) scripts via the popular `browser-use` library, with built-in Playwright tracing support.

## Supports

- Python 3.11+

## Objective

Agents are still unreliable and far from production-ready—especially web agents, given the enormous diversity and challenges of navigating real-world sites. 
This package lets you keep your existing Playwright flows and seamlessly “drop in” agent tasks where you need them, without wrestling with a clunky API. It also exposes Playwright’s powerful tracing features around each agent run so you can debug and audit exactly what happened in the browser.

## Installation

Requires Python 3.11+ and a [PEP 517](https://www.python.org/dev/peps/pep-0517/)-compatible build backend.

```bash
# UV
uv add managed-browser
# Pip
pip install managed-browser
```

## Usage

```python
import asyncio
from browser_use import BrowserConfig
from langchain_openai import ChatOpenAI
from managed_browser import BrowserManager

async def main():
    # Initialize the manager with your Playwright config
    bm = BrowserManager(
        browser_config=BrowserConfig(headless=False)
    )

    # Your LLM of choice
    llm = ChatOpenAI(model='gpt-4o')

    # Create a managed context (with tracing enabled under the hood)
    async with bm.managed_context() as session:
        page = await session.browser_context.new_page()

        # -- Your deterministic Playwright steps --
        await page.goto("https://github.com", wait_until="domcontentloaded")
        await page.wait_for_timeout(1_000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1_000)

        # -- Drop in an agent task --
        agent = session.make_agent(
            llm=llm,
            task=(
                "Extract the visible link text in the website's footer. "
                "Return your answer wrapped in <result>...</result>."
            )
        )
        result = await agent.run()
        print(result)

    # After exit, Playwright trace(s) for the agent run will be saved in ./playwright_traces/

if __name__ == '__main__':
    asyncio.run(main())
```

## API Reference

### `BrowserManager`

```python
BrowserManager(
    browser_config: BrowserConfig,
    *,
    trace_dir: Optional[str] = "./playwright_traces"
)
```
- **browser_config**: your `browser-use`/Playwright settings  
- **trace_dir**: where to dump Playwright trace ZIPs

#### `.managed_context()`

An async context manager yielding a `ManagedSession`:

- `session.browser_context` → Playwright `BrowserContext`  
- `session.make_agent(llm: BaseChatModel, task: str, **kwargs)` → ready-to-run agent

#### `ManagedSession.make_agent(...)`

Wraps `browser-use` arguments into an LLM agent. Returns an object with a `.run()` coroutine.

### Tracing

By default, each `agent.run()` invocation is wrapped in a Playwright trace. Trace files (ZIP) will appear under `trace_dir` with timestamps for easy playback:

```bash
# Playwright CLI
npx playwright show-trace ./playwright_traces/<timestamp>.zip
```

## Contributing

Contributions, issues, and feature requests are welcome!  
Please fork the repo and submit a pull request, or open an issue for discussion.

## License
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.  

