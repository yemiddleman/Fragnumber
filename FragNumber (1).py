# meta developer: @boostpayc
# scope: heroku_min 1.6.2

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from html import unescape
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from datetime import datetime

from herokutl.types import Message

from .. import loader, utils


FRAGMENT_LIST_URL = 'https://fragment.com/numbers?sort=price_asc&filter=sale'
FRAGMENT_SOLD_URL = 'https://fragment.com/numbers?sort=price_desc&filter=sold'
USER_AGENT = (
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
)
ROW_RE = re.compile(r'<tr class="tm-row-selectable">(.*?)</tr>', re.S)
NUMBER_RE = re.compile(r'<div class="table-cell-value tm-value">(\+888\s*\d{4}\s*\d{4})</div>')
PRICE_RE = re.compile(r'icon-before icon-ton">([\d,]+)</div>')
STATUS_RE = re.compile(r'tm-status-avail">([^<]+)</div>')
THIN_STATUS_RE = re.compile(r'table-cell-status-thin[^>]*>([^<]+)</div>')
TON_RATE_RE = re.compile(r'"tonRate":([0-9.]+)')
SALE_PRICE_RE = re.compile(r'<th[^>]*>Sale Price</th>.*?icon-before icon-ton">([\d,]+)</div>', re.S | re.I)
SOLD_DATE_RE = re.compile(r'Purchased on <time datetime="([^"]+)"', re.I)
START_BID_RE = re.compile(r'<input[^>]+id="bid_value"[^>]+value="([^"]+)"', re.S | re.I)
CLEAN_DIGITS_RE = re.compile(r'\D+')
USD_QUANT = Decimal('0.01')
TON_EMOJI = '<tg-emoji emoji-id="5193179982775476271">🪙</tg-emoji>'
USD_EMOJI = '<tg-emoji emoji-id="5255933397750014894">💱</tg-emoji>'
CHART_EMOJI = '<tg-emoji emoji-id="5278778882848220741">📊</tg-emoji>'
SOLD_EMOJI = '<tg-emoji emoji-id="5278578973595427038">🚫</tg-emoji>'
PERCENT_EMOJI = '<tg-emoji emoji-id="5242602592357345985">🔤</tg-emoji>'
INSELL_EMOJI = '<tg-emoji emoji-id=5278411813468269386>✅</tg-emoji>'
SELLDATE_EMOJI = '<tg-emoji emoji-id=5276398496008663230>👝</tg-emoji>'
STARTPR_EMOJI = '<tg-emoji emoji-id=5206222720416643915>🔔</tg-emoji>'
ROST_EMOJI = '<tg-emoji emoji-id=5278778882848220741>📊</tg-emoji>'
URL_EMOJI = '<tg-emoji emoji-id=5399998467852483666>🫤</tg-emoji>'
 
@dataclass(slots=True)
class FragmentNumber:
    display_number: str
    digits: str
    price_ton: int
    status: str
    url: str
    sold: bool = False
    sold_date: str | None = None
    start_bid_ton: int | None = None


@dataclass(slots=True)
class FragmentSnapshot:
    ton_usd_rate: Decimal
    numbers: list[FragmentNumber]


class FragmentFetchError(RuntimeError):
    pass


