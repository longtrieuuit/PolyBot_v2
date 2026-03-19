#!/usr/bin/env python3
"""
Demo script: Lấy giá BTC 15m → Kiểm tra Bundle Cost → Chọn Clip Size.

Usage:
  python demo_check_bundle.py              # Live mode (kết nối Polymarket WSS)
  python demo_check_bundle.py --mock       # Mock mode (data giả lập, chạy offline)
  python demo_check_bundle.py --mock 0.55 0.43 25 20   # Custom mock data
"""

import sys
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Tuple, Optional

# ─── Config (giống project gốc) ──────────────────────────────────
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST_URL = "https://clob.polymarket.com"
EASTERN = ZoneInfo("America/New_York")

MAX_BUNDLE_COST = 1.002                # Giá bundle tối đa chấp nhận
CLIP_LADDER = (10, 12, 14, 16)        # Các mức clip size (shares)
DEPTH_MIN = 1.0                        # Depth tối thiểu (shares)
ORDER_SIZE_FALLBACK = 5                # Clip fallback nếu ladder rỗng
BURST_CHILD_COUNT = 10                 # Số cặp lệnh mỗi burst


# ─── Data Structures ─────────────────────────────────────────────
@dataclass
class OrderBookState:
    """Snapshot orderbook 2 bên YES / NO."""
    best_ask_yes: float = 1.0
    best_ask_yes_size: float = 0.0
    best_bid_yes: float = 0.0
    best_bid_yes_size: float = 0.0

    best_ask_no: float = 1.0
    best_ask_no_size: float = 0.0
    best_bid_no: float = 0.0
    best_bid_no_size: float = 0.0

    @property
    def bundle_cost(self) -> float:
        """Chi phí mua 1 cặp (YES + NO) ở giá ask."""
        return self.best_ask_yes + self.best_ask_no

    @property
    def profit_potential(self) -> float:
        """Lợi nhuận tiềm năng mỗi cặp = $1.00 - bundle_cost."""
        return 1.00 - self.bundle_cost


# ─── Helper Functions ─────────────────────────────────────────────
def extract_best(side_data: List, is_ask: bool) -> Tuple[float, float]:
    """
    Lấy best price + size từ 1 bên orderbook.
    is_ask=True  → tìm giá THẤP nhất (best ask)
    is_ask=False → tìm giá CAO nhất (best bid)
    """
    default_price = 0.99 if is_ask else 0.01
    if not side_data:
        return default_price, 0.0

    best_price, best_size = None, 0.0
    for lvl in side_data:
        try:
            if isinstance(lvl, dict):
                p = float(lvl.get("price", 0))
                s = float(lvl.get("size", 0))
            elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                p, s = float(lvl[0]), float(lvl[1])
            else:
                continue
        except (ValueError, TypeError):
            continue

        if best_price is None:
            best_price, best_size = p, s
        elif is_ask and p < best_price:
            best_price, best_size = p, s
        elif not is_ask and p > best_price:
            best_price, best_size = p, s

    return (best_price or default_price, best_size)


def select_clip(ladder: Tuple[float, ...], *depths: float) -> float:
    """
    Chọn clip size LỚN NHẤT mà depth cho phép.
    Quy tắc: depth >= max(DEPTH_MIN, clip × 2)
    """
    valid = [v for v in depths if v is not None and v > 0]
    avail = min(valid) if valid else 0.0

    for size in sorted(ladder, reverse=True):
        required = max(DEPTH_MIN, size * 2)
        if avail >= required:
            return size

    return sorted(ladder)[0] if ladder else ORDER_SIZE_FALLBACK


