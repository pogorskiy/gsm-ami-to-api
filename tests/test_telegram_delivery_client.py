"""Tests for Telegram Delivery Client."""

import pytest
import requests
from unittest.mock import Mock, patch
from sms_message import SMSMessage
from telegram_delivery_client import (
    TelegramDeliveryClient, format_telegram_message, decode_sms_text,
    TELEGRAM_MAX_TEXT_LENGTH
)


@pytest.fixture
def client():
    return TelegramDeliveryClient(bot_token="123:ABC", chat_id="-100500", timeout=7)


@pytest.fixture
def message():
    return SMSMessage(
        gateway_id="2",
        sender="+37491000000",
        message="Код: 1234 <b>&</b>",
        timestamp="2026-11-02T10:35:39Z",
    )


def _response(status_code, json_data=None):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = json_data or {}
    response.text = str(json_data)
    return response


def test_format_escapes_html_and_keeps_layout(message):
    text = format_telegram_message(message)
    assert text.startswith("📱 New SMS\n")
    assert "🔹 Gateway: 2\n" in text
    assert "🔹 From: +37491000000\n" in text
    assert "🔹 Time: 2026-11-02T10:35:39Z\n" in text
    assert text.endswith("💬 Message:\nКод: 1234 &lt;b&gt;&amp;&lt;/b&gt;")


@pytest.mark.parametrize("raw, expected", [
    # Real TG100 format: form-style URL encoding
    ("Your+Inecobank+Online-Banking+Security+Code+is+1234%3ADo+not+pass",
     "Your Inecobank Online-Banking Security Code is 1234:Do not pass"),
    ("%EF%BB%BFfriend+request%3A+https%3A%2F%2Ffb.me%2Fabc",
     "friend request: https://fb.me/abc"),
    ("%D0%9A%D0%BE%D0%B4+%2B374", "Код +374"),
    ("plain text", "plain text"),
    ("100% sure", "100% sure"),
])
def test_decode_sms_text(raw, expected):
    assert decode_sms_text(raw) == expected


def test_format_decodes_then_escapes():
    message = SMSMessage("1", "x", "a+%3C%26%3E+b", "t")
    assert format_telegram_message(message).endswith("\na &lt;&amp;&gt; b")


def test_format_cuts_long_message_without_breaking_entities():
    long_message = SMSMessage("1", "x", "&" * 5000, "t")
    text = format_telegram_message(long_message)
    assert len(text) <= TELEGRAM_MAX_TEXT_LENGTH
    assert text.endswith("&amp;…")


@patch('telegram_delivery_client.requests.post')
def test_send_success(mock_post, client, message):
    mock_post.return_value = _response(200, {"ok": True})
    assert client.send_message(message) is True

    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert kwargs['json']['chat_id'] == "-100500"
    assert kwargs['json']['parse_mode'] == "HTML"
    assert kwargs['json']['text'] == format_telegram_message(message)
    assert kwargs['timeout'] == 7


@patch('telegram_delivery_client.requests.post')
def test_signature_argument_is_ignored(mock_post, client, message):
    mock_post.return_value = _response(200)
    assert client.send_message(message, "some-signature") is True


@pytest.mark.parametrize("status_code", [401, 403, 404, 429, 500, 502])
@patch('telegram_delivery_client.requests.post')
def test_temporary_failures(mock_post, status_code, client, message):
    mock_post.return_value = _response(status_code, {"ok": False, "description": "err"})
    assert client.send_message(message) is None


@patch('telegram_delivery_client.requests.post')
def test_bad_request_is_permanent(mock_post, client, message):
    mock_post.return_value = _response(400, {"ok": False, "description": "Bad Request"})
    assert client.send_message(message) is False


@pytest.mark.parametrize("error", [
    requests.exceptions.Timeout(),
    requests.exceptions.ConnectionError("https://api.telegram.org/bot123:ABC/sendMessage"),
    requests.exceptions.RequestException(),
])
@patch('telegram_delivery_client.requests.post')
def test_network_errors_are_temporary(mock_post, error, client, message):
    mock_post.side_effect = error
    assert client.send_message(message) is None


@patch('telegram_delivery_client.requests.post')
def test_bot_token_not_logged_on_connection_error(mock_post, client, message, caplog):
    mock_post.side_effect = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool: /bot123:ABC/sendMessage"
    )
    with caplog.at_level("DEBUG"):
        client.send_message(message)
    assert "123:ABC" not in caplog.text
