# ChatServer.py
# CMSC 440 - Room-Based Chat Server
# Handles multiple clients, rooms, chat broadcasting, and heartbeat timeouts

from socket import *
import sys
import json
import struct
import select
from datetime import datetime


# Helper Function: get_timestamp
# returns a formatted date/time string used in server logs and JSON messages
def get_timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# Helper Function: send_msg
# sends a framed message over a TCP socket
# every message has two parts: a 4-byte header with the body length,
# followed by the actual JSON body encoded as UTF-8 bytes
# struct.pack('>I', ...) creates a big-endian unsigned 4-byte integer
# returns True if send worked, False if the other side already disconnected
def send_msg(sock, msg_dict):
    # convert the python dict into a JSON string, then encode to bytes
    body = json.dumps(msg_dict).encode('utf-8')
    # pack the body length into a 4-byte big-endian header
    header = struct.pack('>I', len(body))

    # using sendall instead of send because send might not send everything at once
    # sendall keeps going until all the bytes are delivered
    # the other side could disconnect at any time so we catch that here
    try:
        sock.sendall(header + body)
        return True
    except Exception:
        return False


# Helper Function: try_parse_message
# tries to extract one complete framed message from a byte buffer
# if the buffer has enough data (4 bytes for length + N bytes for body),
# it returns the parsed message dict and whatever bytes are left over
# if there isn't enough data yet, it returns None and the unchanged buffer
def try_parse_message(buffer):
    # we need at least 4 bytes to read the length header
    if len(buffer) < 4:
        return None, buffer

    # unpack the first 4 bytes to get the message body length
    msg_len = struct.unpack('>I', buffer[:4])[0]

    # if the length is zero or way too big, the frame is invalid
    if msg_len == 0 or msg_len > 65536:
        return "INVALID", buffer

    # check if we have received enough bytes for the full body
    if len(buffer) < 4 + msg_len:
        return None, buffer

    # pull out just the body bytes from the buffer
    body_bytes = buffer[4 : 4 + msg_len]

    # everything after this message stays in the buffer for next time
    remaining = buffer[4 + msg_len :]

    # decode the bytes into a string and parse it as JSON
    body_str = body_bytes.decode('utf-8')
    msg_dict = json.loads(body_str)
    return msg_dict, remaining


# -- DATA STRUCTURES --
# clients: maps each nickname to their info (socket, address, clientID, room, last_seen)
# socket_to_nick: reverse lookup so we can find a nickname from a socket object
# rooms: maps each room name to a list of nicknames currently in that room
# room_history: maps each room name to a list of the last 20 chat messages in that room
# buffers: each socket has its own byte buffer to hold partial data between recv() calls
# unregistered: tracks sockets that connected but haven't sent a register message yet
clients = {}
socket_to_nick = {}
rooms = {"lobby": []}
room_history = {"lobby": []}
buffers = {}
unregistered = {}


# Helper Function: remove_client
# removes a client from all data structures and closes their socket
# handles both graceful disconnects and unexpected crashes
def remove_client(sock, sockets_list):
    nickname = socket_to_nick.get(sock)

    # make sure we actually have a nickname for this socket
    if nickname is not None:
        # make sure this nickname is still being tracked
        if nickname in clients:
            room = clients[nickname]["room"]

            # take them out of their room's member list
            if room in rooms:
                if nickname in rooms[room]:
                    rooms[room].remove(nickname)

                # Bonus feature: clean up empty rooms to keep the server tidy
                # lobby always stays, but other rooms get deleted when nobody is in them
                if room != "lobby":
                    if len(rooms[room]) == 0:
                        del rooms[room]
                        del room_history[room]

            # let everyone else in the room know this user left
            system_msg = {
                "type": "system",
                "message": nickname + " has left the room",
                "timestamp": get_timestamp()
            }
            if room in rooms:
                for user in rooms[room]:
                    # verify the user is still connected before sending
                    if user in clients:
                        if clients[user]["socket"] is not None:
                            send_msg(clients[user]["socket"], system_msg)

            # delete from both tracking dictionaries
            del clients[nickname]
            del socket_to_nick[sock]

            # print the disconnect log entry
            timestamp = get_timestamp()
            print(f"{timestamp} :: {nickname}: disconnected.")

    # if they never finished registering, clean that up too
    if sock in unregistered:
        del unregistered[sock]

    # remove their read buffer
    if sock in buffers:
        del buffers[sock]

    # take them off the select() watch list
    if sock in sockets_list:
        sockets_list.remove(sock)

    # close the socket
    sock.close()


