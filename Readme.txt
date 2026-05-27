Aaron Tuck V01068858
CMSC 440 - Programming Assignment: Socket Chat Application 
Spring 2026

How to Run
-----------
No compilation needed. Written in Python 3.

Start the server (on one machine):
    python3 ChatServer.py <port>
    Example: python3 ChatServer.py 10000

Start a client (on another machine or terminal):
    python3 ChatClient.py <hostname or ip> <port> <nickname> <clientID>
    Example: python3 ChatClient.py 10.0.1.1 10000 John 001

To stop the server, press Ctrl-C.
To disconnect a client, type /disconnect or press Ctrl-C.

Tested on VCU VM Linux machines (egr-v-cmsc440-1 and egr-v-cmsc440-2)
using ports in the 10000-11000 range.

Supported Commands
-------------------
/join <room>        Join or create a room. Leaves old room automatically.
/leave              Leave current room and return to lobby.
/rooms              List all active rooms with at least one user.
/who <room>         List nicknames currently in a room.
/msg <nick> <text>  Send a private message. Works across all rooms.
/nick <newnick>     Change your nickname. Must be unique.
/disconnect         Gracefully disconnect from the server.

Design Decisions
-----------------
- Used select() instead of threading to handle multiple clients on the server.
  This lets the server watch all connected sockets in a single loop without
  needing locks or multiple threads. The client also uses select() to watch
  the server socket and keyboard input at the same time.

- Messages are framed using a 4-byte big-endian length header followed by a
  JSON body encoded in UTF-8. Both sides use a read buffer to handle partial
  receives, since TCP is a byte stream and one recv() call might not return
  a complete message.

- Used sendall() instead of send() for transmitting messages. send() might
  not send all the bytes in one call, but sendall() loops until everything
  is delivered. This matters for our framing protocol where the header and
  body need to arrive together. Wrapped sendall() in a try/except on both
  the server and client since the grading procedure includes an auto script.
  This prevents a BrokenPipeError crash when input is piped from a script
  and the other side disconnects before the send finishes, so the client
  can still print its session summary instead of crashing.

- The client tracks a "registered" flag in main(). If an error message
  arrives before registration is accepted, the client exits. If an error
  arrives after registration (like a bad command), it just displays the
  error and keeps running.

- Room names are validated to be 1-20 characters using only letters, digits,
  underscores, or hyphens. No spaces allowed.

- Room history stores the last 20 chat messages per room. When a client joins
  a room, the full history is sent to them.

- The heartbeat ping is sent every 10 seconds from the client. The server
  disconnects any client that has been silent for more than 30 seconds.

- The client handles Ctrl-C cleanly by sending a disconnect message to the
  server before printing the session summary and closing the socket.

- The server handles unexpected client crashes. If a client disappears without
  sending a disconnect message, the server detects it either through a failed
  recv() or through the heartbeat timeout, cleans up the client from all data
  structures, and logs the disconnect.

Libraries Used
---------------
socket, sys, json, struct, select, time, datetime (all standard)

Bonus Features
---------------
1. Empty room cleanup: When all users leave a room (other than lobby),
   the server automatically deletes that room and its chat history to
   keep the server tidy. This happens in three places: in remove_client()
   when a client disconnects, in command_line_handler() under /join when
   a client moves to a different room, and under /leave when a client
   returns to lobby. The lobby room always stays regardless of how many
   users are in it.

Notes
------
- The char sent and char rcv counts in the client session summary track
  the user-typed text for chat messages and the text portion of private
  messages. Slash commands other than /msg are not counted.

- Server log formats follow the exact format specified in the assignment
  for automated grading.
