"""Запуск модуля парсинга без API: `python -m app.parsing --help`.

Полезно для отладки источника и для разового сбора данных в БД, когда
backend не поднят.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import Source
from app.parsing.poller import PollReport
from app.parsing.scheduler import PollingScheduler
from app.parsing.seed import seed_default_sources
from app.parsing.registry import get_fetcher


def _print_reports(reports: list[PollReport]) -> None:
    for report in reports:
        line = (
            f"{report.source_name[:34]:34} {report.status:13} "
            f"получено={report.fetched:3} сохранено={report.stored:3} повторов={report.duplicates:3}"
        )
        print(line if not report.error else f"{line}  {report.error}")
    print(
        f"\nисточников: {len(reports)}, "
        f"новых материалов: {sum(report.stored for report in reports)}, "
        f"ошибок: {sum(report.status == 'error' for report in reports)}"
    )


def _list_sources() -> None:
    with SessionLocal() as db:
        for source in db.scalars(select(Source).order_by(Source.name)).all():
            pollable = get_fetcher((source.config or {}).get("fetcher") or source.type) is not None
            state = source.last_status or "не опрашивался"
            flag = "on " if source.enabled else "off"
            print(
                f"{flag} {source.id:24} {source.type:10} каждые {source.poll_interval_minutes:>4} мин  "
                f"{state:13} {'' if pollable else '(ручной тип)'} {source.url or ''}"
            )


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    scheduler = PollingScheduler(SessionLocal, settings)
    if args.source:
        reports = await scheduler.run_sources(args.source)
    elif args.all:
        with SessionLocal() as db:
            ids = [
                source.id
                for source in db.scalars(select(Source).where(Source.enabled.is_(True))).all()
                if source.url and get_fetcher((source.config or {}).get("fetcher") or source.type)
            ]
        reports = await scheduler.run_sources(ids)
    else:
        reports = await scheduler.run_due()
    _print_reports(reports)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.parsing", description="Опрос источников GosRadar")
    parser.add_argument("--seed", action="store_true", help="добавить источники по умолчанию")
    parser.add_argument("--list", action="store_true", help="показать источники и статус последнего опроса")
    parser.add_argument("--all", action="store_true", help="опросить все включённые источники, игнорируя расписание")
    parser.add_argument("--source", action="append", metavar="ID", help="опросить конкретный источник (можно повторять)")
    parser.add_argument("--verbose", action="store_true", help="подробный лог")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    init_db()

    if args.seed:
        with SessionLocal() as db:
            report = seed_default_sources(db)
        print(f"добавлено источников: {len(report.created)}, уже были: {len(report.existing)}")
    if args.list:
        _list_sources()
    if args.seed or args.list:
        if not (args.all or args.source):
            return
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
