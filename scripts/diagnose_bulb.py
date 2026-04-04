"""Diagnostic tool for WiZ bulb connectivity issues."""

import asyncio
import socket
import sys

from pywizlight import wizlight
from pywizlight.discovery import discover_lights


async def diagnose_bulb(ip: str) -> dict:
    """Run comprehensive diagnostics on a WiZ bulb."""
    results = {
        "ip": ip,
        "ping": False,
        "udp_port": False,
        "wiz_response": False,
        "state": None,
        "errors": [],
    }
    
    # 1. Basic ping test
    print(f"[1/4] Testing network reachability to {ip}...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.sendto(b"test", (ip, 38899))
        results["ping"] = True
        print("      ✓ Network path OK")
    except Exception as e:
        results["errors"].append(f"Network: {e}")
        print(f"      ✗ Network error: {e}")
    finally:
        sock.close()
    
    # 2. UDP port test
    print(f"[2/4] Testing UDP port 38899...")
    try:
        # Send WiZ getPilot request
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.sendto(b'{"method":"getPilot","params":{}}', (ip, 38899))
        data, _ = sock.recvfrom(1024)
        results["udp_port"] = True
        print(f"      ✓ UDP response received ({len(data)} bytes)")
    except socket.timeout:
        results["errors"].append("UDP timeout - bulb not responding")
        print("      ✗ UDP timeout - no response from bulb")
    except Exception as e:
        results["errors"].append(f"UDP: {e}")
        print(f"      ✗ UDP error: {e}")
    finally:
        sock.close()
    
    # 3. pywizlight connection test
    print(f"[3/4] Testing pywizlight connection...")
    try:
        bulb = wizlight(ip)
        state = await bulb.updateState()
        results["wiz_response"] = True
        results["state"] = {
            "on": state.get_state(),
            "brightness": state.get_brightness(),
            "rgb": state.get_rgb(),
        }
        print(f"      ✓ Bulb state: {'ON' if state.get_state() else 'OFF'}, brightness={state.get_brightness()}")
        await bulb.async_close()
    except asyncio.TimeoutError:
        results["errors"].append("pywizlight timeout")
        print("      ✗ Connection timeout")
    except Exception as e:
        results["errors"].append(f"pywizlight: {e}")
        print(f"      ✗ Error: {e}")
    
    # 4. Control test
    if results["wiz_response"]:
        print(f"[4/4] Testing bulb control (toggle)...")
        try:
            bulb = wizlight(ip)
            state = await bulb.updateState()
            was_on = state.get_state()
            
            # Toggle
            if was_on:
                await bulb.turn_off()
                await asyncio.sleep(0.5)
                await bulb.turn_on()
            else:
                await bulb.turn_on()
                await asyncio.sleep(0.5)
                await bulb.turn_off()
            
            print("      ✓ Control test passed")
            await bulb.async_close()
        except Exception as e:
            results["errors"].append(f"Control: {e}")
            print(f"      ✗ Control failed: {e}")
    else:
        print("[4/4] Skipping control test (no connection)")
    
    return results


async def discover_and_diagnose():
    """Discover bulbs and diagnose connectivity."""
    print("=" * 50)
    print("WiZ Bulb Connectivity Diagnostics")
    print("=" * 50)
    print()
    
    # Try discovery first
    print("Discovering bulbs on network...")
    try:
        bulbs = await discover_lights(broadcast_space="192.168.1.255")
        if bulbs:
            print(f"Found {len(bulbs)} bulb(s):")
            for bulb in bulbs:
                print(f"  - {bulb.ip} (MAC: {bulb.mac})")
        else:
            print("No bulbs found via discovery.")
            print("This could mean:")
            print("  - Bulbs are on different subnet")
            print("  - Firewall blocking UDP broadcast")
            print("  - Bulbs in power-saving mode")
    except Exception as e:
        print(f"Discovery error: {e}")
    
    print()
    return bulbs if bulbs else []


async def main():
    # Check if IP provided
    if len(sys.argv) > 1:
        ip = sys.argv[1]
    else:
        ip = "192.168.1.4"  # Default Bedroom bulb
    
    await discover_and_diagnose()
    
    print()
    print(f"Diagnosing bulb at {ip}...")
    print("-" * 50)
    
    results = await diagnose_bulb(ip)
    
    print()
    print("=" * 50)
    print("DIAGNOSIS SUMMARY")
    print("=" * 50)
    
    if results["wiz_response"]:
        print("✓ Bulb is ONLINE and responding")
        print()
        print("If you experience intermittent issues:")
        print("  1. Check WiFi signal (RSSI -72 is moderate)")
        print("  2. Ensure bulb is on 2.4GHz network")
        print("  3. Reserve IP in router (prevents DHCP changes)")
        print("  4. Add firewall rule for UDP 38899")
    else:
        print("✗ Bulb is NOT responding")
        print()
        print("Possible causes:")
        for error in results["errors"]:
            print(f"  - {error}")
        print()
        print("Try these fixes:")
        print("  1. Toggle bulb power (off then on)")
        print("  2. Check bulb in WiZ app (should show online)")
        print("  3. Restart your router")
        print("  4. Ensure 'Local Control' is ON in WiZ app settings")


if __name__ == "__main__":
    asyncio.run(main())