@loader.tds
class FragNumberMod(loader.Module):
    """Показывает цены на анонимные номера Fragment"""

    strings = {
        'name': 'FragNumber',
        'usage': 'Используй: .num 03514676 или .num 100-10000',
        'range_usage': 'Диапазон укажи так: .num 100-10000',
        'not_found': 'Сейчас такого номера на аукционе нет.',
        'fetch_error': 'Не удалось получить данные с Fragment.',
        'empty_auction': 'Fragment не вернул номера с аукциона.',
        'rate_missing': 'Не удалось получить курс TON/USD с Fragment.',
        'min_title': 'Самые дешёвые номера щас:\n{}',
        'range_title': 'Номера от {} до {}:\n{}',
        'range_empty': 'Сейчас нет номеров в диапазоне {}-{}.',
    }

    strings_ru = strings

    async def _fetch_html(self, query: str | None = None, sold: bool = False) -> str:
        url = FRAGMENT_SOLD_URL if sold else FRAGMENT_LIST_URL
        if query:
            url += f'&query={quote_plus(query)}'

        def _request() -> str:
            request = Request(url, headers={'User-Agent': USER_AGENT})
            with urlopen(request, timeout=20) as response:
                return response.read().decode('utf-8', errors='replace')

        try:
            return await utils.run_sync(_request)
        except Exception as exc:  # noqa: BLE001
            raise FragmentFetchError(self.strings('fetch_error')) from exc

    def _parse_ton_rate(self, html: str) -> Decimal:
        match = TON_RATE_RE.search(html)
        if not match:
            raise FragmentFetchError(self.strings('rate_missing'))
        return Decimal(match.group(1))

    def _parse_numbers(self, html: str, sold: bool = False) -> list[FragmentNumber]:
        items: list[FragmentNumber] = []
        for row in ROW_RE.findall(html):
            href_match = re.search(r'<a href="(/number/\d+)" class="table-cell">', row)
            number_match = NUMBER_RE.search(row)
            price_match = PRICE_RE.search(row)
            status_match = STATUS_RE.search(row)
            thin_status_match = THIN_STATUS_RE.search(row)
            if not href_match or not number_match or not price_match:
                continue

            display_number = unescape(number_match.group(1)).strip()
            status = (
                unescape(status_match.group(1)).strip()
                if status_match
                else unescape(thin_status_match.group(1)).strip() if thin_status_match else ('Sold' if sold else 'Unknown')
            )
            items.append(
                FragmentNumber(
                    display_number=display_number,
                    digits=CLEAN_DIGITS_RE.sub('', display_number),
                    price_ton=int(price_match.group(1).replace(',', '')),
                    status=status,
                    url='https://fragment.com' + href_match.group(1),
                    sold=sold or status.lower() == 'sold',
                )
            )
        return items

    async def _fetch_snapshot(self, query: str | None = None, sold: bool = False) -> FragmentSnapshot:
        html = await self._fetch_html(query=query, sold=sold)
        items = self._parse_numbers(html, sold=sold)
        if not items and query:
            return FragmentSnapshot(ton_usd_rate=self._parse_ton_rate(html), numbers=[])
        if not items:
            raise FragmentFetchError(self.strings('empty_auction'))
        return FragmentSnapshot(ton_usd_rate=self._parse_ton_rate(html), numbers=items)

    def _normalize_number(self, raw_number: str) -> str:
        digits = CLEAN_DIGITS_RE.sub('', raw_number)
        if not digits:
            raise ValueError(self.strings('usage'))
        if digits.startswith('888'):
            return digits
        return '888' + digits

    async def _find_number(self, raw_number: str) -> tuple[FragmentSnapshot, FragmentNumber | None]:
        normalized = self._normalize_number(raw_number)
        last_snapshot: FragmentSnapshot | None = None
        for sold in (False, True):
            for query in dict.fromkeys([normalized, normalized[-8:], normalized[-7:]]):
                if not query:
                    continue
                snapshot = await self._fetch_snapshot(query=query, sold=sold)
                last_snapshot = snapshot
                for item in snapshot.numbers:
                    if item.digits == normalized:
                        if item.sold:
                            details = await self._fetch_sold_details(item.url)
                            item.sold_date = details.get('sold_date')
                            item.start_bid_ton = details.get('start_bid_ton')
                            if details.get('sale_price_ton'):
                                item.price_ton = details['sale_price_ton']
                        return snapshot, item
        return last_snapshot or FragmentSnapshot(ton_usd_rate=Decimal('0'), numbers=[]), None

    def _parse_range(self, raw_value: str) -> tuple[int, int]:
        match = re.fullmatch(r'\s*(\d+)\s*-\s*(\d+)\s*', raw_value)
        if not match:
            raise ValueError(self.strings('range_usage'))
        low = int(match.group(1))
        high = int(match.group(2))
        return (low, high) if low <= high else (high, low)

    def _usd_value(self, ton_amount: int, ton_usd_rate: Decimal) -> str:
        usd = (Decimal(ton_amount) * ton_usd_rate).quantize(USD_QUANT, rounding=ROUND_HALF_UP)
        return f'{usd} {USD_EMOJI}'

    def _ton_value(self, ton_amount: int) -> str:
        return f'{ton_amount} {TON_EMOJI}'

    def _growth_from_start(self, sold_price_ton: int, start_bid_ton: int) -> str:
        if start_bid_ton <= 0:
            return '—'
        growth = ((Decimal(sold_price_ton) - Decimal(start_bid_ton)) / Decimal(start_bid_ton)) * Decimal('100')
        growth = growth.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return f'{growth}{PERCENT_EMOJI}'

    async def _fetch_page_html(self, url: str) -> str:
        def _request() -> str:
            request = Request(url, headers={'User-Agent': USER_AGENT})
            with urlopen(request, timeout=20) as response:
                return response.read().decode('utf-8', errors='replace')

        try:
            return await utils.run_sync(_request)
        except Exception as exc:  # noqa: BLE001
            raise FragmentFetchError(self.strings('fetch_error')) from exc

    async def _fetch_sold_details(self, url: str) -> dict[str, int | str | None]:
        html = await self._fetch_page_html(url)
        sale_match = SALE_PRICE_RE.search(html)
        sold_date_match = SOLD_DATE_RE.search(html)
        start_bid_match = START_BID_RE.search(html)
        sold_date = None
        if sold_date_match:
            try:
                sold_date = datetime.fromisoformat(sold_date_match.group(1)).strftime('%d.%m.%Y %H:%M')
            except ValueError:
                sold_date = sold_date_match.group(1)
        return {
            'sale_price_ton': int(sale_match.group(1).replace(',', '')) if sale_match else None,
            'sold_date': sold_date,
            'start_bid_ton': int(Decimal(start_bid_match.group(1))) if start_bid_match else None,
        }

    def _format_detail(self, item: FragmentNumber, ton_usd_rate: Decimal) -> str:
        if item.sold:
            lines = [
                f'{SOLD_EMOJI} Продан: {item.display_number}',
                f'{CHART_EMOJI} Цена продажи: {self._ton_value(item.price_ton)} ({self._usd_value(item.price_ton, ton_usd_rate)})',
            ]
            if item.start_bid_ton is not None:
                lines.append(
                    f'{STARTPR_EMOJI}Начальная ставка: {self._ton_value(item.start_bid_ton)} '
                    f'({self._usd_value(item.start_bid_ton, ton_usd_rate)})'
                )
                lines.append(f'{ROST_EMOJI}Рост от старта: {self._growth_from_start(item.price_ton, item.start_bid_ton)}')
            if item.sold_date:
                lines.append(f'{SELLDATE_EMOJI}Дата продажи: {item.sold_date}')
            lines.append(f'{URL_EMOJI}{item.url}')
            return '\n'.join(lines)

        return (
            f'{INSELL_EMOJI} В продаже: {item.display_number}\n'
            f'{CHART_EMOJI} Цена: {self._ton_value(item.price_ton)} ({self._usd_value(item.price_ton, ton_usd_rate)})\n'
            f'{URL_EMOJI}{item.url}'
        )

    def _format_entry(self, index: int, item: FragmentNumber, ton_usd_rate: Decimal) -> str:
        return (
            f'{index}. {item.display_number} — '
            f'{self._ton_value(item.price_ton)} ({self._usd_value(item.price_ton, ton_usd_rate)})'
        )

    def _as_blockquote(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith('<blockquote>') and stripped.endswith('</blockquote>'):
            return stripped
        return f'<blockquote>{stripped}</blockquote>'

    @loader.command(ru_doc='Показать цену конкретного номера или список номеров в диапазоне цен')
    async def num(self, message: Message) -> None:
        """.num <номер> — показывает цену конкретного +888 номера
        .num <мин>-<макс> — показывает номера в заданном диапазоне цен"""
        args = utils.get_args_raw(message)
        if not args:
            await utils.answer(message, self._as_blockquote(self.strings('usage')))
            return

        try:
            if '-' in args:
                low, high = self._parse_range(args)
                snapshot = await self._fetch_snapshot()
                matches = [
                    item for item in snapshot.numbers if low <= item.price_ton <= high
                ][:15]
                if not matches:
                    await utils.answer(message, self._as_blockquote(self.strings('range_empty').format(low, high)))
                    return
                text = self.strings('range_title').format(
                    low,
                    high,
                    '\n'.join(
                        self._format_entry(index, item, snapshot.ton_usd_rate)
                        for index, item in enumerate(matches, start=1)
                    ),
                )
                await utils.answer(message, self._as_blockquote(text))
                return

            snapshot, item = await self._find_number(args)
            if not item:
                await utils.answer(message, self._as_blockquote(self.strings('not_found')))
                return
            await utils.answer(message, self._as_blockquote(self._format_detail(item, snapshot.ton_usd_rate)))

        except ValueError as exc:
            await utils.answer(message, self._as_blockquote(str(exc)))
        except FragmentFetchError as exc:
            await utils.answer(message, self._as_blockquote(str(exc)))

    @loader.command(ru_doc='Показать самые дешёвые анонимные номера на аукционе')
    async def nummin(self, message: Message) -> None:
        """.nummin — показывает самые дешёвые +888 номера, которые сейчас есть на Fragment"""
        try:
            snapshot = await self._fetch_snapshot()
            items = snapshot.numbers[:3]
            text = self.strings('min_title').format(
                '\n'.join(
                    self._format_entry(index, item, snapshot.ton_usd_rate)
                    for index, item in enumerate(items, start=1)
                )
            )
            await utils.answer(message, self._as_blockquote(text))
        except FragmentFetchError as exc:
            await utils.answer(message, self._as_blockquote(str(exc)))