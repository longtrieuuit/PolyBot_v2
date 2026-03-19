#!/usr/bin/env python3
"""
Demo script: Lấy giá BTC 15m qua WSS → Kiểm tra Bundle Cost → Chọn Clip Size.

Flow:
  1. Tìm market BTC 15 phút đang active
  2. Kết nối WSS lấy orderbook real-time
  3. Kiểm tra bundle_cost = best_ask_YES + best_ask_NO
  4. Chọn clip size từ ladder dựa trên depth

Usage:
  python demo_check_bundle.py
"""

import asyncio
import aiohttp
import json
import requests
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict, Optional, Tuple

# ─── Config ───────────────────────────────────────────────────────
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
REST_URL = "https://clob.polymarket.com"
EASTERN = ZoneInfo("America/New_York")

# Bundle Arbitrage config (giống project)
MAX_BUNDLE_COST = 1.002       # Giá bundle tối đa chấp nhận
CLIP_LADDER = (10, 12, 14, 16)  # Các mức clip size
DEPTH_MIN = 1.0               # Depth tối thiểu
ORDER_SIZE_FALLBACK = 5       # Clip fallback nếu ladder rỗng


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

    @property
    def has_liquidity(self) -> bool:
        yes_ok = 0.02 < self.best_ask_yes < 0.98
        no_ok = 0.02 < self.best_ask_no < 0.98
        return yes_ok or no_ok


# ─── Helper Functions ─────────────────────────────────────────────
def extract_best(side_data: List, is_ask: bool) -> Tuple[float, float]:
    """
    Lấy best price + size từ 1 bên orderbook.
    is_ask=True  → tìm giá THẤP nhất (best ask)
    is_ask=False → tìm giá CAO nhất (best bid)
    """
    default_price = 0.99 if is_ask else 0.01
    default_size = 0.0

    if not side_data:
        return default_price, default_size

    best_price = None
    best_size = default_size

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
    → Cần ít nhất GẤP ĐÔI clip trong thanh khoản.

    Ví dụ với ladder (10, 12, 14, 16) và depth = 20:
      clip=16 → cần 32 → 20 < 32 ❌
      clip=14 → cần 28 → 20 < 28 ❌
      clip=12 → cần 24 → 20 < 24 ❌
      clip=10 → cần 20 → 20 ≥ 20 ✅ → chọn clip=10
    """
    valid = [v for v in depths if v is not None and v > 0]
    avail = min(valid) if valid else 0.0

    for size in sorted(ladder, reverse=True):  # Thử từ LỚN → NHỎ
        required = max(DEPTH_MIN, size * 2)
        if avail >= required:
            return size

    # Fallback: clip nhỏ nhất hoặc default
    return sorted(ladder)[0] if ladder else ORDER_SIZE_FALLBACK


# ─── Market Discovery ────────────────────────────────────────────
def discover_btc_market() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Tìm market BTC 15 phút đang active.
    Returns: (yes_token_id, no_token_id, market_question)
    """
    print("=" * 60)
    print("BƯỚC 0: TÌM MARKET BTC 15 PHÚT")
    print("=" * 60)

    try:
        from utils import get_target_markets
        from config import BTC_SLUG_PREFIX

        markets = get_target_markets(slug_prefix=BTC_SLUG_PREFIX, asset_label='BTC')
        if not markets:
            print("❌ Không tìm thấy market BTC 15m nào active!")
            return None, None, None

        m = markets[0]
        condition_id = m.get('condition_id')
        question = m.get('question', '???')
        print(f"  Market  : {question}")
        print(f"  Cond ID : {condition_id}")
        print(f"  Hết hạn : {m.get('end_date_iso')}")

        # Lấy token IDs từ CLOB API
        resp = requests.get(f"{REST_URL}/markets/{condition_id}", timeout=5)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("accepting_orders", True):
            print("❌ Market không accepting orders!")
            return None, None, None

        yes_token = no_token = None
        for t in data.get("tokens", []):
            tid = t.get("token_id") or t.get("tokenId")
            outcome = t.get("outcome", "").upper()
            if outcome in ("YES", "UP"):
                yes_token = tid
                print(f"  YES token: {tid[:30]}...")
            elif outcome in ("NO", "DOWN"):
                no_token = tid
                print(f"  NO token : {tid[:30]}...")

        if yes_token and no_token:
            print("✅ Đã tìm thấy cả 2 token!")
            return yes_token, no_token, question
        else:
            print("❌ Thiếu token YES hoặc NO!")
            return None, None, None

    except Exception as e:
        print(f"❌ Lỗi discovery: {e}")
        return None, None, None


