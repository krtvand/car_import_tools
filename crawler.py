"""Crawlee-based scraper for a filtered bazaraki.com cars search."""
from __future__ import annotations

from datetime import timedelta

from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

import config
import db
import parsers
from config import CarFilters


async def run_scrape(
    filters: CarFilters | None = None,
    max_pages: int = 3,
    details: bool = True,
    concurrency: int = 1,
) -> dict:
    """Scrape up to ``max_pages`` of a filtered cars search into SQLite.

    When the filters include year/engine-size (whose codes are site-specific),
    a one-off "bootstrap" fetch of the category page resolves those codes from
    the live <select> options before the filtered crawl begins.
    """
    filters = filters or config.DEFAULT_FILTERS
    db.init_db()
    run_id = db.start_run(filters)

    seen_ad_ids: set[int] = set()
    # Flipped when a next page exists but max_pages stops us fetching it; a
    # truncated run must not delist adverts merely sitting on an unseen page.
    state = {"truncated": False}

    crawler = BeautifulSoupCrawler(
        max_request_retries=3,
        request_handler_timeout=timedelta(seconds=60),
        concurrency_settings=ConcurrencySettings(desired_concurrency=concurrency),
    )

    async def enqueue_first_page(context: BeautifulSoupCrawlingContext, url: str) -> None:
        await context.add_requests([Request.from_url(url, user_data={"page": 1})])

    @crawler.router.handler("bootstrap")
    async def bootstrap_handler(context: BeautifulSoupCrawlingContext) -> None:
        year_codes = parsers.parse_year_codes(context.soup)
        engine_codes = parsers.parse_engine_codes(context.soup)
        url = config.build_search_url(filters, year_codes=year_codes, engine_codes=engine_codes)
        context.log.info(f"Resolved filters -> {url}")
        await enqueue_first_page(context, url)

    @crawler.router.default_handler
    async def list_handler(context: BeautifulSoupCrawlingContext) -> None:
        page = context.request.user_data.get("page", 1)
        cards = parsers.parse_cards(context.soup)
        context.log.info(f"Page {page}: {len(cards)} listings")

        detail_requests: list[Request] = []
        for card in cards:
            seen_ad_ids.add(card["ad_id"])
            db.upsert_listing(card)
            if details:
                detail_requests.append(Request.from_url(card["url"], label="detail"))
        if detail_requests:
            await context.add_requests(detail_requests)

        has_next = parsers.has_next_page(context.soup, page)
        if page < max_pages and has_next:
            nxt = parsers.with_page(context.request.url, page + 1)
            await context.add_requests([Request.from_url(nxt, user_data={"page": page + 1})])
        elif has_next:
            state["truncated"] = True  # more pages exist but max_pages caps us

    @crawler.router.handler("detail")
    async def detail_handler(context: BeautifulSoupCrawlingContext) -> None:
        ad_id = parsers._ad_id_from_url(context.request.url)
        if ad_id is None:
            return
        data = parsers.parse_detail(context.soup)
        data["ad_id"] = ad_id
        db.upsert_listing(data)

    if config.needs_option_resolution(filters):
        start = Request.from_url(config.BASE_URL + config.base_path(filters), label="bootstrap")
    else:
        start = Request.from_url(config.build_search_url(filters), user_data={"page": 1})

    await crawler.run([start])

    completed = not state["truncated"]
    delisted = db.finalize_run(run_id, seen_ad_ids, completed)
    return {
        "run_id": run_id,
        "seen": len(seen_ad_ids),
        "completed": completed,
        "delisted": delisted,
    }