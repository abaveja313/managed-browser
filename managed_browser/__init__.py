import logging
from managed_browser.managed_browser import ManagedSession, BrowserManager

# Define a default null handler to avoid "No handler found" warnings
logging.getLogger(__name__).addHandler(logging.NullHandler())