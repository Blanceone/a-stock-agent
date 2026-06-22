import paramiko
import time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('8.137.174.58', username='root', password='lxh107016!')

def run(cmd, timeout=60):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out: print(f"OUT: {out[:300]}")
    if err: print(f"ERR: {err[:300]}")
    return out, err

# Remove old container if exists
run('docker rm -f astock_chromadb 2>/dev/null')

# Start ChromaDB with 0.5.15
print("Starting ChromaDB 0.5.15...")
run('docker run -d --name astock_chromadb -p 8000:8000 -v /opt/astock/data/chromadb:/chroma/chroma -e IS_PERSISTENT=TRUE --restart unless-stopped chromadb/chroma:0.5.15')

time.sleep(5)

# Verify
print("\nVerify:")
run('curl -s http://localhost:8000/api/v1/heartbeat')
run('docker ps --format "table {{.Names}}\t{{.Status}}"')

ssh.close()
print("\nDone!")