# Helper Function: handle_message
# takes a fully parsed message from a client and decides what to do with it
# handles registration, ping, disconnect, and text (chat or commands)
def handle_message(sock, msg, sockets_list):
    msg_type = msg.get("type")
    nickname = socket_to_nick.get(sock)

    # -- REGISTRATION --
    # the very first message from a new connection must be type "register"
    if sock in unregistered:
        connecting_client_addr = unregistered[sock]

        # if their first message isn't a register, send an error and drop them
        if msg_type != "register":
            send_msg(sock, {
                "type": "error",
                "message": "first message must be register",
                "timestamp": get_timestamp()
            })
            remove_client(sock, sockets_list)
            return

        # grab the nickname and client ID they want to use
        nickname = msg.get("nickname", "")
        client_id = msg.get("clientID", "")

        # every connected client must have a unique nickname
        if nickname in clients:
            send_msg(sock, {
                "type": "error",
                "message": "nickname already in use",
                "timestamp": get_timestamp()
            })
            remove_client(sock, sockets_list)
            return

        # save all the client info so we can find them later
        clients[nickname] = {
            "socket": sock,
            "address": connecting_client_addr,
            "clientID": client_id,
            "room": "lobby",
            "last_seen": datetime.now()
        }
        socket_to_nick[sock] = nickname
        rooms["lobby"].append(nickname)

        # now registered, remove from the unregistered list
        del unregistered[sock]

        # logs that this client connected successfully
        timestamp = get_timestamp()
        print(f"{timestamp} :: {nickname}: connected. (ClientID={client_id})")

        # send back an "ok" so the client knows registration worked
        send_msg(sock, {
            "type": "ok",
            "message": "registered",
            "room": "lobby",
            "timestamp": get_timestamp()
        })

        # logs that they joined the lobby
        timestamp = get_timestamp()
        print(f"{timestamp} :: {nickname}: joined room lobby.")

        # tells everyone else in lobby that a new user showed up
        system_msg = {
            "type": "system",
            "message": nickname + " has joined lobby",
            "timestamp": get_timestamp()
        }
        for user in rooms["lobby"]:
            if user != nickname:
                if user in clients:
                    if clients[user]["socket"] is not None:
                        send_msg(clients[user]["socket"], system_msg)

        return

    # -- Client is now registered --

    # every valid message resets the heartbeat timer for the client
    if nickname is not None:
        if nickname in clients:
            clients[nickname]["last_seen"] = datetime.now()

    # ping is just a heartbeat, last_seen was already updated above
    if msg_type == "ping":
        return

    # client wants to disconnect cleanly
    if msg_type == "disconnect":
        remove_client(sock, sockets_list)
        return

    # -- TEXT MESSAGE (either a chat message or a slash command) --
    if msg_type == "text":
        text = msg.get("text", "")
        room = msg.get("room", "lobby")
        client_id = msg.get("clientID", "")
        connecting_client_addr = clients[nickname]["address"]

        # log every text message the server receives
        timestamp = get_timestamp()
        ip = connecting_client_addr[0]
        port = connecting_client_addr[1]
        msg_size = len(json.dumps(msg))
        print(f"Received: IP:{ip}, Port:{port}, Client-Nickname:{nickname}, "
              f"ClientID:{client_id}, Room:{room}, Date/Time:{timestamp}, Msg-Size:{msg_size}")

        # if the text starts with / it's a command, otherwise it's chat
        if text.startswith("/"):
            command_line_handler(sock, nickname, client_id, room, text, sockets_list)
        else:
            # regular chat message, send it to everyone in the room except the sender
            deliver_msg = {
                "type": "deliver",
                "room": room,
                "from": nickname,
                "text": text,
                "timestamp": get_timestamp()
            }
            recipients = []
            if room in rooms:
                for user in rooms[room]:
                    if user != nickname:
                        # make sure the user is still connected before sending
                        if user in clients:
                            if clients[user]["socket"] is not None:
                                send_msg(clients[user]["socket"], deliver_msg)
                                recipients.append(user)

            # store this message in room history (keep last 20)
            if room in room_history:
                room_history[room].append({
                    "from": nickname,
                    "text": text,
                    "timestamp": get_timestamp()
                })
                # if history is longer than 20, remove the oldest entry
                if len(room_history[room]) > 20:
                    room_history[room].pop(0)

            # log who received the message
            if len(recipients) > 0:
                print(f"Delivered(Room={room}): {', '.join(recipients)}")
            else:
                print(f"Delivered(Room={room}): (none)")


