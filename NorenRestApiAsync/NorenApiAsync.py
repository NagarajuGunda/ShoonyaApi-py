import asyncio
import datetime
import hashlib
import json
import logging
import os
import socket
import time
import urllib.parse
from datetime import datetime as dt
from types import SimpleNamespace
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Union,
)

import aiohttp
import pyotp
import websockets
import yaml
from aiohttp.resolver import AsyncResolver

logger = logging.getLogger(__name__)


class position:
    prd: str
    exch: str
    instname: str
    symname: str
    exd: int
    optt: str
    strprc: float
    buyqty: int
    sellqty: int
    netqty: int

    def encode(self):
        return self.__dict__


class ProductType:
    Delivery = "C"
    Intraday = "I"
    Normal = "M"
    CF = "M"


class FeedType:
    TOUCHLINE = 1
    SNAPQUOTE = 2


class PriceType:
    Market = "MKT"
    Limit = "LMT"
    StopLossLimit = "SL-LMT"
    StopLossMarket = "SL-MKT"


class BuyorSell:
    Buy = "B"
    Sell = "S"


async def reportmsg(msg):
    logger.debug(msg)


async def reporterror(msg):
    logger.error(msg)


async def reportinfo(msg):
    logger.info(msg)


class NorenApiAsync:
    __service_config = {
        "host": "http://wsapihost/",
        "routes": {
            "authorize": "/QuickAuth",
            "logout": "/Logout",
            "forgot_password": "/ForgotPassword",
            "change_password": "/Changepwd",
            "watchlist_names": "/MWList",
            "watchlist": "/MarketWatch",
            "watchlist_add": "/AddMultiScripsToMW",
            "watchlist_delete": "/DeleteMultiMWScrips",
            "placeorder": "/PlaceOrder",
            "modifyorder": "/ModifyOrder",
            "cancelorder": "/CancelOrder",
            "exitorder": "/ExitSNOOrder",
            "product_conversion": "/ProductConversion",
            "orderbook": "/OrderBook",
            "tradebook": "/TradeBook",
            "singleorderhistory": "/SingleOrdHist",
            "searchscrip": "/SearchScrip",
            "TPSeries": "/TPSeries",
            "optionchain": "/GetOptionChain",
            "holdings": "/Holdings",
            "limits": "/Limits",
            "positions": "/PositionBook",
            "scripinfo": "/GetSecurityInfo",
            "getquotes": "/GetQuotes",
            "span_calculator": "/SpanCalc",
            "option_greek": "/GetOptionGreek",
            "get_daily_price_series": "/EODChartData",
        },
        "websocket_endpoint": "wss://wsendpoint/",
        #'eoddata_endpoint' : 'http://eodhost/'
    }

    @staticmethod
    async def on_request_start(
        session: aiohttp.ClientSession,
        trace_config_ctx: SimpleNamespace,
        params: aiohttp.TraceRequestStartParams,
    ) -> None:
        trace_config_ctx.start = asyncio.get_event_loop().time()
        logger.debug(
            " Request Hook: Initiated HTTP.%s Request To Url: %s | With Header: %s |",
            params.method,
            params.url,
            json.dumps(dict(params.headers)),
        )  # noqa E501
        print("\n")

    @staticmethod
    async def on_request_end(
        session: aiohttp.ClientSession,
        trace_config_ctx: SimpleNamespace,
        params: aiohttp.TraceRequestEndParams,
    ) -> None:
        elapsed = asyncio.get_event_loop().time() - trace_config_ctx.start
        elapsed_msg = f"{elapsed:.3f} Seconds"
        text = await params.response.text()
        if len(text) > 500:
            text = f"{text[:500]}...Truncated to 500 Characters."
        logger.debug(
            " Response Hook: The HTTP.%s Request To Url: %s | Completed In %s | Response Status: %s %s | Response Header: %s | Response Content: %s |",
            params.method,
            params.url,
            elapsed_msg,
            params.response.status,
            params.response.reason,
            json.dumps(dict(params.headers)),
            text,
        )
        print("\n")

    @staticmethod
    async def on_request_exception(
        session: aiohttp.ClientSession,
        trace_config_ctx: SimpleNamespace,
        params: aiohttp.TraceRequestExceptionParams,
    ) -> None:
        logger.debug(
            " Request Exception Hook: The HTTP.%s Request To Url: %s | Request Header: %s | Failed With Exception: %s |",
            params.method,
            params.url,
            json.dumps(dict(params.headers)),
            str(params.exception),
        )
        print("\n")

    @staticmethod
    async def generate_async_client_session(
        base_url: str,
        connector: aiohttp.TCPConnector,
        headers: Dict[str, Union[str, Any]],
        timeout: aiohttp.ClientTimeout,
        raise_for_status: bool,
        trust_env: bool,
        trace_configs: Optional[List[aiohttp.TraceConfig]] = None,
        cookie_jar: Optional[aiohttp.CookieJar] = None,
    ) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            base_url=base_url,
            connector=connector,
            headers=headers,
            timeout=timeout,
            cookie_jar=cookie_jar,
            raise_for_status=raise_for_status,
            trust_env=trust_env,
            trace_configs=trace_configs,
        )

    def __init__(self, host, websocket):
        parsed_url = urllib.parse.urlparse(host)
        self.__service_config["host"] = parsed_url.hostname
        self.__service_config["scheme"] = parsed_url.scheme
        self.__service_config["path"] = parsed_url.path
        self.__service_config["websocket_endpoint"] = websocket

        self.__websocket = None
        self.__websocket_connected = False
        self.__ws_mutex = asyncio.Lock()
        self.__on_error = None
        self.__on_open = None
        self.__subscribe_callback = None
        self.__order_update_callback = None
        self._default_timeout = 10
        self.debug = False
        self.__reqsession = None
        self.__connector = None
        self.__user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self.__headers = {
            "User-Agent": self.__user_agent,
        }

    async def __ws_run_forever(self):
        while not self.__stop_event.is_set():
            try:
                async with websockets.connect(
                    self.__service_config["websocket_endpoint"]
                ) as websocket:
                    self.__websocket = websocket
                    self.__websocket_connected = True
                    await self.__on_open_callback()
                    async for message in websocket:
                        await self.__on_data_callback(message=message)
            except Exception as e:
                logger.warning(f"websocket run forever ended in exception, {e}")

            await asyncio.sleep(0.1)

    async def __ws_send(self, *args, **kwargs):
        while not self.__websocket_connected:
            await asyncio.sleep(0.05)
        async with self.__ws_mutex:
            await self.__websocket.send(*args, **kwargs)

    async def __on_open_callback(self):
        self.__websocket_connected = True

        values = {
            "t": "c",
            "uid": self.__username,
            "actid": self.__username,
            "susertoken": self.__susertoken,
            "source": "API",
        }

        payload = json.dumps(values)
        await reportmsg(payload)
        await self.__ws_send(payload)

    async def __on_error_callback(self, ws=None, error=None):
        if self.__on_error:
            await self.__on_error(error)

    async def __on_data_callback(self, message):
        res = json.loads(message)

        if self.__subscribe_callback is not None:
            if res["t"] in ["tk", "tf", "dk", "df"]:
                await self.__subscribe_callback(res)
                return

        if self.__on_error is not None:
            if res["t"] == "ck" and res["s"] != "OK":
                await self.__on_error(res)
                return

        if self.__order_update_callback is not None:
            if res["t"] == "om":
                await self.__order_update_callback(res)
                return

        if self.__on_open:
            if res["t"] == "ck" and res["s"] == "OK":
                await self.__on_open()
                return

    async def start_websocket(
        self,
        subscribe_callback=None,
        order_update_callback=None,
        socket_open_callback=None,
        socket_close_callback=None,
        socket_error_callback=None,
    ):
        self.__on_open = socket_open_callback
        self.__on_disconnect = socket_close_callback
        self.__on_error = socket_error_callback
        self.__subscribe_callback = subscribe_callback
        self.__order_update_callback = order_update_callback
        self.__stop_event = asyncio.Event()

        self.__ws_task = asyncio.create_task(self.__ws_run_forever())

    async def close_websocket(self):
        if not self.__websocket_connected:
            return
        self.__stop_event.set()
        self.__websocket_connected = False
        if self.__websocket:
            await self.__websocket.close()
        await self.__ws_task

    async def __initialize_session_params(self) -> None:
        self.__timeout = aiohttp.ClientTimeout(
            total=float(self._default_timeout)
        )  # noqa E501
        self.__resolver = AsyncResolver(nameservers=["1.1.1.1", "1.0.0.1"])
        self.__connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=0,
            ttl_dns_cache=22500,
            use_dns_cache=True,
            resolver=self.__resolver,
            family=socket.AF_INET,
        )
        cookie_jar = aiohttp.CookieJar()
        self.__trace_config = aiohttp.TraceConfig()
        self.__trace_config.on_request_start.append(self.on_request_start)
        self.__trace_config.on_request_end.append(self.on_request_end)
        self.__trace_config.on_request_exception.append(self.on_request_exception)
        self.__reqsession = await self.generate_async_client_session(
            base_url=f'{self.__service_config["scheme"]}://{self.__service_config["host"]}',
            connector=self.__connector,
            headers=self.__headers,
            timeout=self.__timeout,
            raise_for_status=True,
            trust_env=True,
            trace_configs=[self.__trace_config] if self.debug else None,
            cookie_jar=cookie_jar,
        )
        await self.__reqsession.get("/")

    async def login(self, userid, password, twoFA, vendor_code, api_secret, imei):
        await self.__initialize_session_params()

        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['authorize']}"

        pwd = hashlib.sha256(password.encode("utf-8")).hexdigest()
        u_app_key = f"{userid}|{api_secret}"
        app_key = hashlib.sha256(u_app_key.encode("utf-8")).hexdigest()

        values = {
            "source": "API",
            "apkversion": "1.0.0",
            "uid": userid,
            "pwd": pwd,
            "factor2": twoFA,
            "vc": vendor_code,
            "appkey": app_key,
            "imei": imei,
        }

        payload = "jData=" + json.dumps(values)
        await reportmsg("Req:" + payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg("Reply:" + text)
            resDict = json.loads(text)

        self.__username = userid
        self.__accountid = userid
        self.__password = password
        self.__susertoken = resDict.get("susertoken")

        return resDict

    async def set_session(self, userid, password, usertoken):
        await self.__initialize_session_params()

        self.__username = userid
        self.__accountid = userid
        self.__password = password
        self.__susertoken = usertoken

        await reportmsg(f"{userid} session set to : {self.__susertoken}")

        return True

    async def forgot_password(self, userid, pan, dob):
        config = NorenApiAsync.__service_config
        url = f"{config['host']}{config['routes']['forgot_password']}"

        values = {"source": "API", "uid": userid, "pan": pan, "dob": dob}

        payload = "jData=" + json.dumps(values)
        await reportmsg("Req:" + payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg("Reply:" + text)
            resDict = json.loads(text)

        return resDict

    async def logout(self):
        config = NorenApiAsync.__service_config
        url = f"{config['host']}{config['routes']['logout']}"

        values = {"ordersource": "API", "uid": self.__username}

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        self.__username = None
        self.__accountid = None
        self.__password = None
        self.__susertoken = None

        return resDict

    async def subscribe(self, instrument, feed_type=FeedType.TOUCHLINE):
        values = {}

        if feed_type == FeedType.TOUCHLINE:
            values["t"] = "t"
        elif feed_type == FeedType.SNAPQUOTE:
            values["t"] = "d"
        else:
            values["t"] = str(feed_type)

        if isinstance(instrument, list):
            values["k"] = "#".join(instrument)
        else:
            values["k"] = instrument

        data = json.dumps(values)
        await self.__ws_send(data)

    async def unsubscribe(self, instrument, feed_type=FeedType.TOUCHLINE):
        values = {}

        if feed_type == FeedType.TOUCHLINE:
            values["t"] = "u"
        elif feed_type == FeedType.SNAPQUOTE:
            values["t"] = "ud"

        if isinstance(instrument, list):
            values["k"] = "#".join(instrument)
        else:
            values["k"] = instrument

        data = json.dumps(values)
        await self.__ws_send(data)

    async def subscribe_orders(self):
        values = {"t": "o", "actid": self.__accountid}
        data = json.dumps(values)
        await reportmsg(data)
        await self.__ws_send(data)

    async def get_watch_list_names(self):
        config = NorenApiAsync.__service_config
        url = f"{config['host']}{config['routes']['watchlist_names']}"

        values = {"ordersource": "API", "uid": self.__username}

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_watch_list(self, wlname):
        config = NorenApiAsync.__service_config
        url = f"{config['host']}{config['routes']['watchlist']}"

        values = {"ordersource": "API", "uid": self.__username, "wlname": wlname}

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def add_watch_list_scrip(self, wlname, instrument):
        config = NorenApiAsync.__service_config
        url = f"{config['host']}{config['routes']['watchlist_add']}"

        values = {"ordersource": "API", "uid": self.__username, "wlname": wlname}

        if isinstance(instrument, list):
            values["scrips"] = "#".join(instrument)
        else:
            values["scrips"] = instrument

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def delete_watch_list_scrip(self, wlname, instrument):
        config = NorenApiAsync.__service_config
        url = f"{config['host']}{config['routes']['watchlist_delete']}"

        values = {"ordersource": "API", "uid": self.__username, "wlname": wlname}

        if isinstance(instrument, list):
            values["scrips"] = "#".join(instrument)
        else:
            values["scrips"] = instrument

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def place_order(
        self,
        buy_or_sell,
        product_type,
        exchange,
        tradingsymbol,
        quantity,
        discloseqty,
        price_type,
        price=0.0,
        trigger_price=None,
        retention="DAY",
        amo=None,
        remarks=None,
        bookloss_price=0.0,
        bookprofit_price=0.0,
        trail_price=0.0,
    ):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['placeorder']}"

        values = {
            "ordersource": "API",
            "uid": self.__username,
            "actid": self.__accountid,
            "trantype": buy_or_sell,
            "prd": product_type,
            "exch": exchange,
            "tsym": urllib.parse.quote_plus(tradingsymbol),
            "qty": str(quantity),
            "dscqty": str(discloseqty),
            "prctyp": price_type,
            "prc": str(price),
            "trgprc": str(trigger_price),
            "ret": retention,
            "remarks": remarks,
        }

        if amo is not None:
            values["amo"] = amo

        if product_type in ["H", "B"]:
            values["blprc"] = str(bookloss_price)
            if trail_price != 0.0:
                values["trailprc"] = str(trail_price)

        if product_type == "B":
            values["bpprc"] = str(bookprofit_price)

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def modify_order(
        self,
        orderno,
        exchange,
        tradingsymbol,
        newquantity,
        newprice_type,
        newprice=0.0,
        newtrigger_price=None,
        bookloss_price=0.0,
        bookprofit_price=0.0,
        trail_price=0.0,
    ):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['modifyorder']}"

        values = {
            "ordersource": "API",
            "uid": self.__username,
            "actid": self.__accountid,
            "norenordno": str(orderno),
            "exch": exchange,
            "tsym": urllib.parse.quote_plus(tradingsymbol),
            "qty": str(newquantity),
            "prctyp": newprice_type,
            "prc": str(newprice),
        }

        if newprice_type in ["SL-LMT", "SL-MKT"]:
            if newtrigger_price is not None:
                values["trgprc"] = str(newtrigger_price)
            else:
                await reporterror("trigger price is missing")
                return None

        if bookloss_price != 0.0:
            values["blprc"] = str(bookloss_price)
        if trail_price != 0.0:
            values["trailprc"] = str(trail_price)
        if bookprofit_price != 0.0:
            values["bpprc"] = str(bookprofit_price)

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def cancel_order(self, orderno):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['cancelorder']}"

        values = {
            "ordersource": "API",
            "uid": self.__username,
            "norenordno": str(orderno),
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def exit_order(self, orderno, product_type):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['exitorder']}"

        values = {
            "ordersource": "API",
            "uid": self.__username,
            "norenordno": orderno,
            "prd": product_type,
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def position_product_conversion(
        self,
        exchange,
        tradingsymbol,
        quantity,
        new_product_type,
        previous_product_type,
        buy_or_sell,
        day_or_cf,
    ):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['product_conversion']}"

        values = {
            "ordersource": "API",
            "uid": self.__username,
            "actid": self.__accountid,
            "exch": exchange,
            "tsym": urllib.parse.quote_plus(tradingsymbol),
            "qty": str(quantity),
            "prd": new_product_type,
            "prevprd": previous_product_type,
            "trantype": buy_or_sell,
            "postype": day_or_cf,
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def single_order_history(self, orderno):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['singleorderhistory']}"

        values = {"ordersource": "API", "uid": self.__username, "norenordno": orderno}

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_order_book(self):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['orderbook']}"

        values = {"ordersource": "API", "uid": self.__username}

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    def __del__(self):
        if self.__reqsession or self.__connector:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.cleanup())
                else:
                    loop.run_until_complete(self.cleanup())
            except RuntimeError:
                # If there's no event loop, we can't do async cleanup
                print("Warning: Unable to perform async cleanup in __del__")

    async def cleanup(self):
        if self.__reqsession:
            await self.__reqsession.close()
            self.__reqsession = None
        if self.__connector:
            await self.__connector.close()
            self.__connector = None
        logger.debug("Cleanup completed")

    async def get_trade_book(self):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['tradebook']}"

        values = {
            "ordersource": "API",
            "uid": self.__username,
            "actid": self.__accountid,
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def searchscrip(self, exchange, searchtext):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['searchscrip']}"

        if searchtext is None:
            await reporterror("search text cannot be null")
            return None

        values = {
            "uid": self.__username,
            "exch": exchange,
            "stext": urllib.parse.quote_plus(searchtext),
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_option_chain(self, exchange, tradingsymbol, strikeprice, count=2):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['optionchain']}"

        values = {
            "uid": self.__username,
            "exch": exchange,
            "tsym": urllib.parse.quote_plus(tradingsymbol),
            "strprc": str(strikeprice),
            "cnt": str(count),
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_security_info(self, exchange, token):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['scripinfo']}"

        values = {"uid": self.__username, "exch": exchange, "token": token}

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_quotes(self, exchange, token):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['getquotes']}"
        await reportmsg(url)

        values = {"uid": self.__username, "exch": exchange, "token": token}

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_time_price_series(
        self, exchange, token, starttime=None, endtime=None, interval=None
    ):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['TPSeries']}"
        await reportmsg(url)

        if starttime is None:
            timestring = time.strftime("%d-%m-%Y") + " 00:00:00"
            timeobj = time.strptime(timestring, "%d-%m-%Y %H:%M:%S")
            starttime = time.mktime(timeobj)

        values = {"ordersource": "API"}
        values["uid"] = self.__username
        values["exch"] = exchange
        values["token"] = token
        values["st"] = str(starttime)
        if endtime is not None:
            values["et"] = str(endtime)
        if interval is not None:
            values["intrv"] = str(interval)

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_daily_price_series(
        self, exchange, tradingsymbol, startdate=None, enddate=None
    ):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['get_daily_price_series']}"
        await reportmsg(url)

        if startdate is None:
            week_ago = datetime.date.today() - datetime.timedelta(days=7)
            startdate = dt.combine(week_ago, dt.min.time()).timestamp()

        if enddate is None:
            enddate = dt.now().timestamp()

        values = {
            "uid": self.__username,
            "sym": f"{exchange}:{tradingsymbol}",
            "from": str(startdate),
            "to": str(enddate),
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"
        await reportmsg(payload)

        headers = {"Content-Type": "application/json; charset=utf-8"}

        async with self.__reqsession.post(
            url, data=payload, headers=headers
        ) as response:
            if response.status != 200:
                return None
            text = await response.text()
            if len(text) == 0:
                return None
            resDict = json.loads(text)

        return resDict

    async def get_holdings(self, product_type=None):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['holdings']}"
        await reportmsg(url)

        if product_type is None:
            product_type = ProductType.Delivery

        values = {
            "uid": self.__username,
            "actid": self.__accountid,
            "prd": product_type,
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_limits(self, product_type=None, segment=None, exchange=None):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['limits']}"
        await reportmsg(url)

        values = {
            "uid": self.__username,
            "actid": self.__accountid,
        }

        if product_type is not None:
            values["prd"] = product_type

        if segment is not None:
            values["seg"] = segment

        if exchange is not None:
            values["exch"] = exchange

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def get_positions(self):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['positions']}"
        await reportmsg(url)

        values = {
            "uid": self.__username,
            "actid": self.__accountid,
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"
        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def span_calculator(self, actid, positions: list):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['span_calculator']}"
        await reportmsg(url)

        senddata = {"actid": self.__accountid, "pos": positions}
        payload = (
            "jData="
            + json.dumps(senddata, default=lambda o: o.encode())
            + f"&jKey={self.__susertoken}"
        )
        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict

    async def option_greek(
        self, expiredate, StrikePrice, SpotPrice, InterestRate, Volatility, OptionType
    ):
        config = NorenApiAsync.__service_config
        url = f"{config['path']}{config['routes']['option_greek']}"
        await reportmsg(url)

        values = {
            "source": "API",
            "actid": self.__accountid,
            "exd": expiredate,
            "strprc": StrikePrice,
            "sptprc": SpotPrice,
            "int_rate": InterestRate,
            "volatility": Volatility,
            "optt": OptionType,
        }

        payload = "jData=" + json.dumps(values) + f"&jKey={self.__susertoken}"

        await reportmsg(payload)

        async with self.__reqsession.post(url, data=payload) as response:
            text = await response.text()
            await reportmsg(text)
            resDict = json.loads(text)

        return resDict


