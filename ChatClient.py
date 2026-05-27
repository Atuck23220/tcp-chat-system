# ChatClient.py
# CMSC 440 - Room-Based Chat Client
# Programming Assignment
# Author - Aaron Tuck
# Connects to ChatServer, registers, and allows the user to send
# chat messages and commands while receiving messages from the server

from socket import *
import sys
import json
import struct
import select
import time
from datetime import datetime


# Helper Function: get_timestamp
# returns a formatted date/time string used in client logs and JSON messages
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


# Helper Function: display_message
# takes a message dict from the server and prints it in the correct format
# display formats from the Requirements:
#   delivered chat:   <date/time> :: [<room>] <SenderNick>: <message text>
#   private message:  <date/time> :: [PM from <SenderNick>] <message text>
#   system message:   <date/time> :: * <system message>
def display_message(msg):
    msg_type = msg.get("type")
    timestamp = get_timestamp()

    if msg_type == "deliver":
        room = msg.get("room", "")
        sender = msg.get("from", "")
        text = msg.get("text", "")
        print(f"{timestamp} :: [{room}] {sender}: {text}")

    elif msg_type == "pm":
        sender = msg.get("from", "")
        text = msg.get("text", "")
        print(f"{timestamp} :: [PM from {sender}] {text}")

    elif msg_type == "system":
        message = msg.get("message", "")
        print(f"{timestamp} :: * {message}")

    elif msg_type == "ok":
        # registration was accepted, server put us in a room
        room = msg.get("room", "lobby")
        print(f"{timestamp} :: * joined room {room}")

    elif msg_type == "error":
        message = msg.get("message", "")
        print(f"{timestamp} :: * ERROR: {message}")

    elif msg_type == "history":
        # display the last messages from a room we just joined
        room = msg.get("room", "")
        messages = msg.get("messages", [])
        print(f"{timestamp} :: * Room history for {room}:")
        for entry in messages:
            sender = entry.get("from", "")
            text = entry.get("text", "")
            entry_time = entry.get("timestamp", "")
            print(f"  {entry_time} :: [{room}] {sender}: {text}")


# Helper Function: print_session_summary
# prints the session statistics when the client exits
# includes start/end time, current room, and counts of messages sent/received
def print_session_summary(start_time, current_room, stat_details):
    end_time = get_timestamp()
    print(f"Summary: start:{start_time}, end:{end_time}, room:{current_room}, "
          f"rooms joined:{stat_details['rooms_joined']}, chat sent:{stat_details['chat_sent']}, "
          f"chat rcv:{stat_details['chat_rcv']}, pm sent:{stat_details['pm_sent']}, "
          f"pm rcv:{stat_details['pm_rcv']}, char sent:{stat_details['char_sent']}, "
          f"char rcv:{stat_details['char_rcv']}")


# Helper Function: send_heartbeat_ping
# sends a ping message to the server if 10 seconds have passed since the last one
# the server expects to hear from us at least every 30 seconds or it drops us
# returns the updated last_ping_time so the caller can track it
def send_heartbeat_ping(client_socket, nickname, client_id, last_ping_time):
    current_time = time.time()
    time_since_last_ping = current_time - last_ping_time
    if time_since_last_ping >= 10:
        send_msg(client_socket, {
            "type": "ping",
            "nickname": nickname,
            "clientID": client_id,
            "timestamp": get_timestamp()
        })
        return current_time
    else:
        return last_ping_time