# Helper Function: command_line_handler
# processes a slash command from a client
# splits the text to figure out which command it is and calls the right logic
# commands: /join, /leave, /rooms, /who, /msg, /nick, /disconnect
def command_line_handler(sock, nickname, client_id, current_room, text, sockets_list):

    # split the command text into parts so we can narrow in on command
    parts = text.split(" ", 2)
    command = parts[0].lower()

    # -- /join <room> --
    # move the user from their current room to the specified room
    # if the room doesn't exist, create it
    # notify users in both the old and new rooms
    # send the new room's history to the joining client
    if command == "/join":
        if len(parts) < 2:
            send_msg(sock, {
                "type": "error",
                "message": "usage: /join <room>",
                "timestamp": get_timestamp()
            })
            return

        new_room = parts[1].strip()

        # validate room name: 1-20 chars, letters/digits/underscore/hyphen only
        valid_room = True
        if len(new_room) < 1 or len(new_room) > 20:
            valid_room = False
        else:
            for char in new_room:
                if char.isalnum():
                    continue
                elif char == '_' or char == '-':
                    continue
                else:
                    valid_room = False
                    break

        if valid_room == False:
            send_msg(sock, {
                "type": "error",
                "message": "invalid room name",
                "timestamp": get_timestamp()
            })
            return

        old_room = clients[nickname]["room"]

        # if they're already in that room, just tell them
        if old_room == new_room:
            send_msg(sock, {
                "type": "system",
                "message": "you are already in room " + new_room,
                "timestamp": get_timestamp()
            })
            return

        # remove from old room
        if old_room in rooms:
            if nickname in rooms[old_room]:
                rooms[old_room].remove(nickname)

        # notify old room that user left
        leave_msg = {
            "type": "system",
            "message": nickname + " has left the room",
            "timestamp": get_timestamp()
        }
        if old_room in rooms:
            for user in rooms[old_room]:
                if user in clients:
                    if clients[user]["socket"] is not None:
                        send_msg(clients[user]["socket"], leave_msg)

        # Bonus feature: clean up empty rooms to keep the server tidy
        if old_room != "lobby":
            if old_room in rooms:
                if len(rooms[old_room]) == 0:
                    del rooms[old_room]
                    del room_history[old_room]

        # create new room if it doesn't exist
        if new_room not in rooms:
            rooms[new_room] = []
            room_history[new_room] = []

        # add user to new room
        rooms[new_room].append(nickname)
        clients[nickname]["room"] = new_room

        # log room change
        timestamp = get_timestamp()
        print(f"{timestamp} :: {nickname}: joined room {new_room}.")

        # notify new room that user joined
        join_msg = {
            "type": "system",
            "message": nickname + " has joined " + new_room,
            "timestamp": get_timestamp()
        }
        for user in rooms[new_room]:
            if user != nickname:
                if user in clients:
                    if clients[user]["socket"] is not None:
                        send_msg(clients[user]["socket"], join_msg)

        # send confirmation to the user
        send_msg(sock, {
            "type": "system",
            "message": "joined room " + new_room,
            "timestamp": get_timestamp()
        })

        # send room history to the joining client
        history = room_history.get(new_room, [])
        send_msg(sock, {
            "type": "history",
            "room": new_room,
            "messages": history,
            "timestamp": get_timestamp()
        })
        # log history delivery
        print(f"HistoryDelivered: Room:{new_room}, To:{nickname}, Count:{len(history)}")

    # -- /leave --
    # move the user back to the lobby
    elif command == "/leave":
        old_room = clients[nickname]["room"]

        # if already in lobby, just tell them
        if old_room == "lobby":
            send_msg(sock, {
                "type": "system",
                "message": "you are already in lobby",
                "timestamp": get_timestamp()
            })
            return

        # remove from old room
        if old_room in rooms:
            if nickname in rooms[old_room]:
                rooms[old_room].remove(nickname)

        # notify old room that user left
        leave_msg = {
            "type": "system",
            "message": nickname + " has left the room",
            "timestamp": get_timestamp()
        }
        if old_room in rooms:
            for user in rooms[old_room]:
                if user in clients:
                    if clients[user]["socket"] is not None:
                        send_msg(clients[user]["socket"], leave_msg)

        # Bonus feature: clean up empty rooms to keep the server tidy
        if old_room != "lobby":
            if old_room in rooms:
                if len(rooms[old_room]) == 0:
                    del rooms[old_room]
                    del room_history[old_room]

        # add to lobby
        rooms["lobby"].append(nickname)
        clients[nickname]["room"] = "lobby"

        # log room change
        timestamp = get_timestamp()
        print(f"{timestamp} :: {nickname}: joined room lobby.")

        # notify lobby that user joined
        join_msg = {
            "type": "system",
            "message": nickname + " has joined lobby",
            "timestamp": get_timestamp()
        }
        for user in rooms["lobby"]:
            if user != nickname:
                if user in clients:
                    if clients[user]["socket"] is not None:
                        send_msg(clients[user]["socket"], join_msg)

        # send confirmation and lobby history
        send_msg(sock, {
            "type": "system",
            "message": "joined room lobby",
            "timestamp": get_timestamp()
        })
        history = room_history.get("lobby", [])
        send_msg(sock, {
            "type": "history",
            "room": "lobby",
            "messages": history,
            "timestamp": get_timestamp()
        })
        print(f"HistoryDelivered: Room:lobby, To:{nickname}, Count:{len(history)}")

    # -- /rooms --
    # send a list of all active rooms (rooms with at least one client)
    elif command == "/rooms":
        active_rooms = []
        for room_name in rooms:
            if len(rooms[room_name]) > 0:
                active_rooms.append(room_name)
        room_list = ", ".join(active_rooms)
        send_msg(sock, {
            "type": "system",
            "message": "active rooms: " + room_list,
            "timestamp": get_timestamp()
        })

    # -- /who <room> --
    # send a list of nicknames in the specified room
    elif command == "/who":
        if len(parts) < 2:
            send_msg(sock, {
                "type": "error",
                "message": "usage: /who <room>",
                "timestamp": get_timestamp()
            })
            return

        target_room = parts[1].strip()
        if target_room in rooms:
            user_list = ", ".join(rooms[target_room])
            send_msg(sock, {
                "type": "system",
                "message": "users in " + target_room + ": " + user_list,
                "timestamp": get_timestamp()
            })
        else:
            send_msg(sock, {
                "type": "error",
                "message": "room " + target_room + " does not exist",
                "timestamp": get_timestamp()
            })

    # -- /msg <nickname> <text> --
    # send a private message to a specific user, works across all rooms
    elif command == "/msg":
        if len(parts) < 3:
            send_msg(sock, {
                "type": "error",
                "message": "usage: /msg <nickname> <text>",
                "timestamp": get_timestamp()
            })
            return

        # parts[1] is the target nickname, parts[2] is the message text
        # we already checked len(parts) < 3 above, so parts[2] should always exist
        # the else is just a backup in case it somehow doesn't
        target_nick = parts[1]
        if len(parts) >= 3:
            pm_text = parts[2]
        else:
            pm_text = ""

        # check if the target user exists
        if target_nick in clients:
            target_sock = clients[target_nick]["socket"]
            if target_sock is not None:
                # send the private message to the target
                send_msg(target_sock, {
                    "type": "pm",
                    "from": nickname,
                    "text": pm_text,
                    "timestamp": get_timestamp()
                })
                # log private message delivery
                timestamp = get_timestamp()
                msg_size = len(pm_text)
                print(f"PrivateDelivered: From:{nickname}, To:{target_nick}, "
                      f"Date/Time:{timestamp}, Msg-Size:{msg_size}")
                # confirm to the sender
                send_msg(sock, {
                    "type": "system",
                    "message": "PM sent to " + target_nick,
                    "timestamp": get_timestamp()
                })
            else:
                send_msg(sock, {
                    "type": "error",
                    "message": "user " + target_nick + " is not available",
                    "timestamp": get_timestamp()
                })
        else:
            send_msg(sock, {
                "type": "error",
                "message": "user " + target_nick + " not found",
                "timestamp": get_timestamp()
            })

    # -- /nick <newnick> --
    # change the user's nickname if the new one is not already taken
    elif command == "/nick":
        if len(parts) < 2:
            send_msg(sock, {
                "type": "error",
                "message": "usage: /nick <newnick>",
                "timestamp": get_timestamp()
            })
            return

        new_nick = parts[1].strip()

        # check if the new nickname is already in use
        if new_nick in clients:
            send_msg(sock, {
                "type": "error",
                "message": "nickname " + new_nick + " is already in use",
                "timestamp": get_timestamp()
            })
            return

        old_nick = nickname
        current_room = clients[nickname]["room"]

        # update the clients dict with the new nickname
        clients[new_nick] = clients[old_nick]
        del clients[old_nick]

        # update the socket-to-nickname mapping
        socket_to_nick[sock] = new_nick

        # update the room member list
        if current_room in rooms:
            if old_nick in rooms[current_room]:
                rooms[current_room].remove(old_nick)
                rooms[current_room].append(new_nick)

        # log the nickname change
        timestamp = get_timestamp()
        print(f"{timestamp} :: {old_nick}: changed nickname to {new_nick}.")

        # notify the user
        send_msg(sock, {
            "type": "system",
            "message": "nickname changed to " + new_nick,
            "timestamp": get_timestamp()
        })

        # notify everyone in the room about the name change
        rename_msg = {
            "type": "system",
            "message": old_nick + " is now known as " + new_nick,
            "timestamp": get_timestamp()
        }
        if current_room in rooms:
            for user in rooms[current_room]:
                if user != new_nick:
                    if user in clients:
                        if clients[user]["socket"] is not None:
                            send_msg(clients[user]["socket"], rename_msg)

    # -- /disconnect --
    # graceful disconnect, same as sending a disconnect message type
    elif command == "/disconnect":
        remove_client(sock, sockets_list)

    # -- unknown command --
    else:
        send_msg(sock, {
            "type": "error",
            "message": "unknown command: " + command,
            "timestamp": get_timestamp()
        })