# ─── Display Functions ────────────────────────────────────────────
def display_bundle_check(book: OrderBookState, question: str):
    """Hiển thị kết quả kiểm tra bundle cost và chọn clip."""

    # ── BƯỚC 2: KIỂM TRA BUNDLE COST ──
    print()
    print("=" * 60)
    print("BUOC 2: KIEM TRA BUNDLE COST")
    print("=" * 60)
    print()
    print(f"  +------------------------------------------------+")
    print(f"  |  YES side                                      |")
    print(f"  |    Best Ask: ${book.best_ask_yes:.3f}  (depth: {book.best_ask_yes_size:.0f} shares)     |")
    print(f"  |    Best Bid: ${book.best_bid_yes:.3f}  (depth: {book.best_bid_yes_size:.0f} shares)     |")
    print(f"  |                                                |")
    print(f"  |  NO side                                       |")
    print(f"  |    Best Ask: ${book.best_ask_no:.3f}  (depth: {book.best_ask_no_size:.0f} shares)     |")
    print(f"  |    Best Bid: ${book.best_bid_no:.3f}  (depth: {book.best_bid_no_size:.0f} shares)     |")
    print(f"  +------------------------------------------------+")
    print()

    bundle_cost = book.bundle_cost
    profit = book.profit_potential

    print(f"  Bundle Cost = ask_YES + ask_NO")
    print(f"              = ${book.best_ask_yes:.3f} + ${book.best_ask_no:.3f}")
    print(f"              = ${bundle_cost:.4f}")
    print()

    if bundle_cost > MAX_BUNDLE_COST:
        print(f"  [NO] Bundle ${bundle_cost:.4f} > max ${MAX_BUNDLE_COST:.3f} --> KHONG VAO LENH!")
        print(f"  Lo neu vao = ${profit:.4f}/pair")
        print()
        print("  --> Skip buoc 3 (clip size) vi bundle qua dat.")
        return

    print(f"  [OK] Bundle ${bundle_cost:.4f} <= max ${MAX_BUNDLE_COST:.3f} --> CO THE VAO LENH!")
    print(f"  Profit potential = $1.00 - ${bundle_cost:.4f} = ${profit:.4f}/pair")

    # ── BƯỚC 3: CHỌN CLIP SIZE (chỉ khi bundle OK) ──
    print()
    print("=" * 60)
    print("BUOC 3: CHON CLIP SIZE")
    print("=" * 60)
    print()
    print(f"  Clip Ladder : {CLIP_LADDER}")
    print(f"  Depth YES   : {book.best_ask_yes_size:.0f} shares")
    print(f"  Depth NO    : {book.best_ask_no_size:.0f} shares")

    avail_depth = min(book.best_ask_yes_size, book.best_ask_no_size)
    print(f"  Min depth   : min({book.best_ask_yes_size:.0f}, {book.best_ask_no_size:.0f}) = {avail_depth:.0f}")
    print()

    print(f"  Thu tung clip (lon -> nho):")
    for size in sorted(CLIP_LADDER, reverse=True):
        required = max(DEPTH_MIN, size * 2)
        ok = avail_depth >= required
        mark = "[OK]" if ok else "[X] "
        cmp = ">=" if ok else "< "
        print(f"    clip={size:>2} -> can depth >= max({DEPTH_MIN:.0f}, {size}x2)={required:.0f} -> {avail_depth:.0f} {cmp} {required:.0f} {mark}")
        if ok:
            break

    clip = select_clip(CLIP_LADDER, book.best_ask_yes_size, book.best_ask_no_size)
    print()
    print(f"  --> Clip duoc chon: {clip:.0f} shares/lenh")

    # ── TÓM TẮT ──
    print()
    print("=" * 60)
    print("TOM TAT")
    print("=" * 60)
    print(f"  Market      : {question}")
    print(f"  Bundle Cost : ${bundle_cost:.4f}")
    print(f"  Profit/pair : ${profit:.4f}")
    print(f"  Clip Size   : {clip:.0f} shares")

    burst = BURST_CHILD_COUNT
    total_shares = clip * burst
    total_cost = bundle_cost * clip * burst
    print()
    print(f"  Neu ban burst ({burst} cap):")
    print(f"    Tong lenh  : {burst * 2} lenh IOC ({burst} YES + {burst} NO)")
    print(f"    Moi lenh   : {clip:.0f} shares")
    print(f"    Tong shares: {total_shares:.0f} YES + {total_shares:.0f} NO")
    print(f"    Tong cost  : ~${total_cost:.2f}")
    print(f"    Profit neu full fill: ~${profit * total_shares:.2f}")