# sets up the connection and runs the main client loop
def main():
    # client expects exactly four arguments: hostname, port, nickname, clientID
    if len(sys.argv) != 5:
        print("ERR - arg 1")
        sys.exit(1)

    # argument 1: hostname or IP of the server
    # gethostbyname resolves a hostname to an IP, or passes an IP through unchanged
    # this can fail if the hostname doesn't exist
    host_arg = sys.argv[1]
    try:
        server_ip = gethostbyname(host_arg)
    except Exception:
        print("ERR - arg 1")
        sys.exit(1)

    # argument 2: port number the server is running on
    port_str = sys.argv[2]
    if port_str.isdigit():
        port = int(port_str)
    else:
        print("ERR - arg 2")
        sys.exit(1)
    if port <= 0 or port >= 65536:
        print("ERR - arg 2")
        sys.exit(1)

    # argument 3: nickname for this client session
    nickname = sys.argv[3]

    # argument 4: unique client ID for this session
    client_id = sys.argv[4]

    # create a TCP socket and connect to the server
    # connect() can fail if the server isn't running
    try:
        client_socket = socket(AF_INET, SOCK_STREAM)
        client_socket.connect((server_ip, port))
    except Exception:
        print(f"ERR - cannot connect to server at {server_ip}:{port}")
        sys.exit(1)

    # record the start time for the session summary later
    start_time = get_timestamp()

    # print the startup message
    print(f"ChatClient started with server IP: {server_ip}, port: {port}, "
          f"nickname: {nickname}, client ID: {client_id}, Date/Time: {start_time}")

    # send the registration message to the server
    # this must be the very first message after connecting
    send_msg(client_socket, {
        "type": "register",
        "nickname": nickname,
        "clientID": client_id,
        "timestamp": get_timestamp()
    })

    # track which room we are currently in (starts as lobby after registration)
    current_room = "lobby"

    # session statistics for the summary printout at the end
    stat_details = {
        "rooms_joined": 1,
        "chat_sent": 0,
        "chat_rcv": 0,
        "pm_sent": 0,
        "pm_rcv": 0,
        "char_sent": 0,
        "char_rcv": 0
    }

    # read buffer for incoming data from the server
    read_buffer = b''

    # track when we last sent a ping so we can send one every 10 seconds
    last_ping_time = time.time()

    # tracks whether the server has accepted our registration
    # if an error comes before registration is accepted, we need to exit
    # if an error comes after, it's just a command error and we keep going
    registered = False

    # -- MAIN CLIENT LOOP --
    # select() watches a list of sockets and returns which ones have data ready to read
    # this lets us handle the server socket and keyboard input in a single loop
    # it takes 3 lists (read, write, error) and a timeout in seconds
    # it returns 3 lists in the same order, we only care about the first one (readable)
    # the 1-second timeout makes sure we check heartbeat timing regularly
    # wrapping the main loop in try so we can catch Ctrl-C and exit cleanly

    client_running = True
    try:
        while client_running:

            # wait up to 1 second for the server socket or keyboard to have data
            readable_sockets, _, _ = select.select([client_socket, sys.stdin], [], [], 1.0)

            for sock in readable_sockets:

                # -- DATA FROM SERVER --
                if sock is client_socket:
                    # recv() can fail if the server drops
                    try:
                        data = sock.recv(4096)
                    except Exception:
                        print("Connection to server lost.")
                        client_running = False
                        break

                    # empty bytes means the server closed the connection
                    if data == b'':
                        print("Server closed the connection.")
                        client_running = False
                        break

                    # add received bytes to the read buffer
                    read_buffer = read_buffer + data

                    # pull out complete messages from the buffer
                    # there could be more than one if data arrived quickly
                    parsing_messages = True
                    while parsing_messages:
                        result, read_buffer = try_parse_message(read_buffer)

                        if result is None:
                            # buffer doesn't have a complete message yet, wait for more
                            parsing_messages = False

                        elif result == "INVALID":
                            # bad frame from server, disconnect
                            print("Invalid message from server.")
                            client_running = False
                            parsing_messages = False

                        else:
                            # got a valid message from the server
                            msg_type = result.get("type")

                            # update session stat_details based on what type of message we received
                            if msg_type == "deliver":
                                stat_details["chat_rcv"] = stat_details["chat_rcv"] + 1
                                stat_details["char_rcv"] = stat_details["char_rcv"] + len(result.get("text", ""))

                            elif msg_type == "pm":
                                stat_details["pm_rcv"] = stat_details["pm_rcv"] + 1
                                stat_details["char_rcv"] = stat_details["char_rcv"] + len(result.get("text", ""))

                            elif msg_type == "ok":
                                # registration accepted, update our current room
                                current_room = result.get("room", "lobby")
                                registered = True

                            elif msg_type == "error":
                                # if we haven't received an "ok" yet, this is a registration error
                                # and we need to exit because the server rejected us
                                # if we already registered, it's just a command error so keep going
                                if registered == False:
                                    display_message(result)
                                    client_running = False
                                    parsing_messages = False
                                    break

                            elif msg_type == "system":
                                # check if the system message is about us joining a room
                                # the server sends "joined room <name>" when we successfully move
                                sys_msg = result.get("message", "")
                                if sys_msg.startswith("joined room "):
                                    # extract the room name from the message
                                    new_room_name = sys_msg[len("joined room "):]
                                    current_room = new_room_name
                                    stat_details["rooms_joined"] = stat_details["rooms_joined"] + 1

                                # check if the system message is about our nickname changing
                                # the server sends "nickname changed to <name>" after a /nick command
                                if sys_msg.startswith("nickname changed to "):
                                    # extract the new nickname from the message
                                    new_nickname = sys_msg[len("nickname changed to "):]
                                    nickname = new_nickname

                            # print the message to the screen
                            display_message(result)

                # -- INPUT FROM KEYBOARD --
                if sock is sys.stdin:
                    user_input = sys.stdin.readline().strip()

                    # if the user didn't type anything, skip it
                    if user_input == "":
                        continue

                    # check if the user typed /disconnect
                    if user_input == "/disconnect":
                        # send a disconnect message to the server and stop the loop
                        send_msg(client_socket, {
                            "type": "disconnect",
                            "nickname": nickname,
                            "clientID": client_id,
                            "timestamp": get_timestamp()
                        })
                        client_running = False
                        break

                    # wrap whatever the user typed into a text message dict and send it
                    # the server decides if it's a command or chat by checking for /
                    send_msg(client_socket, {
                        "type": "text",
                        "room": current_room,
                        "nickname": nickname,
                        "clientID": client_id,
                        "text": user_input,
                        "timestamp": get_timestamp()
                    })

                    # update session stat_details depending on what kind of input it was
                    if user_input.startswith("/msg "):

                        # private message, count it separately
                        stat_details["pm_sent"] = stat_details["pm_sent"] + 1

                        # the actual message text is everything after "/msg nickname "
                        parts = user_input.split(" ", 2)
                        if len(parts) >= 3:
                            stat_details["char_sent"] = stat_details["char_sent"] + len(parts[2])
                    elif user_input.startswith("/"):

                        # other slash commands don't count toward chat stat_details
                        pass
                    else:
                        # regular chat message
                        stat_details["chat_sent"] = stat_details["chat_sent"] + 1
                        stat_details["char_sent"] = stat_details["char_sent"] + len(user_input)

            # after processing readable sockets, send a heartbeat ping if needed
            if client_running:
                last_ping_time = send_heartbeat_ping(client_socket, nickname, client_id, last_ping_time)

    # handle Ctrl-C so the client exits cleanly instead of crashing with a traceback
    # tries to send a disconnect message to the server so it knows we're leaving
    except KeyboardInterrupt:
        print("")
        send_msg(client_socket, {
            "type": "disconnect",
            "nickname": nickname,
            "clientID": client_id,
            "timestamp": get_timestamp()
        })

    # -- SESSION ENDED --
    # print the session summary and close the socket
    print_session_summary(start_time, current_room, stat_details)
    client_socket.close()


if __name__ == "__main__":
    main()
