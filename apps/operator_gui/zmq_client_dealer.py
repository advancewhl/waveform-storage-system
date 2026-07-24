import socket

import zmq
import time
def send_start(server_host="127.0.0.1", server_port=5000, message=""):
    context = zmq.Context()
    socket = context.socket(zmq.DEALER)
    local_ip = get_local_ip()
    # message = f"{local_ip}"
    socket.setsockopt(zmq.IDENTITY, local_ip.encode('utf-8'))
    socket.connect(f"tcp://{server_host}:{server_port}")

    #set timeout=3000ms
    socket.setsockopt(zmq.RCVTIMEO, 3000)
    time.sleep(0.1)
    try:
        message = message
        socket.send_string(message)
        response = socket.recv()
        response_str = str(response, 'utf-8')
        #print("response from server:",str(response, 'utf-8'))
        return response_str
    except Exception as e:
        return False
    finally:
        socket.close()
        context.term()

def send_start_waiting(server_host="127.0.0.1", server_port=5000, message=""):
    context = zmq.Context()
    socket = context.socket(zmq.DEALER)
    local_ip = get_local_ip()
    # message = f"{local_ip}"
    socket.setsockopt(zmq.IDENTITY, local_ip.encode('utf-8'))
    socket.connect(f"tcp://{server_host}:{server_port}")
    time.sleep(0.1)
    while True:
        try:
            response = socket.recv()
            response_str = str(response, 'utf-8')
            #print("response from server:",str(response, 'utf-8'))
            return response_str
        except Exception as e:
            return False
        finally:
            socket.close()
            context.term()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.connect(('8.8.8.8',80))
        loacl_ip = s.getsockname()[0]
        s.close()
        print(loacl_ip)
        return loacl_ip
    except:
        return '127.0.0.1'
