"""URL-to-SiteAdapter registry for the 7 main job boards plus Generic external fallback."""

from typing import Tuple

from browser.adapters.apec import APECAdapter
from browser.adapters.base import GenericAdapter, SiteAdapter
from browser.adapters.hellowork import HelloWorkAdapter
from browser.adapters.indeed import IndeedAdapter
from browser.adapters.jobteaser import JobTeaserAdapter
from browser.adapters.linkedin import LinkedInAdapter
from browser.adapters.meteojob import MeteojobAdapter
from browser.adapters.wttj import WTTJAdapter

SITE_ADAPTERS: Tuple[SiteAdapter, ...] = (
    LinkedInAdapter(),
    HelloWorkAdapter(),
    WTTJAdapter(),
    JobTeaserAdapter(),
    IndeedAdapter(),
    APECAdapter(),
    MeteojobAdapter(),
)

GENERIC_ADAPTER: SiteAdapter = GenericAdapter()


def resolve_adapter(url: str) -> SiteAdapter:
    """Return the matching SiteAdapter for a URL, or GenericAdapter for external ATS portals."""
    for adapter in SITE_ADAPTERS:
        if adapter.matches_url(url):
            return adapter
    return GENERIC_ADAPTER
