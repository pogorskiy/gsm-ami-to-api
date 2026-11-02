#!/usr/bin/env python3
import asyncio
import logging
from typing import Dict, Callable, Optional

logger = logging.getLogger("yeastar_ami")


class YeastarAmiClient:
    """
    Minimal, production-ready AMI client for Yeastar TG100.
    Features:
      - persistent connection with auto-reconnect;
      - AMI login (username/secret from .env);
      - parsing text messages into dict;
      - calling custom on_sms() handler for incoming SMS events.
    """

    def __init__(
        self,
        host: str,
        port: int = 5038,
        username: str = "admin",
        secret: str = "your-ami-password",
        reconnect_delay: float = 5.0,
        on_sms: Optional[Callable[[Dict[str, str]], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.secret = secret
        self.reconnect_delay = reconnect_delay
        self.on_sms = on_sms

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._running = False

    # ------------------- public API -------------------

    async def start(self) -> None:
        """
        Main loop: attempts to connect, reconnects on disconnect.
        Call from asyncio.run().
        """
        self._running = True
        logger.info("Starting Yeastar AMI client (%s:%s)", self.host, self.port)

        while self._running:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Connection loop error: %s", exc)

            if self._running:
                logger.warning("Disconnected from AMI, reconnecting in %.1f s", self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)

        logger.info("Yeastar AMI client stopped")

    async def stop(self) -> None:
        """
        Gracefully stops the client.
        """
        self._running = False
        if self._writer is not None:
            try:
                await self._send_action({"Action": "Logoff"})
            except Exception:
                pass
            self._writer.close()
            await self._writer.wait_closed()

    # ------------------- internal logic -------------------

    async def _connect_and_run(self) -> None:
        logger.info("Connecting to AMI %s:%s ...", self.host, self.port)
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

        # Some AMI implementations send a greeting line, read it
        greeting = await self._reader.readline()
        logger.debug("Server greeting: %s", greeting.decode(errors="ignore").rstrip())

        await self._login()
        logger.info("AMI login successful")

        # Main message reading loop
        while self._running:
            msg = await self._read_message()
            if msg is None:
                # EOF / connection lost
                raise ConnectionError("EOF from AMI server")

            await self._handle_message(msg)

    async def _login(self) -> None:
        """
        Sends Action: Login and verifies successful response.
        """
        await self._send_action(
            {
                "Action": "Login",
                "Username": self.username,
                "Secret": self.secret,
                # 'Events': 'on',  # if needed
            }
        )

        # Wait for Login Response
        while True:
            msg = await self._read_message()
            if msg is None:
                raise ConnectionError("EOF while waiting for Login response")

            if msg.get("Response") == "Success":
                return
            if msg.get("Response") == "Error":
                raise RuntimeError(f"Login failed: {msg.get('Message', 'no message')}")

            # Ignore greeting Events until we see Response

    async def _send_action(self, fields: Dict[str, str]) -> None:
        """
        Sends AMI action (Action).
        """
        if self._writer is None:
            raise RuntimeError("Writer is not initialized")

        lines = [f"{k}: {v}" for k, v in fields.items()]
        data = ("\r\n".join(lines) + "\r\n\r\n").encode()
        logger.debug("Sending action:\n%s", data.decode(errors="ignore"))
        self._writer.write(data)
        await self._writer.drain()

    async def _read_message(self) -> Optional[Dict[str, str]]:
        """
        Reads one AMI message (until empty line) and parses into dict.
        Returns None on EOF.
        """
        if self._reader is None:
            raise RuntimeError("Reader is not initialized")

        lines = []
        while True:
            line = await self._reader.readline()
            if not line:
                # EOF
                if not lines:
                    return None
                break
            line = line.decode(errors="ignore").rstrip("\r\n")
            if line == "":
                break
            lines.append(line)

        if not lines:
            return None

        msg: Dict[str, str] = {}
        for line in lines:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            msg[key.strip()] = value.strip()

        logger.debug("Received message: %s", msg)
        return msg

    async def _handle_message(self, msg: Dict[str, str]) -> None:
        """
        Handles incoming message. Can branch by event types here.
        """
        event = msg.get("Event")
        if event:
            await self._handle_event(msg)
        else:
            # This is a Response to some Action - just log it.
            logger.debug("AMI response: %s", msg)

    async def _handle_event(self, msg: Dict[str, str]) -> None:
        event = msg.get("Event", "")
        event_lower = event.lower()

        # For TG100, incoming SMS arrive as Event: ReceivedSMS
        if event_lower in ("smsincoming", "receivedsms"):
            logger.info("Incoming SMS event: %s", msg)
            if self.on_sms:
                try:
                    self.on_sms(msg)
                except Exception:
                    logger.exception("Error in on_sms handler")
        else:
            # other events (port status, SMS sending, etc.)
            logger.info("Unhandled event %s: %s", event, msg)


# ------------------- usage example -------------------

def sms_handler(event: Dict[str, str]) -> None:
    """
    Custom incoming SMS handler.
    Here you can:
      - send HTTP-POST to your service,
      - write to database,
      - forward to Telegram, etc.
    """
    # TG100 (ReceivedSMS):
    # {'Event': 'ReceivedSMS', 'Privilege': 'all,smscommand', 'ID': '',
    #  'GsmSpan': '2', 'Sender': '+79...', 'Recvtime': '2025-11-18 14:41:26',
    #  'Index': '1', 'Total': '1', 'Smsc': '+79...', 'Content': 'Ggg'}

    port = (
        event.get("Port")
        or event.get("PortNum")
        or event.get("GsmSpan")  # for ReceivedSMS
    )
    caller = (
        event.get("CallerID")
        or event.get("From")
        or event.get("Sender")  # for ReceivedSMS
    )
    text = (
        event.get("Message")
        or event.get("Text")
        or event.get("Content")  # for ReceivedSMS
    )
    recv_time = event.get("Recvtime")  # handy to have

    logger.info(
        "SMS from %s on port %s at %s: %r",
        caller,
        port,
        recv_time,
        text,
    )
    # TODO: insert your logic here (HTTP request, database write, etc.)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    client = YeastarAmiClient(
        host="192.168.1.100",  # IP of your TG100
        port=5038,
        username="admin",
        secret="your-ami-password",
        reconnect_delay=5.0,
        on_sms=sms_handler,
    )

    # graceful start/stop via Ctrl+C
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_sig():
        logger.info("Stopping on signal...")
        stop_event.set()

    for signame in ("SIGINT", "SIGTERM"):
        try:
            loop.add_signal_handler(getattr(__import__("signal"), signame), _handle_sig)
        except (NotImplementedError, AttributeError):
            # Windows / limited environments - can be ignored
            pass

    client_task = asyncio.create_task(client.start())

    await stop_event.wait()
    await client.stop()
    await client_task


if __name__ == "__main__":
    asyncio.run(main())