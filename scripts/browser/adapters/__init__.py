"""Site adapters for the 7 main job boards plus a Generic external ATS adapter."""

from browser.adapters.apec import APECAdapter
from browser.adapters.base import GenericAdapter, SiteAdapter
from browser.adapters.hellowork import HelloWorkAdapter
from browser.adapters.indeed import IndeedAdapter
from browser.adapters.jobteaser import JobTeaserAdapter
from browser.adapters.linkedin import LinkedInAdapter
from browser.adapters.meteojob import MeteojobAdapter
from browser.adapters.registry import GENERIC_ADAPTER, SITE_ADAPTERS, resolve_adapter
from browser.adapters.wttj import WTTJAdapter

__all__ = [
    "APECAdapter",
    "GENERIC_ADAPTER",
    "GenericAdapter",
    "HelloWorkAdapter",
    "IndeedAdapter",
    "JobTeaserAdapter",
    "LinkedInAdapter",
    "MeteojobAdapter",
    "SITE_ADAPTERS",
    "SiteAdapter",
    "WTTJAdapter",
    "resolve_adapter",
]
