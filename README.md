# TG100 SMS Gateway Service

A Python service that integrates Yeastar TG100 GSM gateway with Telegram. The service connects to TG100 via the Asterisk Manager Interface (AMI) protocol, monitors incoming SMS messages in real-time, and forwards them to Telegram.

## Delivery Modes

| Mode | How SMS goes to Telegram | Required settings |
|------|--------------------------|-------------------|
| `telegram` (default) | Straight to Telegram Bot API | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| `relay` (legacy) | Signed POST to the relay server (gsm-to-telegram-bot), which sends to Telegram | `API_URL`, `API_BEARER_TOKEN`, `HMAC_SECRET_KEY` |

Choose the mode with `DELIVERY_MODE` in `.env` or at start:

```bash
python main.py                # mode from .env, default: telegram
python main.py --mode relay   # legacy mode via relay server
```

Telegram errors: 400 — message is set aside as `failed` in the queue database (retry with `queue_tool.py retry`); 401/403/404 (wrong token or chat), 429, 5xx and network errors — message stays in queue and is retried.

The sections below about the remote API, message format and signature apply to `relay` mode only.

## Features

- **Real-time SMS Monitoring**: Connects to TG100 via AMI protocol and processes incoming SMS messages instantly
- **Cryptographic Signatures**: Signs all messages with HMAC-SHA256 for authenticity verification
- **Reliable Delivery**: Message queue with exponential backoff retry logic ensures no messages are lost
- **Persistent Queue**: The queue is stored in SQLite (`queue.db`, `QUEUE_DB_PATH`): undelivered messages survive a restart. Delivered messages are kept 7 days, failed ones 30 days. `python queue_tool.py list|show|retry` to inspect and retry them
- **Automatic Reconnection**: Handles connection failures gracefully with automatic reconnection
- **Comprehensive Logging**: Detailed logging with timestamps for monitoring and troubleshooting
- **Simple Configuration**: Environment-based configuration via .env file

## Requirements

- Python 3.7 or higher
- Yeastar TG100 GSM gateway with AMI access enabled
- Remote HTTPS API endpoint for receiving SMS notifications

## Installation

1. Clone or download this repository

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file based on the provided `.env.example`:
```bash
cp .env.example .env
```

4. Edit the `.env` file with your configuration (see Configuration section below)

## Configuration

All configuration is done through the `.env` file. The following parameters are required:

### TG100 Connection Parameters

- `TG100_HOST`: IP address or hostname of your TG100 gateway (e.g., `192.168.1.100`)
- `TG100_PORT`: AMI port number (default is `5038`)
- `TG100_USERNAME`: AMI username configured on TG100
- `TG100_PASSWORD`: AMI password for the username

### Remote API Parameters

- `API_URL`: Full HTTPS URL of your remote API endpoint (e.g., `https://api.example.com/sms/incoming`)
- `API_BEARER_TOKEN`: Bearer token for API authentication

### Security Parameters

- `HMAC_SECRET_KEY`: Secret key used for HMAC-SHA256 signature generation (keep this secure!)

### Optional Parameters

- `INITIAL_RETRY_DELAY`: Initial delay in seconds before retrying failed operations (default: `5`)
- `MAX_RETRY_DELAY`: Maximum delay in seconds between retry attempts (default: `60`)
- `PING_INTERVAL`: Interval in seconds between AMI keepalive pings (default: `300`, set to `0` to disable)
  - Recommended: 60-300 seconds for active monitoring, 0 to rely only on TCP keepalive
- `LOG_LEVEL`: Logging verbosity level - `DEBUG`, `INFO`, `WARNING`, or `ERROR` (default: `INFO` for console, `DEBUG` for file)
  - Use `DEBUG` to see detailed protocol messages from TG100, helpful for troubleshooting authentication issues

### Example .env File

```bash
# TG100 Connection
TG100_HOST=192.168.1.100
TG100_PORT=5038
TG100_USERNAME=admin
TG100_PASSWORD=your_password_here

# Remote API
API_URL=https://api.example.com/sms/incoming
API_BEARER_TOKEN=your_bearer_token_here

# Security
HMAC_SECRET_KEY=your_secret_key_here

# Retry Configuration (optional)
INITIAL_RETRY_DELAY=5
MAX_RETRY_DELAY=60

# Keepalive Configuration (optional)
PING_INTERVAL=300  # Send AMI Ping every 5 minutes (0 to disable)

# Logging Configuration (optional)
# LOG_LEVEL=DEBUG  # Use DEBUG to see detailed TG100 protocol messages
```

## Running the Service

### Start the Service

```bash
python main.py
```

Or make it executable and run directly:

```bash
chmod +x main.py
./main.py
```

### Run as a Background Service

Using nohup:
```bash
nohup python main.py > sms_gateway.log 2>&1 &
```

Using systemd (recommended for production):

