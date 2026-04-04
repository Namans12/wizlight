#!/usr/bin/env python3
"""Network diagnostic and bulb finder for WizLight."""

import socket
import json
import sys
import concurrent.futures
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_local_ip():
    """Get local IP address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    finally:
        s.close()


def get_broadcast_address(ip: str) -> str:
    """Get broadcast address from IP (assumes /24 subnet)."""
    parts = ip.split('.')
    parts[3] = '255'
    return '.'.join(parts)


def probe_ip_sync(ip: str, timeout: float = 1.0) -> dict | None:
    """Probe a single IP for WiZ bulb (synchronous)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        
        message = json.dumps({"method": "getPilot"}).encode()
        sock.sendto(message, (ip, 38899))
        
        try:
            data, addr = sock.recvfrom(1024)
            response = json.loads(data.decode())
            if 'result' in response or 'method' in response:
                return {'ip': ip, 'response': response}
        except socket.timeout:
            pass
        finally:
            sock.close()
    except Exception:
        pass
    return None


def check_configured_bulbs(config) -> tuple[list[dict], list]:
    """Probe bulbs already stored in config."""
    reachable = []
    unreachable = []

    if not config.bulbs:
        return reachable, unreachable

    print("Checking configured bulb IPs...")
    for bulb in config.bulbs:
        result = probe_ip_sync(bulb.ip, timeout=2.0)
        if result:
            print(f"  Reachable: {bulb.name} ({bulb.ip})")
            reachable.append(result)
        else:
            print(f"  Unreachable: {bulb.name} ({bulb.ip})")
            unreachable.append(bulb)
    print()

    return reachable, unreachable


def dedupe_bulbs(bulbs: list[dict]) -> list[dict]:
    """Deduplicate bulbs by IP while preserving order."""
    unique = {}
    for bulb in bulbs:
        unique.setdefault(bulb["ip"], bulb)
    return list(unique.values())


def scan_subnet_parallel(base_ip: str, start: int = 1, end: int = 254):
    """Scan subnet for WiZ bulbs using thread pool."""
    parts = base_ip.split('.')
    prefix = '.'.join(parts[:3])
    
    print(f"Scanning {prefix}.{start}-{end} for WiZ bulbs...")
    
    ips = [f"{prefix}.{i}" for i in range(start, end + 1)]
    
    bulbs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(probe_ip_sync, ip, 1.5): ip for ip in ips}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                print(f"  Found: {result['ip']}")
                bulbs.append(result)
    
    return bulbs


def broadcast_discover_sync(broadcast: str, timeout: float = 3.0):
    """Try broadcast discovery (synchronous)."""
    print(f"Trying broadcast discovery on {broadcast}...")
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.5)
        
        local_ip = get_local_ip()
        message = json.dumps({
            "method": "registration",
            "params": {
                "phoneMac": "AABBCCDDEEFF",
                "register": False,
                "phoneIp": local_ip
            }
        }).encode()
        
        sock.sendto(message, (broadcast, 38899))
        
        bulbs = []
        import time
        end_time = time.time() + timeout
        
        while time.time() < end_time:
            try:
                data, addr = sock.recvfrom(1024)
                response = json.loads(data.decode())
                if addr[0] != local_ip:  # Don't add self
                    bulbs.append({'ip': addr[0], 'response': response})
                    print(f"  Found: {addr[0]}")
            except socket.timeout:
                continue
            except Exception:
                break
        
        sock.close()
        return bulbs
    except Exception as e:
        print(f"  Broadcast error: {e}")
        return []


def main():
    print("=" * 50)
    print("WizLight Network Diagnostic")
    print("=" * 50)
    
    # Get network info
    local_ip = get_local_ip()
    broadcast = get_broadcast_address(local_ip)
    
    print(f"\nYour PC IP: {local_ip}")
    print(f"Broadcast address: {broadcast}")
    print()

    from src.core.config import Config

    config = Config.load()
    configured_reachable, configured_unreachable = check_configured_bulbs(config)
    
    # Try broadcast discovery first
    bulbs = broadcast_discover_sync(broadcast)
    
    if not bulbs and not configured_reachable:
        # Try subnet scan
        print("\nBroadcast didn't find bulbs. Scanning individual IPs...")
        print("(This may take 20-30 seconds)\n")
        bulbs = scan_subnet_parallel(local_ip)
    else:
        bulbs = dedupe_bulbs(configured_reachable + bulbs)

    if bulbs:
        print(f"\nFound {len(bulbs)} WiZ bulb(s).")
        for bulb in bulbs:
            print(f"   IP: {bulb['ip']}")
        
        # Save to config
        print("\nSaving to config...")
        for i, bulb in enumerate(bulbs, 1):
            config.add_bulb(bulb['ip'], f"Bulb {i}")
        print(f"Saved to: {config._config_path}")
    else:
        print("\nNo WiZ bulbs found on the network.")
        if configured_unreachable:
            print("\nConfigured bulbs that did not respond:")
            for bulb in configured_unreachable:
                print(f"  - {bulb.name} ({bulb.ip})")
            print("These IPs are likely stale or the bulbs are currently unreachable.")
        print("\nTroubleshooting:")
        print("1. Make sure the bulb is powered ON")
        print("2. Check bulb has solid BLUE light (connected to WiFi)")
        print("3. WiZ bulbs ONLY work on 2.4GHz WiFi")
        print(f"   - Your PC is on: {local_ip}")
        print("   - If your PC is on 5GHz, the bulb might be unreachable")
        print("4. Try connecting your PC to the 2.4GHz network (Excitel_Naman-2.4G)")
        print("5. Open the WiZ app on your phone, open bulb Settings, and check the bulb IP address")
        print("   Then run: wizlight add-bulb <IP>")


if __name__ == "__main__":
    main()