# ─── MOCK MODE ────────────────────────────────────────────────────
def run_mock(ask_yes=0.55, ask_no=0.43, depth_yes=25, depth_no=20):
    """Chạy demo với data giả lập — không cần mạng."""
    print("=" * 60)
    print("BUOC 0: MOCK MODE (data gia lap)")
    print("=" * 60)
    print(f"  Khong ket noi mang, dung data gia lap de hoc flow.")
    print()

    question = "Will BTC go Up or Down in the next 15 minutes? (MOCK)"

    # Giả lập orderbook
    book = OrderBookState(
        best_ask_yes=ask_yes,
        best_ask_yes_size=depth_yes,
        best_bid_yes=ask_yes - 0.02,   # spread ~2 cent
        best_bid_yes_size=depth_yes * 0.8,

        best_ask_no=ask_no,
        best_ask_no_size=depth_no,
        best_bid_no=ask_no - 0.02,
        best_bid_no_size=depth_no * 0.8,
    )

    print("=" * 60)
    print("BUOC 1: ORDER BOOK (gia lap)")
    print("=" * 60)
    print()
    print(f"  YES: ask=${book.best_ask_yes:.3f} (depth:{book.best_ask_yes_size:.0f})  bid=${book.best_bid_yes:.3f} (depth:{book.best_bid_yes_size:.0f})")
    print(f"  NO : ask=${book.best_ask_no:.3f} (depth:{book.best_ask_no_size:.0f})  bid=${book.best_bid_no:.3f} (depth:{book.best_bid_no_size:.0f})")

    display_bundle_check(book, question)