# ─── WSS Listener ─────────────────────────────────────────────────
async def ws_ping_loop(ws):
    """Gửi PING mỗi 10 giây theo Polymarket docs."""
    try:
        while True:
            await ws.send_str("PING")
            await asyncio.sleep(10)
    except Exception:
        pass


async def run_demo():
    """Main: kết nối WSS → nhận giá → kiểm tra bundle → chọn clip."""

    # ── Bước 0: Tìm market ──
    yes_token, no_token, question = discover_btc_market()
    if not yes_token or not no_token:
        return

    tokens = [yes_token, no_token]
    token_labels = {yes_token: "BTC-YES", no_token: "BTC-NO"}

    book = OrderBookState()
    yes_updated = False
    no_updated = False

    print()
    print("=" * 60)
    print("BƯỚC 1: KẾT NỐI WSS LẤY GIÁ REAL-TIME")
    print("=" * 60)
    print(f"  URL: {WS_URL}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(WS_URL, heartbeat=20, timeout=15) as ws:
                print("  ✅ Đã kết nối WSS!")

                # Subscribe
                payload = {"assets_ids": tokens, "type": "market"}
                await ws.send_json(payload)
                print(f"  ✅ Đã subscribe {len(tokens)} tokens")

                # Start ping
                ping_task = asyncio.create_task(ws_ping_loop(ws))

                print()
                print("  Đang chờ orderbook data...")
                print("-" * 60)

                start = asyncio.get_event_loop().time()
                timeout_sec = 30

                try:
                    while asyncio.get_event_loop().time() - start < timeout_sec:
                        # Đã có đủ data cả 2 bên → thoát
                        if yes_updated and no_updated:
                            break

                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=5)
                        except asyncio.TimeoutError:
                            print("  ⏳ Chờ data...")
                            continue

                        if msg.type != aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                print("  ❌ WSS đóng kết nối!")
                                break
                            continue

                        raw = msg.data.strip()
                        if raw in ("PONG", ""):
                            continue

                        try:
                            parsed = json.loads(raw)
                        except Exception:
                            continue

                        if parsed == []:
                            continue

                        messages = parsed if isinstance(parsed, list) else [parsed]

                        for data in messages:
                            if not isinstance(data, dict):
                                continue

                            event_type = data.get("event_type")

                            # ── Xử lý BOOK event (full snapshot) ──
                            if event_type in ("book", None):
                                asset_id = (
                                    data.get("asset_id") or
                                    data.get("id") or
                                    data.get("token_id")
                                )
                                if not asset_id:
                                    continue

                                bids = data.get("bids") or []
                                asks = data.get("asks") or []
                                bid_price, bid_size = extract_best(bids, is_ask=False)
                                ask_price, ask_size = extract_best(asks, is_ask=True)

                                label = token_labels.get(asset_id, "???")
                                now = datetime.now(EASTERN).strftime("%H:%M:%S")

                                if asset_id == yes_token:
                                    book.best_ask_yes = ask_price
                                    book.best_ask_yes_size = ask_size
                                    book.best_bid_yes = bid_price
                                    book.best_bid_yes_size = bid_size
                                    yes_updated = True
                                    print(f"  {now} | {label:8} | bid=${bid_price:.3f}({bid_size:.0f}) ask=${ask_price:.3f}({ask_size:.0f})")

                                elif asset_id == no_token:
                                    book.best_ask_no = ask_price
                                    book.best_ask_no_size = ask_size
                                    book.best_bid_no = bid_price
                                    book.best_bid_no_size = bid_size
                                    no_updated = True
                                    print(f"  {now} | {label:8} | bid=${bid_price:.3f}({bid_size:.0f}) ask=${ask_price:.3f}({ask_size:.0f})")

                            # ── Xử lý PRICE_CHANGE event (incremental) ──
                            elif event_type == "price_change":
                                for change in data.get("price_changes", []):
                                    asset_id = change.get("asset_id")
                                    price = float(change.get("price", 0))
                                    size = float(change.get("size", 0))
                                    side = change.get("side", "").upper()

                                    if asset_id == yes_token:
                                        if side == "SELL" and (not yes_updated or price <= book.best_ask_yes):
                                            book.best_ask_yes = price
                                            book.best_ask_yes_size = size
                                            yes_updated = True
                                        elif side == "BUY" and price >= book.best_bid_yes:
                                            book.best_bid_yes = price
                                            book.best_bid_yes_size = size
                                    elif asset_id == no_token:
                                        if side == "SELL" and (not no_updated or price <= book.best_ask_no):
                                            book.best_ask_no = price
                                            book.best_ask_no_size = size
                                            no_updated = True
                                        elif side == "BUY" and price >= book.best_bid_no:
                                            book.best_bid_no = price
                                            book.best_bid_no_size = size

                finally:
                    ping_task.cancel()
                    try:
                        await ping_task
                    except asyncio.CancelledError:
                        pass

    except Exception as e:
        print(f"  ❌ Lỗi WSS: {e}")

    # Fallback: nếu WSS không nhận đủ data → dùng REST
    if not yes_updated or not no_updated:
        print()
        print("  ⚠️ WSS không đủ data, thử REST API fallback...")
        for tid, label in [(yes_token, "YES"), (no_token, "NO")]:
            is_yes = (label == "YES")
            if is_yes and yes_updated:
                continue
            if not is_yes and no_updated:
                continue

            try:
                resp = requests.get(f"{REST_URL}/book", params={"token_id": tid}, timeout=5)
                resp.raise_for_status()
                data = resp.json()
                bid_price, bid_size = extract_best(data.get("bids", []), is_ask=False)
                ask_price, ask_size = extract_best(data.get("asks", []), is_ask=True)

                if is_yes:
                    book.best_ask_yes, book.best_ask_yes_size = ask_price, ask_size
                    book.best_bid_yes, book.best_bid_yes_size = bid_price, bid_size
                    yes_updated = True
                else:
                    book.best_ask_no, book.best_ask_no_size = ask_price, ask_size
                    book.best_bid_no, book.best_bid_no_size = bid_price, bid_size
                    no_updated = True

                print(f"  REST {label}: bid=${bid_price:.3f}({bid_size:.0f}) ask=${ask_price:.3f}({ask_size:.0f})")
            except Exception as e:
                print(f"  ❌ REST {label} failed: {e}")

    if not yes_updated or not no_updated:
        print("\n❌ Không lấy được orderbook đầy đủ. Dừng lại.")
        return

    # ═══════════════════════════════════════════════════════════════
    # BƯỚC 2: KIỂM TRA BUNDLE COST
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 60)
    print("BƯỚC 2: KIỂM TRA BUNDLE COST")
    print("=" * 60)
    print()
    print(f"  ┌──────────────────────────────────────────────┐")
    print(f"  │  YES side                                    │")
    print(f"  │    Best Ask: ${book.best_ask_yes:.3f}  (depth: {book.best_ask_yes_size:.0f} shares)   │")
    print(f"  │    Best Bid: ${book.best_bid_yes:.3f}  (depth: {book.best_bid_yes_size:.0f} shares)   │")
    print(f"  │                                              │")
    print(f"  │  NO side                                     │")
    print(f"  │    Best Ask: ${book.best_ask_no:.3f}  (depth: {book.best_ask_no_size:.0f} shares)   │")
    print(f"  │    Best Bid: ${book.best_bid_no:.3f}  (depth: {book.best_bid_no_size:.0f} shares)   │")
    print(f"  └──────────────────────────────────────────────┘")
    print()

    bundle_cost = book.bundle_cost
    profit = book.profit_potential

    print(f"  Bundle Cost = ask_YES + ask_NO")
    print(f"              = ${book.best_ask_yes:.3f} + ${book.best_ask_no:.3f}")
    print(f"              = ${bundle_cost:.4f}")
    print()

    if bundle_cost <= MAX_BUNDLE_COST:
        print(f"  ✅ Bundle ${bundle_cost:.4f} <= max ${MAX_BUNDLE_COST:.3f} → CÓ THỂ VÀO LỆNH!")
        print(f"  💰 Profit potential = $1.00 - ${bundle_cost:.4f} = ${profit:.4f}/pair")
    else:
        print(f"  ❌ Bundle ${bundle_cost:.4f} > max ${MAX_BUNDLE_COST:.3f} → KHÔNG VÀO LỆNH!")
        print(f"  📉 Lỗ nếu vào = ${profit:.4f}/pair")

    # ═══════════════════════════════════════════════════════════════
    # BƯỚC 3: CHỌN CLIP SIZE
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 60)
    print("BƯỚC 3: CHỌN CLIP SIZE")
    print("=" * 60)
    print()
    print(f"  Clip Ladder : {CLIP_LADDER}")
    print(f"  Depth YES   : {book.best_ask_yes_size:.0f} shares")
    print(f"  Depth NO    : {book.best_ask_no_size:.0f} shares")

    avail_depth = min(book.best_ask_yes_size, book.best_ask_no_size)
    print(f"  Min depth   : min({book.best_ask_yes_size:.0f}, {book.best_ask_no_size:.0f}) = {avail_depth:.0f}")
    print()

    # Hiển thị quá trình chọn
    print(f"  Thử từng clip (lớn → nhỏ):")
    for size in sorted(CLIP_LADDER, reverse=True):
        required = max(DEPTH_MIN, size * 2)
        ok = avail_depth >= required
        mark = "✅" if ok else "❌"
        print(f"    clip={size:>2} → cần depth ≥ max({DEPTH_MIN:.0f}, {size}×2)={required:.0f} → {avail_depth:.0f} {'≥' if ok else '<'} {required:.0f} {mark}")
        if ok:
            break

    clip = select_clip(CLIP_LADDER, book.best_ask_yes_size, book.best_ask_no_size)
    print()
    print(f"  → Clip được chọn: {clip:.0f} shares/lệnh")

    # ═══════════════════════════════════════════════════════════════
    # TÓM TẮT
    # ═══════════════════════════════════════════════════════════════
    print()
    print("=" * 60)
    print("TÓM TẮT")
    print("=" * 60)
    print(f"  Market      : {question}")
    print(f"  Bundle Cost : ${bundle_cost:.4f}")
    print(f"  Profit/pair : ${profit:.4f}")
    print(f"  Clip Size   : {clip:.0f} shares")
    print(f"  Có vào lệnh?: {'CÓ ✅' if bundle_cost <= MAX_BUNDLE_COST else 'KHÔNG ❌'}")

    if bundle_cost <= MAX_BUNDLE_COST:
        burst = 10  # burst_child_count mặc định
        total_shares = clip * burst
        total_cost = bundle_cost * clip * burst
        print()
        print(f"  Nếu bắn burst ({burst} cặp):")
        print(f"    Tổng lệnh  : {burst * 2} lệnh IOC ({burst} YES + {burst} NO)")
        print(f"    Mỗi lệnh   : {clip:.0f} shares")
        print(f"    Tổng shares : {total_shares:.0f} YES + {total_shares:.0f} NO")
        print(f"    Tổng cost   : ~${total_cost:.2f}")
        print(f"    Profit nếu full fill: ~${profit * total_shares:.2f}")


if __name__ == "__main__":
    asyncio.run(run_demo())