1. Create a systemd service file `/etc/systemd/system/tg100-sms-gateway.service`:

```ini
[Unit]
Description=TG100 SMS Gateway Service
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/tg100-sms-gateway
ExecStart=/usr/bin/python3 /path/to/tg100-sms-gateway/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

2. Enable and start the service:
```bash
sudo systemctl enable tg100-sms-gateway
sudo systemctl start tg100-sms-gateway
```

3. Check service status:
```bash
sudo systemctl status tg100-sms-gateway
```

### Graceful Shutdown

Press `Ctrl+C` to gracefully shut down the service. The service will:
1. Stop processing new messages
2. Close the AMI connection
3. Log the shutdown event
4. Exit cleanly

## Message Format

The service sends SMS messages to your remote API as JSON payloads with the following structure:

```json
{
    "gateway_id": "dongle0",
    "sender": "+1234567890",
    "timestamp": "2024-01-15T10:30:45Z",
    "message": "Hello World",
    "signature": "a1b2c3d4e5f6789..."
}
```

### Fields

- `gateway_id`: Identifier of the TG100 dongle that received the SMS
- `sender`: Phone number of the SMS sender
- `timestamp`: ISO 8601 UTC timestamp when the message was received
- `message`: The SMS message content (decoded from Base64)
- `signature`: HMAC-SHA256 signature (hex-encoded) for authenticity verification

### Signature Verification

To verify the signature on your API server:

1. Extract the `signature` field from the JSON
2. Remove the `signature` field from the JSON object
3. Serialize the remaining JSON with sorted keys
4. Compute HMAC-SHA256 using your shared secret key
5. Compare the computed signature with the received signature

Example in Python:
```python
import json
import hmac
import hashlib

