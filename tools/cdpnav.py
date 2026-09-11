import json, socket, os, struct, sys, urllib.request, base64
url = sys.argv[1]
targets = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
pages = [t for t in targets if t['type'] == 'page']
print("pages:", [(t['title'], t['url']) for t in pages], flush=True)
if not pages: sys.exit(1)
ws = pages[0]['webSocketDebuggerUrl']  # ws://127.0.0.1:9222/devtools/page/ID
host, path = ws[5:].split('/', 1)
h, p = host.split(':')
s = socket.create_connection((h, int(p)), timeout=10)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(f"GET /{path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
resp = b""
while b"\r\n\r\n" not in resp: resp += s.recv(4096)
print("handshake:", resp.split(b"\r\n")[0], flush=True)
def send(obj):
    data = json.dumps(obj).encode(); mask = os.urandom(4)
    hdr = bytes([0x81]) + (bytes([0x80 | len(data)]) if len(data) < 126 else bytes([0x80 | 126]) + struct.pack(">H", len(data)))
    s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
def recv():
    hdr = s.recv(2); ln = hdr[1] & 0x7f
    if ln == 126: ln = struct.unpack(">H", s.recv(2))[0]
    elif ln == 127: ln = struct.unpack(">Q", s.recv(8))[0]
    data = b""
    while len(data) < ln: data += s.recv(ln - len(data))
    return data
send({"id": 1, "method": "Page.navigate", "params": {"url": url}})
for _ in range(5):
    r = recv(); print("reply:", r[:200], flush=True)
    if b'"id":1' in r: break
s.close()