# ─── LIVE MODE ────────────────────────────────────────────────────
async def run_live():
    """Chạy demo với data thật từ Polymarket WSS."""
    import aiohttp
    import requests

    print("=" * 60)
    print("BUOC 0: TIM MARKET BTC 15 PHUT")
    print("=" * 60)

    try:
        from utils import get_target_markets
        from config import BTC_SLUG_PREFIX
    except ImportError as e:
        print(f"  [X] Khong import duoc utils/config: {e}")
        return

    markets = get_target_markets(slug_prefix=BTC_SLUG_PREFIX, asset_label='BTC')
    if not markets:
        print("  [X] Khong tim thay market BTC 15m nao active!")
        return

    m = markets[0]
    condition_id = m.get('condition_id')
    question = m.get('question', '???')
    print(f"  Market  : {question}")
    print(f"  Cond ID : {condition_id}")

    # Lấy token IDs
    resp = requests.get(f"{REST_URL}/markets/{condition_id}", timeout=5)
    resp.raise_for_status()
    data = resp.json()

    yes_token = no_token = None
    for t in data.get("tokens", []):
        tid = t.get("token_id") or t.get("tokenId")
        outcome = t.get("outcome", "").upper()
        if outcome in ("YES", "UP"):
            yes_token = tid
        elif outcome in ("NO", "DOWN"):
            no_token = tid

    if not yes_token or not no_token:
        print("  [X] Thieu token!")
        return

    print(f"  YES: {yes_token[:30]}...")
    print(f"  NO : {no_token[:30]}...")

    # ── WSS ──
    print()
    print("=" * 60)
    print("BUOC 1: KET NOI WSS LAY GIA REAL-TIME")
    print("=" * 60)

    book = OrderBookState()
    yes_ok = no_ok = False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, heartbeat=20, timeout=15) as ws:
                print("  [OK] Da ket noi WSS!")
                await ws.send_json({"assets_ids": [yes_token, no_token], "type": "market"})

                # Ping task
                async def ping():
                    try:
                        while True:
                            await ws.send_str("PING")
                            await asyncio.sleep(10)
                    except Exception:
                        pass

                ping_task = asyncio.create_task(ping())
                start = asyncio.get_event_loop().time()

                try:
                    while asyncio.get_event_loop().time() - start < 30:
                        if yes_ok and no_ok:
                            break
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=5)
                        except asyncio.TimeoutError:
                            continue

                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        raw = msg.data.strip()
                        if raw in ("PONG", ""):
                            continue

                        try:
                            parsed = json.loads(raw)
                        except Exception:
                            continue
                        if parsed == []:
                            continue

                        for d in (parsed if isinstance(parsed, list) else [parsed]):
                            if not isinstance(d, dict):
                                continue
                            et = d.get("event_type")

                            if et in ("book", None):
                                aid = d.get("asset_id") or d.get("id")
                                if not aid:
                                    continue
                                bids = d.get("bids") or []
                                asks = d.get("asks") or []
                                bp, bs = extract_best(bids, is_ask=False)
                                ap, az = extract_best(asks, is_ask=True)

                                if aid == yes_token:
                                    book.best_bid_yes, book.best_bid_yes_size = bp, bs
                                    book.best_ask_yes, book.best_ask_yes_size = ap, az
                                    yes_ok = True
                                    print(f"  YES: bid=${bp:.3f}({bs:.0f}) ask=${ap:.3f}({az:.0f})")
                                elif aid == no_token:
                                    book.best_bid_no, book.best_bid_no_size = bp, bs
                                    book.best_ask_no, book.best_ask_no_size = ap, az
                                    no_ok = True
                                    print(f"  NO : bid=${bp:.3f}({bs:.0f}) ask=${ap:.3f}({az:.0f})")
                finally:
                    ping_task.cancel()
    except Exception as e:
        print(f"  [X] WSS error: {e}")

    # REST fallback
    if not yes_ok or not no_ok:
        print("  Fallback sang REST API...")
        for tid, side in [(yes_token, "YES"), (no_token, "NO")]:
            if side == "YES" and yes_ok:
                continue
            if side == "NO" and no_ok:
                continue
            try:
                r = requests.get(f"{REST_URL}/book", params={"token_id": tid}, timeout=5)
                r.raise_for_status()
                d = r.json()
                bp, bs = extract_best(d.get("bids", []), is_ask=False)
                ap, az = extract_best(d.get("asks", []), is_ask=True)
                if side == "YES":
                    book.best_bid_yes, book.best_bid_yes_size = bp, bs
                    book.best_ask_yes, book.best_ask_yes_size = ap, az
                    yes_ok = True
                else:
                    book.best_bid_no, book.best_bid_no_size = bp, bs
                    book.best_ask_no, book.best_ask_no_size = ap, az
                    no_ok = True
                print(f"  REST {side}: bid=${bp:.3f}({bs:.0f}) ask=${ap:.3f}({az:.0f})")
            except Exception as e:
                print(f"  [X] REST {side}: {e}")

    if not yes_ok or not no_ok:
        print("\n  [X] Khong lay duoc orderbook. Dung lai.")
        return

    display_bundle_check(book, question)


# ─── MAIN ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--mock" in args:
        # Parse optional custom values: --mock [ask_yes] [ask_no] [depth_yes] [depth_no]
        nums = [a for a in args if a != "--mock"]
        if len(nums) >= 4:
            run_mock(float(nums[0]), float(nums[1]), float(nums[2]), float(nums[3]))
        elif len(nums) >= 2:
            run_mock(float(nums[0]), float(nums[1]))
        else:
            run_mock()
    else:
        asyncio.run(run_live())
