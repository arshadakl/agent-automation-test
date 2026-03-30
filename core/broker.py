"""Angel One SmartAPI broker wrapper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import pyotp
from loguru import logger

try:
    from SmartApi import SmartConnect
except ImportError:  # pragma: no cover
    SmartConnect = None  # type: ignore[assignment,misc]


@dataclass
class Order:
    """Represents a single trade order."""

    symbol: str
    token: str
    exchange: str = "NSE"
    transaction_type: str = "BUY"  # BUY | SELL
    quantity: int = 0
    order_type: str = "MARKET"  # MARKET | LIMIT | SL | SL-M
    product_type: str = "INTRADAY"  # INTRADAY | DELIVERY | CARRYFORWARD
    price: float = 0.0
    trigger_price: float = 0.0
    variety: str = "NORMAL"  # NORMAL | STOPLOSS | ROBO
    tag: str = ""
    order_id: str = field(default="", compare=False)
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)


class AngelBroker:
    """Async wrapper around Angel One SmartConnect API."""

    def __init__(self, credentials: dict[str, str]) -> None:
        self._creds = credentials
        self._api_key: str = credentials.get("api_key", "")
        self._client_id: str = credentials.get("client_id", "")
        self._password: str = credentials.get("password", "")
        self._totp_secret: str = credentials.get("totp_secret", "")
        self._smart: Any = None
        self._auth_token: str = ""
        self._refresh_token_val: str = ""
        self._feed_token: str = ""
        self._logged_in: bool = False

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def login(self) -> bool:
        """TOTP-based login to Angel One SmartAPI."""
        if SmartConnect is None:
            logger.warning("smartapi-python not installed; running in mock mode.")
            self._logged_in = True
            return True

        try:
            loop = asyncio.get_event_loop()
            self._smart = SmartConnect(api_key=self._api_key)
            totp_code = pyotp.TOTP(self._totp_secret).now()
            data = await loop.run_in_executor(
                None,
                lambda: self._smart.generateSession(
                    self._client_id, self._password, totp_code
                ),
            )
            if data and data.get("status"):
                self._auth_token = data["data"]["jwtToken"]
                self._refresh_token_val = data["data"]["refreshToken"]
                self._feed_token = self._smart.getfeedToken()
                self._logged_in = True
                logger.info("Logged in to Angel One successfully.")
                return True
            logger.error(f"Login failed: {data}")
            return False
        except Exception as exc:
            logger.exception(f"Login error: {exc}")
            return False

    async def refresh_token(self) -> bool:
        """Refresh the JWT session token."""
        if SmartConnect is None or not self._smart:
            return True  # mock mode

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: self._smart.generateToken(self._refresh_token_val),
            )
            if data and data.get("status"):
                self._auth_token = data["data"]["jwtToken"]
                logger.debug("Token refreshed.")
                return True
            return False
        except Exception as exc:
            logger.exception(f"Token refresh error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def get_funds(self) -> dict[str, Any]:
        """Return available funds and margin."""
        if SmartConnect is None or not self._smart:
            return {"availablecash": 100000.0, "net": 100000.0}

        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, self._smart.rmsLimit)
            return data.get("data", {}) if data else {}
        except Exception as exc:
            logger.error(f"get_funds error: {exc}")
            return {}

    async def get_positions(self) -> list[dict[str, Any]]:
        """Return current open positions."""
        if SmartConnect is None or not self._smart:
            return []

        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, self._smart.position)
            return data.get("data", []) or []
        except Exception as exc:
            logger.error(f"get_positions error: {exc}")
            return []

    async def get_orders(self) -> list[dict[str, Any]]:
        """Return today's order book."""
        if SmartConnect is None or not self._smart:
            return []

        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, self._smart.orderBook)
            return data.get("data", []) or []
        except Exception as exc:
            logger.error(f"get_orders error: {exc}")
            return []

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_order(self, order: Order) -> str:
        """Place an order and return the order ID."""
        if SmartConnect is None or not self._smart:
            mock_id = f"MOCK-{datetime.now().strftime('%H%M%S%f')}"
            logger.info(f"[MOCK] Placed order {mock_id}: {order}")
            return mock_id

        params = {
            "variety": order.variety,
            "tradingsymbol": order.symbol,
            "symboltoken": order.token,
            "transactiontype": order.transaction_type,
            "exchange": order.exchange,
            "ordertype": order.order_type,
            "producttype": order.product_type,
            "duration": "DAY",
            "price": str(order.price),
            "triggerprice": str(order.trigger_price),
            "quantity": str(order.quantity),
        }

        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None, lambda: self._smart.placeOrder(params)
            )
            if data and data.get("status"):
                order_id = data["data"]["orderid"]
                logger.info(f"Order placed: {order_id} – {order.symbol} {order.transaction_type} {order.quantity}")
                return order_id
            logger.error(f"place_order failed: {data}")
            return ""
        except Exception as exc:
            logger.exception(f"place_order error: {exc}")
            return ""

    async def modify_order(self, order_id: str, params: dict[str, Any]) -> bool:
        """Modify an existing order."""
        if SmartConnect is None or not self._smart:
            logger.info(f"[MOCK] Modified order {order_id}: {params}")
            return True

        params["orderid"] = order_id
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None, lambda: self._smart.modifyOrder(params)
            )
            return bool(data and data.get("status"))
        except Exception as exc:
            logger.exception(f"modify_order error: {exc}")
            return False

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order."""
        if SmartConnect is None or not self._smart:
            logger.info(f"[MOCK] Cancelled order {order_id}")
            return True

        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None,
                lambda: self._smart.cancelOrder(
                    order_id, variety="NORMAL"
                ),
            )
            return bool(data and data.get("status"))
        except Exception as exc:
            logger.exception(f"cancel_order error: {exc}")
            return False

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def get_historical(
        self,
        symbol: str,
        token: str,
        interval: str,
        from_dt: datetime,
        to_dt: datetime,
        exchange: str = "NSE",
    ) -> "pd.DataFrame":
        """Download historical OHLCV candles."""
        import pandas as pd

        if SmartConnect is None or not self._smart:
            logger.warning(f"[MOCK] get_historical called for {symbol}")
            return pd.DataFrame(
                columns=["datetime", "open", "high", "low", "close", "volume"]
            )

        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,  # ONE_MINUTE, FIVE_MINUTE, etc.
            "fromdate": from_dt.strftime("%Y-%m-%d %H:%M"),
            "todate": to_dt.strftime("%Y-%m-%d %H:%M"),
        }

        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(
                None, lambda: self._smart.getCandleData(params)
            )
            if data and data.get("status") and data.get("data"):
                df = pd.DataFrame(
                    data["data"],
                    columns=["datetime", "open", "high", "low", "close", "volume"],
                )
                df["datetime"] = pd.to_datetime(df["datetime"])
                df = df.sort_values("datetime").reset_index(drop=True)
                return df
            return pd.DataFrame(
                columns=["datetime", "open", "high", "low", "close", "volume"]
            )
        except Exception as exc:
            logger.exception(f"get_historical error: {exc}")
            return pd.DataFrame(
                columns=["datetime", "open", "high", "low", "close", "volume"]
            )

    async def subscribe_ticks(
        self, symbols: list[dict[str, str]], callback: Callable[[dict], None]
    ) -> None:
        """Subscribe to live tick stream via WebSocket."""
        if SmartConnect is None or not self._smart:
            logger.warning("[MOCK] subscribe_ticks called – no-op in mock mode.")
            return

        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # type: ignore

            correlation_id = "algotrader"
            mode = 3  # snap quote mode

            token_list = [
                {"exchangeType": 1, "tokens": [s["token"] for s in symbols if s.get("exchange", "NSE") == "NSE"]},
                {"exchangeType": 5, "tokens": [s["token"] for s in symbols if s.get("exchange", "") == "NFO"]},
            ]
            token_list = [t for t in token_list if t["tokens"]]

            ws = SmartWebSocketV2(
                self._auth_token,
                self._api_key,
                self._client_id,
                self._feed_token,
            )

            def _on_data(ws_obj: Any, message: Any) -> None:  # noqa: ANN401
                callback(message)

            def _on_error(ws_obj: Any, error: Any) -> None:  # noqa: ANN401
                logger.error(f"WebSocket error: {error}")

            def _on_close(ws_obj: Any) -> None:
                logger.warning("WebSocket closed.")

            def _on_open(ws_obj: Any) -> None:
                logger.info("WebSocket connected. Subscribing…")
                ws.subscribe(correlation_id, mode, token_list)

            ws.on_open = _on_open
            ws.on_data = _on_data
            ws.on_error = _on_error
            ws.on_close = _on_close

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, ws.connect)
        except Exception as exc:
            logger.exception(f"subscribe_ticks error: {exc}")

    async def get_ltp(self, symbols: list[dict[str, str]]) -> dict[str, float]:
        """Return last traded prices for a list of symbols."""
        if SmartConnect is None or not self._smart:
            return {s["symbol"]: 1000.0 for s in symbols}

        result: dict[str, float] = {}
        loop = asyncio.get_event_loop()
        try:
            for sym in symbols:
                params = {
                    "exchange": sym.get("exchange", "NSE"),
                    "tradingsymbol": sym["symbol"],
                    "symboltoken": sym["token"],
                }
                data = await loop.run_in_executor(
                    None, lambda p=params: self._smart.ltpData(p["exchange"], p["tradingsymbol"], p["symboltoken"])
                )
                if data and data.get("status"):
                    result[sym["symbol"]] = float(data["data"].get("ltp", 0))
        except Exception as exc:
            logger.error(f"get_ltp error: {exc}")
        return result