if __name__ == "__main__":

    async def main():
        cred = None
        with open("cred.yml") as f:
            creds = yaml.load(f, Loader=yaml.FullLoader)

            if creds is None or "Finvasia" not in creds:
                raise ValueError("No credentials found")

            cred = creds["Finvasia"]

        api = NorenApiAsync(
            host="https://api.shoonya.com/NorenWClientTP/",
            websocket="wss://api.shoonya.com/NorenWSTP/",
        )

        userToken = None
        tokenFile = "shoonyakey.txt"
        if os.path.exists(tokenFile) and (
            datetime.datetime.fromtimestamp(os.path.getmtime(tokenFile)).date()
            == datetime.datetime.today().date()
        ):
            logger.info("Token has been created today already. Re-using it")
            with open(tokenFile, "r") as f:
                userToken = f.read()
            logger.info(
                f"userid {cred['user']} password ******** usertoken {userToken}"
            )
            loginStatus = await api.set_session(
                userid=cred["user"], password=cred["pwd"], usertoken=userToken
            )
        else:
            logger.info("Logging in and persisting user token")
            loginStatus = await api.login(
                userid=cred["user"],
                password=cred["pwd"],
                twoFA=pyotp.TOTP(cred["factor2"]).now(),
                vendor_code=cred["vc"],
                api_secret=cred["apikey"],
                imei=cred["imei"],
            )

            if loginStatus:
                with open(tokenFile, "w") as f:
                    f.write(loginStatus.get("susertoken"))

                logger.info(
                    f"{loginStatus.get('uname')}={loginStatus.get('stat')} token={loginStatus.get('susertoken')}"
                )
            else:
                raise ValueError("Login failed")

        order1 = await api.place_order(
            buy_or_sell="B",
            product_type="C",
            exchange="NSE",
            tradingsymbol="YESBANK-EQ",
            quantity=1,
            discloseqty=0,
            price_type="LMT",
            price=20,
            trigger_price=None,
            retention="DAY",
            remarks="my_order_001",
        )

        print(order1)

        order2 = await api.modify_order(
            orderno=order1["norenordno"],
            exchange="NSE",
            tradingsymbol="YESBANK-EQ",
            newquantity=1,
            newprice_type="LMT",
            newprice=18,
        )

        print(order2)

    asyncio.run(main())