# Helper Function: check_heartbeat_timeouts
# checks every connected client's last_seen time
# if anyone has been silent for more than 30 seconds, disconnect them
# we collect the timed-out names into a list first, then remove them after,
# because you can't delete from a dict while you're looping over it
def check_heartbeat_timeouts(sockets_list):
    now = datetime.now()

    # find all clients who have been silent too long
    timed_out_nicks = []
    for nickname in clients:
        last_seen_stamp = clients[nickname]["last_seen"]
        time_passed = (now - last_seen_stamp).total_seconds()
        if time_passed > 30:
            timed_out_nicks.append(nickname)

    # now disconnect each one
    for nickname in timed_out_nicks:
        sock = clients[nickname]["socket"]
        remove_client(sock, sockets_list)


# sets up the server socket and runs the main loop
def main():
    # server expects exactly one argument: the port number
    if len(sys.argv) != 2:
        print("ERR - arg 1")
        sys.exit(1)

    # make sure the argument is actually a number
    port_str = sys.argv[1]
    if port_str.isdigit():
        port = int(port_str)
    else:
        print("ERR - arg 1")
        sys.exit(1)

    # port must be in a valid range
    if port <= 0 or port >= 65536:
        print("ERR - arg 1")
        sys.exit(1)

    # create the welcoming TCP socket, bind it to the port, and start listening
    try:
        server_socket = socket(AF_INET, SOCK_STREAM)
        server_socket.bind(('', port))
        server_socket.listen(5)
    except Exception:
        print(f"ERR - cannot create ChatServer socket using port number {port}")
        sys.exit(1)

    # print the startup message with the server's IP, port, and current time
    server_ip = gethostbyname(gethostname())
    timestamp = get_timestamp()
    print(f"ChatServer started with server IP: {server_ip}, port: {port}, Date/Time: {timestamp}")

    # -- SELECT LOOP --
    # select() watches a list of sockets and returns which ones have data ready to read
    # this lets us handle multiple clients in a single loop without needing threads
    # it takes 3 lists (read, write, error) and a timeout in seconds
    # it returns 3 lists in the same order, we only care about the first one (readable)
    # the 1-second timeout makes sure we check heartbeat timeouts regularly
    sockets_list = [server_socket]

    server_running = True
    try:
        while server_running:
            # wait up to 1 second for any socket to become readable
            readable_sockets, _, _ = select.select(sockets_list, [], [], 1.0)

            for sock in readable_sockets:

                # if the server socket is readable, a new client is trying to connect
                if sock is server_socket:
                    conn_socket, connecting_client_addr = server_socket.accept()

                    # add the new client socket to the watch list so select() monitors it
                    sockets_list.append(conn_socket)

                    # give this socket an empty byte buffer for accumulating partial reads
                    buffers[conn_socket] = b''

                    # mark them as unregistered until they send a register message
                    unregistered[conn_socket] = connecting_client_addr

                # if a client socket is readable, they sent us some data
                else:
                    # recv() can fail if the client drops unexpectedly
                    try:
                        data = sock.recv(4096)
                    except Exception:
                        remove_client(sock, sockets_list)
                        continue

                    # if recv() returns empty bytes, the client closed their connection
                    if data == b'':
                        remove_client(sock, sockets_list)
                        continue

                    # append the received bytes to this socket's read buffer
                    buffers[sock] = buffers[sock] + data

                    # check the buffer for complete framed messages
                    # there could be more than one if data arrived quickly
                    parsing_messages = True
                    while parsing_messages:
                        result, buffers[sock] = try_parse_message(buffers[sock])

                        if result is None:
                            # buffer doesn't have a complete message yet, wait for more
                            parsing_messages = False

                        elif result == "INVALID":
                            # the frame length was bad, close this connection
                            remove_client(sock, sockets_list)
                            parsing_messages = False

                        else:
                            # we got a complete valid message, handle it
                            handle_message(sock, result, sockets_list)

                            # the socket might have been removed during handling
                            # for example if the message was a disconnect
                            if sock not in buffers:
                                parsing_messages = False

            # after processing all readable sockets, check for silent clients
            check_heartbeat_timeouts(sockets_list)

    except KeyboardInterrupt:
        print("\nServer shutting down.")
        server_socket.close()


if __name__ == "__main__":
    main()
