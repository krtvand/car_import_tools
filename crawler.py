"""Crawlee-based scraper for a bazaraki.com category (default: cars)."""
from __future__ import annotations

from datetime import timedelta

from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

import db
import parsers

CARS_URL = "https://www.bazaraki.com/car-motorbikes-boats-and-parts/cars-trucks-and-vans/"


async def run_scrape(
    category_url: str = CARS_URL,
    max_pages: int = 3,
    details: bool = True,
    concurrency: int = 5,
) -> None:
    """Scrape up to ``max_pages`` of a category, optionally enriching each
    advert with detail-page fields, and upsert everything into SQLite.
    """
    db.init_db()

    crawler = BeautifulSoupCrawler(
        max_request_retries=3,
        request_handler_timeout=timedelta(seconds=60),
        concurrency_settings=ConcurrencySettings(desired_concurrency=concurrency),
    )

    @crawler.router.default_handler
    async def list_handler(context: BeautifulSoupCrawlingContext) -> None:
        page = context.request.user_data.get("page", 1)
        cards = parsers.parse_cards(context.soup)
        context.log.info(f"Page {page}: {len(cards)} listings")

        detail_requests: list[Request] = []
        for card in cards:
            db.upsert_listing(card)
            if details:
                detail_requests.append(Request.from_url(card["url"], label="detail"))
        if detail_requests:
            await context.add_requests(detail_requests)

        if page < max_pages:
            nxt = parsers.next_page_url(context.soup, page)
            if nxt:
                await context.add_requests(
                    [Request.from_url(nxt, user_data={"page": page + 1})]
                )

    @crawler.router.handler("detail")
    async def detail_handler(context: BeautifulSoupCrawlingContext) -> None:
        ad_id = parsers._ad_id_from_url(context.request.url)
        if ad_id is None:
            return
        data = parsers.parse_detail(context.soup)
        data["ad_id"] = ad_id
        db.upsert_listing(data)

    await crawler.run([Request.from_url(category_url, user_data={"page": 1})])