def verify_signature(payload, secret_key):
    # Extract signature
    received_signature = payload.pop('signature')
    
    # Serialize with sorted keys
    message = json.dumps(payload, sort_keys=True)
    
    # Compute HMAC-SHA256
    computed_signature = hmac.new(
        secret_key.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Compare signatures
    return hmac.compare_digest(received_signature, computed_signature)
```

## Logging

The service generates detailed logs for monitoring and troubleshooting.

### Log Format

**Main Application Log (sms_gateway.log):**
```
[TIMESTAMP] [LEVEL] [COMPONENT] Message
```

Example:
```
[2024-01-15T10:30:45Z] [INFO] [AMIClient] Connected to TG100 at 192.168.1.100:5038
[2024-01-15T10:30:46Z] [INFO] [AMIClient] Authentication successful
[2024-01-15T10:31:00Z] [INFO] [MessageProcessor] Received SMS from +1234567890
[2024-01-15T10:31:01Z] [INFO] [APIDelivery] Successfully delivered message to remote API
```

**AMI Protocol Trace (trace.log):**

The `trace.log` file contains a complete record of all data sent to and received from TG100. This is extremely useful for debugging protocol issues.

Format:
```
================================================================================
[TIMESTAMP] SEND or RECV
--------------------------------------------------------------------------------
<AMI protocol data>
================================================================================
```

Example:
```
================================================================================
[2025-11-18T12:00:00.123456Z] RECV
--------------------------------------------------------------------------------
Asterisk Call Manager/2.10.0

================================================================================

================================================================================
[2025-11-18T12:00:00.234567Z] SEND
--------------------------------------------------------------------------------
Action: Login
Username: admin
Secret: mypassword

================================================================================

================================================================================
[2025-11-18T12:00:00.345678Z] RECV
--------------------------------------------------------------------------------
Response: Success
Message: Authentication accepted

================================================================================
```

See `trace_example.log` for a complete example of trace output.

### Log Levels

- **INFO**: Normal operations (connections, message processing, successful deliveries)
- **WARNING**: Non-critical issues (malformed events, skipped messages)
- **ERROR**: Errors that require attention (connection failures, delivery failures)

### Log Files

Logs are written to:
- **`sms_gateway.log`**: Main log file in the current directory with automatic rotation
- **`trace.log`**: Raw AMI protocol trace showing all data sent to and received from TG100
- **Console (stdout)**: Real-time log output for monitoring

Log files are automatically rotated when they reach 10MB, with up to 5 backup files kept (`sms_gateway.log.1`, `sms_gateway.log.2`, etc.).

To view logs in real-time:
```bash
# Main application log
tail -f sms_gateway.log

# Raw AMI protocol trace (for debugging)
tail -f trace.log
```

## Troubleshooting

### Service Won't Start

**Problem**: Service exits immediately after starting

**Solutions**:
1. Check that `.env` file exists and contains all required parameters
2. Verify TG100 connection parameters (host, port, username, password)
3. Check logs for specific error messages
4. Ensure TG100 AMI is enabled and accessible from your network

### Connection Failures

**Problem**: "Failed to establish initial connection to TG100"

**Solutions**:
1. Verify TG100 is powered on and network-accessible
2. Check firewall rules allow TCP connection to AMI port (default 5038)
3. Ping the TG100 host to verify network connectivity
4. Verify the AMI port number in your configuration

### Authentication Failures

**Problem**: "Failed to authenticate with TG100"

**Solutions**:
1. Check the `trace.log` file to see the exact AMI protocol exchange:
   ```bash
   cat trace.log
   ```
   This file shows all data sent to and received from TG100, marked with `SEND` and `RECV`
2. Enable DEBUG logging to see the exact response from TG100:
   ```bash
   # Add to .env file
   LOG_LEVEL=DEBUG
   ```
   Then check `sms_gateway.log` for the full TG100 response
3. Verify AMI username and password in `.env` file
4. Check TG100 AMI user configuration
5. Ensure the AMI user has appropriate permissions
6. Check TG100 logs for authentication errors

### Messages Not Being Delivered

**Problem**: Messages are queued but not delivered to remote API

**Solutions**:
1. Check API_URL is correct and accessible
2. Verify API_BEARER_TOKEN is valid
3. Check remote API logs for errors
4. Look for HTTP error codes in service logs
5. Verify network connectivity to remote API
6. Check if remote API is rate-limiting requests

### Connection Keeps Dropping

**Problem**: Service repeatedly reconnects to TG100

**Solutions**:
1. Check network stability between service and TG100
2. Verify TG100 firmware is up to date
3. Check TG100 system logs for issues
4. Increase reconnection delays if network is unstable
5. Monitor TG100 resource usage (CPU, memory)

### High Memory Usage

**Problem**: Service memory usage grows over time

**Solutions**:
1. Check message queue size in logs
2. Verify remote API is responding to requests
3. If API is down, messages will queue indefinitely
4. Consider implementing queue size limits for production use

### Viewing Logs

To view real-time logs:
```bash
tail -f sms_gateway.log
```

To search logs for errors:
```bash
grep ERROR sms_gateway.log
```

To view logs from systemd service:
```bash
sudo journalctl -u tg100-sms-gateway -f
```

## Error Handling

### Temporary Failures (Automatic Retry)

The service automatically retries these errors with exponential backoff:
- HTTP 5xx server errors
- HTTP 408 Request Timeout
- HTTP 429 Too Many Requests
- Network connection errors
- TG100 connection loss

### Permanent Failures (Message Discarded)

These errors cause messages to be discarded after logging:
- HTTP 400 Bad Request
- HTTP 401 Unauthorized
- HTTP 403 Forbidden
- HTTP 404 Not Found
- HTTP 422 Unprocessable Entity

### Malformed Events

If the service receives malformed AMI events:
- A warning is logged with event details
- The event is skipped
- Service continues monitoring for new events

## Architecture

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────┐
│   TG100     │  AMI    │  SMS Gateway     │  HTTPS  │  Remote     │
│   Gateway   │────────▶│  Service         │────────▶│  API        │
│             │  TCP    │                  │  POST   │             │
└─────────────┘         └──────────────────┘         └─────────────┘
                              │
                              │ Logs
                              ▼
                        ┌──────────────┐
                        │  Log Files   │
                        └──────────────┘
```

### Components

1. **AMI Client**: Manages TCP connection to TG100 and parses AMI protocol messages
2. **Message Queue**: FIFO queue for pending message deliveries
3. **API Delivery Client**: Handles HTTPS delivery with retry logic
4. **Service Orchestrator**: Coordinates all components and manages service lifecycle

## Development

### Running Tests

```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest test_config.py

# Run with coverage
python -m pytest --cov=. --cov-report=html
```

### Project Structure

```
.
├── main.py                      # Main entry point
├── config.py                    # Configuration management
├── sms_gateway_service.py       # Main service orchestrator
├── ami_client.py                # AMI protocol client
├── sms_message.py               # SMS message model
├── message_queue.py             # Message queue (SQLite on disk, or in memory)
├── queue_tool.py                # Inspect the queue database, retry failed messages
├── api_delivery_client.py       # API delivery client
├── retry_logic.py               # Retry and backoff logic
├── logging_config.py            # Logging configuration
├── requirements.txt             # Python dependencies
├── .env.example                 # Example configuration file
├── README.md                    # This file
└── test_*.py                    # Test files
```

## Security Considerations

1. **Keep .env file secure**: Never commit `.env` to version control
2. **Use strong HMAC keys**: Generate cryptographically secure random keys
3. **Use HTTPS for API**: Always use HTTPS for the remote API endpoint
4. **Rotate credentials**: Regularly rotate passwords and tokens
5. **Network security**: Use firewalls to restrict access to TG100 and the service
6. **Monitor logs**: Regularly review logs for suspicious activity

## License

[Your License Here]

## Support

For issues, questions, or contributions, please [contact information or repository link].
