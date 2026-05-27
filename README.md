# TCP Multi-Client Chat System

A room-based TCP chat server and client written in Python, built for CMSC 440 (Data Communication and Networking, Spring 2026).

The server uses `select()` to multiplex multiple client connections in a single thread. Messages are framed with a 4-byte length prefix followed by a JSON body, decoded out of per-socket read buffers to handle TCP's byte-stream semantics correctly. Application-layer heartbeats detect dead clients within 30 seconds.

## Features

- Single-threaded server using `select()` for I/O multiplexing
- Length-prefixed JSON message framing over TCP
- Room-based chat with automatic empty-room cleanup
- Private messaging across rooms
- Room history (last 20 messages per room, replayed on join)
- Nickname management with uniqueness enforcement
- Heartbeat ping every 10 seconds with 30-second timeout
- Graceful disconnect handling (commanded, crashed, or script-piped)
- Session statistics summary on client exit

## Requirements

- Python 3 (no external dependencies, standard library only)

## How to Run

Start the server on one machine:

```bash
python3 ChatServer.py <port>
```

Example:

```bash
python3 ChatServer.py 10000
```

Start a client on another machine or terminal:

```bash
python3 ChatClient.py <hostname-or-ip> <port> <nickname> <clientID>
```

Example:

```bash
python3 ChatClient.py 10.0.1.1 10000 John 001
```

To stop the server, press `Ctrl-C`. To disconnect a client, type `/disconnect` or press `Ctrl-C`.

Tested on VCU VM Linux machines (`egr-v-cmsc440-1` and `egr-v-cmsc440-2`) using ports in the 10000-11000 range.

## Supported Commands

| Command | Description |
|---|---|
| `/join <room>` | Join or create a room. Leaves old room automatically. |
| `/leave` | Leave current room and return to lobby. |
| `/rooms` | List all active rooms with at least one user. |
| `/who <room>` | List nicknames currently in a room. |
| `/msg <nick> <text>` | Send a private message. Works across all rooms. |
| `/nick <newnick>` | Change your nickname. Must be unique. |
| `/disconnect` | Gracefully disconnect from the server. |

## Design Decisions

**`select()` instead of threading.** The server watches all connected sockets in a single loop without needing locks or multiple threads. The client also uses `select()` to watch the server socket and keyboard input at the same time.

**Length-prefixed JSON framing.** Messages are framed with a 4-byte big-endian length header followed by a JSON body encoded in UTF-8. Both sides use a per-socket read buffer to handle partial receives, since TCP is a byte stream and one `recv()` call might not return a complete message.

**`sendall()` over `send()`.** `send()` might not transmit all the bytes in one call. `sendall()` loops until everything is delivered, which matters for the framing protocol where the header and body need to arrive together. `sendall()` is wrapped in `try`/`except` on both the server and client to prevent `BrokenPipeError` crashes when input is piped from a test script and the other side disconnects mid-send. This lets the client still print its session summary instead of crashing.

**Registration state tracking.** The client tracks a `registered` flag in `main()`. If an error message arrives before registration is accepted (e.g., nickname taken), the client exits. If an error arrives after registration (e.g., a bad command), it displays the error and keeps running.

**Room name validation.** Room names must be 1-20 characters, letters/digits/underscores/hyphens only. No spaces.

**Room history.** The last 20 chat messages per room are buffered server-side and replayed to clients on join.

**Heartbeat.** Client sends a ping every 10 seconds. Server disconnects any client silent for more than 30 seconds.

**Graceful shutdown.** The client handles `Ctrl-C` by sending a disconnect message to the server, printing the session summary, and closing the socket. The server detects unexpected client crashes through failed `recv()` calls or heartbeat timeouts and cleans up the client from all data structures.

## Bonus Features

**Empty room cleanup.** When all users leave a room (other than lobby), the server automatically deletes the room and its chat history to keep state tidy. This happens in three places: `remove_client()` when a client disconnects, the `/join` handler when a client moves to a different room, and the `/leave` handler when a client returns to the lobby. The lobby always stays regardless of occupancy.

## Notes

- The `char sent` and `char rcv` counts in the client session summary track user-typed text for chat messages and the text portion of private messages. Slash commands other than `/msg` are not counted.
- Server log formats follow the exact format specified in the assignment for automated grading.

## Libraries Used

`socket`, `sys`, `json`, `struct`, `select`, `time`, `datetime`. All standard library.

## Author

Aaron Tuck
CMSC 440, Spring 2026
