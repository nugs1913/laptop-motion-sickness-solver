# test_receiver.py
import socket
import json

PORT = 8989
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", PORT))

print(f"📡 데이터 수신 대기 중... Port: {PORT}")

while True:
    data, addr = sock.recvfrom(1024)
    try:
        text = data.decode('utf-8')
        jd = json.loads(text)
        
        # 여기서 ax, ay가 들어오는지 눈으로 확인
        print(f"받은 데이터: {jd}") 
        
    except Exception as e:
        print(e)