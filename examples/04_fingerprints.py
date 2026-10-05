"""Fingerprint strategies, cheap change indicators, and a custom strategy."""

import asyncio

from refresh_engine import (
    ContentHash,
    DiscoveryResult,
    FingerprintStrategyABC,
    PlanAction,
    RefreshEngine,
    RefreshRequest,
    Resource,
    ResourceFingerprint,
    ResourceSnapshot,
    canonical_digest,
)


class ArticleSource:
    """Articles with an ETag-like revision that changes with every edit."""

    def __init__(self) -> None:
        self.articles = {
            "intro": {"title": "Intro", "body": "Hello", "views": 10},
            "guide": {"title": "Guide", "body": "Steps", "views": 20},
        }
        self.revisions = {"intro": "r1", "guide": "r1"}
        self.reads = 0

    async def discover(self) -> DiscoveryResult:
        return DiscoveryResult(
            Resource(
                article_id,
                cheap_indicator=self.revisions[article_id],
                cheap_indicator_reliable=True,
            )
            for article_id in sorted(self.articles)
        )

    async def snapshot(self, resource: Resource, /) -> ResourceSnapshot:
        self.reads += 1
        return ResourceSnapshot(
            resource.resource_id, content=self.articles[resource.resource_id]
        )


class PublishedFields(FingerprintStrategyABC):
    """Fingerprint only the fields that affect the published page."""

    async def fingerprint(self, snapshot: ResourceSnapshot, /) -> ResourceFingerprint:
        article = snapshot.content
        published = {"title": article["title"], "body": article["body"]}
        return ResourceFingerprint("sha256:published", canonical_digest(published))


async def noop(
    resource: Resource,
    snapshot: ResourceSnapshot | None,
    action: PlanAction,
    request: RefreshRequest,
    /,
) -> None:
    return None


async def main() -> None:
    source = ArticleSource()
    async with RefreshEngine(source, noop, fingerprint=ContentHash()) as engine:
        await engine.refresh()
        source.reads = 0
        unchanged = await engine.refresh()
        print(
            f"reliable indicator: reads={source.reads}, "
            f"unchanged={unchanged.unchanged_count}"
        )

        source.articles["intro"] = {"title": "Intro", "body": "Hello", "views": 11}
        source.revisions["intro"] = "r2"
        source.reads = 0
        counted = await engine.refresh()
        print(
            f"content hash:       reads={source.reads}, "
            f"modified={counted.modified_count}"
        )

    source = ArticleSource()
    async with RefreshEngine(source, noop, fingerprint=PublishedFields()) as engine:
        await engine.refresh()
        source.articles["intro"] = {"title": "Intro", "body": "Hello", "views": 12}
        source.revisions["intro"] = "r3"
        views_only = await engine.refresh()
        print(
            f"custom strategy:    modified={views_only.modified_count}, "
            f"unchanged={views_only.unchanged_count}"
        )
        source.articles["intro"] = {"title": "Intro", "body": "Hi", "views": 12}
        source.revisions["intro"] = "r4"
        edited = await engine.refresh()
        print(f"                    after an edit: modified={edited.modified_count}")


if __name__ == "__main__":
    asyncio.run(main())
